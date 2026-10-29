//! Fixture schema, keyed MCP golden client, normalization and diff oracle shared by the replay oracles.

use std::fmt::Write as _;
use std::io::{self, BufRead, BufReader, Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::path::{Component, Path, PathBuf};
use std::process::ExitCode;
use std::time::Duration;

use cairn_query::json_compat::dumps_with;
use cairn_query::textfmt::{split_lines_universal, title_bool};
use regex::{Captures, Regex};
use serde_json::{Map, Value, json};

pub(crate) const SCHEMA: &str = "cairn-rust-golden-mcp/1";
const HTTP_TIMEOUT: Duration = Duration::from_secs(120);
const PROTOCOL_VERSION: &str = "2025-03-26";
const SNIPPET_CHARS: usize = 200;
/// `CAIRN_WATCH=0` keeps the live watcher from reindexing the workspace mid-capture.
pub(crate) const ENV_PINS: [(&str, &str); 3] = [
    ("CAIRN_EMBED_BACKEND", "hash"),
    ("CAIRN_RERANK", "0"),
    ("CAIRN_WATCH", "0"),
];

/// `cargo xtask golden-record`: library-only; the MCP goldens are frozen and replayed by `parity`.
pub fn run(_args: &[String]) -> ExitCode {
    eprintln!(
        "golden-record: no recorder; the MCP goldens are frozen (replay: cargo xtask parity)"
    );
    ExitCode::SUCCESS
}

// --- http ---------------------------------------------------------------------

/// One HTTP/1.1 response; `close` is set when the connection cannot be reused.
pub(crate) struct HttpResponse {
    pub(crate) status: u16,
    headers: Vec<(String, String)>,
    body: Vec<u8>,
    close: bool,
}

impl HttpResponse {
    fn header(&self, name: &str) -> Option<&str> {
        self.headers
            .iter()
            .find(|(key, _)| key.eq_ignore_ascii_case(name))
            .map(|(_, value)| value.as_str())
    }
}

/// Connect to `host:port` with `timeout` applied to connect, reads and writes.
pub(crate) fn http_connect(addr: &str, timeout: Duration) -> io::Result<BufReader<TcpStream>> {
    let mut last = io::Error::new(io::ErrorKind::NotFound, format!("no address for {addr}"));
    for sock in addr.to_socket_addrs()? {
        match TcpStream::connect_timeout(&sock, timeout) {
            Ok(stream) => {
                stream.set_read_timeout(Some(timeout))?;
                stream.set_write_timeout(Some(timeout))?;
                return Ok(BufReader::new(stream));
            }
            Err(err) => last = err,
        }
    }
    Err(last)
}

/// Send one request (`head` without the blank line) and read the full response.
pub(crate) fn http_exchange(
    conn: &mut BufReader<TcpStream>,
    head: &str,
    body: &[u8],
) -> io::Result<HttpResponse> {
    let stream = conn.get_mut();
    stream.write_all(format!("{head}Content-Length: {}\r\n\r\n", body.len()).as_bytes())?;
    stream.write_all(body)?;
    stream.flush()?;
    read_response(conn)
}

fn read_line(conn: &mut BufReader<TcpStream>) -> io::Result<String> {
    let mut buf = Vec::new();
    if conn.read_until(b'\n', &mut buf)? == 0 {
        return Err(io::Error::new(
            io::ErrorKind::UnexpectedEof,
            "remote end closed connection without response",
        ));
    }
    Ok(String::from_utf8_lossy(&buf).trim_end().to_string())
}

fn read_response(conn: &mut BufReader<TcpStream>) -> io::Result<HttpResponse> {
    let (version, status) = loop {
        let line = read_line(conn)?;
        let mut parts = line.split_whitespace();
        let version = parts.next().unwrap_or_default().to_string();
        let status: u16 = parts
            .next()
            .and_then(|code| code.parse().ok())
            .ok_or_else(|| {
                io::Error::new(
                    io::ErrorKind::InvalidData,
                    format!("bad status line {line:?}"),
                )
            })?;
        if status == 100 {
            while !read_line(conn)?.is_empty() {}
            continue;
        }
        break (version, status);
    };
    let mut headers = Vec::new();
    loop {
        let line = read_line(conn)?;
        if line.is_empty() {
            break;
        }
        if let Some((key, value)) = line.split_once(':') {
            headers.push((key.trim().to_string(), value.trim().to_string()));
        }
    }
    let mut resp = HttpResponse {
        status,
        headers,
        body: Vec::new(),
        close: false,
    };
    let conn_header = resp.header("connection").map(str::to_ascii_lowercase);
    resp.close = match conn_header.as_deref() {
        Some(value) if value.contains("close") => true,
        Some(value) if value.contains("keep-alive") => false,
        _ => version == "HTTP/1.0",
    };
    let chunked = resp
        .header("transfer-encoding")
        .is_some_and(|value| value.to_ascii_lowercase().contains("chunked"));
    let length = resp
        .header("content-length")
        .and_then(|value| value.parse::<usize>().ok());
    if status == 204 || status == 304 || (100..200).contains(&status) {
    } else if chunked {
        resp.body = read_chunked(conn)?;
    } else if let Some(length) = length {
        resp.body = vec![0; length];
        conn.read_exact(&mut resp.body)?;
    } else {
        conn.read_to_end(&mut resp.body)?;
        resp.close = true;
    }
    Ok(resp)
}

fn read_chunked(conn: &mut BufReader<TcpStream>) -> io::Result<Vec<u8>> {
    let mut body = Vec::new();
    loop {
        let line = read_line(conn)?;
        let size_text = line.split(';').next().unwrap_or_default().trim();
        let size = usize::from_str_radix(size_text, 16).map_err(|_| {
            io::Error::new(
                io::ErrorKind::InvalidData,
                format!("bad chunk size {line:?}"),
            )
        })?;
        if size == 0 {
            while !read_line(conn)?.is_empty() {}
            return Ok(body);
        }
        let start = body.len();
        body.resize(start + size, 0);
        conn.read_exact(&mut body[start..])?;
        read_line(conn)?;
    }
}

// --- client -------------------------------------------------------------------

/// Keyed streamable-HTTP MCP session: initialize -> initialized -> tools/call over keep-alive.
pub(crate) struct GoldenClient {
    addr: String,
    path: String,
    api_key: String,
    conn: Option<BufReader<TcpStream>>,
    session_id: Option<String>,
    next_id: u64,
}

impl GoldenClient {
    /// Client for an `http://host:port/path` endpoint authenticated with a bearer `api_key`.
    pub(crate) fn new(url: &str, api_key: &str) -> Result<Self, String> {
        let rest = url
            .strip_prefix("http://")
            .ok_or_else(|| format!("not an http url: {url}"))?;
        let (addr, path) = rest.split_once('/').unwrap_or((rest, ""));
        Ok(Self {
            addr: addr.to_string(),
            path: format!("/{path}"),
            api_key: api_key.to_string(),
            conn: None,
            session_id: None,
            next_id: 0,
        })
    }

    fn post(&mut self, payload: &Value) -> io::Result<HttpResponse> {
        let result = self.post_once(payload);
        if result.as_ref().map_or(true, |resp| resp.close) {
            self.conn = None;
        }
        result
    }

    fn post_once(&mut self, payload: &Value) -> io::Result<HttpResponse> {
        let conn = match self.conn.take() {
            Some(conn) => self.conn.insert(conn),
            None => self.conn.insert(http_connect(&self.addr, HTTP_TIMEOUT)?),
        };
        let mut head = format!(
            "POST {} HTTP/1.1\r\nHost: {}\r\nAccept-Encoding: identity\r\n\
             Content-Type: application/json\r\nAccept: application/json, text/event-stream\r\n\
             Authorization: Bearer {}\r\n",
            self.path, self.addr, self.api_key
        );
        if let Some(session) = &self.session_id {
            let _ = write!(head, "mcp-session-id: {session}\r\n");
        }
        http_exchange(conn, &head, &serde_json::to_vec(payload)?)
    }

    fn rpc(&mut self, payload: &Value) -> Result<Vec<Value>, String> {
        let resp = match self.post(payload) {
            Ok(resp) => resp,
            Err(_) => self
                .post(payload)
                .map_err(|err| format!("MCP request failed: {err}"))?,
        };
        parse_messages(&resp.body)
    }

    /// Initialize the session and return the tools/list catalog.
    pub(crate) fn start(&mut self) -> Result<Vec<String>, String> {
        self.next_id = 0;
        let init = json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "rust-migration-baseline", "version": "0"},
            },
        });
        let resp = self
            .post(&init)
            .map_err(|err| format!("MCP initialize failed: {err}"))?;
        parse_messages(&resp.body)?;
        let session = resp
            .header("mcp-session-id")
            .filter(|value| !value.is_empty())
            .ok_or("initialize carried no mcp-session-id")?;
        self.session_id = Some(session.to_string());
        self.rpc(&json!({"jsonrpc": "2.0", "method": "notifications/initialized"}))?;
        let listing = self.rpc(&json!({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}))?;
        let tools = listing
            .first()
            .and_then(|msg| msg["result"]["tools"].as_array())
            .ok_or("tools/list carried no result.tools")?;
        Ok(tools
            .iter()
            .map(|tool| tool["name"].as_str().unwrap_or_default().to_string())
            .collect())
    }

    fn tools_call(&mut self, tool: &str, args: &Value) -> Result<Value, String> {
        self.next_id += 1;
        let messages = self.rpc(&json!({
            "jsonrpc": "2.0",
            "id": self.next_id,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        }))?;
        Ok(messages.into_iter().next().unwrap_or_else(|| json!({})))
    }

    /// One tools/call; returns (is_error, first content text truncated to a snippet).
    pub(crate) fn call(&mut self, tool: &str, args: &Value) -> Result<(bool, String), String> {
        let msg = self.tools_call(tool, args)?;
        if let Some(error) = msg.get("error") {
            return Ok((true, truncate_chars(&repr_value(error), SNIPPET_CHARS)));
        }
        let result = truthy_or(msg.get("result"), json!({}));
        let text = result
            .get("content")
            .and_then(Value::as_array)
            .and_then(|content| content.first())
            .and_then(|first| first.get("text"))
            .and_then(Value::as_str)
            .unwrap_or_default();
        Ok((
            is_truthy(result.get("isError")),
            truncate_chars(text, SNIPPET_CHARS),
        ))
    }

    /// One tools/call; the full result block (`isError`, `content`, `structuredContent`).
    pub(crate) fn capture(&mut self, tool: &str, args: &Value) -> Result<Value, String> {
        let msg = self.tools_call(tool, args)?;
        if let Some(error) = msg.get("error") {
            return Ok(json!({
                "isError": true,
                "content": [{"type": "text", "text": dumps_with(error, None, true)}],
                "structuredContent": null,
            }));
        }
        let result = truthy_or(msg.get("result"), json!({}));
        Ok(json!({
            "isError": is_truthy(result.get("isError")),
            "content": truthy_or(result.get("content"), json!([])),
            "structuredContent": result.get("structuredContent").cloned().unwrap_or(Value::Null),
        }))
    }
}

