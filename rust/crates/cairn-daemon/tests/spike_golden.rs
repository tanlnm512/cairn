//! Spike golden test: the served `search_symbols` responses match the recorded
//! fixture over streamable HTTP (queries taken from the fixture file).

use std::fs;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use regex::Regex;
use serde_json::{Value, json};

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../..")
        .canonicalize()
        .expect("repo root")
}

/// Copy-on-create scratch store; removes the directory on drop.
struct TempStore(PathBuf);

impl TempStore {
    fn from_fixture(src: &Path) -> Self {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let dir =
            std::env::temp_dir().join(format!("cairn-p05-spike-{}-{}", std::process::id(), nanos));
        fs::create_dir_all(&dir).expect("temp dir");
        fs::copy(src, dir.join("search-store.kg")).expect("copy fixture store");
        Self(dir)
    }
}

impl Drop for TempStore {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

// --- minimal streamable-HTTP MCP client (wire-level, like the recorder) ------

struct McpClient {
    stream: TcpStream,
    session_id: Option<String>,
    next_id: u64,
}

struct HttpResponse {
    headers: Vec<(String, String)>,
    body: String,
}

impl HttpResponse {
    fn header(&self, name: &str) -> Option<&str> {
        self.headers
            .iter()
            .find(|(k, _)| k.eq_ignore_ascii_case(name))
            .map(|(_, v)| v.as_str())
    }
}

impl McpClient {
    fn connect(addr: SocketAddr) -> std::io::Result<Self> {
        let stream = TcpStream::connect(addr)?;
        stream.set_read_timeout(Some(Duration::from_secs(30)))?;
        stream.set_write_timeout(Some(Duration::from_secs(30)))?;
        Ok(Self {
            stream,
            session_id: None,
            next_id: 0,
        })
    }

    fn post(&mut self, payload: &Value) -> std::io::Result<HttpResponse> {
        let body = payload.to_string();
        let mut request = format!(
            "POST /mcp HTTP/1.1\r\nHost: {}\r\nContent-Type: application/json\r\n\
             Accept: application/json, text/event-stream\r\nContent-Length: {}\r\n\
             Connection: keep-alive\r\n",
            self.stream.peer_addr()?,
            body.len()
        );
        if let Some(session) = &self.session_id {
            request.push_str(&format!("mcp-session-id: {session}\r\n"));
        }
        request.push_str("\r\n");
        request.push_str(&body);
        self.stream.write_all(request.as_bytes())?;
        self.read_response()
    }

    fn read_response(&mut self) -> std::io::Result<HttpResponse> {
        let mut pending: Vec<u8> = Vec::new();
        let mut buffer = [0u8; 4096];
        let header_end = loop {
            if let Some(pos) = find_subsequence(&pending, b"\r\n\r\n") {
                break pos;
            }
            let n = self.stream.read(&mut buffer)?;
            if n == 0 {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::UnexpectedEof,
                    "connection closed before headers",
                ));
            }
            pending.extend_from_slice(&buffer[..n]);
        };
        let head = String::from_utf8_lossy(&pending[..header_end]).to_string();
        let mut lines = head.split("\r\n");
        let status_line = lines.next().unwrap_or_default();
        assert!(
            status_line.contains(" 2"),
            "unexpected status: {status_line}"
        );
        let headers: Vec<(String, String)> = lines
            .filter_map(|l| l.split_once(':'))
            .map(|(k, v)| (k.to_string(), v.trim().to_string()))
            .collect();
        let header = |name: &str| {
            headers
                .iter()
                .find(|(k, _)| k.eq_ignore_ascii_case(name))
                .map(|(_, v)| v.clone())
        };
        pending.drain(..header_end + 4);
        let mut body: Vec<u8> = Vec::new();
        if header("transfer-encoding")
            .map(|v| v.to_ascii_lowercase().contains("chunked"))
            .unwrap_or(false)
        {
            loop {
                let size_line = read_line(&mut pending, &mut self.stream, &mut buffer)?;
                let size = usize::from_str_radix(size_line.trim(), 16)
                    .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
                if size == 0 {
                    // Consume the trailer section up to the final blank line.
                    loop {
                        let line = read_line(&mut pending, &mut self.stream, &mut buffer)?;
                        if line.trim().is_empty() {
                            break;
                        }
                    }
                    break;
                }
                let mut chunk_data =
                    read_exact_n(size + 2, &mut pending, &mut self.stream, &mut buffer)?;
                chunk_data.truncate(size);
                body.extend_from_slice(&chunk_data);
            }
        } else if let Some(len) = header("content-length").and_then(|v| v.parse::<usize>().ok()) {
            body = read_exact_n(len, &mut pending, &mut self.stream, &mut buffer)?;
        } else {
            loop {
                let n = self.stream.read(&mut buffer)?;
                if n == 0 {
                    break;
                }
                body.extend_from_slice(&buffer[..n]);
            }
        }
        Ok(HttpResponse {
            headers,
            body: String::from_utf8_lossy(&body).to_string(),
        })
    }

    fn initialize(&mut self) {
        let response = self
            .post(&json!({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "cairn-rust-spike-test", "version": "0"}
                }
            }))
            .expect("initialize");
        self.session_id = response.header("mcp-session-id").map(str::to_string);
        assert!(
            self.session_id.is_some(),
            "initialize carried no session id"
        );
        let _ = self
            .post(&json!({"jsonrpc": "2.0", "method": "notifications/initialized"}))
            .expect("initialized notification");
    }

    fn call_tool(&mut self, tool: &str, args: Value) -> Value {
        self.next_id += 1;
        let id = self.next_id;
        let response = self
            .post(&json!({
                "jsonrpc": "2.0",
                "id": id,
                "method": "tools/call",
                "params": {"name": tool, "arguments": args}
            }))
            .expect("tools/call");
        for message in parse_messages(&response.body) {
            if message.get("id").and_then(Value::as_u64) == Some(id) {
                if let Some(error) = message.get("error") {
                    panic!("tools/call returned error: {error}");
                }
                return message["result"].clone();
            }
        }
        panic!("no JSON-RPC response for id {id} in: {}", response.body);
    }
}

