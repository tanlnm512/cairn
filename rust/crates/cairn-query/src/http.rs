//! Shared minimal HTTP/1.1 client: request write, response framing, chunked decode.

use std::io::{Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::time::Duration;

/// One parsed HTTP/1.1 response.
pub struct HttpReply {
    pub status: u16,
    pub body: Vec<u8>,
}

/// Perform one `Connection: close` HTTP/1.1 request over a fresh connection;
/// `extra_headers` are pre-rendered `Name: value` lines. Loopback-tool grade:
/// no TLS, no redirects, no keep-alive.
pub fn request(
    host: &str,
    port: u16,
    method: &str,
    path: &str,
    body: Option<&str>,
    extra_headers: &[&str],
    timeout: Duration,
) -> Result<HttpReply, String> {
    let bare = host.trim_matches(['[', ']']);
    let addr = (bare, port)
        .to_socket_addrs()
        .map_err(|e| format!("resolve {host}:{port}: {e}"))?
        .next()
        .ok_or_else(|| format!("resolve {host}:{port}: no address"))?;
    let mut stream = TcpStream::connect_timeout(&addr, timeout)
        .map_err(|e| format!("connect {host}:{port}: {e}"))?;
    stream
        .set_read_timeout(Some(timeout))
        .map_err(|e| e.to_string())?;
    stream
        .set_write_timeout(Some(timeout))
        .map_err(|e| e.to_string())?;
    let mut request = format!("{method} {path} HTTP/1.1\r\nHost: {host}:{port}\r\n");
    if let Some(body) = body {
        request.push_str("Content-Type: application/json\r\n");
        request.push_str(&format!("Content-Length: {}\r\n", body.len()));
    }
    for header in extra_headers {
        request.push_str(header);
        request.push_str("\r\n");
    }
    request.push_str("Connection: close\r\n\r\n");
    stream
        .write_all(request.as_bytes())
        .map_err(|e| format!("send: {e}"))?;
    if let Some(body) = body {
        stream
            .write_all(body.as_bytes())
            .map_err(|e| format!("send: {e}"))?;
    }
    let mut raw = Vec::new();
    stream
        .read_to_end(&mut raw)
        .map_err(|e| format!("read: {e}"))?;
    parse_response(&raw)
}

/// Decode one complete (read-to-end) response: status line, header walk, then
/// content-length, chunked, or eof-terminated body framing.
pub fn parse_response(raw: &[u8]) -> Result<HttpReply, String> {
    let header_end = find_subsequence(raw, b"\r\n\r\n")
        .ok_or_else(|| "response missing header terminator".to_string())?;
    let head = String::from_utf8_lossy(&raw[..header_end]);
    let mut lines = head.split("\r\n");
    let status_line = lines.next().unwrap_or_default();
    let status = status_line
        .split_whitespace()
        .nth(1)
        .and_then(|s| s.parse::<u16>().ok())
        .ok_or_else(|| format!("bad status line: {status_line}"))?;
    let mut content_length: Option<usize> = None;
    let mut chunked = false;
    for line in lines {
        let Some((name, value)) = line.split_once(':') else {
            continue;
        };
        let value = value.trim();
        if name.eq_ignore_ascii_case("content-length") {
            content_length = value.parse().ok();
        } else if name.eq_ignore_ascii_case("transfer-encoding")
            // contains(), so multi-coding headers ("gzip, chunked") still mean chunked
            && value.to_ascii_lowercase().contains("chunked")
        {
            chunked = true;
        }
    }
    let body_raw = &raw[header_end + 4..];
    let body = if chunked {
        decode_chunked(body_raw)?
    } else if let Some(len) = content_length {
        body_raw[..len.min(body_raw.len())].to_vec()
    } else {
        body_raw.to_vec()
    };
    Ok(HttpReply { status, body })
}

/// Concatenate chunked framing into the decoded body; chunk extensions after
/// `;` on a size line are ignored.
pub fn decode_chunked(body: &[u8]) -> Result<Vec<u8>, String> {
    let mut out = Vec::new();
    let mut rest = body;
    loop {
        let line_end = find_subsequence(rest, b"\r\n").ok_or("bad chunk size")?;
        let size_text = String::from_utf8_lossy(&rest[..line_end]);
        let size = usize::from_str_radix(size_text.split(';').next().unwrap_or("").trim(), 16)
            .map_err(|_| "bad chunk size".to_string())?;
        rest = &rest[line_end + 2..];
        if size == 0 {
            return Ok(out);
        }
        if rest.len() < size {
            return Err("truncated chunk".to_string());
        }
        out.extend_from_slice(&rest[..size]);
        rest = rest.get(size + 2..).unwrap_or(&[]);
    }
}

fn find_subsequence(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    haystack
        .windows(needle.len())
        .position(|window| window == needle)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_content_length_chunked_and_eof_framings() {
        let raw =
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 5\r\n\r\nhello";
        let reply = parse_response(raw).expect("parse");
        assert_eq!(reply.status, 200);
        assert_eq!(reply.body, b"hello");

        let chunked =
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n0\r\n\r\n";
        let reply = parse_response(chunked).expect("parse chunked");
        assert_eq!(reply.status, 200);
        assert_eq!(reply.body, b"hello");

        let eof = b"HTTP/1.1 200 OK\r\nX-Nothing: 1\r\n\r\neof-terminated";
        let reply = parse_response(eof).expect("parse eof");
        assert_eq!(reply.body, b"eof-terminated");
    }

    #[test]
    fn reports_bad_status_lines() {
        assert!(parse_response(b"garbage\r\n\r\n").is_err());
    }

    #[test]
    fn decodes_extension_suffixed_chunk_sizes() {
        let raw =
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n4;q=1\r\ncair\r\n0\r\n\r\n";
        let reply = parse_response(raw).expect("extension-suffixed chunk size");
        assert_eq!(reply.status, 200);
        assert_eq!(reply.body, b"cair");
    }

    #[test]
    fn multi_coding_transfer_encoding_still_means_chunked() {
        let raw =
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: gzip, chunked\r\n\r\n5\r\nhello\r\n0\r\n\r\n";
        let reply = parse_response(raw).expect("multi-coding chunked");
        assert_eq!(reply.status, 200);
        assert_eq!(reply.body, b"hello");
    }

    #[test]
    fn truncated_chunk_is_an_error() {
        assert!(decode_chunked(b"5\r\nhel").is_err());
    }
}
