// F8
//! Every dispatched MCP call lands a tool_metrics row.

mod common;

use serde_json::{Value, json};

use cairn_daemon::server::{self, DaemonConfig};
use common::{McpClient, http_request, seeded_store};

#[tokio::test(flavor = "multi_thread")]
async fn dispatched_calls_record_tool_metrics_rows() {
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
