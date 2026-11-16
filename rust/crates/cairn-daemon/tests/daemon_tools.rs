//! Daemon-surface contract tests: full tool registration, dispatch over HTTP,
//! bearer gate, healthz parity, and the non-loopback bind guard.

use std::net::SocketAddr;

use cairn_daemon::server::{self, DaemonConfig};
use common::{McpClient, http_request, repo_root, seeded_store};
use serde_json::{Value, json};

mod common;

/// The registered surface: every tool cairn-query ports, in catalog order.
const EXPECTED_TOOLS: [&str; 26] = [
    "search_symbols",
    "semantic_search",
    "find_definition",
    "get_callers",
    "get_callees",
    "impact_analysis",
    "path",
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
    "repo_map",
    "file_api",
    "generate_flow",
    "federated_search",
    "wiki_generate",
];

#[test]
fn catalog_registers_the_ported_surface() {
    let catalog = cairn_daemon::tools::catalog();
    let names: Vec<&str> = catalog.iter().map(|t| t.name.as_ref()).collect();
    assert_eq!(names, EXPECTED_TOOLS);
    for tool in &catalog {
        let schema = &tool.input_schema;
        assert_eq!(schema.get("type").and_then(Value::as_str), Some("object"));
        let props = schema
            .get("properties")
            .and_then(Value::as_object)
            .unwrap_or_else(|| panic!("{}: properties object missing", tool.name));
        for (prop_name, prop_schema) in props {
            assert!(
                prop_schema.get("type").and_then(Value::as_str).is_some(),
                "{}.{prop_name}: missing top-level type in {prop_schema}",
                tool.name
            );
            assert!(
                !prop_schema.get(prop_name).is_some_and(Value::is_object),
                "{}.{prop_name}: double-nested property schema in {prop_schema}",
                tool.name
            );
        }
        assert!(
            tool.output_schema
                .as_ref()
                .is_some_and(|s| s.get("required").is_some()),
            "{}: result envelope output schema missing",
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
    // Healthz parity: unauthenticated peers see the store file name and the
    // store_id identity (never the directory); the version names the binary.
    let health = http_request(addr, "GET", "/healthz", &[], None).expect("healthz");
    assert_eq!(health.status(), 200);
    let payload: Value = serde_json::from_str(&health.body).expect("healthz json");
    assert_eq!(payload["status"], "ok");
    assert_eq!(payload["db"], "nav-store.kg");
    assert!(
        !payload["db"]
            .as_str()
            .unwrap_or("")
            .contains(&served_db[..served_db.len() - "nav-store.kg".len()])
    );
    assert!(
        payload["store_id"]
            .as_str()
            .is_some_and(|s| s.starts_with("StoreId(")),
        "store_id identity: {payload}"
    );
    assert_eq!(payload["version"], env!("CARGO_PKG_VERSION"));
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
        allow_writes: true,
        compact: false,
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
async fn keyless_daemon_refuses_every_write_tool() {
    let temp = seeded_store("write-gate");
    let mut config = DaemonConfig::for_store(temp.0.join("nav-store.kg"));
    config.api_key = None;
    config.allow_writes = false;
    let (addr, handle) = server::serve_config(config, "127.0.0.1:0".parse().unwrap())
        .await
        .expect("serve");

    let mut client = McpClient::connect(addr).expect("connect");
    client.initialize();
    for tool in [
        "record_memory",
        "knowledge_add",
        "knowledge_delete",
        "knowledge_status",
        "generate_flow",
        "wiki_generate",
    ] {
        let message = client.call_tool(tool, json!({}));
        let result = &message["result"];
        assert_eq!(result["isError"], true, "{tool} must be refused: {result}");
        let text = result["content"][0]["text"].as_str().expect("text content");
        assert!(
            text.contains("allow-writes"),
            "{tool} must name the write gate: {text}"
        );
    }
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

#[tokio::test(flavor = "multi_thread")]
async fn compact_daemon_omits_output_schema_and_structured_content() {
    let temp = seeded_store("compact");
    let mut config = DaemonConfig::for_store(temp.0.join("nav-store.kg"));
    config.api_key = None;
    config.compact = true;
    let (addr, handle) = server::serve_config(config, "127.0.0.1:0".parse().unwrap())
        .await
        .expect("serve");

    let mut client = McpClient::connect(addr).expect("connect");
    client.initialize();
    for tool in client.list_tools_raw() {
        assert!(
            tool.get("outputSchema").is_none_or(Value::is_null),
            "compact mode must omit outputSchema on {}",
            tool["name"]
        );
    }

    let ok = client.call_tool("search_symbols", json!({"pattern": "search_symbols"}));
    assert_eq!(ok["result"]["isError"], false);
    assert!(
        ok["result"]["content"][0]["text"]
            .as_str()
            .is_some_and(|t| !t.is_empty()),
        "ok text content: {ok}"
    );
    assert!(
        ok["result"]["structuredContent"].is_null(),
        "compact ok call must omit structuredContent: {ok}"
    );

    let err = client.call_tool("not_a_tool", json!({}));
    assert_eq!(err["result"]["isError"], true);
    assert!(
        err["result"]["structuredContent"].is_null(),
        "compact error call must omit structuredContent: {err}"
    );

    handle.stop();
}

fn call_text(client: &mut McpClient, tool: &str, args: Value) -> (bool, String) {
    let resp = client.call_tool(tool, args);
    let is_err = resp["result"]["isError"].as_bool().unwrap_or(true);
    let text = resp["result"]["content"][0]["text"]
        .as_str()
        .unwrap_or("")
        .to_string();
    (is_err, text)
}

#[tokio::test(flavor = "multi_thread")]
async fn explore_compass_batch_and_compact_reduce_turns_and_tokens() {
    let temp = seeded_store("token-turn");
    let mut config = DaemonConfig::for_store(temp.0.join("nav-store.kg"));
    config.api_key = None;
    let (addr, handle) = server::serve_config(config, "127.0.0.1:0".parse().unwrap())
        .await
        .expect("serve");

    let mut client = McpClient::connect(addr).expect("connect");
    client.initialize();

    let (err, text) = call_text(
        &mut client,
        "explore",
        json!({"query": "OKFBundle", "include_source": false}),
    );
    assert!(!err, "explore omit source failed: {text}");
    assert!(
        text.contains("=== Source (omitted: include_source=false) ==="),
        "{text}"
    );

    let (err, text) = call_text(
        &mut client,
        "explore",
        json!({"query": "OKFBundle", "budget": 120}),
    );
    assert!(!err, "explore budget failed: {text}");
    assert!(text.contains("[token budget:"), "{text}");

    let (err, text) = call_text(
        &mut client,
        "ask_compass",
        json!({"file_path": "src/cairn/graph/embeddings.py", "prepare_edit": true}),
    );
    assert!(!err, "ask_compass prepare_edit failed: {text}");
    assert!(text.contains("=== File API ==="), "{text}");
    assert!(text.contains("src/cairn/graph/embeddings.py"), "{text}");

    let (err, text) = call_text(
        &mut client,
        "ask_compass",
        json!({"query": "build resolve", "file_path": "src/cairn/graph/embeddings.py"}),
    );
    assert!(!err, "ask_compass merged mode failed: {text}");
    assert!(text.contains("Intent:"), "{text}");
    assert!(!text.contains("ignored in routing mode"), "{text}");

    let (err, _) = call_text(
        &mut client,
        "ask_compass",
        json!({"query": "", "file_path": ""}),
    );
    assert!(err, "empty ask_compass query + file_path must fail");

    let (err, csv_text) = call_text(
        &mut client,
        "find_definition",
        json!({"name": "OKFBundle, get_db"}),
    );
    assert!(!err, "batch csv failed: {csv_text}");
    assert!(
        csv_text.contains("=== OKFBundle ===") && csv_text.contains("=== get_db ==="),
        "{csv_text}"
    );

    let (err, arr_text) = call_text(
        &mut client,
        "find_definition",
        json!({"names": ["OKFBundle", "chunk_for_symbol"]}),
    );
    assert!(!err, "batch array failed: {arr_text}");
    assert!(
        arr_text.contains("=== OKFBundle ===") && arr_text.contains("=== chunk_for_symbol ==="),
        "{arr_text}"
    );

    let (_, search_full) = call_text(&mut client, "search_symbols", json!({"pattern": "*chunk*"}));
    let (err, search_compact) = call_text(
        &mut client,
        "search_symbols",
        json!({"pattern": "*chunk*", "compact": true}),
    );
    assert!(
        !err && search_compact.contains("[cairn] "),
        "{search_compact}"
    );
    assert!(search_compact.len() < search_full.len(), "{search_compact}");

    let (_, callers_full) = call_text(
        &mut client,
        "get_callers",
        json!({"name": "OKFBundle", "limit": 20}),
    );
    let (err, callers_compact) = call_text(
        &mut client,
        "get_callers",
        json!({"name": "OKFBundle", "limit": 20, "compact": true}),
    );
    assert!(
        !err && callers_compact.contains("[cairn] "),
        "{callers_compact}"
    );
    assert!(
        callers_compact.len() < callers_full.len(),
        "{callers_compact}"
    );

    let (_, callees_full) = call_text(
        &mut client,
        "get_callees",
        json!({"name": "test_dashboard_app", "limit": 20}),
    );
    let (err, callees_compact) = call_text(
        &mut client,
        "get_callees",
        json!({"name": "test_dashboard_app", "limit": 20, "compact": true}),
    );
    assert!(
        !err && callees_compact.contains("[cairn] "),
        "{callees_compact}"
    );
    assert!(
        callees_compact.len() < callees_full.len(),
        "{callees_compact}"
    );

    handle.stop();
}
