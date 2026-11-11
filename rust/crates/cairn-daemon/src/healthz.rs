//! /healthz: the bounded health payload `cairn connect`'s reuse guard reads.

use std::net::{IpAddr, SocketAddr};
use std::sync::{Arc, OnceLock};
use std::time::Instant;

use axum::body::Body;
use axum::extract::{ConnectInfo, State};
use axum::http::{HeaderValue, StatusCode};
use axum::response::Response;
use serde_json::{Value, json};

/// Serving-implementation marker this daemon reports (`impl=rust`).
pub const IMPL_MARKER: &str = "rust";

/// State behind GET /healthz: the served store, mode, and process start time.
pub struct HealthzState {
    db_path: String,
    read_only: bool,
    api_key: Option<String>,
    started: Instant,
}

impl HealthzState {
    pub fn new(db_path: impl Into<String>, read_only: bool) -> Self {
        Self {
            db_path: db_path.into(),
            read_only,
            api_key: None,
            started: Instant::now(),
        }
    }

    /// The bearer key whose presentation unlocks the full store path.
    pub fn with_api_key(mut self, key: Option<String>) -> Self {
        self.api_key = key;
        self
    }

    /// Health payload for one peer: the full store path only for the
    /// authenticated; everyone else sees the `store_id` identity hash.
    pub fn payload(&self, peer: IpAddr, authed: bool) -> Value {
        let (store_reachable, degradations) = match probe_store(&self.db_path) {
            Ok(()) => (true, degradation_count()),
            Err(_) => (false, 0),
        };
        let db = if authed {
            self.db_path.clone()
        } else {
            // Unauthenticated peers see the file name only — loopback
            // included; the reuse guard compares store_id instead.
            db_field(&self.db_path, peer)
        };
        json!({
            "status": if store_reachable { "ok" } else { "unhealthy" },
            "store_reachable": store_reachable,
            "read_only": self.read_only,
            "degradations": degradations,
            "db": db,
            "store_id": store_id_hex(&self.db_path),
            "uptime_seconds": self.started.elapsed().as_secs_f64(),
            "impl": IMPL_MARKER,
            "version": env!("CARGO_PKG_VERSION"),
        })
    }
}

/// Short stable hash of the store file identity (the keyless reuse guard's
/// comparison key); "unknown" when the file cannot be stat'ed.
fn store_id_hex(db_path: &str) -> String {
    match cairn_store::swap::store_id(std::path::Path::new(db_path)) {
        Some(id) => format!("{id:?}"),
        None => "unknown".to_string(),
    }
}

/// The store file name alone — the unauthenticated view; the reuse guard
/// compares the `store_id` identity hash, never this string.
fn db_field(db_path: &str, _peer: IpAddr) -> String {
    std::path::Path::new(db_path)
        .file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_default()
}

fn probe_store(db_path: &str) -> Result<(), rusqlite::Error> {
    let conn = cairn_store::conn::open_read_only(std::path::Path::new(db_path))?;
    conn.query_row("SELECT 1", [], |row| row.get::<_, i64>(0))?;
    Ok(())
}

/// Degradation count, computed once per process: resolving it probes the
/// embedding server over a blocking socket.
fn degradation_count() -> usize {
    static COUNT: OnceLock<usize> = OnceLock::new();
    *COUNT.get_or_init(degradation_markers)
}

/// Degradation markers derivable without a store probe; an explicit
/// CAIRN_EMBED_BACKEND=hash / CAIRN_ANN_BACKEND=off is an informed choice.
fn degradation_markers() -> usize {
    let mut count = 0usize;
    // Hash in effect without an explicit hash configuration: the default
    // `local` resolution or the native cold-cache fallback.
    let name = cairn_query::tools_search::backend_name();
    let env = cairn_query::tools_search::semantic_env(None);
    if env.hash_fallback && name != "hash" {
        count += 1; // embeddings=hash_fallback
    }
    if !cairn_query::tools_search::ann_backend_enabled() {
        count += 1; // ann=unavailable
    }
    count
}

fn json_response(status: StatusCode, body: Vec<u8>) -> Response {
    let mut response = Response::new(Body::from(body));
    *response.status_mut() = status;
    response
        .headers_mut()
        .insert("content-type", HeaderValue::from_static("application/json"));
    response
}

/// GET /healthz: the bounded health payload as JSON (HTTP 200 even when unhealthy).
pub async fn respond(
    State(state): State<Arc<HealthzState>>,
    ConnectInfo(peer): ConnectInfo<SocketAddr>,
    headers: axum::http::HeaderMap,
) -> Response {
    let provided = headers
        .get(axum::http::header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok());
    let expected = state.api_key.as_deref().unwrap_or("");
    let authed = !expected.is_empty() && crate::auth::bearer_key_ok(provided, expected);
    json_response(
        StatusCode::OK,
        state.payload(peer.ip(), authed).to_string().into_bytes(),
    )
}