/// JSON-RPC messages from a plain JSON body or an SSE stream (empty keepalive frames skipped).
fn parse_messages(body: &[u8]) -> Result<Vec<Value>, String> {
    let raw = std::str::from_utf8(body).map_err(|err| format!("MCP body is not UTF-8: {err}"))?;
    let raw = raw.trim();
    let parse = |text: &str| {
        serde_json::from_str(text).map_err(|err| format!("bad MCP JSON {text:?}: {err}"))
    };
    if raw.starts_with('{') {
        return Ok(vec![parse(raw)?]);
    }
    split_lines_universal(raw)
        .into_iter()
        .filter_map(|line| line.strip_prefix("data:").map(str::trim))
        .filter(|payload| !payload.is_empty())
        .map(parse)
        .collect()
}

// --- normalization ------------------------------------------------------------

enum Replacement {
    Template(String),
    RoundFloat,
}

/// One named scrub rule: a regex and its replacement template (`${1}` expands captures).
pub(crate) struct ScrubRule {
    pub(crate) name: String,
    pattern: Regex,
    replacement: Replacement,
}

impl ScrubRule {
    /// Rule replacing every `pattern` match with `template`; panics on an invalid pattern.
    pub(crate) fn new(name: &str, pattern: &str, template: &str) -> Self {
        Self::build(name, pattern, Replacement::Template(template.to_string()))
    }

