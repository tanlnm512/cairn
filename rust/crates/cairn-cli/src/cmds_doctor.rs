//! `cairn doctor`: system health checks (system/doctor.py port).

use std::io::{Read as _, Write as _};
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::time::Duration;

use rusqlite::Connection;
use serde_json::{Value, json};

use crate::agent_install;
use crate::display;
use crate::py_json;
use crate::store_paths;

const PASS: &str = "PASS";
const WARN: &str = "WARN";
const FAIL: &str = "FAIL";

const STALE_BUILD_DAYS: i64 = 7;
const MEMORY_REF_WINDOW_DAYS: i64 = 30;
const CONTENTION_WINDOW_DAYS: i64 = 7;
const TOOL_HEALTH_WINDOW_DAYS: i64 = 7;
const TOOL_ERROR_RATE_WARN: f64 = 0.10;
const TOOL_P95_LATENCY_MS_WARN: f64 = 5000.0;
const PARITY_GATE: f64 = 0.98;
const PARITY_SAMPLE_LIMIT: i64 = 16;
const SERVER_FAMILY: [&str; 3] = ["server", "omlx", "ollama"];
const FIX_TRADEOFF: &str = "tradeoff: every client shares the single HTTP daemon instead of spawning per-client stdio processes";

pub struct DoctorArgs {
    pub db: Option<String>,
    pub as_json: bool,
    pub fix: bool,
}

#[derive(Debug)]
pub(crate) struct CheckResult {
    pub(crate) name: String,
    pub(crate) status: &'static str,
    pub(crate) detail: String,
    pub(crate) hint: Option<String>,
}

fn result(
    name: &str,
    status: &'static str,
    detail: impl Into<String>,
    hint: Option<String>,
) -> CheckResult {
    CheckResult {
        name: name.to_string(),
        status,
        detail: detail.into(),
        hint,
    }
}

impl CheckResult {
    fn to_json(&self) -> Value {
        json!({
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "hint": self.hint,
        })
    }
}

/// Run the health checks; exit 0 on PASS/WARN-only, 1 on any FAIL.
pub fn run(args: DoctorArgs) -> ExitCode {
    let db = args.db.clone().unwrap_or_else(|| {
        store_paths::resolve_store(None)
            .db
            .to_string_lossy()
            .into_owned()
    });
    let mut results = run_doctor(&db);
    let mut actions: Option<Vec<Value>> = None;
    if args.fix {
        actions = Some(apply_doctor_fixes());
        for (name, rerun) in [
            ("environment", check_environment(&db)),
            ("agent_hooks", check_agent_hooks()),
        ] {
            if let Some(slot) = results.iter_mut().find(|r| r.name == name) {
                *slot = rerun;
            }
        }
    }
    if args.as_json {
        let payload = match &actions {
            None => Value::Array(results.iter().map(CheckResult::to_json).collect()),
            Some(actions) => {
                let summary = fix_summary_lines(actions);
                json!({
                    "checks": results.iter().map(CheckResult::to_json).collect::<Vec<_>>(),
                    "actions": actions,
                    "summary": summary,
                })
            }
        };
        println!("{}", py_json::dumps(&payload));
    } else {
        for r in &results {
            let (glyph, style) = match r.status {
                PASS => ("✓", display::SUCCESS),
                WARN => ("!", display::WARNING),
                _ => ("✗", display::ERROR),
            };
            println!(
                "{} {}: {}",
                display::paint(style, &format!("{glyph} {}", r.status)),
                display::paint(display::LABEL, &r.name),
                r.detail
            );
            if let Some(hint) = &r.hint {
                display::dim(&format!("      hint: {hint}"));
            }
        }
        if let Some(actions) = &actions {
            println!(
                "{}",
                display::paint(display::LABEL, &format!("fix actions: {}", actions.len()))
            );
            for line in fix_summary_lines(actions) {
                display::dim(&format!("      {line}"));
            }
        }
    }
    if results.iter().any(|r| r.status == FAIL) {
        ExitCode::FAILURE
    } else {
        ExitCode::SUCCESS
    }
}

pub(crate) fn run_doctor(db: &str) -> Vec<CheckResult> {
    let path = Path::new(db);
    if !path.exists() {
        let mut degraded = db_unavailable_results(&format!(
            "store not found at {db} -- run `cairn init` + `cairn build` first"
        ));
        degraded.push(check_environment(db));
        degraded.push(check_agent_hooks());
        return degraded;
    }
    let conn = match open_store(path) {
        Ok(conn) => conn,
        Err(e) => {
            let mut degraded = db_unavailable_results(&e.to_string());
            degraded.push(check_environment(db));
            degraded.push(check_agent_hooks());
            return degraded;
        }
    };
    let mut results = vec![check_schema(&conn), check_embeddings(), check_ann(&conn)];
    results.extend(check_embed_server(&conn));
    results.push(check_freshness(&conn));
    results.push(check_parse_errors(&conn));
    results.push(check_edge_signals(&conn));
    results.push(check_concurrency(&conn));
    results.push(check_tool_health(&conn));
    results.push(check_memory_staleness(&conn, db));
    results.push(check_config());
    results.push(check_daemon());
    results.push(check_environment(db));
    results.push(check_agent_hooks());
    results
}

fn open_store(path: &Path) -> rusqlite::Result<Connection> {
    let conn = cairn_store::conn::open_read_only(path)?;
    conn.query_row("SELECT 1 FROM sqlite_master LIMIT 1", [], |row| {
        row.get::<_, i64>(0)
    })?;
    Ok(conn)
}

fn db_unavailable_results(error: &str) -> Vec<CheckResult> {
    let msg = format!("cannot open database: {error}");
    vec![
        result("schema", FAIL, msg, None),
        result(
            "embeddings",
            WARN,
            "database unavailable (see schema)",
            None,
        ),
        result("ann", WARN, "database unavailable (see schema)", None),
        result(
            "embed_server",
            WARN,
            "database unavailable (see schema)",
            None,
        ),
        result("freshness", WARN, "database unavailable (see schema)", None),
        result(
            "parse_errors",
            WARN,
            "database unavailable (see schema)",
            None,
        ),
        result(
            "edge_signals",
            WARN,
            "database unavailable (see schema)",
            None,
        ),
        result(
            "concurrency",
            WARN,
            "database unavailable (see schema)",
            None,
        ),
        result(
            "tool_health",
            WARN,
            "database unavailable (see schema)",
            None,
        ),
        result(
            "memory_staleness",
            WARN,
            "database unavailable (see schema)",
            None,
        ),
        check_config(),
        check_daemon(),
    ]
}

fn query_i64(conn: &Connection, sql: &str, params: &[&dyn rusqlite::ToSql], fallback: i64) -> i64 {
    conn.query_row(sql, params, |row| row.get::<_, i64>(0))
        .unwrap_or(fallback)
}

// --- 1. schema ------------------------------------------------------------------

fn check_schema(conn: &Connection) -> CheckResult {
    match conn.query_row("PRAGMA quick_check", [], |row| row.get::<_, String>(0)) {
        Ok(verdict) if verdict == "ok" => result("schema", PASS, "integrity ok", None),
        Ok(verdict) => result(
            "schema",
            FAIL,
            format!("integrity check reported: {verdict}"),
            None,
        ),
        Err(e) => result("schema", FAIL, format!("integrity check failed: {e}"), None),
    }
}

// --- 2. embeddings ----------------------------------------------------------------

fn check_embeddings() -> CheckResult {
    use cairn_query::tools_search;
    let configured = tools_search::backend_name();
    let effective_hash = !matches!(
        tools_search::resolve_backend(&configured).0,
        tools_search::EffectiveBackend::Server
    );
    if effective_hash {
        return result(
            "embeddings",
            WARN,
            "hash backend active -- token-overlap vectors, retrieval degraded",
            Some(tools_search::install_hint()),
        );
    }
    result("embeddings", PASS, format!("backend: {configured}"), None)
}

// --- 3. ann ------------------------------------------------------------------------

fn check_ann(conn: &Connection) -> CheckResult {
    use cairn_query::tools_search;
    use cairn_store::vec0::{self, VecSource};
    let configured =
        tools_search::config_value("CAIRN_ANN_BACKEND").unwrap_or_else(|| "sqlite-vec".to_string());
    if configured != "sqlite-vec" {
        return result(
            "ann",
            PASS,
            format!("disabled by config (CAIRN_ANN_BACKEND={configured}); brute-force scan in use"),
            None,
        );
    }
    let model = tools_search::semantic_env(None).model;
    let emb_n = query_i64(
        conn,
        "SELECT COUNT(*) FROM embeddings WHERE model = ?1",
        &[&model],
        0,
    );
    if emb_n <= 0 {
        return result(
            "ann",
            PASS,
            "sqlite-vec available (no embeddings to index yet)",
            None,
        );
    }
    match vec0::index_exists(conn, &model, VecSource::Embeddings) {
        Ok(false) | Err(_) => {
            return result(
                "ann",
                WARN,
                format!(
                    "no vec0 index for model '{model}' ({emb_n} embedding(s) unindexed) -- \
                     brute-force scan in use"
                ),
                Some("run `cairn embed` to build the ANN index".to_string()),
            );
        }
        Ok(true) => {}
    }
    if let Ok(Some(idx_n)) = vec0::index_row_count(conn, &model)
        && idx_n != emb_n
    {
        let detail = if idx_n < emb_n {
            format!(
                "ANN index stale: {emb_n} embedding(s) vs {idx_n} indexed ({} unindexed) -- \
                 recent symbols invisible to semantic queries",
                emb_n - idx_n
            )
        } else {
            format!(
                "ANN index stale: {idx_n} indexed vs {emb_n} embedding(s) ({} stale vector(s)) -- \
                 deleted symbols can shadow new ones via reused rowids",
                idx_n - emb_n
            )
        };
        return result(
            "ann",
            WARN,
            detail,
            Some("run `cairn embed` to rebuild the ANN index".to_string()),
        );
    }
    result(
        "ann",
        PASS,
        format!("sqlite-vec available ({emb_n} vector(s) indexed)"),
        None,
    )
}

