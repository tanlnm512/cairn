//! Search-family tools: `search_symbols` (FTS5 bm25 + LIKE fallback) and `semantic_search` (fusion pipeline).

use std::collections::{HashMap, HashSet};
use std::env;
use std::path::PathBuf;
use std::time::Duration;

use rusqlite::Connection;
use sha2::{Digest, Sha256};

// --- lexical: FTS5 bm25 with the LIKE fallback ------------------------------

/// Shown rows before the "... and N more" truncation line.
const SHOWN: usize = 50;

/// Primitive result cap matching `queries.search_symbols(limit=100)`.
const SEARCH_LIMIT: i64 = 100;

/// One `search_symbols` result row.
#[derive(Clone)]
pub struct SymbolRow {
    pub id: String,
    pub kind: String,
    pub name: String,
    pub qualified_name: Option<String>,
    pub file_path: String,
    pub line_start: Option<i64>,
    pub repo: String,
}

fn row_symbol(row: &rusqlite::Row<'_>) -> rusqlite::Result<SymbolRow> {
    Ok(SymbolRow {
        id: row.get("id")?,
        kind: row.get("kind")?,
        name: row.get("name")?,
        qualified_name: row.get("qualified_name")?,
        file_path: row.get("file_path")?,
        line_start: row.get("line_start")?,
        repo: row.get("repo")?,
    })
}

/// True iff `pattern` is a pure single-trailing-wildcard token (`"Api*"`).
pub fn is_fts_prefix_pattern(pattern: &str) -> bool {
    let p = pattern.trim();
    let Some(stripped) = p.strip_suffix('*') else {
        return false;
    };
    if stripped.contains('*') || p.contains('_') {
        return false;
    }
    let token = stripped.trim();
    !token.is_empty() && !token.contains([' ', '"', ':', '(', ')'])
}

/// Convert a user pattern into an FTS5 MATCH query; `None` selects the LIKE path.
pub fn pattern_to_fts(pattern: &str) -> Option<String> {
    let p = pattern.trim();
    if p.is_empty() {
        return None;
    }
    if is_fts_prefix_pattern(p) {
        return Some(format!(
            "{}*",
            p.strip_suffix('*').expect("suffix checked").trim()
        ));
    }
    let cleaned: String = p
        .chars()
        .map(|c| if c == '*' || c == '%' { ' ' } else { c })
        .collect();
    let tokens: Vec<&str> = cleaned
        .split(|c: char| !c.is_ascii_alphanumeric())
        .filter(|t| !t.is_empty())
        .collect();
    if tokens.is_empty() {
        return None;
    }
    Some(format!("\"{}\"*", tokens.join(" ")))
}

fn fts_rows(
    conn: &Connection,
    fts_query: &str,
    kind: Option<&str>,
    limit: i64,
) -> Result<Vec<SymbolRow>, rusqlite::Error> {
    let mut sql = String::from(
        "SELECT s.id, s.kind, s.name, s.qualified_name, s.line_start, \
         f.path AS file_path, f.repo_id AS repo, \
         bm25(symbols_fts) AS rank \
         FROM symbols_fts \
         JOIN symbols s ON s.rowid = symbols_fts.rowid \
         JOIN files f ON s.file_id = f.id \
         WHERE symbols_fts MATCH ?",
    );
    if kind.is_some() {
        sql.push_str(" AND s.kind = ?");
    }
    sql.push_str(" ORDER BY rank LIMIT ?");
    let mut stmt = conn.prepare(&sql)?;
    let map = |row: &rusqlite::Row<'_>| row_symbol(row);
    let rows = match kind {
        Some(k) => stmt.query_map(rusqlite::params![fts_query, k, limit], map)?,
        None => stmt.query_map(rusqlite::params![fts_query, limit], map)?,
    };
    rows.collect()
}

fn like_rows(
    conn: &Connection,
    pattern: &str,
    kind: Option<&str>,
    limit: i64,
) -> Result<Vec<SymbolRow>, rusqlite::Error> {
    let escaped: Vec<String> = pattern
        .split('*')
        .map(|segment| {
            segment
                .replace('\\', "\\\\")
                .replace('%', "\\%")
                .replace('_', "\\_")
        })
        .collect();
    let mut sql_pattern = escaped.join("%");
    if !pattern.contains('*') {
        sql_pattern = format!("%{sql_pattern}%");
    }
    let sql = if kind.is_some() {
        "SELECT s.id, s.kind, s.name, s.qualified_name, s.line_start, \
         f.path AS file_path, f.repo_id AS repo \
         FROM symbols s JOIN files f ON s.file_id = f.id \
         WHERE s.name LIKE ? ESCAPE '\\' AND s.kind = ? \
         ORDER BY s.name LIMIT ?"
    } else {
        "SELECT s.id, s.kind, s.name, s.qualified_name, s.line_start, \
         f.path AS file_path, f.repo_id AS repo \
         FROM symbols s JOIN files f ON s.file_id = f.id \
         WHERE s.name LIKE ? ESCAPE '\\' \
         ORDER BY s.name LIMIT ?"
    };
    let mut stmt = conn.prepare(sql)?;
    let map = |row: &rusqlite::Row<'_>| row_symbol(row);
    let rows = match kind {
        Some(k) => stmt.query_map(rusqlite::params![sql_pattern, k, limit], map)?,
        None => stmt.query_map(rusqlite::params![sql_pattern, limit], map)?,
    };
    rows.collect()
}