    fn build(name: &str, pattern: &str, replacement: Replacement) -> Self {
        Self {
            name: name.to_string(),
            pattern: Regex::new(pattern).unwrap_or_else(|err| panic!("scrub rule {name}: {err}")),
            replacement,
        }
    }

    fn apply(&self, text: &str) -> String {
        match &self.replacement {
            Replacement::Template(template) => self
                .pattern
                .replace_all(text, template.as_str())
                .into_owned(),
            Replacement::RoundFloat => self
                .pattern
                .replace_all(text, |caps: &Captures| match caps[0].parse::<f64>() {
                    Ok(value) => format!("{value:.4}"),
                    Err(_) => caps[0].to_string(),
                })
                .into_owned(),
        }
    }
}

/// Ordered scrub rules: longest path rules first, each in unresolved and realpath variants.
pub(crate) fn scrub_rules(store_home: &Path, home: &Path, workspace: &Path) -> Vec<ScrubRule> {
    let variants = [
        ("store_home", store_home, "<STORE_HOME>"),
        ("home", home, "<CAIRN_HOME>"),
        ("workspace", workspace, "<WORKSPACE>"),
    ];
    let mut resolved = Vec::new();
    let mut bases = Vec::new();
    for (name, base, placeholder) in variants {
        let real = realpath(base);
        if real != base {
            let pattern = regex::escape(&real.to_string_lossy());
            resolved.push(ScrubRule::new(
                &format!("{name}_resolved"),
                &pattern,
                placeholder,
            ));
        }
        bases.push(ScrubRule::new(
            name,
            &regex::escape(&base.to_string_lossy()),
            placeholder,
        ));
    }
    resolved.sort_by_key(|rule| std::cmp::Reverse(rule.pattern.as_str().len()));
    bases.sort_by_key(|rule| std::cmp::Reverse(rule.pattern.as_str().len()));
    let mut rules = resolved;
    rules.append(&mut bases);
    rules.extend([
        ScrubRule::new(
            "iso_ts",
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?",
            "<ISO>",
        ),
        ScrubRule::new("epoch", r"\b1[0-9]{9}\.\d+\b", "<EPOCH>"),
        ScrubRule::new("date", r"\d{4}-\d{2}-\d{2}", "<DATE>"),
        ScrubRule::new("duration", r"\b\d+\.\d+ ?m?s\b|\b\d+ ?ms\b", "<DUR>"),
        ScrubRule::new("age", r"\bjust now\b|\b\d+[smhd] old\b", "<AGE>"),
        ScrubRule::new("id12", r"\b[0-9a-f]{12}\b", "<ID>"),
        ScrubRule::new("id6", r"([-_.])[0-9a-f]{6}\b", "${1}<ID6>"),
        ScrubRule::build("float", r"\b\d+\.\d{5,}\b", Replacement::RoundFloat),
    ]);
    rules
}

