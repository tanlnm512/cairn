//! Shared wire-level test harness: a minimal streamable-HTTP MCP client plus a
//! plain HTTP probe, shaped like the golden recorder.
#![allow(dead_code)]

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::time::Duration;

use serde_json::{Value, json};

pub struct HttpResponse {
    pub status_line: String,
    pub headers: Vec<(String, String)>,
    pub body: String,
}

impl HttpResponse {
    pub fn status(&self) -> u16 {
        self.status_line
            .split_whitespace()
            .nth(1)
            .and_then(|s| s.parse().ok())
            .unwrap_or(0)
    }

    pub fn header(&self, name: &str) -> Option<&str> {
        self.headers
            .iter()
            .find(|(k, _)| k.eq_ignore_ascii_case(name))
            .map(|(_, v)| v.as_str())
    }
}

/// One raw HTTP request (keep-alive not reused); returns the parsed response.
pub fn http_request(
    addr: SocketAddr,
    method: &str,
    path: &str,
    headers: &[(&str, &str)],
    body: Option<&str>,
) -> std::io::Result<HttpResponse> {
    let mut stream = TcpStream::connect(addr)?;
    stream.set_read_timeout(Some(Duration::from_secs(30)))?;
    stream.set_write_timeout(Some(Duration::from_secs(30)))?;
    let payload = body.unwrap_or("");
    let mut request = format!(
        "{method} {path} HTTP/1.1\r\nHost: {}\r\nContent-Length: {}\r\nConnection: close\r\n",
        stream.peer_addr()?,
        payload.len()
    );
    for (name, value) in headers {
        request.push_str(&format!("{name}: {value}\r\n"));
    }
    request.push_str("\r\n");
    request.push_str(payload);
    stream.write_all(request.as_bytes())?;
    read_full_response(&mut stream)
}

fn read_full_response(stream: &mut TcpStream) -> std::io::Result<HttpResponse> {
    let mut pending: Vec<u8> = Vec::new();
    let mut buffer = [0u8; 4096];
    let header_end = loop {
        if let Some(pos) = find_subsequence(&pending, b"\r\n\r\n") {
            break pos;
        }
        let n = stream.read(&mut buffer)?;
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
    let status_line = lines.next().unwrap_or_default().to_string();
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
            let size_line = read_line(&mut pending, stream, &mut buffer)?;
            let size = usize::from_str_radix(size_line.trim(), 16)
                .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
            if size == 0 {
                // Consume the trailer section up to the final blank line.
                loop {
                    let line = read_line(&mut pending, stream, &mut buffer)?;
                    if line.trim().is_empty() {
                        break;
                    }
                }
                break;
            }
            let mut chunk_data = read_exact_n(size + 2, &mut pending, stream, &mut buffer)?;
            chunk_data.truncate(size);
            body.extend_from_slice(&chunk_data);
        }
    } else if let Some(len) = header("content-length").and_then(|v| v.parse::<usize>().ok()) {
        body = read_exact_n(len, &mut pending, stream, &mut buffer)?;
    } else {
        loop {
            let n = stream.read(&mut buffer)?;
            if n == 0 {
                break;
            }
            body.extend_from_slice(&buffer[..n]);
        }
    }
    Ok(HttpResponse {
        status_line,
        headers,
        body: String::from_utf8_lossy(&body).to_string(),
    })
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

// --- minimal streamable-HTTP MCP client --------------------------------------

pub struct McpClient {
    addr: SocketAddr,
    session_id: Option<String>,
    next_id: u64,
    /// Bearer credential sent with every request (None for the keyless surface).
    pub token: Option<String>,
}

impl McpClient {
    pub fn connect(addr: SocketAddr) -> std::io::Result<Self> {
        Ok(Self {
            addr,
            session_id: None,
            next_id: 0,
            token: None,
        })
    }

    pub fn keyed(addr: SocketAddr, token: &str) -> std::io::Result<Self> {
        Ok(Self {
            addr,
            session_id: None,
            next_id: 0,
            token: Some(token.to_string()),
        })
    }

    fn post(&self, payload: &Value) -> std::io::Result<HttpResponse> {
        let mut headers: Vec<(&str, &str)> = vec![
            ("Content-Type", "application/json"),
            ("Accept", "application/json, text/event-stream"),
        ];
        let session_storage;
        if let Some(session) = &self.session_id {
            session_storage = session.clone();
            headers.push(("mcp-session-id", &session_storage));
        }
        let token_storage;
        if let Some(token) = &self.token {
            token_storage = format!("Bearer {token}");
            headers.push(("Authorization", &token_storage));
        }
        http_request(
            self.addr,
            "POST",
            "/mcp",
            &headers,
            Some(&payload.to_string()),
        )
    }

    pub fn initialize(&mut self) {
        let response = self
            .post(&json!({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "cairn-rust-daemon-test", "version": "0"}
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

    pub fn list_tools(&mut self) -> Vec<String> {
        self.next_id += 1;
        let id = self.next_id;
        let response = self
            .post(&json!({
                "jsonrpc": "2.0",
                "id": id,
                "method": "tools/list",
                "params": {}
            }))
            .expect("tools/list");
        for message in parse_messages(&response.body) {
            if message.get("id").and_then(Value::as_u64) == Some(id) {
                if let Some(error) = message.get("error") {
                    panic!("tools/list returned error: {error}");
                }
                return message["result"]["tools"]
                    .as_array()
                    .expect("tools array")
                    .iter()
                    .map(|t| t["name"].as_str().expect("tool name").to_string())
                    .collect();
            }
        }
        panic!("no JSON-RPC response for id {id} in: {}", response.body);
    }

    pub fn call_tool(&mut self, tool: &str, args: Value) -> Value {
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
                return message;
            }
        }
        panic!("no JSON-RPC response for id {id} in: {}", response.body);
    }
}

pub fn parse_messages(body: &str) -> Vec<Value> {
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

/// Copy-on-create scratch store; removes the directory on drop.
pub struct TempStore(pub PathBuf);

impl TempStore {
    pub fn new(tag: &str) -> Self {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let dir = std::env::temp_dir().join(format!(
            "cairn-daemon-test-{}-{nanos}-{tag}",
            std::process::id()
        ));
        std::fs::create_dir_all(&dir).expect("temp dir");
        Self(dir)
    }

    /// Scratch store seeded with a copy of `src` (fixture file or tree).
    pub fn from_fixture(src: &std::path::Path, tag: &str) -> Self {
        let temp = Self::new(tag);
        if src.is_dir() {
            copy_tree(src, &temp.0);
        } else {
            let name = src.file_name().expect("fixture file name");
            std::fs::copy(src, temp.0.join(name)).expect("copy fixture store");
        }
        temp
    }
}

fn copy_tree(src: &std::path::Path, dst: &std::path::Path) {
    std::fs::create_dir_all(dst).expect("dst dir");
    for entry in std::fs::read_dir(src).expect("src dir") {
        let entry = entry.expect("entry");
        let target = dst.join(entry.file_name());
        if entry.path().is_dir() {
            copy_tree(&entry.path(), &target);
        } else {
            std::fs::copy(entry.path(), &target).expect("copy file");
        }
    }
}

impl Drop for TempStore {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}
