//! Spike golden test: the served `search_symbols` responses match the recorded
//! fixture over streamable HTTP (queries taken from the fixture file).

use std::fs;
use std::path::PathBuf;

use common::{McpClient, TempStore, http_request};
use regex::Regex;
use serde_json::{Value, json};

mod common;

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../..")
        .canonicalize()
        .expect("repo root")
}

/// The store a fixture replays over: its `replay_store` pointer when present,
/// else the legacy checked-in default.
fn fixture_store(root: &std::path::Path, fixture: &Value, default: &str) -> PathBuf {
    root.join(fixture["replay_store"].as_str().unwrap_or(default))
}

// --- fixture normalization (ported from the recorder's scrub rules) ----------

fn normalize_text(text: &str, rules: &[Rule]) -> String {
    let mut out = text.to_string();
    for rule in rules {
        out = rule(&out);
    }
    out
}

type Rule = Box<dyn Fn(&str) -> String>;

fn re_replace(pattern: &str, replacement: &'static str) -> Rule {
    let re = Regex::new(pattern).expect("rule regex");
    Box::new(move |text: &str| re.replace_all(text, replacement).to_string())
}

fn round_float(text: &str) -> String {
    let re = Regex::new(r"\b\d+\.\d{5,}\b").expect("float regex");
    re.replace_all(text, |caps: &regex::Captures| {
        format!("{:.4}", caps[0].parse::<f64>().unwrap_or(0.0))
    })
    .to_string()
}

fn mask_id6(text: &str) -> String {
    // Port of the lookbehind rule (?<=[-_.])[0-9a-f]{6}\b.
    let re = Regex::new(r"([-_.])[0-9a-f]{6}\b").expect("id6 regex");
    re.replace_all(text, "${1}<ID6>").to_string()
}

fn no_op(text: &str) -> String {
    text.to_string()
}

/// The fixture's declared normalization rules, in order. Path rules
/// (store_home/home/workspace) were already applied at capture time; spike
/// responses carry store-relative paths only, so they map to no-ops.
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

// --- the golden test ---------------------------------------------------------

#[tokio::test(flavor = "multi_thread")]
async fn spike_serves_search_symbols_golden_equal() {
    let root = repo_root();
    let fixture_path = root.join("rust/golden/mcp/search_symbols.json");
    let fixture: Value = serde_json::from_str(
        &fs::read_to_string(&fixture_path)
            .unwrap_or_else(|e| panic!("read fixture {}: {e}", fixture_path.display())),
    )
    .expect("fixture json");
    let store_path = fixture_store(&root, &fixture, "rust/golden/spike/search-store.kg");
    let entries = fixture["spike_queries"].as_array().unwrap_or_else(|| {
        panic!(
            "spike_queries missing in {}; regenerate with scripts/rust_migration/spike_golden.py",
            fixture_path.display()
        )
    });
    assert!(
        entries.len() >= 5,
        "expected >=5 spike queries, found {}",
        entries.len()
    );
    // The fixture's own capture participates as its first case.
    assert_eq!(
        fixture["arguments"]["pattern"], entries[0]["pattern"],
        "fixture arguments drift from spike_queries[0]"
    );
    assert_eq!(
        fixture["response"], entries[0]["response"],
        "fixture response drift from spike_queries[0]"
    );
    let rules = normalization_rules(&fixture);

    let temp = TempStore::from_fixture(&store_path, "spike");
    let db = temp.0.join(store_path.file_name().expect("store name"));
    let (addr, handle) = cairn_daemon::server::serve(db).await.expect("serve");
    let mut client = McpClient::connect(addr).expect("connect");
    client.initialize();

    for entry in entries {
        let pattern = entry["pattern"].as_str().expect("pattern");
        let message = client.call_tool("search_symbols", json!({ "pattern": pattern }));
        let result = &message["result"];
        let actual = json!({
            "isError": result.get("isError").cloned().unwrap_or(Value::Bool(false)),
            "content": result.get("content").cloned().unwrap_or(Value::Array(vec![])),
            "structuredContent": result.get("structuredContent").cloned().unwrap_or(Value::Null),
        });
        let expected = normalize_value(&entry["response"], &rules);
        let actual = normalize_value(&actual, &rules);
        assert_eq!(
            actual, expected,
            "spike response differs from fixture for pattern {pattern:?}"
        );
    }

    let health = http_request(addr, "GET", "/healthz", &[], None).expect("healthz");
    assert_eq!(
        health.status(),
        200,
        "healthz answered {}",
        health.status_line
    );
    assert_eq!(
        health.header("content-type"),
        Some("application/json"),
        "healthz content type"
    );

    handle.stop();
}
