//! Daemon-surface contract tests: full tool registration, dispatch over HTTP,
//! bearer gate, healthz parity, and the non-loopback bind guard.

use std::net::SocketAddr;
use std::path::PathBuf;

use cairn_daemon::server::{self, DaemonConfig};
use common::{McpClient, TempStore, http_request};
use serde_json::{Value, json};

mod common;

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../..")
        .canonicalize()
        .expect("repo root")
}

fn seeded_store(tag: &str) -> TempStore {
    let temp = TempStore::from_fixture(&repo_root().join("rust/golden/nav/nav-store.kg"), tag);
    // The km tools need their contract tables alongside the nav fixture's
    // graph tables (the authoritative DDL lives in the cairn-query km golden).
    let conn = rusqlite::Connection::open(temp.0.join("nav-store.kg")).expect("open seeded store");
    conn.execute_batch(KM_TABLE_DDL).expect("seed km tables");
    copy_tree(
        &repo_root().join("rust/golden/km/bundle-seed"),
        &temp.0.join(".knowledge"),
    );
    temp
}

const KM_TABLE_DDL: &str = r#"
CREATE TABLE IF NOT EXISTS memory_refs (id TEXT PRIMARY KEY, memory_path TEXT NOT NULL, session_id TEXT NOT NULL, referenced_at TIMESTAMP NOT NULL, context TEXT);
CREATE TABLE IF NOT EXISTS memory_validity (concept_id TEXT PRIMARY KEY, valid_from TEXT NOT NULL, valid_until TEXT, successor_symbol TEXT);
CREATE TABLE IF NOT EXISTS memory_embeddings (doc_id TEXT NOT NULL, chunk_index INTEGER NOT NULL DEFAULT 0, model TEXT NOT NULL, dim INTEGER NOT NULL, vec BLOB NOT NULL, chunk TEXT NOT NULL, embedded_at TIMESTAMP, PRIMARY KEY (doc_id, chunk_index, model));
CREATE TABLE IF NOT EXISTS knowledge_embeddings (doc_id TEXT NOT NULL, chunk_index INTEGER NOT NULL DEFAULT 0, model TEXT NOT NULL, dim INTEGER NOT NULL, vec BLOB NOT NULL, chunk TEXT NOT NULL, embedded_at TIMESTAMP, PRIMARY KEY (doc_id, chunk_index, model));
CREATE TABLE IF NOT EXISTS knowledge_edges (doc_id TEXT NOT NULL, related_id TEXT NOT NULL, relation TEXT NOT NULL, kind TEXT NOT NULL, provenance TEXT, created_at REAL, PRIMARY KEY (doc_id, related_id, relation, kind));
CREATE TABLE IF NOT EXISTS agent_symbols (agent_id TEXT NOT NULL, symbol TEXT NOT NULL, memory_id TEXT, kind TEXT NOT NULL, ts TEXT NOT NULL);
"#;

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

/// The registered surface: every tool cairn-query ports, in catalog order.
const EXPECTED_TOOLS: [&str; 20] = [
    "search_symbols",
    "semantic_search",
    "find_definition",
    "get_callers",
    "get_callees",
    "impact_analysis",
    "explore",
    "trace_flow",
    "cross_repo_deps",
    "visualize_graph",
    "recall_memory",
    "record_memory",
    "knowledge_add",
    "knowledge_search",
    "knowledge_delete",
    "knowledge_status",
    "trace_workflow",
    "get_compass",
    "search_knowledge",
    "ask_compass",
];