/// Apply `rules` in order, then optionally mask `label: [..]` list lines and sort lines.
pub(crate) fn normalize_text(
    text: &str,
    rules: &[ScrubRule],
    sort_lines: bool,
    mask_list_lines: bool,
) -> String {
    let mut text = text.to_string();
    for rule in rules {
        text = rule.apply(&text);
    }
    if mask_list_lines {
        let list_line =
            Regex::new(r"^(\s*[\w ]+:) \[(.*)\]\s*$").unwrap_or_else(|err| panic!("{err}"));
        text = split_lines_universal(&text)
            .into_iter()
            .map(|line| {
                list_line
                    .replace(line, |caps: &Captures| {
                        let body = caps[2].trim();
                        let members = if body.is_empty() {
                            0
                        } else {
                            body.split("', '").count()
                        };
                        format!("{} [{members} members]", &caps[1])
                    })
                    .into_owned()
            })
            .collect::<Vec<_>>()
            .join("\n");
    }
    if sort_lines {
        let mut lines = split_lines_universal(&text);
        lines.sort_unstable();
        text = lines.join("\n");
    }
    text
}

/// Normalize every string inside a JSON value (object keys untouched).
pub(crate) fn normalize_obj(
    obj: &Value,
    rules: &[ScrubRule],
    sort_lines: bool,
    mask_list_lines: bool,
) -> Value {
    match obj {
        Value::String(text) => {
            Value::String(normalize_text(text, rules, sort_lines, mask_list_lines))
        }
        Value::Array(items) => Value::Array(
            items
                .iter()
                .map(|item| normalize_obj(item, rules, sort_lines, mask_list_lines))
                .collect(),
        ),
        Value::Object(map) => Value::Object(
            map.iter()
                .map(|(key, value)| {
                    (
                        key.clone(),
                        normalize_obj(value, rules, sort_lines, mask_list_lines),
                    )
                })
                .collect(),
        ),
        other => other.clone(),
    }
}