// --- 4. embed server -----------------------------------------------------------------

fn check_embed_server(conn: &Connection) -> Vec<CheckResult> {
    use cairn_query::tools_search;
    let configured = tools_search::backend_name();
    if !SERVER_FAMILY.contains(&configured.as_str()) {
        return vec![result(
            "embed_server",
            PASS,
            format!("disabled by config (CAIRN_EMBED_BACKEND={configured})"),
            None,
        )];
    }

    let mut results = Vec::new();
    let base = match tools_search::server_base_url(&configured) {
        Ok(base) => base.trim_end_matches('/').to_string(),
        Err(e) => {
            results.push(result(
                "embed_server",
                FAIL,
                e,
                Some(
                    "set CAIRN_EMBED_BASE_URL to an OpenAI-compatible /v1 URL, \
                     or use the omlx/ollama presets"
                        .to_string(),
                ),
            ));
            return results;
        }
    };
    let model = tools_search::server_model();

    let Some((ids, has_model)) = fetch_model_listing(&base) else {
        results.push(result(
            "embed_server",
            FAIL,
            format!("embedding server unreachable (GET {base}/models failed)"),
            Some(format!(
                "start the embedding server and verify GET {base}/models lists \
                 the model (presets: omlx http://127.0.0.1:8000/v1, ollama \
                 http://127.0.0.1:11434/v1)"
            )),
        ));
        return results;
    };
    if !has_model {
        let available = if ids.is_empty() {
            "<none>".to_string()
        } else {
            ids.join(", ")
        };
        results.push(result(
            "embed_server",
            FAIL,
            format!("configured model '{model}' not served; available: {available}"),
            Some("serve the model or set CAIRN_EMBED_SERVER_MODEL to a served id".to_string()),
        ));
        return results;
    }

    // Parity: sample stored chunks under the stamp and compare against the
    // server's fresh vectors.
    let stamp = tools_search::semantic_env(None).model;
    let mut parity_note = "parity vacuous (no stored rows under this stamp)".to_string();
    match check_parity(conn, &stamp, &base, &model) {
        Ok(ParityOutcome::Passed(mean)) => {
            parity_note = format!("parity {mean:.4} over {PARITY_SAMPLE_LIMIT} stored chunk(s)");
        }
        Ok(ParityOutcome::Vacuous) => {}
        Ok(ParityOutcome::Failed(reason)) => {
            results.push(result(
                "embed_server",
                WARN,
                format!("parity sample failed ({PARITY_SAMPLE_LIMIT} chunk(s) sampled): {reason}"),
                Some("re-embed with `cairn embed` to realign stored vectors".to_string()),
            ));
            return results;
        }
        Err(e) => {
            results.push(result(
                "embed_server",
                WARN,
                format!("parity sample failed to run: {e}"),
                Some("re-run `cairn doctor` once the embedding server is stable".to_string()),
            ));
            return results;
        }
    }

    match embed_ping(&base, &model) {
        Ok(latency) => {
            let netloc = netloc_of(&base);
            results.push(result(
                "embed_server",
                PASS,
                format!(
                    "server ok: {netloc} model '{model}', embed latency {latency}, {parity_note}"
                ),
                None,
            ));
        }
        Err(e) => {
            results.push(result(
                "embed_server",
                FAIL,
                format!("embed request failed: {e}"),
                Some(format!(
                    "verify the server serves POST {base}/embeddings with model '{model}'"
                )),
            ));
        }
    }
    results
}

fn netloc_of(url: &str) -> String {
    let rest = url.split_once("://").map(|(_, rest)| rest).unwrap_or(url);
    rest.split(['/', '?', '#'])
        .next()
        .unwrap_or(rest)
        .replace(['[', ']'], "")
}

fn split_base_url(base: &str) -> Option<(String, u16, String)> {
    let (scheme, rest) = base.split_once("://")?;
    if scheme != "http" {
        return None;
    }
    let (authority, path) = rest.split_once('/').map_or((rest, ""), |(a, p)| (a, p));
    let (host, port) = match authority.rsplit_once(':') {
        Some((h, p)) if p.chars().all(|c| c.is_ascii_digit()) && !p.is_empty() => {
            (h.to_string(), p.parse::<u16>().ok()?)
        }
        _ => (authority.to_string(), 80),
    };
    Some((host, port, format!("/{path}")))
}

/// Bounded std-HTTP request (loopback-tool grade, no TLS).
fn http_request(
    host: &str,
    port: u16,
    method: &str,
    path: &str,
    body: Option<&str>,
    timeout: Duration,
) -> Option<(u16, Vec<u8>)> {
    use std::net::ToSocketAddrs as _;
    let addr = (host, port).to_socket_addrs().ok()?.next()?;
    let mut stream = std::net::TcpStream::connect_timeout(&addr, timeout).ok()?;
    stream.set_read_timeout(Some(timeout)).ok()?;
    stream.set_write_timeout(Some(timeout)).ok()?;
    let mut request =
        format!("{method} {path} HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n");
    if let Some(body) = body {
        request.push_str("Content-Type: application/json\r\n");
        request.push_str(&format!("Content-Length: {}\r\n", body.len()));
    }
    request.push_str("\r\n");
    if let Some(body) = body {
        request.push_str(body);
    }
    stream.write_all(request.as_bytes()).ok()?;
    let mut raw = Vec::new();
    stream.read_to_end(&mut raw).ok()?;
    // Reuse the daemon's framing parser through the public GET is not possible;
    // parse inline (status line + headers + length-delimited/eof body).
    let header_end = raw.windows(4).position(|w| w == b"\r\n\r\n")?;
    let head = String::from_utf8_lossy(&raw[..header_end]);
    let status = head
        .lines()
        .next()?
        .split_whitespace()
        .nth(1)?
        .parse::<u16>()
        .ok()?;
    let mut content_length: Option<usize> = None;
    let mut chunked = false;
    for line in head.lines().skip(1) {
        let Some((name, value)) = line.split_once(':') else {
            continue;
        };
        let value = value.trim();
        if name.eq_ignore_ascii_case("content-length") {
            content_length = value.parse().ok();
        } else if name.eq_ignore_ascii_case("transfer-encoding")
            && value.eq_ignore_ascii_case("chunked")
        {
            chunked = true;
        }
    }
    let body_bytes = &raw[header_end + 4..];
    let body = if chunked {
        decode_chunked(body_bytes)?
    } else if let Some(len) = content_length {
        body_bytes
            .get(..len.min(body_bytes.len()))
            .unwrap_or(body_bytes)
            .to_vec()
    } else {
        body_bytes.to_vec()
    };
    Some((status, body))
}

fn decode_chunked(body: &[u8]) -> Option<Vec<u8>> {
    let mut out = Vec::new();
    let mut rest = body;
    loop {
        let line_end = rest.windows(2).position(|w| w == b"\r\n")?;
        let size = usize::from_str_radix(
            String::from_utf8_lossy(&rest[..line_end])
                .split(';')
                .next()?
                .trim(),
            16,
        )
        .ok()?;
        rest = &rest[line_end + 2..];
        if size == 0 {
            return Some(out);
        }
        if rest.len() < size {
            return None;
        }
        out.extend_from_slice(&rest[..size]);
        rest = rest.get(size + 2..).unwrap_or(&[]);
    }
}

fn embed_auth_header() -> Option<String> {
    cairn_query::tools_search::config_value("CAIRN_EMBED_API_KEY")
        .map(|key| format!("Authorization: Bearer {key}\r\n"))
}

/// GET {base}/models -> (ids, configured model listed).
fn fetch_model_listing(base: &str) -> Option<(Vec<String>, bool)> {
    let (host, port, prefix) = split_base_url(base)?;
    let (_, body) = http_request(
        &host,
        port,
        "GET",
        &format!("{prefix}/models"),
        None,
        Duration::from_secs(10),
    )?;
    let parsed: Value = serde_json::from_slice(&body).ok()?;
    let ids: Vec<String> = parsed
        .get("data")?
        .as_array()?
        .iter()
        .filter_map(|e| e.get("id").and_then(Value::as_str).map(String::from))
        .collect();
    let model = cairn_query::tools_search::server_model();
    let has_model = ids.iter().any(|id| id == &model);
    Some((ids, has_model))
}

