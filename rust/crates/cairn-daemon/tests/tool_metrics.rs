// F8
//! Every dispatched MCP call lands a tool_metrics row.

mod common;

use std::ffi::OsString;
use std::sync::{Mutex, MutexGuard};

use serde_json::{Value, json};

use cairn_daemon::server::{self, DaemonConfig};
use common::{McpClient, http_request, seeded_store};

static ENV_LOCK: Mutex<()> = Mutex::new(());

struct EnvPin {
    saved: Vec<(&'static str, Option<OsString>)>,
    _guard: MutexGuard<'static, ()>,
}

impl EnvPin {
    fn new(overrides: &[(&'static str, Option<&str>)]) -> Self {
        let guard = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let saved = overrides
            .iter()
            .map(|(k, _)| (*k, std::env::var_os(k)))
            .collect();
        for (k, v) in overrides {
            // SAFETY: serialized via ENV_LOCK for all tests in this binary.
            unsafe {
                match v {
                    Some(val) => std::env::set_var(k, val),
                    None => std::env::remove_var(k),
                }
            }
        }
        Self {
            saved,
            _guard: guard,
        }
    }
}

impl Drop for EnvPin {
    fn drop(&mut self) {
        for (k, v) in &self.saved {
            // SAFETY: still holding ENV_LOCK.
            unsafe {
                match v {
                    Some(val) => std::env::set_var(k, val),
                    None => std::env::remove_var(k),
                }
            }
        }
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn dispatched_calls_record_tool_metrics_rows() {
    let _pin = EnvPin::new(&[("CAIRN_TELEMETRY", None)]);
    let temp = seeded_store("metrics");
    let db = temp.0.join("nav-store.kg");
    let mut config = DaemonConfig::for_store(db.clone());
    config.api_key = None;
    let (addr, handle) = server::serve_config(config, "127.0.0.1:0".parse().unwrap())
        .await
        .expect("serve");

    let health = http_request(addr, "GET", "/healthz", &[], None).expect("healthz");
    assert_eq!(health.status(), 200);

    let mut client = McpClient::connect(addr).expect("connect");
    client.initialize();
    let ok = client.call_tool("search_symbols", json!({"pattern": "search_symbols"}));
    assert!(
        ok["result"]["structuredContent"]["result"].is_string(),
        "ok call: {ok}"
    );
    let error = client.call_tool("get_callers", json!({}));
    assert_eq!(
        error["result"]["isError"],
        Value::Bool(true),
        "error call: {error}"
    );
    drop(client);
    handle.stop();

    let conn =
        rusqlite::Connection::open_with_flags(&db, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)
            .expect("open store");
    let mut stmt = conn
        .prepare(
            "SELECT tool_name, status, source, session_id, duration_ms, resp_chars \
             FROM tool_metrics ORDER BY id",
        )
        .expect("prepare");
    let rows: Vec<(String, String, String, String, f64, i64)> = stmt
        .query_map([], |row| {
            Ok((
                row.get(0)?,
                row.get(1)?,
                row.get(2)?,
                row.get(3)?,
                row.get(4)?,
                row.get(5)?,
            ))
        })
        .expect("query")
        .collect::<Result<_, _>>()
        .expect("rows");
    assert_eq!(rows.len(), 2, "one row per dispatched call: {rows:?}");
    assert_eq!(rows[0].0, "search_symbols");
    assert_eq!(rows[0].1, "ok");
    assert_eq!(rows[0].2, "mcp");
    assert!(
        rows[0].3.starts_with("mcp-"),
        "session stamped: {}",
        rows[0].3
    );
    assert!(rows[0].5 > 0, "ok rows carry resp chars: {}", rows[0].5);
    assert_eq!(rows[1].0, "get_callers");
    assert_eq!(rows[1].1, "error");
}

#[tokio::test(flavor = "multi_thread")]
async fn distinct_clients_get_distinct_session_ids_and_quality_events() {
    let _pin = EnvPin::new(&[
        ("CAIRN_TELEMETRY", None),
        ("CAIRN_MAX_RESULT_CHARS", Some("80")),
        ("CAIRN_TOOL_METRICS_MAX_ROWS", Some("10")),
    ]);
    let temp = seeded_store("sessions-quality");
    let db = temp.0.join("nav-store.kg");
    let mut config = DaemonConfig::for_store(db.clone());
    config.api_key = None;
    let (addr, handle) = server::serve_config(config, "127.0.0.1:0".parse().unwrap())
        .await
        .expect("serve");

    let mut c1 = McpClient::connect(addr).expect("connect c1");
    c1.initialize();
    let _ = c1.call_tool("search_symbols", json!({"pattern": "search_symbols"}));
    let _ = c1.call_tool("find_definition", json!({"name": "no_such_symbol_xyz"}));

    let mut c2 = McpClient::connect(addr).expect("connect c2");
    c2.initialize();
    let _ = c2.call_tool("semantic_search", json!({"query": "navigation"}));
    drop(c1);
    drop(c2);
    handle.stop();

    let conn =
        rusqlite::Connection::open_with_flags(&db, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)
            .expect("open store");
    let sessions: Vec<String> = conn
        .prepare("SELECT session_id FROM tool_metrics ORDER BY id")
        .unwrap()
        .query_map([], |r| r.get(0))
        .unwrap()
        .collect::<Result<_, _>>()
        .unwrap();
    assert_eq!(sessions.len(), 3, "3 calls recorded: {sessions:?}");
    assert_eq!(sessions[0], sessions[1], "same client shares session_id");
    assert_ne!(
        sessions[0], sessions[2],
        "separate clients get distinct session_id values"
    );

    let event_names: Vec<String> = conn
        .prepare("SELECT name FROM events ORDER BY id")
        .unwrap()
        .query_map([], |r| r.get(0))
        .unwrap()
        .collect::<Result<_, _>>()
        .unwrap();
    assert!(
        event_names.contains(&"truncate_result".to_string()),
        "truncate_result event recorded: {event_names:?}"
    );
    assert!(
        event_names.contains(&"empty_result".to_string()),
        "empty_result event recorded: {event_names:?}"
    );
    assert!(
        event_names.contains(&"semantic_backend".to_string()),
        "semantic_backend event recorded: {event_names:?}"
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn retention_prunes_excess_rows_and_telemetry_off_skips_writes() {
    let _pin = EnvPin::new(&[
        ("CAIRN_TELEMETRY", None),
        ("CAIRN_TOOL_METRICS_MAX_ROWS", Some("2")),
    ]);
    let temp = seeded_store("retention");
    let db = temp.0.join("nav-store.kg");
    let mut config = DaemonConfig::for_store(db.clone());
    config.api_key = None;
    let (addr, handle) = server::serve_config(config, "127.0.0.1:0".parse().unwrap())
        .await
        .expect("serve");

    let mut client = McpClient::connect(addr).expect("connect");
    client.initialize();
    for _ in 0..4 {
        let _ = client.call_tool("search_symbols", json!({"pattern": "search_symbols"}));
    }

    let conn =
        rusqlite::Connection::open_with_flags(&db, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)
            .expect("open store");
    let count: i64 = conn
        .query_row("SELECT COUNT(*) FROM tool_metrics", [], |r| r.get(0))
        .unwrap();
    assert_eq!(count, 2, "retention cap must keep only 2 newest rows");
    drop(conn);

    // SAFETY: ENV_LOCK held by _pin.
    unsafe { std::env::set_var("CAIRN_TELEMETRY", "off") };
    let _ = client.call_tool("find_definition", json!({"name": "find_definition"}));
    drop(client);
    handle.stop();

    let conn =
        rusqlite::Connection::open_with_flags(&db, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)
            .expect("open store");
    let tools: Vec<String> = conn
        .prepare("SELECT tool_name FROM tool_metrics")
        .unwrap()
        .query_map([], |r| r.get(0))
        .unwrap()
        .collect::<Result<_, _>>()
        .unwrap();
    assert!(
        !tools.contains(&"find_definition".to_string()),
        "CAIRN_TELEMETRY=off must suppress inserts: {tools:?}"
    );
}