/// Normalized `{isError, content, structuredContent}` block of a captured or golden response.
pub(crate) fn normalize_response(
    response: &Value,
    rules: &[ScrubRule],
    sort_lines: bool,
    mask_list_lines: bool,
) -> Value {
    let content: Vec<Value> = response
        .get("content")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .map(|item| match item.get("text").and_then(Value::as_str) {
                    Some(text) if item.is_object() => {
                        let mut item = item.clone();
                        item["text"] =
                            Value::String(normalize_text(text, rules, sort_lines, mask_list_lines));
                        item
                    }
                    _ => item.clone(),
                })
                .collect()
        })
        .unwrap_or_default();
    let structured = response.get("structuredContent").unwrap_or(&Value::Null);
    json!({
        "isError": response.get("isError").cloned().unwrap_or(Value::Null),
        "content": content,
        "structuredContent": normalize_obj(structured, rules, sort_lines, mask_list_lines),
    })
}

// --- diff oracle ----------------------------------------------------------------

/// The fixture fields the parity oracle compares (capture meta excluded).
pub(crate) fn compare_scope(fixture: &Value) -> Value {
    let scope: Map<String, Value> = ["schema", "tool", "arguments", "normalization", "response"]
        .into_iter()
        .map(|key| {
            (
                key.to_string(),
                fixture.get(key).cloned().unwrap_or(Value::Null),
            )
        })
        .collect();
    Value::Object(scope)
}

/// First structural difference between two JSON values as a `$.path: reason` line.
pub(crate) fn diff_path(expected: &Value, actual: &Value, path: &str) -> Option<String> {
    let (want, got) = (value_type_name(expected), value_type_name(actual));
    if want != got {
        return Some(format!("{path}: type {want} != {got}"));
    }
    match (expected, actual) {
        (Value::Object(left), Value::Object(right)) => {
            let mut keys: Vec<&String> = left.keys().chain(right.keys()).collect();
            keys.sort_unstable();
            keys.dedup();
            keys.into_iter()
                .find_map(|key| match (left.get(key), right.get(key)) {
                    (None, _) => Some(format!("{path}.{key}: missing in golden")),
                    (_, None) => Some(format!("{path}.{key}: missing in capture")),
                    (Some(left), Some(right)) => diff_path(left, right, &format!("{path}.{key}")),
                })
        }
        (Value::Array(left), Value::Array(right)) => {
            if left.len() != right.len() {
                return Some(format!("{path}: len {} != {}", left.len(), right.len()));
            }
            left.iter()
                .zip(right)
                .enumerate()
                .find_map(|(i, (left, right))| diff_path(left, right, &format!("{path}[{i}]")))
        }
        _ if expected == actual => None,
        _ => Some(format!(
            "{path}: {} != {}",
            repr_value(expected),
            repr_value(actual)
        )),
    }
}

// --- json-compat helpers ------------------------------------------------

/// `os.path.realpath`: absolute, symlinks resolved as far as the path exists.
pub(crate) fn realpath(path: &Path) -> PathBuf {
    let absolute = std::path::absolute(path).unwrap_or_else(|_| path.to_path_buf());
    if let Ok(real) = absolute.canonicalize() {
        return real;
    }
    let mut tail = Vec::new();
    let mut head = absolute.as_path();
    while let (Some(parent), Some(name)) = (head.parent(), head.file_name()) {
        tail.push(name.to_os_string());
        head = parent;
        if let Ok(real) = head.canonicalize() {
            return tail.iter().rev().fold(real, |acc, part| acc.join(part));
        }
    }
    absolute
        .components()
        .filter(|c| !matches!(c, Component::CurDir))
        .collect()
}