#[test]
fn catalog_registers_the_ported_surface() {
    let catalog = cairn_daemon::tools::catalog();
    let names: Vec<&str> = catalog.iter().map(|t| t.name.as_ref()).collect();
    assert_eq!(names, EXPECTED_TOOLS);
    for tool in &catalog {
        let schema = &tool.input_schema;
        assert_eq!(schema.get("type").and_then(Value::as_str), Some("object"));
        assert!(
            schema
                .get("properties")
                .and_then(Value::as_object)
                .is_some(),
            "{}: properties object missing",
            tool.name
        );
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn keyless_daemon_serves_tools_and_healthz() {
    let temp = seeded_store("tools");
    let db = temp.0.join("nav-store.kg");
    let mut config = DaemonConfig::for_store(db.clone());
    config.api_key = None;
    let (addr, handle) = server::serve_config(config, "127.0.0.1:0".parse().unwrap())
        .await
        .expect("serve");

    let served_db = db.to_string_lossy().into_owned();
    // Healthz parity: the reuse guard's `db` field names the served store.
    let health = http_request(addr, "GET", "/healthz", &[], None).expect("healthz");
    assert_eq!(health.status(), 200);
    let payload: Value = serde_json::from_str(&health.body).expect("healthz json");
    assert_eq!(payload["status"], "ok");
    assert_eq!(payload["db"], served_db);
    assert_eq!(payload["impl"], "rust");
    assert!(payload["uptime_seconds"].is_number());

    let mut client = McpClient::connect(addr).expect("connect");
    client.initialize();

    let tools = client.list_tools();
    assert_eq!(tools.len(), EXPECTED_TOOLS.len(), "tools: {tools:?}");
    for name in EXPECTED_TOOLS {
        assert!(tools.contains(&name.to_string()), "{name} not registered");
    }

    // Dispatch round-trips on graph + km tools (parity text lives in the
    // cairn-query goldens; here the contract is the served envelope).
    for (tool, args) in [
        ("search_symbols", json!({"pattern": "search_symbols"})),
        ("find_definition", json!({"name": "search_symbols"})),
        ("get_callers", json!({"name": "search_symbols"})),
        ("explore", json!({"query": "search_symbols"})),
        (
            "trace_flow",
            json!({"entry": "search_symbols", "max_depth": 2}),
        ),
        ("recall_memory", json!({"query": "search_symbols"})),
        ("get_compass", json!({"module": "cairn-daemon"})),
        (
            "ask_compass",
            json!({"query": "where do we handle retries"}),
        ),
    ] {
        let message = client.call_tool(tool, args);
        let result = &message["result"];
        assert!(
            message.get("error").is_none(),
            "{tool} protocol error: {message}"
        );
        assert_eq!(result["isError"], false, "{tool} errored: {result}");
        let text = result["content"][0]["text"].as_str().expect("text content");
        assert!(!text.is_empty(), "{tool} returned empty text");
        assert_eq!(
            result["structuredContent"]["result"], text,
            "{tool} envelope must carry the prose as {{\"result\"}}"
        );
    }

    // Unknown tool: an isError result naming it (the FastMCP exception path,
    // mcp/server/lowlevel/server.py catches every tool error into the result).
    let message = client.call_tool("not_a_tool", json!({}));
    assert!(
        message.get("error").is_none(),
        "unknown tool is an error result, not a protocol error: {message}"
    );
    let result = &message["result"];
    assert_eq!(result["isError"], true);
    assert!(
        result["content"][0]["text"]
            .as_str()
            .is_some_and(|t| t.contains("Unknown tool: not_a_tool")),
        "unknown tool must name itself: {result}"
    );

    handle.stop();
}

#[tokio::test(flavor = "multi_thread")]
async fn bearer_gate_admits_healthz_and_keyed_clients_only() {
    let temp = seeded_store("auth-gate");
    let db = temp.0.join("nav-store.kg");
    let config = DaemonConfig {
        db_path: db.clone(),
        workspace: repo_root(),
        knowledge_path: temp.0.join(".knowledge"),
        read_only: false,
        api_key: Some("secret-key".to_string()),
    };
    let (addr, handle) = server::serve_config(config, "127.0.0.1:0".parse().unwrap())
        .await
        .expect("serve");

    // /healthz passes the gate without credentials.
    let health = http_request(addr, "GET", "/healthz", &[], None).expect("healthz");
    assert_eq!(health.status(), 200, "healthz must bypass the bearer gate");

    // Unauthenticated /mcp: 401 with the unauthorized envelope.
    let denied = http_request(
        addr,
        "POST",
        "/mcp",
        &[
            ("Content-Type", "application/json"),
            ("Accept", "application/json, text/event-stream"),
        ],
        Some(
            json!({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
                .to_string()
                .as_str(),
        ),
    )
    .expect("denied call");
    assert_eq!(denied.status(), 401, "denied: {}", denied.status_line);
    assert_eq!(denied.body, "{\"error\":\"unauthorized\"}");
    assert_eq!(denied.header("www-authenticate"), Some("Bearer"));

    // Wrong credential: same 401.
    let wrong = http_request(
        addr,
        "POST",
        "/mcp",
        &[
            ("Authorization", "Bearer nope"),
            ("Content-Type", "application/json"),
            ("Accept", "application/json, text/event-stream"),
        ],
        Some(
            json!({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
                .to_string()
                .as_str(),
        ),
    )
    .expect("wrong key call");
    assert_eq!(wrong.status(), 401);

    // The right credential: the MCP surface answers.
    let mut client = McpClient::keyed(addr, "secret-key").expect("connect");
    client.initialize();
    let tools = client.list_tools();
    assert_eq!(tools.len(), EXPECTED_TOOLS.len());

    handle.stop();
}

#[tokio::test(flavor = "multi_thread")]
async fn non_loopback_bind_requires_a_key() {
    let temp = seeded_store("bind-guard");
    let config = DaemonConfig::for_store(temp.0.join("nav-store.kg"));
    assert!(config.api_key.is_none(), "test env must be keyless");
    let bind: SocketAddr = "192.0.2.1:0".parse().expect("test-net bind addr");
    let result = server::serve_config(config, bind).await;
    let err = result.expect_err("non-loopback keyless bind must be refused pre-bind");
    assert!(
        err.to_string().contains("without an API key"),
        "guard error must name the remediation: {err}"
    );
}