/// POST {base}/embeddings with `texts`; returns one float vector per text.
fn embed_vectors(base: &str, model: &str, texts: &[String]) -> Result<Vec<Vec<f32>>, String> {
    use std::net::ToSocketAddrs as _;
    let (host, port, prefix) = split_base_url(base)
        .ok_or_else(|| format!("{base} is not an http URL; the doctor probe is http-only"))?;
    let payload = json!({ "model": model, "input": texts }).to_string();
    let headers = embed_auth_header();
    let request_path = format!("{prefix}/embeddings");
    let request = |body: &str| {
        let mut raw = format!(
            "POST {request_path} HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n\
             Content-Type: application/json\r\nContent-Length: {}\r\n",
            body.len()
        );
        if let Some(header) = &headers {
            raw.push_str(header);
        }
        raw.push_str("\r\n");
        raw.push_str(body);
        raw
    };
    let raw = request(&payload);
    let addr = (host.as_str(), port)
        .to_socket_addrs()
        .map_err(|e| e.to_string())?
        .next()
        .ok_or_else(|| "resolve failed".to_string())?;
    let mut stream = std::net::TcpStream::connect_timeout(&addr, Duration::from_secs(30))
        .map_err(|e| e.to_string())?;
    stream
        .set_read_timeout(Some(Duration::from_secs(30)))
        .map_err(|e| e.to_string())?;
    stream
        .set_write_timeout(Some(Duration::from_secs(30)))
        .map_err(|e| e.to_string())?;
    stream
        .write_all(raw.as_bytes())
        .map_err(|e| e.to_string())?;
    let mut buf = Vec::new();
    stream.read_to_end(&mut buf).map_err(|e| e.to_string())?;
    let header_end = buf
        .windows(4)
        .position(|w| w == b"\r\n\r\n")
        .ok_or("malformed HTTP response")?;
    let head = String::from_utf8_lossy(&buf[..header_end]);
    let status: u16 = head
        .lines()
        .next()
        .and_then(|l| l.split_whitespace().nth(1))
        .and_then(|s| s.parse().ok())
        .ok_or("malformed status line")?;
    let body_bytes = &buf[header_end + 4..];
    let body = if head
        .to_ascii_lowercase()
        .contains("transfer-encoding: chunked")
    {
        decode_chunked(body_bytes).ok_or("malformed chunked body")?
    } else {
        body_bytes.to_vec()
    };
    if status != 200 {
        return Err(format!(
            "embedding server rejected the request (HTTP {status})"
        ));
    }
    let body_text = String::from_utf8_lossy(&body).into_owned();
    let parsed: Value = serde_json::from_slice(&body).map_err(|_| {
        let prefix: String = body_text.chars().take(120).collect();
        format!("embedding server returned malformed response: {prefix}")
    })?;
    let mut data: Vec<(i64, Vec<f32>)> = parsed
        .get("data")
        .and_then(Value::as_array)
        .ok_or_else(|| {
            let prefix: String = body_text.chars().take(120).collect();
            format!("embedding server returned malformed response: {prefix}")
        })?
        .iter()
        .filter_map(|entry| {
            let index = entry.get("index")?.as_i64()?;
            let vector: Vec<f32> = entry
                .get("embedding")?
                .as_array()?
                .iter()
                .filter_map(Value::as_f64)
                .map(|f| f as f32)
                .collect();
            Some((index, vector))
        })
        .collect();
    if data.len() != texts.len() {
        return Err(format!(
            "embedding server returned {} vectors for {} inputs",
            data.len(),
            texts.len()
        ));
    }
    data.sort_by_key(|(index, _)| *index);
    Ok(data.into_iter().map(|(_, vector)| vector).collect())
}

fn embed_ping(base: &str, model: &str) -> Result<String, String> {
    let start = std::time::Instant::now();
    embed_vectors(base, model, &["ping".to_string()])?;
    let ms = start.elapsed().as_secs_f64() * 1000.0;
    Ok(ms_bucket(ms))
}

fn ms_bucket(ms: f64) -> String {
    for (bound, label) in [
        (10.0, "0-10ms"),
        (100.0, "10-100ms"),
        (1000.0, "100-1000ms"),
    ] {
        if ms < bound {
            return label.to_string();
        }
    }
    ">1000ms".to_string()
}

enum ParityOutcome {
    Vacuous,
    Passed(f64),
    Failed(String),
}

/// Parity-check sampled stored chunks; Err = the sample could not run.
fn check_parity(
    conn: &Connection,
    stamp: &str,
    base: &str,
    model: &str,
) -> Result<ParityOutcome, String> {
    let rows: Vec<(String, Vec<u8>)> = {
        let mut stmt = conn
            .prepare("SELECT chunk, vec FROM embeddings WHERE model = ?1 ORDER BY rowid LIMIT ?2")
            .map_err(|e| e.to_string())?;
        let mapped = stmt
            .query_map(rusqlite::params![stamp, PARITY_SAMPLE_LIMIT], |row| {
                Ok((row.get::<_, String>(0)?, row.get::<_, Vec<u8>>(1)?))
            })
            .map_err(|e| e.to_string())?;
        mapped
            .collect::<Result<Vec<_>, _>>()
            .map_err(|e| e.to_string())?
    };
    if rows.is_empty() {
        return Ok(ParityOutcome::Vacuous);
    }
    let chunks: Vec<String> = rows.iter().map(|(chunk, _)| chunk.clone()).collect();
    let vectors = embed_vectors(base, model, &chunks)?;
    let mut cosines = Vec::new();
    for ((_, stored_blob), served) in rows.iter().zip(&vectors) {
        let stored = decode_f32(stored_blob);
        if served.len() != stored.len() {
            return Ok(ParityOutcome::Failed(format!(
                "dim_mismatch stored={} server={}",
                stored.len(),
                served.len()
            )));
        }
        cosines.push(cosine(&stored, served));
    }
    let mean = cosines.iter().sum::<f64>() / cosines.len() as f64;
    if mean >= PARITY_GATE {
        Ok(ParityOutcome::Passed(mean))
    } else {
        Ok(ParityOutcome::Failed(format!(
            "mean_cosine {mean:.4} below gate {PARITY_GATE}"
        )))
    }
}

fn decode_f32(blob: &[u8]) -> Vec<f32> {
    blob.as_chunks::<4>()
        .0
        .iter()
        .map(|c| f32::from_le_bytes(*c))
        .collect()
}

fn cosine(left: &[f32], right: &[f32]) -> f64 {
    let mut dot = 0.0f64;
    let mut nl = 0.0f64;
    let mut nr = 0.0f64;
    for (a, b) in left.iter().zip(right) {
        dot += *a as f64 * *b as f64;
        nl += (*a as f64) * (*a as f64);
        nr += (*b as f64) * (*b as f64);
    }
    if nl == 0.0 || nr == 0.0 {
        return 0.0;
    }
    dot / (nl.sqrt() * nr.sqrt())
}

// --- 5. freshness ---------------------------------------------------------------------

fn check_freshness(conn: &Connection) -> CheckResult {
    let now = now_epoch();
    let mut parts: Vec<String> = Vec::new();
    let mut status = PASS;
    let mut hint: Option<String> = None;

    let pending = conn.query_row(
        "SELECT COUNT(*), MIN(changed_at) FROM pending_sync",
        [],
        |row| Ok((row.get::<_, i64>(0)?, row.get::<_, Option<String>>(1)?)),
    );
    if let Ok((pending_n, oldest)) = pending
        && pending_n > 0
    {
        status = WARN;
        parts.push(format!(
            "{pending_n} pending-sync file(s), oldest {}",
            age_str(now, oldest.as_deref())
        ));
    }

    let interrupted: Vec<String> = conn
        .prepare(
            "SELECT repo_id FROM repo_build_state WHERE state = 'building' ORDER BY started_at",
        )
        .and_then(|mut stmt| {
            let mapped = stmt.query_map([], |row| row.get::<_, String>(0))?;
            mapped.collect::<rusqlite::Result<Vec<_>>>()
        })
        .unwrap_or_default();
    if !interrupted.is_empty() {
        status = WARN;
        parts.push(format!(
            "interrupted rebuild of {} (marker from a crashed `cairn build --repo`)",
            interrupted.join(", ")
        ));
        hint = Some(format!(
            "re-run `cairn build --repo {}` to rebuild the partial repo",
            interrupted[0]
        ));
    }

    let last_build: Option<String> = conn
        .query_row(
            "SELECT started_at FROM build_runs ORDER BY started_at DESC LIMIT 1",
            [],
            |row| row.get::<_, String>(0),
        )
        .ok();
    match last_build {
        Some(last_build) => {
            let last_epoch = parse_ts(&last_build);
            let stale = last_epoch.is_some_and(|ts| now - ts > (STALE_BUILD_DAYS * 86400) as f64);
            let tag = if stale {
                format!(" (>{STALE_BUILD_DAYS}d)")
            } else {
                String::new()
            };
            parts.push(format!(
                "last build {}{tag}",
                age_str(now, Some(&last_build))
            ));
            if stale {
                status = WARN;
            }
        }
        None => {
            let sym_n = query_i64(conn, "SELECT COUNT(*) FROM symbols", &[], 0);
            if sym_n > 0 {
                status = WARN;
                parts.push(format!(
                    "no build_runs recorded (but {sym_n} symbols indexed)"
                ));
            } else {
                parts.push("no builds yet (fresh install)".to_string());
            }
        }
    }

    result(
        "freshness",
        status,
        if parts.is_empty() {
            "up to date".to_string()
        } else {
            parts.join("; ")
        },
        hint,
    )
}

