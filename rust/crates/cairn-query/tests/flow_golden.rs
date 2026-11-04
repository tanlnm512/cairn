//! Flow-tool golden parity: renders match the recorded fixtures
//! over the checked-in corpus store (queries taken from the fixtures).

mod common;

use std::fs;
use std::path::{Path, PathBuf};

use cairn_store::conn;
use serde_json::Value;

use common::{TempStore, envelope, normalization_rules, normalize_value, repo_root, sorted_lines};

fn flow_dir() -> PathBuf {
    repo_root().join("rust/golden/flow")
}

/// The flow-corpus (store, workspace) pair every fixture block replays over.
fn flow_artifacts() -> (PathBuf, PathBuf) {
    (flow_dir().join("flow-store.kg"), flow_dir().join("corpus"))
}

/// Port of the daemon's central result cap: cut at the last newline past
/// 60_000 chars with the fixed notice (the `@instrument` contract).
fn truncate_result(name: &str, result: String) -> String {
    const CAP: usize = 60_000;
    let char_count = result.chars().count();
    if char_count <= CAP {
        return result;
    }
    let mut head: String = result.chars().take(CAP).collect();
    if let Some(cut) = head.rfind('\n')
        && cut > 0
    {
        head.truncate(cut);
    }
    format!(
        "{head}\n\n[TRUNCATED: '{name}' returned {char_count} chars, over the \
         {CAP}-char cap. Narrow the query -- e.g. pass a smaller `limit`, use \
         fuzzy=False, a more specific pattern, or a lower `depth` -- rather \
         than relying on this truncated output.]"
    )
}

fn run_tool(conn: &rusqlite::Connection, tool: &str, args: &Value, ws: &Path) -> String {
    match tool {
        "explore" => cairn_query::tools_flow::explore_text(
            conn,
            None,
            ws,
            args["query"].as_str().expect("query"),
            true,
            None,
        ),
        "trace_flow" => cairn_query::tools_flow::trace_flow_text(
            conn,
            args["entry"].as_str().expect("entry"),
            args.get("max_depth").and_then(Value::as_i64).unwrap_or(8),
        ),
        "cross_repo_deps" => cairn_query::tools_flow::cross_repo_deps_text(
            conn,
            ws,
            args["repo"].as_str().expect("repo"),
            args.get("limit").and_then(Value::as_i64).unwrap_or(50),
        ),
        "visualize_graph" => cairn_query::tools_flow::visualize_graph_text(
            conn,
            ws,
            cairn_query::tools_flow::VizQuery {
                scope: args
                    .get("scope")
                    .and_then(Value::as_str)
                    .unwrap_or("symbol"),
                symbol: args.get("symbol").and_then(Value::as_str).unwrap_or(""),
                module: args.get("module").and_then(Value::as_str).unwrap_or(""),
                repo: args.get("repo").and_then(Value::as_str).unwrap_or(""),
                depth: args.get("depth").and_then(Value::as_i64).unwrap_or(3),
                format: args
                    .get("format")
                    .and_then(Value::as_str)
                    .unwrap_or("mermaid"),
            },
        ),
        "taint" => cairn_query::tools_flow::taint_warning_text(
            conn,
            args["from"].as_str().expect("from"),
            args["to"].as_str().expect("to"),
        ),
        other => panic!("no runner for tool {other}"),
    }
    .expect("tool run")
}

/// Compare one fixture entry (canonical response or a case) against the port.
fn assert_case(fixture: &Value, case: Option<&Value>, label: &str, store: &Path, ws: &Path) {
    let rules = normalization_rules(fixture);
    let entry = case.unwrap_or(fixture);
    let args = &entry["arguments"];
    let tool = fixture["tool"].as_str().expect("tool");
    let sort_lines = entry
        .get("sort_lines")
        .and_then(Value::as_bool)
        .or_else(|| {
            fixture["normalization"]
                .get("sort_lines")
                .and_then(Value::as_bool)
        })
        .unwrap_or(false);
    let temp = TempStore::from_fixture(store, "flow");
    let conn = conn::open_read_only(&temp.db(store)).expect("open store");
    let produced = truncate_result(tool, run_tool(&conn, tool, args, ws));
    let mut actual = normalize_value(&envelope(produced), &rules);
    if sort_lines {
        // Capture-time normalization sorted the drawn line order; the
        // produced text must be sorted the same way before comparing.
        let text = sorted_lines(actual["content"][0]["text"].as_str().expect("text"));
        actual["content"][0]["text"] = Value::String(text.clone());
        actual["structuredContent"]["result"] = Value::String(text);
    }
    let expected = normalize_value(&entry["response"], &rules);
    assert_eq!(actual, expected, "{label} differs from the fixture");
}

fn fixture(name: &str) -> Value {
    let path = flow_dir().join(format!("{name}.json"));
    serde_json::from_str(
        &fs::read_to_string(&path).unwrap_or_else(|e| panic!("read {}: {e}", path.display())),
    )
    .expect("fixture json")
}

#[test]
fn explore_matches_the_golden_fixture() {
    let fixture = fixture("explore");
    let (store, ws) = flow_artifacts();
    assert_case(&fixture, None, "explore(fetch_user)", &store, &ws);
    for (i, case) in fixture["cases"]
        .as_array()
        .expect("cases")
        .iter()
        .enumerate()
    {
        assert_case(
            &fixture,
            Some(case),
            &format!("explore case {i}"),
            &store,
            &ws,
        );
    }
}

#[test]
fn trace_flow_matches_the_golden_fixture() {
    let fixture = fixture("trace_flow");
    let (store, ws) = flow_artifacts();
    assert_case(&fixture, None, "trace_flow(kickoff)", &store, &ws);
    for (i, case) in fixture["cases"]
        .as_array()
        .expect("cases")
        .iter()
        .enumerate()
    {
        assert_case(
            &fixture,
            Some(case),
            &format!("trace_flow case {i}"),
            &store,
            &ws,
        );
    }
}

#[test]
fn cross_repo_deps_matches_the_golden_fixture() {
    let fixture = fixture("cross_repo_deps");
    let (store, ws) = flow_artifacts();
    assert_case(&fixture, None, "cross_repo_deps(corpus)", &store, &ws);
    for (i, case) in fixture["cases"]
        .as_array()
        .expect("cases")
        .iter()
        .enumerate()
    {
        assert_case(
            &fixture,
            Some(case),
            &format!("cross_repo_deps case {i}"),
            &store,
            &ws,
        );
    }
}

#[test]
fn visualize_graph_matches_the_golden_fixture() {
    let fixture = fixture("visualize_graph");
    let (store, ws) = flow_artifacts();
    assert_case(
        &fixture,
        None,
        "visualize_graph(symbol/mermaid)",
        &store,
        &ws,
    );
    for (i, case) in fixture["cases"]
        .as_array()
        .expect("cases")
        .iter()
        .enumerate()
    {
        assert_case(
            &fixture,
            Some(case),
            &format!("visualize_graph case {i}"),
            &store,
            &ws,
        );
    }
}

#[test]
fn taint_matches_the_golden_fixture() {
    let fixture = fixture("taint");
    let (store, ws) = flow_artifacts();
    assert_case(&fixture, None, "taint(file-read -> sql)", &store, &ws);
}