fn read_line(
    pending: &mut Vec<u8>,
    stream: &mut std::net::TcpStream,
    buffer: &mut [u8; 4096],
) -> std::io::Result<String> {
    loop {
        if let Some(pos) = pending.iter().position(|&b| b == b'\n') {
            let line: Vec<u8> = pending.drain(..=pos).collect();
            return Ok(String::from_utf8_lossy(&line)
                .trim_end_matches(['\r', '\n'])
                .to_string());
        }
        let n = stream.read(buffer)?;
        if n == 0 {
            return Err(std::io::Error::new(
                std::io::ErrorKind::UnexpectedEof,
                "connection closed mid-line",
            ));
        }
        pending.extend_from_slice(&buffer[..n]);
    }
}

fn read_exact_n(
    n: usize,
    pending: &mut Vec<u8>,
    stream: &mut std::net::TcpStream,
    buffer: &mut [u8; 4096],
) -> std::io::Result<Vec<u8>> {
    while pending.len() < n {
        let read = stream.read(buffer)?;
        if read == 0 {
            return Err(std::io::Error::new(
                std::io::ErrorKind::UnexpectedEof,
                "connection closed mid-body",
            ));
        }
        pending.extend_from_slice(&buffer[..read]);
    }
    Ok(pending.drain(..n).collect())
}

fn find_subsequence(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    haystack
        .windows(needle.len())
        .position(|window| window == needle)
}

fn parse_messages(body: &str) -> Vec<Value> {
    let trimmed = body.trim();
    if trimmed.starts_with('{') || trimmed.starts_with('[') {
        match serde_json::from_str::<Value>(trimmed).expect("json body") {
            Value::Array(items) => items,
            one => vec![one],
        }
    } else {
        trimmed
            .lines()
            .filter_map(|line| line.strip_prefix("data:"))
            .map(str::trim)
            .filter(|l| !l.is_empty() && *l != "[DONE]")
            .filter_map(|l| serde_json::from_str(l).ok())
            .collect()
    }
}

// --- fixture normalization (ported from the recorder's scrub rules) ----------

fn normalize_text(text: &str, rules: &[Rule]) -> String {
    let mut out = text.to_string();
    for rule in rules {
        out = rule(&out);
    }
    out
}

type Rule = Box<dyn Fn(&str) -> String>;

fn re_replace(pattern: &str, replacement: &'static str) -> Rule {
    let re = Regex::new(pattern).expect("rule regex");
    Box::new(move |text: &str| re.replace_all(text, replacement).to_string())
}

fn round_float(text: &str) -> String {
    let re = Regex::new(r"\b\d+\.\d{5,}\b").expect("float regex");
    re.replace_all(text, |caps: &regex::Captures| {
        format!("{:.4}", caps[0].parse::<f64>().unwrap_or(0.0))
    })
    .to_string()
}