// --- 6. parse errors --------------------------------------------------------------------

fn check_parse_errors(conn: &Connection) -> CheckResult {
    let total = query_i64(conn, "SELECT COUNT(*) FROM parse_errors", &[], -1);
    if total < 0 {
        return result("parse_errors", WARN, "parse_errors table unavailable", None);
    }
    if total == 0 {
        return result("parse_errors", PASS, "0 parse errors", None);
    }
    let samples: Vec<String> = conn
        .prepare(
            "SELECT file_path, error_message FROM parse_errors ORDER BY timestamp DESC LIMIT 5",
        )
        .and_then(|mut stmt| {
            let mapped = stmt.query_map([], |row| {
                Ok((row.get::<_, String>(0)?, row.get::<_, Option<String>>(1)?))
            })?;
            mapped.collect::<rusqlite::Result<Vec<_>>>()
        })
        .unwrap_or_default()
        .into_iter()
        .map(|(path, message)| {
            let message = message.unwrap_or_default();
            let message: String = message.chars().take(80).collect();
            if message.is_empty() {
                path
            } else {
                format!("{path}: {message}")
            }
        })
        .collect();
    let mut detail = format!("{total} parse error(s)");
    if !samples.is_empty() {
        detail.push_str("; newest: ");
        detail.push_str(&samples.join(" | "));
    }
    result(
        "parse_errors",
        WARN,
        detail,
        Some("run `cairn status` for the full list".to_string()),
    )
}

// --- 6b. edge signals (stores predating the signal columns) --------------------------

const EDGE_SIGNAL_COLUMNS: [&str; 3] = ["ref_name", "receiver_type", "call_arity"];
const EDGE_SIGNALS_HINT: &str =
    "run `cairn build` to rebuild the store and populate the edge signals";

// contains/imports edges are structural (never resolved); scip-authored rows
// and language-server-upgraded exact edges legitimately carry no ref_name.
fn check_edge_signals(conn: &Connection) -> CheckResult {
    let cols: Vec<String> = conn
        .prepare("SELECT name FROM pragma_table_info('edges')")
        .and_then(|mut stmt| {
            let mapped = stmt.query_map([], |row| row.get::<_, String>(0))?;
            mapped.collect::<rusqlite::Result<Vec<_>>>()
        })
        .unwrap_or_default();
    if cols.is_empty() {
        return result(
            "edge_signals",
            WARN,
            "edges table unavailable (see schema)",
            None,
        );
    }
    if !EDGE_SIGNAL_COLUMNS
        .iter()
        .all(|c| cols.iter().any(|col| col == c))
    {
        return result(
            "edge_signals",
            WARN,
            "edges signal columns missing (store predates \
             ref_name/receiver_type/call_arity) -- repair runs name-only until \
             the store is rebuilt",
            Some(EDGE_SIGNALS_HINT.to_string()),
        );
    }
    match conn.query_row(
        "SELECT COUNT(*) FROM edges
          WHERE ref_name IS NULL
            AND resolution IN ('ambiguous', 'unresolved')
            AND kind NOT IN ('contains', 'imports')
            AND COALESCE(source, 'tree_sitter') != 'scip'",
        [],
        |row| row.get::<_, i64>(0),
    ) {
        Ok(0) => result(
            "edge_signals",
            PASS,
            "reference edges carry resolver signals",
            None,
        ),
        Ok(n) => result(
            "edge_signals",
            WARN,
            format!(
                "{n} reference edge(s) without resolver signals -- incremental \
                 repair runs name-only for them until the store is rebuilt"
            ),
            Some(EDGE_SIGNALS_HINT.to_string()),
        ),
        Err(e) => result(
            "edge_signals",
            WARN,
            format!("cannot read edge signals: {e}"),
            None,
        ),
    }
}

// --- 7. concurrency -----------------------------------------------------------------------

fn check_concurrency(conn: &Connection) -> CheckResult {
    let cutoff = now_epoch() - (CONTENTION_WINDOW_DAYS * 86400) as f64;
    let contention = query_i64(
        conn,
        "SELECT COUNT(*) FROM events WHERE name = ?1 AND ts >= ?2",
        &[&"lock_contention", &cutoff],
        0,
    );
    let mut stray_total = 0i64;
    if let Ok(mut stmt) = conn.prepare("SELECT attrs FROM events WHERE name = ?1 AND ts >= ?2")
        && let Ok(rows) = stmt.query_map(rusqlite::params!["stray_swept", cutoff], |row| {
            row.get::<_, Option<String>>(0)
        })
    {
        for row in rows.filter_map(|r| r.ok().flatten()) {
            if let Ok(attrs) = serde_json::from_str::<Value>(&row)
                && let Some(count) = attrs.get("count").and_then(Value::as_i64)
            {
                stray_total += count;
            }
        }
    }
    let mut parts = vec![format!(
        "{contention} lock-contention event(s) in {CONTENTION_WINDOW_DAYS}d"
    )];
    if stray_total > 0 {
        parts.push(format!("{stray_total} stray process(es) swept"));
    }
    let status = if contention > 0 { WARN } else { PASS };
    result("concurrency", status, parts.join("; "), None)
}

// --- 8. tool health -------------------------------------------------------------------------

fn percentile(values: &mut [f64], pct: f64) -> Option<f64> {
    if values.is_empty() {
        return None;
    }
    values.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let k = (values.len() - 1) as f64 * (pct / 100.0);
    let lo = k.floor() as usize;
    let hi = (lo + 1).min(values.len() - 1);
    Some(values[lo] + (values[hi] - values[lo]) * (k - lo as f64))
}

fn check_tool_health(conn: &Connection) -> CheckResult {
    let cutoff = now_epoch() - (TOOL_HEALTH_WINDOW_DAYS * 86400) as f64;
    let tools: Vec<String> = conn
        .prepare("SELECT DISTINCT tool_name FROM tool_metrics WHERE invoked_at >= ?1")
        .and_then(|mut stmt| {
            let mapped = stmt.query_map([&cutoff], |row| row.get::<_, String>(0))?;
            mapped.collect::<rusqlite::Result<Vec<_>>>()
        })
        .unwrap_or_default();
    if tools.is_empty() {
        return result("tool_health", PASS, "no tool metrics recorded yet", None);
    }

    let mut offenders: Vec<String> = Vec::new();
    let mut healthy = 0usize;
    for tool in &tools {
        let (calls, errs) = conn
            .query_row(
                "SELECT COUNT(*), SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) \
                 FROM tool_metrics WHERE tool_name = ?1 AND invoked_at >= ?2",
                rusqlite::params![tool, cutoff],
                |row| {
                    Ok((
                        row.get::<_, i64>(0)?,
                        row.get::<_, Option<i64>>(1)?.unwrap_or(0),
                    ))
                },
            )
            .unwrap_or((0, 0));
        if calls == 0 {
            continue;
        }
        let err_rate = errs as f64 / calls as f64;
        let mut durations: Vec<f64> = conn
            .prepare(
                "SELECT duration_ms FROM tool_metrics WHERE tool_name = ?1 \
                 AND invoked_at >= ?2 AND duration_ms IS NOT NULL",
            )
            .and_then(|mut stmt| {
                let mapped = stmt.query_map(rusqlite::params![tool, cutoff], |row| {
                    row.get::<_, Option<f64>>(0)
                })?;
                Ok(mapped
                    .filter_map(Result::ok)
                    .flatten()
                    .collect::<Vec<f64>>())
            })
            .unwrap_or_default();
        let p95 = percentile(&mut durations, 95.0);
        let bad_rate = err_rate > TOOL_ERROR_RATE_WARN;
        let bad_lat = p95.is_some_and(|p| p > TOOL_P95_LATENCY_MS_WARN);
        if bad_rate || bad_lat {
            let lat = p95.map(|p| format!(", p95 {p:.0}ms")).unwrap_or_default();
            offenders.push(format!("{}: {:.0}% err{lat}", tool, err_rate * 100.0));
        } else {
            healthy += 1;
        }
    }
    if !offenders.is_empty() {
        return result(
            "tool_health",
            WARN,
            format!(
                "{} tool(s) over threshold; {}",
                offenders.len(),
                offenders.join("; ")
            ),
            None,
        );
    }
    result(
        "tool_health",
        PASS,
        format!("{healthy} tool(s) within thresholds"),
        None,
    )
}

// --- 9. memory staleness ----------------------------------------------------------------------