/// Search symbols by name/docstring, ranked by bm25, with the LIKE fallback and
/// non-prefix LIKE merge (the Python `lexical.search_symbols` contract).
pub fn search_symbols(
    conn: &Connection,
    pattern: &str,
    kind: Option<&str>,
    limit: i64,
) -> Result<Vec<SymbolRow>, rusqlite::Error> {
    let Some(fts_query) = pattern_to_fts(pattern) else {
        return like_rows(conn, pattern, kind, limit);
    };
    let mut rows = match fts_rows(conn, &fts_query, kind, limit) {
        Ok(rows) => rows,
        // FTS unavailable or malformed query: degrade to the unranked LIKE scan.
        Err(_) => return like_rows(conn, pattern, kind, limit),
    };
    if !is_fts_prefix_pattern(pattern) && (rows.len() as i64) < limit {
        let mut seen: HashSet<String> = rows.iter().map(|r| r.id.clone()).collect();
        for r in like_rows(conn, pattern, kind, limit)? {
            let id = r.id.clone();
            if seen.insert(id) {
                rows.push(r);
                if rows.len() as i64 >= limit {
                    break;
                }
            }
        }
    }
    Ok(rows)
}

/// Render the `search_symbols` prose response (the Python tool text contract).
pub fn render_search_symbols(pattern: &str, rows: &[SymbolRow]) -> String {
    let total = rows.len();
    if total == 0 {
        return format!(
            "No symbols matching '{pattern}'. The token may not be indexed or \
             may use different casing/wording. Try a broader pattern (fewer \
             characters, a leading wildcard), or semantic_search(\"{pattern}\") \
             to match by meaning."
        );
    }
    let mut out = format!("{total} symbols matching '{pattern}':");
    for s in rows.iter().take(SHOWN) {
        // Python renders line_start via f-string: a NULL line shows as "None".
        let line = s
            .line_start
            .map(|l| l.to_string())
            .unwrap_or_else(|| "None".to_string());
        out.push_str(&format!(
            "\n  {} {}  {}:{}  ({})",
            s.kind, s.name, s.file_path, line, s.repo
        ));
    }
    if total > SHOWN {
        out.push_str(&format!("\n  ... and {} more", total - SHOWN));
    }
    out
}

/// Tool-boundary `search_symbols`: primitive run at its default limit, then rendered.
pub fn search_symbols_response(conn: &Connection, pattern: &str, kind: Option<&str>) -> String {
    match search_symbols(conn, pattern, kind, SEARCH_LIMIT) {
        Ok(rows) => render_search_symbols(pattern, &rows),
        Err(_) => render_search_symbols(pattern, &[]),
    }
}

// --- embedding knobs --------------------------------------------------------

/// Model stamp of the deterministic hash backend.
pub const HASH_MODEL: &str = "hash-256-v1";

/// Dimensionality of the hash fallback embedder.
pub const DEFAULT_DIM: usize = 256;

/// Knob value resolved env > config file > None (the `get_config_value` chain).
pub fn config_value(name: &str) -> Option<String> {
    if let Ok(raw) = env::var(name) {
        let raw = raw.trim().to_string();
        if !raw.is_empty() {
            return Some(raw);
        }
    }
    let file = cairn_home()?.join("config.json");
    let raw = std::fs::read_to_string(file).ok()?;
    let parsed: serde_json::Value = serde_json::from_str(&raw).ok()?;
    parsed
        .get(name)?
        .as_str()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(String::from)
}

/// Effective CAIRN_HOME: the env value when set, else `~/.cairn`.
pub fn cairn_home() -> Option<PathBuf> {
    if let Ok(home) = env::var("CAIRN_HOME")
        && !home.trim().is_empty()
    {
        return Some(PathBuf::from(home));
    }
    env::var("HOME")
        .ok()
        .map(|h| PathBuf::from(h).join(".cairn"))
}

/// Backend name resolved env > config file > `local`.
pub fn backend_name() -> String {
    config_value("CAIRN_EMBED_BACKEND")
        .unwrap_or_else(|| "local".to_string())
        .to_lowercase()
}

/// Embedding-server preset base URLs (`omlx`/`ollama` aliases).
fn server_preset_base_url(name: &str) -> Option<&'static str> {
    match name {
        "omlx" => Some("http://127.0.0.1:8000/v1"),
        "ollama" => Some("http://127.0.0.1:11434/v1"),
        _ => None,
    }
}

/// Active server base URL (config override, then the `omlx`/`ollama` preset).
pub fn server_base_url(name: &str) -> Result<String, String> {
    if let Some(configured) = config_value("CAIRN_EMBED_BASE_URL") {
        return Ok(configured);
    }
    if let Some(preset) = server_preset_base_url(name) {
        return Ok(preset.to_string());
    }
    Err("CAIRN_EMBED_BACKEND=server requires CAIRN_EMBED_BASE_URL \
         (an OpenAI-compatible base URL ending in /v1); \
         or use the 'omlx'/'ollama' presets"
        .to_string())
}

/// Adopted or configured server model id (the transport id, not the row stamp).
pub fn server_model() -> String {
    config_value("CAIRN_EMBED_SERVER_MODEL").unwrap_or_else(|| "bge-m3".to_string())
}

/// Model stamp for the resolved server backend; vec0 table names derive from it.
pub fn server_model_stamp(name: &str) -> Result<String, String> {
    if let Some(stamp) = config_value("CAIRN_EMBED_MODEL_STAMP") {
        return Ok(stamp);
    }
    let base = server_base_url(name)?;
    Ok(format!("server/{}/{}", netloc_of(&base), server_model()))
}

fn netloc_of(url: &str) -> String {
    let rest = url.split_once("://").map(|(_, rest)| rest).unwrap_or(url);
    let authority = rest.split(['/', '?', '#']).next().unwrap_or(rest);
    authority.replace(['[', ']'], "")
}

// --- the resolved per-call environment ---------------------------------------

/// Effective embedding backend after availability probing and fallback.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum EffectiveBackend {
    Hash,
    Server,
    Unavailable,
}

