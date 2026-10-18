//! /healthz: the bounded health payload `cairn connect`'s reuse guard reads.

use std::sync::Arc;
use std::time::Instant;

use axum::body::Body;
use axum::extract::State;
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

    /// The bounded health payload: an unreachable store degrades to status "unhealthy".
    pub fn payload(&self) -> Value {
        let (store_reachable, degradations) = match probe_store(&self.db_path) {
            Ok(()) => (true, degradation_count()),
            Err(_) => (false, 0),
        };
        json!({
            "status": if store_reachable { "ok" } else { "unhealthy" },
            "store_reachable": store_reachable,
            "read_only": self.read_only,
            "degradations": degradations,
            "db": self.db_path,
            "uptime_seconds": self.started.elapsed().as_secs_f64(),
            "impl": IMPL_MARKER,
        })
    }
}

fn probe_store(db_path: &str) -> Result<(), rusqlite::Error> {
    let conn = cairn_store::conn::open_read_only(std::path::Path::new(db_path))?;
    conn.query_row("SELECT 1", [], |row| row.get::<_, i64>(0))?;
    Ok(())
}

/// Degradation markers derivable without a store probe; an explicit
/// CAIRN_EMBED_BACKEND=hash / CAIRN_ANN_BACKEND=off is an informed choice.
fn degradation_count() -> usize {
    let mut count = 0usize;
    // The default `local` resolution is the hash embedder in the Rust port.
    if cairn_query::tools_search::backend_name() == "local" {
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
pub async fn respond(State(state): State<Arc<HealthzState>>) -> Response {
    json_response(StatusCode::OK, state.payload().to_string().into_bytes())
}