fn check_memory_staleness(conn: &Connection, db: &str) -> CheckResult {
    let tribal_dir = Path::new(db)
        .parent()
        .map(|p| p.join(".knowledge").join("memory").join("tribal"))
        .unwrap_or_else(|| PathBuf::from(".knowledge/memory/tribal"));
    let cutoff = now_epoch() - (MEMORY_REF_WINDOW_DAYS * 86400) as f64;
    let mut tribal: Vec<PathBuf> = std::fs::read_dir(&tribal_dir)
        .map(|entries| {
            entries
                .flatten()
                .map(|e| e.path())
                .filter(|p| p.extension().is_some_and(|e| e == "md"))
                .collect()
        })
        .unwrap_or_default();
    tribal.sort();
    let old_count = tribal
        .iter()
        .filter(|p| {
            p.metadata()
                .and_then(|m| m.modified())
                .ok()
                .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                .is_some_and(|age| (age.as_secs_f64()) < cutoff)
        })
        .count();
    let refs = query_i64(
        conn,
        "SELECT COUNT(*) FROM memory_refs WHERE referenced_at >= ?1",
        &[&iso_cutoff(MEMORY_REF_WINDOW_DAYS)],
        0,
    );
    if old_count == 0 {
        return result(
            "memory_staleness",
            PASS,
            format!(
                "no tribal memories older than {MEMORY_REF_WINDOW_DAYS}d \
                 ({} total, {refs} reference(s) in that window)",
                tribal.len()
            ),
            None,
        );
    }
    if refs == 0 {
        return result(
            "memory_staleness",
            WARN,
            format!(
                "{old_count} tribal memories older than {MEMORY_REF_WINDOW_DAYS}d, \
                 0 references recorded in that window — memory is write-only"
            ),
            Some(
                "read memories through explore / recall_memory so lookups record \
                 memory_refs; check wiring with the environment check"
                    .to_string(),
            ),
        );
    }
    result(
        "memory_staleness",
        PASS,
        format!(
            "{old_count} tribal memories older than {MEMORY_REF_WINDOW_DAYS}d, \
             {refs} reference(s) recorded in that window"
        ),
        None,
    )
}

fn iso_cutoff(days: i64) -> String {
    let secs = now_epoch() as i64 - days * 86400;
    cairn_build::timefmt::iso_from_epoch(secs, 0)
}

// --- 10. config echo -----------------------------------------------------------------------------

pub(crate) fn knob_source(name: &str, default: &str) -> (String, &'static str) {
    if let Ok(env_value) = std::env::var(name) {
        let trimmed = env_value.trim();
        if !trimmed.is_empty() {
            return (trimmed.to_string(), "env");
        }
    }
    if let Some(file_value) = cairn_query::tools_search::config_value(name) {
        return (file_value, "file");
    }
    (default.to_string(), "default")
}

fn check_config() -> CheckResult {
    let env_echo =
        |name: &str, fallback: &str| std::env::var(name).unwrap_or_else(|_| fallback.to_string());
    let mut knobs: Vec<(String, String)> = vec![
        ("workers".into(), env_echo("CAIRN_WORKERS", "<unset>")),
        ("read_only".into(), env_echo("CAIRN_READ_ONLY", "<unset>")),
        ("fusion".into(), env_echo("CAIRN_FUSION", "<unset>")),
        (
            "ann_backend".into(),
            env_echo("CAIRN_ANN_BACKEND", "<unset (=sqlite-vec)>"),
        ),
        (
            "telemetry".into(),
            env_echo("CAIRN_TELEMETRY", "<unset (=on)>"),
        ),
        (
            "log_level".into(),
            env_echo("CAIRN_LOG_LEVEL", "<unset (=WARNING)>"),
        ),
    ];
    let embed_knobs: [(&str, &str, &str); 6] = [
        ("embed_backend", "CAIRN_EMBED_BACKEND", "local"),
        ("embed_base_url", "CAIRN_EMBED_BASE_URL", "<preset>"),
        ("embed_server_model", "CAIRN_EMBED_SERVER_MODEL", "bge-m3"),
        ("embed_timeout", "CAIRN_EMBED_TIMEOUT", "30"),
        ("embed_server_batch", "CAIRN_EMBED_SERVER_BATCH", "32"),
        ("embed_model_stamp", "CAIRN_EMBED_MODEL_STAMP", "<derived>"),
    ];
    for (label, env_name, default) in embed_knobs {
        let (value, source) = knob_source(env_name, default);
        knobs.push((label.to_string(), format!("{value} ({source})")));
    }
    let key_source = knob_source("CAIRN_EMBED_API_KEY", "").1;
    let key_echo = if key_source == "default" {
        "<unset>".to_string()
    } else {
        format!("<set via {key_source}>")
    };
    knobs.push(("api_key".to_string(), key_echo));
    result(
        "config",
        PASS,
        knobs
            .into_iter()
            .map(|(k, v)| format!("{k}={v}"))
            .collect::<Vec<_>>()
            .join("; "),
        None,
    )
}

// --- daemon (the serving-impl report) ---------------------------------------------------------------

fn check_daemon() -> CheckResult {
    let (d_port, d_host) = cairn_daemon::lifecycle::daemon_bind();
    let port = d_port.unwrap_or(cairn_daemon::lifecycle::DEFAULT_PORT);
    let host = d_host.unwrap_or_else(|| cairn_daemon::lifecycle::DEFAULT_HOST.to_string());
    let url = cairn_daemon::lifecycle::mcp_url(port, &host);
    match cairn_daemon::lifecycle::healthz_json(port, &host) {
        None => result("daemon", PASS, format!("no daemon serving at {url}"), None),
        Some(payload) => {
            // Payloads without an impl marker come from daemons of older installs.
            let impl_name = payload
                .get("impl")
                .and_then(Value::as_str)
                .unwrap_or("legacy");
            result(
                "daemon",
                PASS,
                format!("{impl_name} daemon serving at {url}"),
                None,
            )
        }
    }
}

// --- agent hooks (retired python runner) -------------------------------------------------------------

/// Hook files whose cairn commands may still name the retired python module runner.
fn agent_hook_files() -> Vec<PathBuf> {
    let ws = crate::store_paths::resolve_workspace(None);
    let home = home_dir();
    vec![
        ws.join(".claude").join("settings.json"),
        home.join(".claude").join("settings.json"),
        ws.join(".cursor").join("hooks.json"),
        home.join(".cursor").join("hooks.json"),
    ]
}

fn check_agent_hooks() -> CheckResult {
    let stale: Vec<String> = agent_hook_files()
        .into_iter()
        .filter(|path| path.is_file())
        .filter_map(|path| {
            let count = crate::agent_install::retired_hook_commands(&path).len();
            (count > 0).then(|| format!("{} ({count})", path.display()))
        })
        .collect();
    if stale.is_empty() {
        return result("agent_hooks", PASS, "no retired hook commands", None);
    }
    result(
        "agent_hooks",
        WARN,
        format!(
            "retired `python -m cairn.hooks.claude_hooks` commands in {}",
            stale.join(", ")
        ),
        Some("run `cairn doctor --fix` to migrate them to `cairn hooks run`".to_string()),
    )
}

// --- 11. environment wiring ---------------------------------------------------------------------------