/// Knobs one semantic-search call resolves before running.
pub struct SemanticEnv {
    pub fusion_enabled: bool,
    pub backend: EffectiveBackend,
    /// `effective == hash && name == local` — provenance labeling only.
    pub hash_fallback: bool,
    pub model: String,
    pub rerank_on: bool,
    pub rerank_override: Option<bool>,
    pub reranker_available: bool,
    pub ann_enabled: bool,
    pub min_margin: f64,
    pub server_base_url: Option<String>,
    pub server_api_key: Option<String>,
}

fn env_flag_raw(name: &str) -> Option<bool> {
    let raw = env::var(name).ok()?.trim().to_ascii_lowercase();
    match raw.as_str() {
        "0" | "false" | "off" => Some(false),
        "1" | "true" | "on" => Some(true),
        _ => None,
    }
}

/// The persistent marker that enables reranking after a successful download.
fn rerank_marker_enabled() -> bool {
    cairn_home()
        .map(|home| home.join("rerank_enabled").exists())
        .unwrap_or(false)
}

/// Whether the rerank stage is requested by env/marker (availability is separate).
pub fn rerank_enabled() -> bool {
    match env_flag_raw("CAIRN_RERANK") {
        Some(flag) => flag,
        None => rerank_marker_enabled(),
    }
}

/// Whether a cross-encoder reranker can run; false until a native one exists.
pub fn reranker_available() -> bool {
    false
}

fn rerank_min_margin() -> f64 {
    const DEFAULT: f64 = 0.45;
    match env::var("CAIRN_RERANK_MIN_MARGIN") {
        Err(_) => DEFAULT,
        Ok(raw) if raw.trim().is_empty() => DEFAULT,
        Ok(raw) => raw
            .trim()
            .parse::<f64>()
            .map_or(DEFAULT, |v| v.clamp(0.0, 1.0)),
    }
}

/// ANN on unless `CAIRN_ANN_BACKEND` opts out (sqlite-vec is statically linked).
pub fn ann_backend_enabled() -> bool {
    match config_value("CAIRN_ANN_BACKEND") {
        Some(v) => v.eq_ignore_ascii_case("sqlite-vec"),
        None => true,
    }
}

fn fusion_enabled() -> bool {
    env::var("CAIRN_FUSION").map(|v| v != "0").unwrap_or(true)
}

/// Resolve `CAIRN_EMBED_BACKEND` to the effective backend, probing the server family.
pub fn resolve_backend(name: &str) -> (EffectiveBackend, Option<String>) {
    match name {
        "hash" | "local" => (EffectiveBackend::Hash, None),
        "server" | "omlx" | "ollama" => {
            let Ok(base) = server_base_url(name) else {
                return (EffectiveBackend::Unavailable, None);
            };
            let api_key = config_value("CAIRN_EMBED_API_KEY");
            if probe_server(&base, &server_model(), api_key.as_deref()) {
                (EffectiveBackend::Server, Some(base))
            } else {
                (EffectiveBackend::Unavailable, None)
            }
        }
        // Unknown names and unported transports (openai) resolve to local semantics.
        _ => (EffectiveBackend::Hash, None),
    }
}

/// Resolve every knob one semantic-search call needs; `rerank` is the tool arg.
pub fn semantic_env(rerank: Option<bool>) -> SemanticEnv {
    let name = backend_name();
    let (backend, server_base_url) = resolve_backend(&name);
    let model = match backend {
        EffectiveBackend::Server => server_model_stamp(&name).unwrap_or_default(),
        EffectiveBackend::Hash => HASH_MODEL.to_string(),
        EffectiveBackend::Unavailable => String::new(),
    };
    let hash_fallback = backend == EffectiveBackend::Hash && name == "local";
    let reranker_available = reranker_available();
    let rerank_on = match rerank {
        Some(false) => false,
        Some(true) => reranker_available,
        None => rerank_enabled(),
    };
    SemanticEnv {
        fusion_enabled: fusion_enabled(),
        backend,
        hash_fallback,
        model,
        rerank_on,
        rerank_override: rerank,
        reranker_available,
        ann_enabled: ann_backend_enabled(),
        min_margin: rerank_min_margin(),
        server_base_url,
        server_api_key: config_value("CAIRN_EMBED_API_KEY"),
    }
}

// --- hash embedder -----------------------------------------------------------

/// Deterministic unit-norm hash vector for text (the Python `_hash_vec` contract).
pub fn hash_vec(text: &str) -> Vec<f64> {
    let mut vec = vec![0.0f64; DEFAULT_DIM];
    let mut seen: HashSet<String> = HashSet::new();
    let mut tokens: Vec<String> = Vec::new();
    let mut current = String::new();
    let mut flush = |current: &mut String| {
        if !current.is_empty() && seen.insert(current.clone()) {
            tokens.push(std::mem::take(current));
        } else {
            current.clear();
        }
    };
    for ch in text.to_lowercase().chars() {
        if ch.is_alphanumeric() {
            current.push(ch);
        } else {
            flush(&mut current);
        }
    }
    flush(&mut current);
    for token in &tokens {
        let digest = Sha256::digest(token.as_bytes());
        let idx = (u32::from_le_bytes([digest[0], digest[1], digest[2], digest[3]])
            % DEFAULT_DIM as u32) as usize;
        let sign = if digest[4] & 1 == 0 { 1.0 } else { -1.0 };
        let mag = (u32::from_le_bytes([digest[5], digest[6], digest[7], digest[8]]) % 1000) as f64
            / 1000.0;
        vec[idx] += sign * (0.5 + mag);
    }
    let norm = vec.iter().map(|v| v * v).sum::<f64>().sqrt();
    if norm > 0.0 {
        for v in &mut vec {
            *v /= norm;
        }
    }
    vec
}