fn truncate_chars(text: &str, max: usize) -> String {
    text.chars().take(max).collect()
}

/// Truthiness of an optional JSON value (null, false, zero, empty are false).
pub(crate) fn is_truthy(value: Option<&Value>) -> bool {
    match value {
        None | Some(Value::Null) => false,
        Some(Value::Bool(flag)) => *flag,
        Some(Value::Number(n)) => n.as_f64().is_some_and(|v| v != 0.0),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(items)) => !items.is_empty(),
        Some(Value::Object(map)) => !map.is_empty(),
    }
}

fn truthy_or(value: Option<&Value>, fallback: Value) -> Value {
    match value {
        Some(value) if is_truthy(Some(value)) => value.clone(),
        _ => fallback,
    }
}

/// Type name of a JSON value.
pub(crate) fn value_type_name(value: &Value) -> &'static str {
    match value {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Number(n) if n.is_f64() => "float",
        Value::Number(_) => "int",
        Value::String(_) => "str",
        Value::Array(_) => "list",
        Value::Object(_) => "dict",
    }
}

/// `repr()` of a string: double-quoted when it holds `'` but no `"`, `\xNN` for controls.
pub(crate) fn repr_str(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::from(quote);
    for ch in text.chars() {
        match ch {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if (c as u32) < 0x20 || c as u32 == 0x7f => {
                let _ = write!(out, "\\x{:02x}", c as u32);
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

/// `repr()` of a JSON-decoded value.
fn repr_value(value: &Value) -> String {
    match value {
        Value::Null => "None".to_string(),
        Value::Bool(b) => title_bool(*b).to_string(),
        Value::Number(n) => match n.as_f64() {
            Some(v) if n.is_f64() => format!("{v:?}"),
            _ => n.to_string(),
        },
        Value::String(s) => repr_str(s),
        Value::Array(items) => format!(
            "[{}]",
            items.iter().map(repr_value).collect::<Vec<_>>().join(", ")
        ),
        Value::Object(map) => format!(
            "{{{}}}",
            map.iter()
                .map(|(key, value)| format!("{}: {}", repr_str(key), repr_value(value)))
                .collect::<Vec<_>>()
                .join(", ")
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_text_matches_the_normalizer() {
        let rules = scrub_rules(
            Path::new("/s"),
            Path::new("/s/home"),
            Path::new("/s/corpus"),
        );
        let cases = [
            (
                "/s/corpus/a.py and /s/home/x in /s",
                false,
                false,
                "<WORKSPACE>/a.py and <CAIRN_HOME>/x in <STORE_HOME>",
            ),
            ("tmp-abc123 x_0a1b2c.y", false, false, "tmp-<ID6> x_<ID6>.y"),
            (
                "abcdef012345 0.1234567 at 2026-01-02T03:04:05Z",
                false,
                false,
                "<ID> 0.1235 at <ISO>",
            ),
            (
                "  members: ['a', 'b', 'c']\nempty: []",
                false,
                true,
                "  members: [3 members]\nempty: [0 members]",
            ),
            ("b\r\na\rc\n", true, false, "a\nb\nc"),
        ];
        for (text, sort_lines, mask, want) in cases {
            assert_eq!(
                normalize_text(text, &rules, sort_lines, mask),
                want,
                "input {text:?}"
            );
        }
    }

    #[test]
    fn diff_path_reports_the_first_difference() {
        let golden = json!({"a": [1, {"b": "x"}], "n": 1});
        assert_eq!(diff_path(&golden, &golden.clone(), "$"), None);
        let cases = [
            (
                json!({"a": [1, {"b": "y"}], "n": 1}),
                "$.a[1].b: 'x' != 'y'",
            ),
            (
                json!({"a": [1, {"b": "x"}], "n": 1.0}),
                "$.n: type int != float",
            ),
            (json!({"a": [1], "n": 1}), "$.a: len 2 != 1"),
            (
                json!({"a": [1, {"b": "x"}], "n": 1, "z": null}),
                "$.z: missing in golden",
            ),
        ];
        for (actual, want) in cases {
            assert_eq!(diff_path(&golden, &actual, "$").as_deref(), Some(want));
        }
    }
}