fn check_environment(db: &str) -> CheckResult {
    let mut findings: Vec<(&'static str, String)> = Vec::new();
    let mut hints: Vec<String> = Vec::new();

    if !Path::new(db).exists() {
        findings.push((WARN, format!("store not found at {db}")));
        hints.push("run `cairn init` + `cairn build` first".to_string());
    } else {
        findings.push((PASS, "resolved store present".to_string()));
    }

    let (reg_findings, reg_hints) = registration_findings();
    findings.extend(reg_findings);
    hints.extend(reg_hints);

    let status = if findings.iter().any(|(st, _)| *st == FAIL) {
        FAIL
    } else if findings.iter().any(|(st, _)| *st == WARN) {
        WARN
    } else {
        PASS
    };
    let detail = findings
        .into_iter()
        .map(|(_, text)| text)
        .collect::<Vec<_>>()
        .join("; ");
    let mut seen = std::collections::BTreeSet::new();
    let hint = hints
        .into_iter()
        .filter(|h| seen.insert(h.clone()))
        .collect::<Vec<_>>()
        .join("; ");
    result(
        "environment",
        status,
        detail,
        (!hint.is_empty()).then_some(hint),
    )
}

/// Registration config files that may hold a cairn entry:
/// (path, display, workspace_owned).
fn client_config_paths(client: &str) -> Vec<(PathBuf, String, bool)> {
    let home = home_dir();
    let ws_files: &[(&str, &str)] = &[
        ("claude", ".mcp.json"),
        ("cursor", ".cursor/mcp.json"),
        ("droid", ".factory/mcp.json"),
        ("zcode", ".zcode/config.json"),
        ("opencode", "opencode.json"),
        ("kilo", "kilo.json"),
        ("omp", ".omp/mcp.json"),
    ];
    let home_files: &[(&str, &[&str])] = &[
        ("claude", &["~/.claude.json"]),
        ("cursor", &["~/.cursor/mcp.json"]),
        ("droid", &["~/.factory/mcp.json"]),
        (
            "zcode",
            &["~/.zcode/config.json", "~/.zcode/cli/config.json"],
        ),
        ("opencode", &["~/.config/opencode/opencode.json"]),
        ("kilo", &["~/.config/kilo/kilo.json"]),
        ("omp", &["~/.omp/agent/mcp.json"]),
    ];
    let mut pairs = Vec::new();
    if let Some((_, rel)) = ws_files.iter().find(|(c, _)| *c == client) {
        let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
        pairs.push((cwd.join(rel), rel.to_string(), true));
    }
    if client == "claude-desktop" {
        let path = agent_install::claude_desktop_config_path();
        pairs.push((path.clone(), scrub_safe_display(&path, &home), false));
    }
    if client == "agy" {
        let path = agent_install::agy_config_path();
        pairs.push((path.clone(), scrub_safe_display(&path, &home), false));
    }
    if let Some((_, rels)) = home_files.iter().find(|(c, _)| *c == client) {
        for rel in *rels {
            let stripped = rel.trim_start_matches("~/");
            pairs.push((home.join(stripped), rel.to_string(), false));
        }
    }
    pairs
}

fn home_dir() -> PathBuf {
    cairn_store::home::platform_home().unwrap_or_else(|| PathBuf::from("/"))
}

fn scrub_safe_display(path: &Path, home: &Path) -> String {
    path.strip_prefix(home)
        .map(|rel| format!("~/{rel}", rel = rel.display()))
        .unwrap_or_else(|_| path.to_string_lossy().into_owned())
}

fn registration_url_of(entry: &Value) -> Option<String> {
    for key in ["url", "serverUrl"] {
        if let Some(value) = entry.get(key).and_then(Value::as_str)
            && value.contains("://")
        {
            return Some(value.to_string());
        }
    }
    None
}

/// Transport-class findings: stdio spawn-shaped, SSE endpoint, unsupported desktop.
fn registration_findings() -> (Vec<(&'static str, String)>, Vec<String>) {
    let rewrite_hint = "run `cairn install-agents` to rewrite registrations to the \
                        daemon URL (`cairn doctor --fix` repoints user-owned configs)"
        .to_string();
    let daemon_url = agent_install::daemon_mcp_url();
    let mut findings = Vec::new();
    let mut hints = Vec::new();
    let installed = agent_install::check_installed(&std::env::current_dir().unwrap_or_default());
    for (client, is_installed) in &installed {
        if !is_installed {
            continue;
        }
        for (path, disp, workspace_owned) in client_config_paths(client) {
            let Some(entry) = agent_install::registration_entry(&path) else {
                continue;
            };
            if client == "claude-desktop" {
                findings.push((
                    WARN,
                    format!(
                        "claude-desktop: registration in {disp} is unsupported \
                         (Claude Desktop has no URL-based MCP surface; the daemon \
                         serves HTTP only)"
                    ),
                ));
                hints.push(
                    "remove the cairn entry from the Claude Desktop config (it is \
                     never repointed)"
                        .to_string(),
                );
                continue;
            }
            let is_stdio = entry.get("command").is_some();
            if is_stdio {
                if workspace_owned {
                    findings.push((
                        WARN,
                        format!(
                            "{client}: stdio registration in {disp} is workspace-owned \
                             (untrusted repo content; doctor does not execute or \
                             rewrite it)"
                        ),
                    ));
                } else {
                    findings.push((
                        WARN,
                        format!(
                            "{client}: stdio registration in {disp} predates the HTTP \
                             daemon (`cairn serve` serves HTTP only)"
                        ),
                    ));
                }
                hints.push(rewrite_hint.clone());
                continue;
            }
            match registration_url_of(&entry) {
                None => {}
                Some(url) if url == daemon_url => {}
                Some(url) => {
                    findings.push((
                        FAIL,
                        format!(
                            "{client}: SSE registration in {disp} points at {url} but \
                             the daemon serves {daemon_url} (pre-HTTP-era endpoint)"
                        ),
                    ));
                    hints.push(rewrite_hint.clone());
                }
            }
        }
    }
    (findings, hints)
}

// --- --fix ---------------------------------------------------------------------------------

const REPOINTED: &str = "repointed-to-url";
const REFUSED_RECENT: &str = "refused-recent-write";
const SKIPPED_WORKSPACE: &str = "skipped-workspace-owned";
const UNSUPPORTED_DESKTOP: &str = "remove-unsupported-desktop";
const UNREADABLE_CONFIG: &str = "unreadable-config";
const MALFORMED_CONFIG: &str = "malformed-config";
const UNREPLACEABLE: &str = "unrecognized-shape";
const WRITE_FAILED: &str = "write-failed";
const MIGRATED_HOOKS: &str = "migrated-hooks";

/// Stale = stdio spawn-shaped, or a non-daemon endpoint.
fn stale_registration(entry: &Value, daemon_url: &str) -> bool {
    if entry.get("command").is_some() {
        return true;
    }
    match registration_url_of(entry) {
        Some(url) => url != daemon_url,
        None => false,
    }
}

fn url_config_for(client: &str) -> Option<Value> {
    match client {
        "claude" | "cursor" | "droid" | "omp" => Some(agent_install::mcp_config_json()),
        "agy" => Some(agent_install::agy_mcp_config_json()),
        "zcode" => Some(agent_install::zcode_mcp_config_json()),
        "opencode" | "kilo" => Some(agent_install::opencode_format_mcp_config_json()),
        _ => None,
    }
}

fn replace_registration(data: &mut Value, client: &str) -> bool {
    let Some(generated) = url_config_for(client) else {
        return false;
    };
    let replacement = match client {
        "zcode" => generated
            .get("mcp")
            .and_then(|m| m.get("servers"))
            .and_then(|s| s.get("cairn")),
        "opencode" | "kilo" => generated.get("mcp").and_then(|m| m.get("cairn")),
        _ => generated.get("mcpServers").and_then(|s| s.get("cairn")),
    };
    let Some(replacement) = replacement else {
        return false;
    };
    let parent = match client {
        "zcode" => data
            .get_mut("mcp")
            .and_then(|m| m.get_mut("servers"))
            .and_then(Value::as_object_mut),
        "opencode" | "kilo" => data.get_mut("mcp").and_then(Value::as_object_mut),
        _ => data.get_mut("mcpServers").and_then(Value::as_object_mut),
    };
    let Some(parent) = parent else {
        return false;
    };
    parent.insert("cairn".to_string(), replacement.clone());
    true
}

/// Repoint stale user-owned registrations to the daemon URL; action records out.
fn apply_doctor_fixes() -> Vec<Value> {
    let destination = agent_install::daemon_mcp_url();
    let mut actions = Vec::new();
    let installed = agent_install::check_installed(&std::env::current_dir().unwrap_or_default());
    for (client, is_installed) in &installed {
        if !is_installed {
            continue;
        }
        for (path, disp, workspace_owned) in client_config_paths(client) {
            let Some(entry) = agent_install::registration_entry(&path) else {
                continue;
            };
            let url = registration_url_of(&entry);
            if client == "claude-desktop" {
                actions.push(json!({
                    "client": client, "config": disp, "action": UNSUPPORTED_DESKTOP,
                }));
                continue;
            }
            if !stale_registration(&entry, &destination) {
                continue;
            }
            let action = |name: &str| {
                json!({
                    "client": client,
                    "config": disp,
                    "action": name,
                    "url": url,
                })
            };
            if workspace_owned {
                actions.push(action(SKIPPED_WORKSPACE));
                continue;
            }
            if changed_within_a_minute(&path) {
                actions.push(action(REFUSED_RECENT));
                continue;
            }
            let Some(mut data) = load_json_object(&path) else {
                actions.push(action(MALFORMED_CONFIG));
                continue;
            };
            if !replace_registration(&mut data, client) {
                actions.push(action(UNREPLACEABLE));
                continue;
            }
            actions.push(action(if backup_and_write(&path, &data) {
                REPOINTED
            } else {
                WRITE_FAILED
            }));
        }
    }
    actions.extend(migrate_agent_hooks());
    actions
}

/// A client may be mid-write on a file changed in the last minute.
fn changed_within_a_minute(path: &Path) -> bool {
    path.metadata()
        .and_then(|m| m.modified())
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .is_some_and(|age| age.as_secs_f64() > now_epoch() - 60.0)
}

/// Backup beside the file, then write (the merge module's contract).
fn backup_and_write(path: &Path, data: &Value) -> bool {
    let backup = path.with_file_name(format!(
        "{}.bak",
        path.file_name().unwrap_or_default().to_string_lossy()
    ));
    crate::agent_install::copy_to_backup(path, &backup)
        && std::fs::write(path, py_json::dumps(data) + "\n").is_ok()
}

/// Rewrite retired python-runner hook commands in every scanned hook file; action records out.
fn migrate_agent_hooks() -> Vec<Value> {
    agent_hook_files()
        .into_iter()
        .filter(|path| path.is_file())
        .filter(|path| !agent_install::retired_hook_commands(path).is_empty())
        .map(|path| migrate_hook_file(&path))
        .collect()
}

fn migrate_hook_file(path: &Path) -> Value {
    let config = scrub_safe_display(path, &home_dir());
    let action = |name: &str, count: usize| json!({"client": "hooks", "config": config, "action": name, "count": count});
    if changed_within_a_minute(path) {
        return action(REFUSED_RECENT, 0);
    }
    let Some(mut data) = load_json_object(path) else {
        return action(MALFORMED_CONFIG, 0);
    };
    let count = agent_install::migrate_retired_hook_commands(&mut data);
    if backup_and_write(path, &data) {
        action(MIGRATED_HOOKS, count)
    } else {
        action(WRITE_FAILED, 0)
    }
}

fn load_json_object(path: &Path) -> Option<Value> {
    let text = std::fs::read_to_string(path).ok()?;
    let parsed: Value = serde_json::from_str(&text).ok()?;
    if parsed.is_object() {
        Some(parsed)
    } else {
        None
    }
}

fn fix_summary_lines(actions: &[Value]) -> Vec<String> {
    let mut lines = Vec::new();
    for action in actions {
        let client = action
            .get("client")
            .and_then(Value::as_str)
            .unwrap_or("unknown");
        let config = action
            .get("config")
            .and_then(Value::as_str)
            .unwrap_or("unknown");
        let url = action.get("url").and_then(Value::as_str).unwrap_or("");
        let name = action.get("action").and_then(Value::as_str).unwrap_or("");
        let line = match name {
            REPOINTED => format!("repointed {client} in {config} to the daemon URL ({url})"),
            UNSUPPORTED_DESKTOP => format!(
                "{client} in {config} is unsupported: remove the cairn entry \
                 (Claude Desktop cannot speak the HTTP transport)"
            ),
            REFUSED_RECENT => format!(
                "refused {config}: changed recently; retry `cairn doctor --fix` \
                 after it settles"
            ),
            SKIPPED_WORKSPACE => format!(
                "skipped {config}: workspace-owned; edit the repo registration \
                 deliberately"
            ),
            UNREADABLE_CONFIG => format!(
                "could not inspect {config} (stat failed); retry `cairn doctor \
                 --fix` once the file is readable"
            ),
            MALFORMED_CONFIG => format!(
                "skipped {config}: not a JSON object; fix or remove the stale \
                 cairn entry manually"
            ),
            UNREPLACEABLE => format!(
                "could not repoint {config}: unrecognized registration shape; \
                 edit it manually"
            ),
            WRITE_FAILED => format!(
                "failed to update {config}: backup or write error; fix the file \
                 and re-run `cairn doctor --fix`"
            ),
            MIGRATED_HOOKS => format!(
                "migrated {} retired hook command(s) in {config} to `cairn hooks run`",
                action.get("count").and_then(Value::as_u64).unwrap_or(0)
            ),
            other => format!("{client} {config}: {other} ({url})"),
        };
        lines.push(line);
    }
    if actions
        .iter()
        .any(|a| a.get("client") != Some(&json!("hooks")))
    {
        lines.push(FIX_TRADEOFF.to_string());
    }
    lines
}

// --- clock helpers -----------------------------------------------------------------------------

fn now_epoch() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

/// Epoch seconds from an epoch float or ISO-8601 string; None when unparseable.
pub fn parse_ts(value: &str) -> Option<f64> {
    if let Ok(epoch) = value.parse::<f64>() {
        return Some(epoch);
    }
    parse_iso(value)
}

fn parse_iso(value: &str) -> Option<f64> {
    let value = value.trim();
    let (date_part, rest) = value.split_once('T').or_else(|| value.split_once(' '))?;
    let mut date = date_part.split('-');
    let year: i64 = date.next()?.parse().ok()?;
    let month: u32 = date.next()?.parse().ok()?;
    let day: u32 = date.next()?.parse().ok()?;
    let (time_part, offset) = split_offset(rest);
    let mut time = time_part.split(':');
    let hour: i64 = time.next()?.parse().ok()?;
    let minute: i64 = time.next()?.parse().ok()?;
    let sec_text = time.next().unwrap_or("0");
    let (sec_text, fraction) = match sec_text.split_once('.') {
        Some((s, f)) => (s, f),
        None => (sec_text, ""),
    };
    let sec: i64 = sec_text.parse().ok()?;
    let micros: u32 = if fraction.is_empty() {
        0
    } else {
        let padded = format!("{:<06}", &fraction[..fraction.len().min(6)]);
        padded.parse().unwrap_or(0)
    };
    let days = days_from_civil(year, month, day);
    let mut epoch = days * 86400 + hour * 3600 + minute * 60 + sec;
    match offset {
        Offset::Utc => {}
        Offset::Hours(sign, hours, minutes) => {
            epoch -= sign * (hours * 3600 + minutes * 60);
        }
    }
    Some(epoch as f64 + micros as f64 / 1e6)
}

enum Offset {
    Utc,
    Hours(i64, i64, i64),
}

fn split_offset(rest: &str) -> (&str, Offset) {
    if let Some(stripped) = rest.strip_suffix('Z').or_else(|| rest.strip_suffix('z')) {
        return (stripped, Offset::Utc);
    }
    if let Some(sign_pos) = rest.rfind(['+', '-']) {
        let (time, off) = rest.split_at(sign_pos);
        let sign = if off.starts_with('-') { -1 } else { 1 };
        let digits: String = off[1..].chars().filter(|c| c.is_ascii_digit()).collect();
        if digits.len() == 4
            && let (Ok(h), Ok(m)) = (digits[..2].parse::<i64>(), digits[2..].parse::<i64>())
        {
            return (time, Offset::Hours(sign, h, m));
        }
        if digits.len() == 2
            && let Ok(h) = digits.parse::<i64>()
        {
            return (time, Offset::Hours(sign, h, 0));
        }
    }
    (rest, Offset::Utc)
}

/// Howard Hinnant's civil-to-days (inverse of the build crate's days-to-civil).
fn days_from_civil(y: i64, m: u32, d: u32) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let mp = ((m + 9) % 12) as i64;
    let doy = (153 * mp + 2) / 5 + d as i64 - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

/// Human-readable age of `ts` relative to `now` ('3d old', '2h old').
fn age_str(now: f64, ts: Option<&str>) -> String {
    let Some(ts) = ts.and_then(parse_ts) else {
        return "age unknown".to_string();
    };
    let secs = (now - ts) as i64;
    if secs < 0 {
        return "just now".to_string();
    }
    if secs >= 86_400 {
        return format!("{}d old", secs / 86_400);
    }
    if secs >= 3_600 {
        return format!("{}h old", secs / 3_600);
    }
    if secs >= 60 {
        return format!("{}m old", secs / 60);
    }
    format!("{secs}s old")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ts_parsing_covers_the_schema_shapes() {
        assert_eq!(parse_ts("1760000000.5"), Some(1_760_000_000.5));
        assert_eq!(
            parse_iso("2025-10-09T08:53:20+00:00"),
            Some(1_760_000_000.0)
        );
        assert_eq!(
            parse_iso("2025-10-09T08:53:20.909587+00:00"),
            Some(1_760_000_000.909587)
        );
        // Z suffix and a non-UTC offset.
        assert_eq!(parse_iso("2025-10-09T08:53:20Z"), Some(1_760_000_000.0));
        assert_eq!(
            parse_iso("2025-10-09T10:53:20+02:00"),
            Some(1_760_000_000.0)
        );
        assert_eq!(parse_ts("not a timestamp"), None);
        // Inverse of the build crate's epoch formatting.
        assert_eq!(
            parse_iso(&cairn_build::timefmt::iso_from_epoch(
                1_760_849_999,
                999_999
            )),
            Some(1_760_849_999.999_999)
        );
    }

    #[test]
    fn age_str_buckets() {
        let now = 1_000_000.0;
        assert_eq!(age_str(now, Some("999999.5")), "0s old");
        assert_eq!(age_str(now, Some(&format!("{}", now - 90.0))), "1m old");
        assert_eq!(age_str(now, Some(&format!("{}", now - 7200.0))), "2h old");
        assert_eq!(
            age_str(now, Some(&format!("{}", now - 3.0 * 86400.0))),
            "3d old"
        );
        assert_eq!(age_str(now, Some(&format!("{}", now + 5.0))), "just now");
        assert_eq!(age_str(now, Some("garbage")), "age unknown");
        assert_eq!(age_str(now, None), "age unknown");
    }

    #[test]
    fn percentile_is_linear_interpolated() {
        let mut values = vec![10.0, 20.0, 30.0, 40.0];
        assert_eq!(percentile(&mut values, 95.0), Some(38.5));
        let mut single = vec![7.0];
        assert_eq!(percentile(&mut single, 95.0), Some(7.0));
        assert_eq!(percentile(&mut Vec::new(), 95.0), None);
    }

    #[test]
    fn json_result_rows_carry_the_contract_keys() {
        let row = result("schema", PASS, "integrity ok", Some("hint".to_string()));
        let value = row.to_json();
        let obj = value.as_object().expect("object");
        let keys: Vec<&String> = obj.keys().collect();
        assert_eq!(keys, ["name", "status", "detail", "hint"]);
        // An unset hint serializes as an explicit null (the Python json.dumps shape).
        let row = result("x", WARN, "d", None);
        assert!(matches!(row.to_json().get("hint"), Some(Value::Null)));
    }

    #[test]
    fn fresh_temp_store_doctor_is_fail_free() {
        let dir = std::env::temp_dir().join(format!(
            "cairn-doctor-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("clock")
                .as_nanos()
        ));
        std::fs::create_dir_all(&dir).expect("dir");
        let db_path = dir.join("store.kg");
        {
            let conn = cairn_store::conn::open_writable(&db_path).expect("create store");
            cairn_store::schema::apply_schema(&conn).expect("schema");
        }
        let results = run_doctor(db_path.to_str().expect("utf8 db path"));
        let names: Vec<&str> = results.iter().map(|r| r.name.as_str()).collect();
        for expected in [
            "schema",
            "embeddings",
            "ann",
            "freshness",
            "parse_errors",
            "edge_signals",
            "concurrency",
            "tool_health",
            "memory_staleness",
            "config",
            "daemon",
            "environment",
        ] {
            assert!(
                names.contains(&expected),
                "missing check {expected}: {names:?}"
            );
        }
        let schema = results
            .iter()
            .find(|r| r.name == "schema")
            .expect("schema row");
        assert_eq!(schema.status, PASS);
        let freshness = results.iter().find(|r| r.name == "freshness").expect("row");
        assert_eq!(freshness.status, PASS);
        assert_eq!(freshness.detail, "no builds yet (fresh install)");
        let failures: Vec<_> = results.iter().filter(|r| r.status == FAIL).collect();
        assert!(
            failures.iter().all(|r| r.name != "schema"),
            "fresh store must not fail the schema check: {failures:?}"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    fn edge_signals_store(dir_tag: &str, edge_rows: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "cairn-doctor-edgesig-{dir_tag}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("clock")
                .as_nanos()
        ));
        std::fs::create_dir_all(&dir).expect("dir");
        let db_path = dir.join("store.kg");
        {
            let conn = cairn_store::conn::open_writable(&db_path).expect("create store");
            cairn_store::schema::apply_schema(&conn).expect("schema");
            conn.execute_batch(&format!(
                "INSERT INTO repos (id, name, path, language) VALUES ('r1', 'r1', '.', 'python');
                 INSERT INTO files (id, repo_id, path, language) \
                     VALUES ('f1', 'r1', 'a.py', 'python');
                 INSERT INTO symbols (id, file_id, name, kind) \
                     VALUES ('s1', 'f1', 'caller', 'function');
                 {edge_rows}"
            ))
            .expect("seed");
        }
        db_path
    }

    #[test]
    fn edge_signals_warns_on_pre_signal_column_edges() {
        let db_path = edge_signals_store(
            "legacy",
            "INSERT INTO edges (id, source_id, target_id, target_name, kind, line, \"column\",
                                    resolution, source)
             VALUES ('e1', 's1', NULL, 'gone', 'calls', 1, 0, 'unresolved', 'tree_sitter');",
        );
        let results = run_doctor(db_path.to_str().expect("utf8 db path"));
        let row = results
            .iter()
            .find(|r| r.name == "edge_signals")
            .expect("edge_signals check emitted");
        assert_eq!(row.status, WARN);
        assert!(row.detail.contains("1 reference edge"), "{}", row.detail);
        let hint = row.hint.as_deref().unwrap_or_default();
        assert!(hint.contains("cairn build"), "{hint}");
        let _ = std::fs::remove_dir_all(db_path.parent().expect("parent"));
    }

    #[test]
    fn edge_signals_passes_when_reference_edges_carry_signals() {
        let db_path = edge_signals_store(
            "current",
            "INSERT INTO edges (id, source_id, target_id, target_name, kind, line, \"column\",
                                    resolution, source, ref_name)
             VALUES ('e1', 's1', NULL, 'gone', 'calls', 1, 0, 'unresolved', 'tree_sitter', 'gone');",
        );
        let results = run_doctor(db_path.to_str().expect("utf8 db path"));
        let row = results
            .iter()
            .find(|r| r.name == "edge_signals")
            .expect("edge_signals check emitted");
        assert_eq!(row.status, PASS, "{}", row.detail);
        let _ = std::fs::remove_dir_all(db_path.parent().expect("parent"));
    }

    #[test]
    fn edge_signals_warns_when_columns_are_absent() {
        let db_path = edge_signals_store("pre-migration", "");
        {
            let conn = cairn_store::conn::open_writable(&db_path).expect("reopen");
            for col in ["ref_name", "receiver_type", "call_arity"] {
                conn.execute_batch(&format!("ALTER TABLE edges DROP COLUMN {col}"))
                    .expect("drop");
            }
        }
        let results = run_doctor(db_path.to_str().expect("utf8 db path"));
        let row = results
            .iter()
            .find(|r| r.name == "edge_signals")
            .expect("edge_signals check emitted");
        assert_eq!(row.status, WARN);
        assert!(
            row.detail.contains("signal columns missing"),
            "{}",
            row.detail
        );
        let _ = std::fs::remove_dir_all(db_path.parent().expect("parent"));
    }

    #[test]
    fn missing_store_degrades_without_failures_outside_schema() {
        // The environment audit reads the real client configs under HOME; a
        // developer machine carries live registrations, so isolate it.
        let results = {
            let _env = crate::test_env::EnvScope::new("doctor-home");
            run_doctor("/nonexistent/cairn-doctor/store.kg")
        };
        let schema = results.iter().find(|r| r.name == "schema").expect("schema");
        assert_eq!(schema.status, FAIL);
        let failures: Vec<&CheckResult> = results.iter().filter(|r| r.status == FAIL).collect();
        assert_eq!(
            failures.len(),
            1,
            "only schema fails on a missing store: {failures:?}"
        );
        // The environment audit still runs on the degraded path.
        let environment = results
            .iter()
            .find(|r| r.name == "environment")
            .expect("row");
        assert_eq!(environment.status, WARN);
    }

    #[test]
    fn daemon_check_reports_the_serving_impl() {
        let report = check_daemon();
        // Report-only and always PASS; the detail names the URL and either the
        // no-daemon branch or a live daemon's impl marker.
        assert_eq!(report.name, "daemon");
        assert_eq!(report.status, PASS);
        assert!(
            report.detail.contains("no daemon serving at http://")
                || report.detail.contains(" daemon serving at http://"),
            "detail must name the serving impl or its absence: {}",
            report.detail
        );
    }

    #[test]
    fn agent_hooks_check_flags_retired_python_hook_commands() {
        let env = crate::test_env::EnvScope::new("doctor-hooks");
        let ws = env.dir().join("ws");
        std::fs::create_dir_all(ws.join(".claude")).expect("ws");
        // SAFETY: EnvScope holds the env lock and restores CAIRN_WORKSPACE on drop.
        unsafe { std::env::set_var("CAIRN_WORKSPACE", &ws) };
        let settings = ws.join(".claude").join("settings.json");

        let retired = json!({"hooks": {"PostToolUse": [{"matcher": "Edit", "hooks": [
            {"type": "command", "command": "'/usr/bin/python3' -m cairn.hooks.claude_hooks post_edit"},
            {"type": "command", "command": "user-lint --fix"}
        ]}]}});
        std::fs::write(&settings, retired.to_string()).expect("settings");
        let report = check_agent_hooks();
        assert_eq!(report.name, "agent_hooks");
        assert_eq!(report.status, WARN, "{}", report.detail);
        assert!(report.detail.contains("settings.json"), "{}", report.detail);
        assert!(
            report
                .hint
                .as_deref()
                .is_some_and(|h| h.contains("cairn doctor --fix")),
            "{:?}",
            report.hint
        );

        let actions = migrate_agent_hooks();
        assert_eq!(actions[0]["action"], REFUSED_RECENT, "{actions:?}");

        let an_hour_ago = std::time::SystemTime::now() - std::time::Duration::from_secs(3600);
        std::fs::File::options()
            .write(true)
            .open(&settings)
            .and_then(|f| f.set_modified(an_hour_ago))
            .expect("backdate");
        let actions = migrate_agent_hooks();
        assert_eq!(actions[0]["action"], MIGRATED_HOOKS, "{actions:?}");
        assert_eq!(actions[0]["count"], 1);
        let hooks: Value =
            serde_json::from_str(&std::fs::read_to_string(&settings).expect("read")).expect("json");
        let commands = &hooks["hooks"]["PostToolUse"][0]["hooks"];
        assert!(
            commands[0]["command"]
                .as_str()
                .is_some_and(|c| c.ends_with(" hooks run post_edit")),
            "{commands}"
        );
        assert_eq!(commands[1]["command"], "user-lint --fix");
        assert_eq!(check_agent_hooks().status, PASS);
        assert!(migrate_agent_hooks().is_empty());
    }

    #[test]
    fn fix_summary_lines_carry_the_action_contract() {
        let lines = fix_summary_lines(&[json!({
            "client": "cursor",
            "config": "~/.cursor/mcp.json",
            "action": REPOINTED,
            "url": "http://127.0.0.1:9876/mcp",
        })]);
        assert_eq!(
            lines[0],
            "repointed cursor in ~/.cursor/mcp.json to the daemon URL (http://127.0.0.1:9876/mcp)"
        );
        assert_eq!(lines[1], FIX_TRADEOFF);
        assert!(fix_summary_lines(&[]).is_empty());
    }
}