/// Float sequence → float32 little-endian BLOB (the `_floats_to_blob` contract).
pub fn floats_to_blob(floats: &[f32]) -> Vec<u8> {
    let mut blob = Vec::with_capacity(floats.len() * 4);
    for f in floats {
        blob.extend_from_slice(&f.to_le_bytes());
    }
    blob
}

// --- OpenAI-compatible embeddings-server transport ---------------------------

struct HttpReply {
    status: u16,
    body: Vec<u8>,
}

fn embed_timeout() -> Result<Duration, String> {
    const DEFAULT_S: f64 = 30.0;
    match config_value("CAIRN_EMBED_TIMEOUT") {
        None => Ok(Duration::from_secs_f64(DEFAULT_S)),
        Some(raw) => match raw.parse::<f64>() {
            Ok(v) if v.is_finite() && v > 0.0 => Ok(Duration::from_secs_f64(v)),
            _ => Err(format!("invalid CAIRN_EMBED_TIMEOUT: {raw}")),
        },
    }
}

fn parse_http_url(url: &str) -> Result<(String, u16, String), String> {
    let (scheme, rest) = url
        .split_once("://")
        .ok_or_else(|| format!("not an absolute http URL: {url}"))?;
    if !scheme.eq_ignore_ascii_case("http") {
        return Err(format!(
            "unsupported URL scheme '{scheme}': the embedding-server transport \
             speaks plain http (loopback servers)"
        ));
    }
    let (authority, path) = match rest.split_once('/') {
        Some((authority, path)) => (authority, format!("/{path}")),
        None => (rest, String::from("/")),
    };
    let (host, port) = match authority.rsplit_once(':') {
        Some((host, port)) => (
            host.to_string(),
            port.parse::<u16>()
                .map_err(|_| format!("bad port in {url}"))?,
        ),
        None => (authority.to_string(), 80),
    };
    Ok((host, port, path))
}

fn http_request(
    method: &str,
    base: &str,
    path_suffix: &str,
    body: Option<&str>,
    api_key: Option<&str>,
    timeout: Duration,
) -> Result<HttpReply, String> {
    let url = format!(
        "{}/{}",
        base.trim_end_matches('/'),
        path_suffix.trim_start_matches('/')
    );
    let (host, port, path) = parse_http_url(&url)?;
    let addr = format!("{host}:{port}")
        .parse()
        .map_err(|e| format!("resolve {host}:{port}: {e}"))?;
    let mut stream = std::net::TcpStream::connect_timeout(&addr, timeout)
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
    if let Some(key) = api_key {
        request.push_str(&format!("Authorization: Bearer {key}\r\n"));
    }
    request.push_str("Connection: close\r\n\r\n");
    use std::io::{Read, Write};
    stream
        .write_all(request.as_bytes())
        .map_err(|e| format!("send: {e}"))?;
    if let Some(body) = body {
        stream
            .write_all(body.as_bytes())
            .map_err(|e| format!("send: {e}"))?;
    }
    let mut raw: Vec<u8> = Vec::new();
    stream
        .read_to_end(&mut raw)
        .map_err(|e| format!("read: {e}"))?;
    parse_http_response(&raw)
}

fn parse_http_response(raw: &[u8]) -> Result<HttpReply, String> {
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
            && value.to_ascii_lowercase().contains("chunked")
        {
            chunked = true;
        }
    }
    let mut body_raw = &raw[header_end + 4..];
    let body = if chunked {
        let mut out = Vec::new();
        while let Some(line_end) = find_subsequence(body_raw, b"\r\n") {
            let size =
                usize::from_str_radix(String::from_utf8_lossy(&body_raw[..line_end]).trim(), 16)
                    .map_err(|_| "bad chunk size".to_string())?;
            body_raw = &body_raw[line_end + 2..];
            if size == 0 {
                break;
            }
            if body_raw.len() < size + 2 {
                return Err("truncated chunk".to_string());
            }
            out.extend_from_slice(&body_raw[..size]);
            body_raw = &body_raw[size + 2..];
        }
        out
    } else if let Some(len) = content_length {
        body_raw[..len.min(body_raw.len())].to_vec()
    } else {
        body_raw.to_vec()
    };
    Ok(HttpReply { status, body })
}

fn find_subsequence(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    haystack
        .windows(needle.len())
        .position(|window| window == needle)
}

fn probe_server(base: &str, model: &str, api_key: Option<&str>) -> bool {
    let Ok(reply) = http_request(
        "GET",
        base,
        "/models",
        None,
        api_key,
        Duration::from_secs(2),
    ) else {
        return false;
    };
    if reply.status != 200 {
        return false;
    }
    let Ok(listing) = serde_json::from_slice::<serde_json::Value>(&reply.body) else {
        return false;
    };
    listing
        .get("data")
        .and_then(|d| d.as_array())
        .map(|entries| {
            entries
                .iter()
                .any(|e| e.get("id").and_then(|id| id.as_str()) == Some(model))
        })
        .unwrap_or(false)
}

fn malformed_error(body: &[u8]) -> String {
    format!(
        "embedding server returned malformed response: {}",
        String::from_utf8_lossy(&body[..body.len().min(120)])
    )
}

