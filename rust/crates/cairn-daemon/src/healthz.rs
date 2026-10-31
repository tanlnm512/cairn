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
    started: Instant,
}

impl HealthzState {
    pub fn new(db_path: impl Into<String>, read_only: bool) -> Self {
        Self {
            db_path: db_path.into(),
            read_only,
            started: Instant::now(),
        }
    }

    /// The bounded health payload for one requesting peer; an unreachable
    /// store degrades to status "unhealthy".
    pub fn payload(&self, peer: IpAddr) -> Value {
        let (store_reachable, degradations) = match probe_store(&self.db_path) {
            Ok(()) => (true, degradation_count()),
            Err(_) => (false, 0),
        };
        json!({
            "status": if store_reachable { "ok" } else { "unhealthy" },
            "store_reachable": store_reachable,
            "read_only": self.read_only,
            "degradations": degradations,
            "db": db_field(&self.db_path, peer),
            "uptime_seconds": self.started.elapsed().as_secs_f64(),
            "impl": IMPL_MARKER,
        })
    }
}

/// The store path for loopback peers (the reuse guard compares it); the file
/// name alone for any other unauthenticated requester.
fn db_field(db_path: &str, peer: IpAddr) -> String {
    if peer.is_loopback() {
        return db_path.to_string();
    }
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
) -> Response {
    json_response(
        StatusCode::OK,
        state.payload(peer.ip()).to_string().into_bytes(),
    )
}
