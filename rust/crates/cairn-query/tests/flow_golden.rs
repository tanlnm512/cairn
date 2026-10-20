//! Flow-tool golden parity: the Rust renders match the recorded Python
//! fixtures over the checked-in corpus store (queries taken from the fixtures).

use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use cairn_store::conn;
use serde_json::{Value, json};

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../..")
        .canonicalize()
        .expect("repo root")
}

fn flow_dir() -> PathBuf {
    repo_root().join("rust/golden/flow")
}

/// A fixture-declared replay artifact (store or workspace) relative to the repo
/// root, falling back to the legacy flow-corpus default when the key is absent.
fn fixture_artifact(root: &std::path::Path, fixture: &Value, key: &str, default: &str) -> PathBuf {
    root.join(fixture[key].as_str().unwrap_or(default))
}

/// Copy-on-create scratch store; removes the directory on drop.
struct TempStore(PathBuf);

impl TempStore {
    fn from_fixture(src: &Path) -> Self {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let dir = std::env::temp_dir().join(format!(
            "cairn-flow-golden-{}-{}",
            std::process::id(),
            nanos
        ));
        fs::create_dir_all(&dir).expect("temp dir");
        fs::copy(src, dir.join(src.file_name().expect("store name"))).expect("copy fixture store");
        Self(dir)
    }
}

impl Drop for TempStore {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

// --- fixture normalization (the recorder's scrub rules) ----------------------

fn normalize_text(text: &str, rules: &[Rule]) -> String {
    let mut out = text.to_string();
    for rule in rules {
        out = rule(&out);
    }
    out
}

type Rule = Box<dyn Fn(&str) -> String>;

fn re_replace(pattern: &str, replacement: &'static str) -> Rule {
    let re = regex::Regex::new(pattern).expect("rule regex");
    Box::new(move |text: &str| re.replace_all(text, replacement).to_string())
}

fn round_float(text: &str) -> String {
    let re = regex::Regex::new(r"\b\d+\.\d{5,}\b").expect("float regex");
    re.replace_all(text, |caps: &regex::Captures| {
        format!("{:.4}", caps[0].parse::<f64>().unwrap_or(0.0))
    })
    .to_string()
}

fn mask_id6(text: &str) -> String {
    // Port of the lookbehind rule (?<=[-_.])[0-9a-f]{6}\b.
    let re = regex::Regex::new(r"([-_.])[0-9a-f]{6}\b").expect("id6 regex");
    re.replace_all(text, "${1}<ID6>").to_string()
}

fn no_op(text: &str) -> String {
    text.to_string()
}

fn normalization_rules(fixture: &Value) -> Vec<Rule> {
    fixture["normalization"]["rules"]
        .as_array()
        .expect("rules list")
        .iter()
        .map(|r| r.as_str().expect("rule name"))
        .map(|name| match name {
            "iso_ts" => re_replace(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?",
                "<ISO>",
            ),
            "epoch" => re_replace(r"\b1[0-9]{9}\.\d+\b", "<EPOCH>"),
            "date" => re_replace(r"\d{4}-\d{2}-\d{2}", "<DATE>"),
            "duration" => re_replace(r"\b\d+\.\d+ ?m?s\b|\b\d+ ?ms\b", "<DUR>"),
            "age" => re_replace(r"\bjust now\b|\b\d+[smhd] old\b", "<AGE>"),
            "id12" => re_replace(r"\b[0-9a-f]{12}\b", "<ID>"),
            "id6" => Box::new(mask_id6 as fn(&str) -> String),
            "float" => Box::new(round_float as fn(&str) -> String),
            _ => Box::new(no_op as fn(&str) -> String),
        })
        .collect()
}

fn normalize_value(value: &Value, rules: &[Rule]) -> Value {
    match value {
        Value::String(text) => Value::String(normalize_text(text, rules)),
        Value::Array(items) => {
            Value::Array(items.iter().map(|v| normalize_value(v, rules)).collect())
        }
        Value::Object(map) => Value::Object(
            map.iter()
                .map(|(k, v)| (k.clone(), normalize_value(v, rules)))
                .collect(),
        ),
        other => other.clone(),
    }
}

fn sorted_lines(text: &str) -> String {
    // Python's `"\n".join(sorted(text.splitlines()))`.
    let mut lines: Vec<&str> = text.lines().collect();
    lines.sort();
    lines.join("\n")
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

// --- case runner --------------------------------------------------------------

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
    let temp = TempStore::from_fixture(store);
    let conn = conn::open_read_only(&temp.0.join(store.file_name().expect("store name")))
        .expect("open store");
    let produced = truncate_result(tool, run_tool(&conn, tool, args, ws));
    let actual = json!({
        "isError": false,
        "content": [{"type": "text", "text": produced}],
        "structuredContent": {"result": produced},
    });
    let mut actual = normalize_value(&actual, &rules);
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
    let path = if name == "taint" {
        flow_dir().join("taint.json")
    } else {
        repo_root().join(format!("rust/golden/mcp/{name}.json"))
    };
    serde_json::from_str(
        &fs::read_to_string(&path).unwrap_or_else(|e| panic!("read {}: {e}", path.display())),
    )
    .expect("fixture json")
}

/// The (store, workspace) pair for a fixture's canonical block: the fixture's
/// `replay_store`/`replay_workspace` pointers when present, else the legacy
/// flow-corpus defaults. Cases always replay over the flow corpus.
fn main_artifacts(fixture: &Value) -> (PathBuf, PathBuf) {
    let root = repo_root();
    (
        fixture_artifact(
            &root,
            fixture,
            "replay_store",
            "rust/golden/flow/flow-store.kg",
        ),
        fixture_artifact(
            &root,
            fixture,
            "replay_workspace",
            "rust/golden/flow/corpus",
        ),
    )
}

fn case_artifacts() -> (PathBuf, PathBuf) {
    (flow_dir().join("flow-store.kg"), flow_dir().join("corpus"))
}

#[test]
fn explore_matches_the_golden_fixture() {
    let fixture = fixture("explore");
    let (store, ws) = main_artifacts(&fixture);
    assert_case(&fixture, None, "explore(fetch_user)", &store, &ws);
    let (store, ws) = case_artifacts();
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
    let (store, ws) = main_artifacts(&fixture);
    assert_case(&fixture, None, "trace_flow(kickoff)", &store, &ws);
    let (store, ws) = case_artifacts();
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
    let (store, ws) = main_artifacts(&fixture);
    assert_case(&fixture, None, "cross_repo_deps(corpus)", &store, &ws);
    let (store, ws) = case_artifacts();
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
    let (store, ws) = main_artifacts(&fixture);
    assert_case(
        &fixture,
        None,
        "visualize_graph(symbol/mermaid)",
        &store,
        &ws,
    );
    let (store, ws) = case_artifacts();
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
    let (store, ws) = case_artifacts();
    assert_case(&fixture, None, "taint(file-read -> sql)", &store, &ws);
}