/// Embed texts through the configured server with bounded retries and validation.
pub fn server_embed(
    texts: &[String],
    base: &str,
    model: &str,
    api_key: Option<&str>,
) -> Result<(Vec<Vec<u8>>, usize), String> {
    if texts.is_empty() {
        return Ok((Vec::new(), 0));
    }
    let timeout = embed_timeout()?;
    let batch: usize = match config_value("CAIRN_EMBED_SERVER_BATCH") {
        None => 32,
        Some(raw) => match raw.parse::<usize>() {
            Ok(b) if b >= 1 => b,
            _ => return Err(format!("invalid CAIRN_EMBED_SERVER_BATCH: {raw}")),
        },
    };
    let mut blobs: Vec<Vec<u8>> = Vec::with_capacity(texts.len());
    let mut dim = 0;
    for chunk in texts.chunks(batch) {
        let payload = serde_json::json!({ "model": model, "input": chunk }).to_string();
        let mut raw: Option<Vec<u8>> = None;
        let mut last_error = String::new();
        for attempt in 0..=3 {
            match http_request(
                "POST",
                base,
                "/embeddings",
                Some(&payload),
                api_key,
                timeout,
            ) {
                Ok(reply) => {
                    if reply.status == 429 || reply.status >= 500 {
                        last_error = format!(
                            "HTTP {}: {}",
                            reply.status,
                            String::from_utf8_lossy(&reply.body)
                        );
                    } else if reply.status >= 400 {
                        let detail = String::from_utf8_lossy(&reply.body);
                        let message = serde_json::from_str::<serde_json::Value>(&detail)
                            .ok()
                            .and_then(|v| {
                                v.get("error")?.get("message")?.as_str().map(String::from)
                            })
                            .unwrap_or_else(|| detail.to_string());
                        return Err(format!(
                            "embedding server rejected the request (HTTP {}): {}",
                            reply.status, message
                        ));
                    } else {
                        raw = Some(reply.body);
                        break;
                    }
                }
                Err(e) => last_error = e,
            }
            if attempt < 3 {
                std::thread::sleep(Duration::from_secs_f64(0.5 * 2f64.powi(attempt)));
            }
        }
        let Some(raw) = raw else {
            return Err(format!(
                "embedding server unreachable after 3 retries: {last_error}"
            ));
        };
        let parsed: serde_json::Value =
            serde_json::from_slice(&raw).map_err(|_| malformed_error(&raw))?;
        let mut entries: Vec<(i64, Vec<f32>)> = parsed
            .get("data")
            .and_then(|d| d.as_array())
            .ok_or_else(|| malformed_error(&raw))?
            .iter()
            .filter_map(|entry| {
                let index = entry.get("index")?.as_i64()?;
                let embedding = entry.get("embedding")?.as_array()?;
                let vec: Vec<f32> = embedding
                    .iter()
                    .map(|v| v.as_f64().map(|f| f as f32))
                    .collect::<Option<Vec<_>>>()?;
                Some((index, vec))
            })
            .collect();
        if entries.len() != chunk.len() {
            return Err(format!(
                "embedding server returned {} vectors for {} inputs",
                entries.len(),
                chunk.len()
            ));
        }
        entries.sort_by_key(|(index, _)| *index);
        for (_, vec) in entries {
            if dim == 0 {
                dim = vec.len();
            } else if vec.len() != dim {
                return Err(format!(
                    "mixed-dimension batch: expected {dim}-dim vectors, got {}",
                    vec.len()
                ));
            }
            blobs.push(floats_to_blob(&vec));
        }
    }
    Ok((blobs, dim))
}

/// Embed one natural-language query with the active backend.
pub fn embed_query(env: &SemanticEnv, text: &str) -> Result<(Vec<u8>, usize), String> {
    match env.backend {
        EffectiveBackend::Hash => {
            let vec = hash_vec(text);
            let dim = vec.len();
            let floats: Vec<f32> = vec.iter().map(|v| *v as f32).collect();
            Ok((floats_to_blob(&floats), dim))
        }
        EffectiveBackend::Server => {
            let base = env
                .server_base_url
                .as_deref()
                .ok_or_else(|| "server backend resolved without a base URL".to_string())?;
            server_embed(
                &[text.to_string()],
                base,
                &env.model,
                env.server_api_key.as_deref(),
            )
            .map(|(mut blobs, dim)| (blobs.remove(0), dim))
        }
        EffectiveBackend::Unavailable => Err("embedding backend unavailable".to_string()),
    }
}

/// Python `install_hint()`: shown when the configured backend cannot serve.
pub fn install_hint() -> String {
    "Semantic search requires the 'semantic' extra. \
     Install it with: pip install 'cairn-intel[semantic]'. \
     Or set CAIRN_EMBED_BACKEND=omlx or ollama (or server + \
     CAIRN_EMBED_BASE_URL) to use a local OpenAI-compatible embeddings \
     server -- no model install needed. \
     Or set CAIRN_EMBED_BACKEND=hash for a dep-free smoke test."
        .to_string()
}

/// Python wrapper text shown when the store carries no embeddings for the model.
pub fn empty_index_hint() -> String {
    "Semantic index is empty. Run `cairn embed` once to index the \
     corpus (build-time, ~1-2 min for 50k symbols), then retry \
     this query. Embedding is not done lazily during search to \
     avoid write-lock contention with the running server."
        .to_string()
}

// --- cosine scan + RRF -------------------------------------------------------

/// Rank `(vec, dim)` rows by cosine to `q_blob`; returns `(score, row index)`
/// descending and stable, skipping dim-mismatched, malformed, zero-norm rows.
pub fn cosine_scan(
    q_blob: &[u8],
    q_dim: usize,
    rows: &[(Vec<u8>, i64)],
    threshold: f64,
) -> Vec<(f64, usize)> {
    let q = decode_f32s(q_blob);
    let q_norm = l2_norm(&q);
    if q_norm == 0.0 {
        return Vec::new();
    }
    let q_unit: Vec<f64> = q
        .iter()
        .map(|x| f64::from(*x) / f64::from(q_norm))
        .collect();
    let blob_size = q_dim * 4;
    let mut scored: Vec<(f64, usize)> = Vec::new();
    for (index, (vec_blob, dim)) in rows.iter().enumerate() {
        if *dim != q_dim as i64 || vec_blob.len() != blob_size {
            continue;
        }
        let v = decode_f32s(vec_blob);
        let vn = l2_norm(&v);
        if vn == 0.0 {
            continue;
        }
        let dot: f64 = q_unit
            .iter()
            .zip(v.iter())
            .map(|(a, b)| *a * f64::from(*b))
            .sum();
        let score = dot / f64::from(vn);
        if score >= threshold {
            scored.push((score, index));
        }
    }
    scored.sort_by(|(a, _), (b, _)| b.partial_cmp(a).unwrap_or(std::cmp::Ordering::Equal));
    scored
}