fn mask_id6(text: &str) -> String {
    // Port of the lookbehind rule (?<=[-_.])[0-9a-f]{6}\b.
    let re = Regex::new(r"([-_.])[0-9a-f]{6}\b").expect("id6 regex");
    re.replace_all(text, "${1}<ID6>").to_string()
}

fn no_op(text: &str) -> String {
    text.to_string()
}

/// The fixture's declared normalization rules, in order. Path rules
/// (store_home/home/workspace) were already applied at capture time; spike
/// responses carry store-relative paths only, so they map to no-ops.
fn normalization_rules(fixture: &Value) -> Vec<Rule> {
    fixture["normalization"]["rules"]
        .as_array()
        .expect("rules list")
        .iter()
        .map(|r| r.as_str().expect("rule name"))
        .map(|name| match name {
            "iso_ts" => re_replace(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?",
                "<ISO>",
            ),
            "epoch" => re_replace(r"\b1[0-9]{9}\.\d+\b", "<EPOCH>"),
            "date" => re_replace(r"\d{4}-\d{2}-\d{2}", "<DATE>"),
            "duration" => re_replace(r"\b\d+\.\d+ ?m?s\b|\b\d+ ?ms\b", "<DUR>"),
            "age" => re_replace(r"\bjust now\b|\b\d+[smhd] old\b", "<AGE>"),
            "id12" => re_replace(r"\b[0-9a-f]{12}\b", "<ID>"),
            "id6" => Box::new(mask_id6 as fn(&str) -> String),
            "float" => Box::new(round_float as fn(&str) -> String),
            _ => Box::new(no_op as fn(&str) -> String),
        })
        .collect()
}

fn normalize_value(value: &Value, rules: &[Rule]) -> Value {
    match value {
        Value::String(text) => Value::String(normalize_text(text, rules)),
        Value::Array(items) => {
            Value::Array(items.iter().map(|v| normalize_value(v, rules)).collect())
        }
        Value::Object(map) => Value::Object(
            map.iter()
                .map(|(k, v)| (k.clone(), normalize_value(v, rules)))
                .collect(),
        ),
        other => other.clone(),
    }
}

// --- the golden test ---------------------------------------------------------

#[tokio::test(flavor = "multi_thread")]
async fn spike_serves_search_symbols_golden_equal() {
    let root = repo_root();
    let fixture_path = root.join("rust/golden/mcp/search_symbols.json");
    let store_path = root.join("rust/golden/spike/search-store.kg");
    let fixture: Value = serde_json::from_str(
        &fs::read_to_string(&fixture_path)
            .unwrap_or_else(|e| panic!("read fixture {}: {e}", fixture_path.display())),
    )
    .expect("fixture json");
    let entries = fixture["spike_queries"].as_array().unwrap_or_else(|| {
        panic!(
            "spike_queries missing in {}; regenerate with scripts/rust_migration/spike_golden.py",
            fixture_path.display()
        )
    });
    assert!(
        entries.len() >= 5,
        "expected >=5 spike queries, found {}",
        entries.len()
    );
    // The fixture's own capture participates as its first case.
    assert_eq!(
        fixture["arguments"]["pattern"], entries[0]["pattern"],
        "fixture arguments drift from spike_queries[0]"
    );
    assert_eq!(
        fixture["response"], entries[0]["response"],
        "fixture response drift from spike_queries[0]"
    );
    let rules = normalization_rules(&fixture);

    let temp = TempStore::from_fixture(&store_path);
    let (addr, handle) = cairn_daemon::server::serve(temp.0.join("search-store.kg"))
        .await
        .expect("serve");
    let mut client = McpClient::connect(addr).expect("connect");
    client.initialize();

    for entry in entries {
        let pattern = entry["pattern"].as_str().expect("pattern");
        let result = client.call_tool("search_symbols", json!({ "pattern": pattern }));
        let actual = json!({
            "isError": result.get("isError").cloned().unwrap_or(Value::Bool(false)),
            "content": result.get("content").cloned().unwrap_or(Value::Array(vec![])),
            "structuredContent": result.get("structuredContent").cloned().unwrap_or(Value::Null),
        });
        let expected = normalize_value(&entry["response"], &rules);
        let actual = normalize_value(&actual, &rules);
        assert_eq!(
            actual, expected,
            "spike response differs from fixture for pattern {pattern:?}"
        );
    }

    handle.stop();
}