fn decode_f32s(blob: &[u8]) -> Vec<f32> {
    blob.as_chunks::<4>()
        .0
        .iter()
        .map(|c| f32::from_le_bytes(*c))
        .collect()
}

fn l2_norm(v: &[f32]) -> f32 {
    v.iter().map(|x| x * x).sum::<f32>().sqrt()
}

/// Reciprocal Rank Fusion: score desc, doc-id ascending on ties.
pub fn rrf_fuse(rankings: &[Vec<String>], k: i64) -> Vec<(String, f64)> {
    let mut scores: HashMap<String, f64> = HashMap::new();
    for ranking in rankings {
        for (position, doc_id) in ranking.iter().enumerate() {
            if doc_id.is_empty() {
                continue;
            }
            *scores.entry(doc_id.clone()).or_insert(0.0) += 1.0 / (k + position as i64 + 1) as f64;
        }
    }
    let mut fused: Vec<(String, f64)> = scores.into_iter().collect();
    fused.sort_by(|(id_a, score_a), (id_b, score_b)| {
        score_b
            .partial_cmp(score_a)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| id_a.cmp(id_b))
    });
    fused
}

fn round4(x: f64) -> f64 {
    (x * 10_000.0).round_ties_even() / 10_000.0
}

// --- semantic pipeline -------------------------------------------------------

/// One semantic-search match (the Python tool's `matches` entry).
#[derive(Clone)]
pub struct SemanticMatch {
    pub id: String,
    pub kind: String,
    pub name: String,
    pub qualified_name: Option<String>,
    pub file_path: String,
    pub repo: String,
    pub score: f64,
    pub provenance: String,
    pub reranked: bool,
    pub chunk: String,
}

/// vec0 rowid-keyed index table name for `model` over `embeddings`.
fn vec_table_name(model: &str) -> String {
    let safe: String = model
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '_' {
                c
            } else {
                '_'
            }
        })
        .collect();
    format!("vec_{safe}")
}

fn ann_index_exists(conn: &Connection, model: &str) -> bool {
    conn.query_row(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        [vec_table_name(model)],
        |_| Ok(()),
    )
    .is_ok()
}

/// ANN cosine search against the vec0 table; `None` degrades to the brute scan.
fn ann_query(conn: &Connection, model: &str, q_blob: &[u8], k: i64) -> Option<Vec<(String, f64)>> {
    let table = vec_table_name(model);
    if !ann_index_exists(conn, model) {
        return None;
    }
    let sql = format!(
        "SELECT e.symbol_id AS symbol_id, v.distance AS distance \
         FROM {table} v JOIN embeddings e ON e.rowid = v.rowid \
         WHERE v.embedding MATCH ? AND v.k = ? \
         ORDER BY v.distance"
    );
    let rows: Vec<(String, f64)> = conn
        .prepare(&sql)
        .ok()?
        .query_map(rusqlite::params![q_blob, k], |row| {
            Ok((
                row.get::<_, String>("symbol_id")?,
                1.0 - row.get::<_, f64>("distance")?,
            ))
        })
        .ok()?
        .filter_map(|r| r.ok())
        .collect();
    Some(rows)
}

struct BruteRow {
    symbol_id: String,
    vec: Vec<u8>,
    chunk: String,
    dim: i64,
    name: String,
    kind: String,
    qualified_name: Option<String>,
    file_path: String,
    repo: String,
}

fn brute_rows(conn: &Connection, model: &str, cap: i64) -> Vec<BruteRow> {
    let sql = "SELECT e.symbol_id, e.vec, e.chunk, e.dim, \
               s.name, s.kind, s.qualified_name, f.path AS file_path, f.repo_id AS repo \
               FROM embeddings e \
               JOIN symbols s ON e.symbol_id = s.id \
               JOIN files f ON s.file_id = f.id \
               WHERE e.model = ? \
               LIMIT ?";
    let Ok(mut stmt) = conn.prepare(sql) else {
        return Vec::new();
    };
    stmt.query_map(rusqlite::params![model, cap], |row| {
        Ok(BruteRow {
            symbol_id: row.get("symbol_id")?,
            vec: row.get("vec")?,
            chunk: row.get::<_, Option<String>>("chunk")?.unwrap_or_default(),
            dim: row.get("dim")?,
            name: row.get("name")?,
            kind: row.get("kind")?,
            qualified_name: row.get("qualified_name")?,
            file_path: row.get("file_path")?,
            repo: row.get("repo")?,
        })
    })
    .map_or(Vec::new(), |rows| rows.filter_map(|r| r.ok()).collect())
}

type MatchMeta = (String, String, Option<String>, String, String, String);

/// Thresholded best-score candidates from ANN hits, in first-hit order.
fn candidates_from_ann_hits(
    conn: &Connection,
    ann_hits: &[(String, f64)],
    threshold: f64,
) -> Vec<SemanticMatch> {
    // A symbol qualifies once, at its best threshold-passing score, in
    // first-hit insertion order.
    let mut order: Vec<String> = Vec::new();
    let mut best: HashMap<String, f64> = HashMap::new();
    for (symbol_id, score) in ann_hits {
        if !best.contains_key(symbol_id) {
            order.push(symbol_id.clone());
        }
        let slot = best.entry(symbol_id.clone()).or_insert(f64::MIN);
        if *score > *slot {
            *slot = *score;
        }
    }
    let ids: Vec<(String, f64)> = order
        .into_iter()
        .filter_map(|id| {
            let score = best[&id];
            (score >= threshold).then_some((id, score))
        })
        .collect();
    if ids.is_empty() {
        return Vec::new();
    }
    let placeholders = vec!["?"; ids.len()].join(",");
    let params: Vec<&str> = ids.iter().map(|(id, _)| id.as_str()).collect();
    let sql = format!(
        "SELECT e.symbol_id, e.chunk, \
         s.name, s.kind, s.qualified_name, f.path AS file_path, f.repo_id AS repo \
         FROM embeddings e \
         JOIN symbols s ON e.symbol_id = s.id \
         JOIN files f ON s.file_id = f.id \
         WHERE e.symbol_id IN ({placeholders})"
    );
    let Ok(mut stmt) = conn.prepare(&sql) else {
        return Vec::new();
    };
    let metadata: HashMap<String, MatchMeta> = stmt
        .query_map(rusqlite::params_from_iter(params), |row| {
            Ok((
                row.get::<_, String>("symbol_id")?,
                row.get::<_, String>("name")?,
                row.get::<_, String>("kind")?,
                row.get::<_, Option<String>>("qualified_name")?,
                row.get::<_, String>("file_path")?,
                row.get::<_, String>("repo")?,
                row.get::<_, Option<String>>("chunk")?.unwrap_or_default(),
            ))
        })
        .into_iter()
        .flat_map(|rows| rows.filter_map(|r| r.ok()))
        .map(
            |(symbol_id, name, kind, qualified_name, file_path, repo, chunk)| {
                (
                    symbol_id,
                    (name, kind, qualified_name, file_path, repo, chunk),
                )
            },
        )
        .collect();
    ids.iter()
        .filter_map(|(id, score)| {
            let (name, kind, qualified_name, file_path, repo, chunk) = metadata.get(id)?;
            Some(SemanticMatch {
                id: id.clone(),
                kind: kind.clone(),
                name: name.clone(),
                qualified_name: qualified_name.clone(),
                file_path: file_path.clone(),
                repo: repo.clone(),
                score: round4(*score),
                provenance: "semantic".to_string(),
                reranked: false,
                chunk: chunk.clone(),
            })
        })
        .collect()
}

fn semantic_provenance(env: &SemanticEnv) -> String {
    if env.hash_fallback {
        "semantic (hash backend)".to_string()
    } else {
        "semantic".to_string()
    }
}

fn fused_provenance(env: &SemanticEnv) -> String {
    if env.hash_fallback {
        "fused(bm25+semantic, hash)".to_string()
    } else {
        "fused(bm25+semantic)".to_string()
    }
}

/// Dense retrieval: ANN when enabled and indexed, else the native cosine scan.
fn dense_retrieve(
    conn: &Connection,
    env: &SemanticEnv,
    dense_query: &str,
    threshold: f64,
    pool_size: usize,
) -> Vec<SemanticMatch> {
    let Ok((q_blob, q_dim)) = embed_query(env, dense_query) else {
        return Vec::new();
    };
    if env.ann_enabled
        && let Some(hits) = ann_query(conn, &env.model, &q_blob, pool_size as i64)
    {
        return candidates_from_ann_hits(conn, &hits, threshold);
    }
    let rows = brute_rows(conn, &env.model, 50_000);
    if rows.is_empty() {
        return Vec::new();
    }
    let vectors: Vec<(Vec<u8>, i64)> = rows.iter().map(|row| (row.vec.clone(), row.dim)).collect();
    let scored = cosine_scan(&q_blob, q_dim, &vectors, threshold);
    scored
        .into_iter()
        .take(pool_size)
        .filter_map(|(score, index)| {
            let row = rows.get(index)?;
            Some(SemanticMatch {
                id: row.symbol_id.clone(),
                kind: row.kind.clone(),
                name: row.name.clone(),
                qualified_name: row.qualified_name.clone(),
                file_path: row.file_path.clone(),
                repo: row.repo.clone(),
                score: round4(score),
                provenance: semantic_provenance(env),
                reranked: false,
                chunk: row.chunk.clone(),
            })
        })
        .collect()
}

/// Normalized top-to-edge margin of a fused (RRF) ranking, in [0, 1].
fn fused_margin(candidates: &[SemanticMatch], limit: usize) -> f64 {
    if candidates.len() <= 1 {
        return 1.0;
    }
    let top = candidates[0].score;
    if top <= 0.0 {
        return 0.0;
    }
    let edge = candidates[(limit - 1).min(candidates.len() - 1)].score;
    (top - edge) / top
}

/// Whether the query is an exact (case-insensitive) reference to the top hit.
fn exact_name_hit(query: &str, top: &SemanticMatch) -> bool {
    let q = query.trim().to_lowercase();
    if q.is_empty() {
        return false;
    }
    let name = top.name.trim().to_lowercase();
    let qual = top
        .qualified_name
        .as_deref()
        .unwrap_or("")
        .trim()
        .to_lowercase();
    q == name || q == qual || qual.ends_with(&format!(".{q}"))
}

/// Whether the fused ranking is decisive enough to skip the rerank stage.
fn fused_confident(
    query: &str,
    candidates: &[SemanticMatch],
    limit: usize,
    env: &SemanticEnv,
) -> bool {
    fused_margin(candidates, limit) >= env.min_margin
        && candidates
            .first()
            .map(|top| exact_name_hit(query, top))
            .unwrap_or(false)
}

fn vectors_carry_token_overlap_only(env: &SemanticEnv) -> bool {
    env.hash_fallback || backend_name() == "hash"
}

/// Reject rerank skipping for sparse, degraded, or hash-backed rankings.
fn confidence_gate(
    query: &str,
    candidates: &[SemanticMatch],
    limit: usize,
    env: &SemanticEnv,
) -> bool {
    !vectors_carry_token_overlap_only(env)
        && !candidates.is_empty()
        && fused_confident(query, candidates, limit, env)
}

/// Return top-k symbols by cosine similarity to a natural-language query.
pub fn semantic_search(
    conn: &Connection,
    query: &str,
    limit: usize,
    threshold: f64,
    env: &SemanticEnv,
) -> Vec<SemanticMatch> {
    let pool_size = if env.rerank_on {
        (limit * 5).max(50)
    } else {
        limit
    };
    let mut candidates = dense_retrieve(conn, env, query, threshold, pool_size);
    let mut fusion_used = false;

    if env.fusion_enabled
        && let Ok(sparse) = search_symbols(conn, query, None, 30)
    {
        let bm25_map: HashMap<String, &SymbolRow> =
            sparse.iter().map(|r| (r.id.clone(), r)).collect();
        let bm25_ids: Vec<String> = sparse.iter().map(|r| r.id.clone()).collect();
        let dense_map: HashMap<String, SemanticMatch> = candidates
            .iter()
            .map(|c| (c.id.clone(), c.clone()))
            .collect();
        let dense_ids: Vec<String> = candidates.iter().map(|c| c.id.clone()).collect();
        let mut fused: Vec<SemanticMatch> = Vec::with_capacity(bm25_map.len() + dense_map.len());
        for (doc_id, fused_score) in rrf_fuse(&[bm25_ids, dense_ids], 60) {
            let in_bm25 = bm25_map.contains_key(&doc_id);
            let mut base = match dense_map.get(&doc_id) {
                Some(dense) => {
                    let mut base = dense.clone();
                    base.provenance = if in_bm25 {
                        fused_provenance(env)
                    } else {
                        semantic_provenance(env)
                    };
                    base
                }
                None => {
                    let row = bm25_map[&doc_id];
                    SemanticMatch {
                        id: doc_id.clone(),
                        kind: row.kind.clone(),
                        name: row.name.clone(),
                        qualified_name: row.qualified_name.clone(),
                        file_path: row.file_path.clone(),
                        repo: row.repo.clone(),
                        score: 0.0,
                        provenance: "bm25".to_string(),
                        reranked: false,
                        chunk: String::new(),
                    }
                }
            };
            base.score = round4(fused_score);
            fused.push(base);
        }
        candidates = fused;
        fusion_used = true;
    }

    // Confidence gate: skip rerank only with a decisive margin and an exact
    // raw-query name hit; never when hash vectors carry the ranking.
    let rerank_on = env.rerank_on
        && !(env.rerank_override.is_none()
            && fusion_used
            && confidence_gate(query, &candidates, limit, env));
    // No native cross-encoder: a requested rerank degrades to the fused order
    // (the rerank call returns candidates unchanged), so both branches truncate.
    debug_assert!(!rerank_on || !env.reranker_available);
    candidates.truncate(limit);
    candidates
}

/// Render the semantic-search prose response (the Python tool text contract).
/// Neighbor lines render only when attached; the attachment lands with the
/// traversal port.
pub fn render_semantic_search(query: &str, rows: &[SemanticMatch], fusion_on: bool) -> String {
    if rows.is_empty() {
        let threshold_note = if fusion_on {
            ""
        } else {
            " above the cosine threshold"
        };
        return format!(
            "No matches for '{query}' -- neither the vector scan nor the \
             BM25 fallback found anything{threshold_note}. \
             The corpus may not be embedded yet (run `cairn embed` to index), or the \
             wording may not match any token. Try search_symbols(\"...\") for a \
             lexical match, or rephrase with more specific terms."
        );
    }
    let mut out = format!(
        "=== semantic_search: \"{query}\" ({} match(es)) ===",
        rows.len()
    );
    for r in rows {
        let short = r.file_path.rsplit('/').next().unwrap_or("");
        let label = if r.reranked {
            format!("rerank {:.2}", r.score)
        } else {
            format!("{} {:.2}", r.provenance, r.score)
        };
        out.push_str(&format!(
            "\n  [{label}] {} {}  ({short})  [{}]",
            r.kind,
            r.qualified_name.as_deref().unwrap_or(&r.name),
            r.repo
        ));
        if !r.chunk.is_empty()
            && let Some(first_line) = r.chunk.split('\n').next()
            && !first_line.is_empty()
        {
            out.push_str(&format!("\n    {first_line}"));
        }
    }
    out.push_str("\n\n");
    out.push_str(
        "Note: these are similarity-scored fuzzy matches, not structural edges. \
         Use get_callers/impact_analysis to verify precise relationships.",
    );
    out
}

/// Tool-boundary `semantic_search`: gates, runs the pipeline, renders.
pub fn semantic_search_response(
    conn: &Connection,
    query: &str,
    limit: usize,
    rerank: Option<bool>,
) -> String {
    let limit = limit.clamp(1, 1000);
    let env = semantic_env(rerank);
    if env.backend == EffectiveBackend::Unavailable {
        return install_hint();
    }
    let count: i64 = conn
        .query_row(
            "SELECT COUNT(*) AS c FROM embeddings WHERE model = ?",
            [&env.model],
            |row| row.get("c"),
        )
        .unwrap_or(0);
    if count == 0 {
        return empty_index_hint();
    }
    let matches = semantic_search(conn, query, limit, 0.3, &env);
    render_semantic_search(query, &matches, env.fusion_enabled)
}

#[cfg(test)]
mod tests;
