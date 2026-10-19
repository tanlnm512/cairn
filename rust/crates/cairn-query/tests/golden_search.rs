//! Golden parity: served `search_symbols` and `semantic_search` responses match
//! the recorded fixtures over the checked-in store fixtures.

use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use cairn_query::tools_search as ts;
use regex::Regex;
use serde_json::{Value, json};

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../..")
        .canonicalize()
        .expect("repo root")
}

/// The store a fixture replays over: its `replay_store` pointer when present,
/// else the legacy checked-in default.
fn fixture_store(root: &Path, fixture: &Value, default: &str) -> PathBuf {
    root.join(fixture["replay_store"].as_str().unwrap_or(default))
}

/// Copy-on-create scratch store; removes the directory on drop.
struct TempStore(PathBuf);

impl TempStore {
    fn from_fixture(src: &Path, tag: &str) -> Self {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let dir =
            std::env::temp_dir().join(format!("cairn-1b-{tag}-{}-{}", std::process::id(), nanos));
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

// --- fixture normalization (ported from the recorder's scrub rules) ----------

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
/// (store_home/home/workspace) were already applied at capture time; replay
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
        Value::String(text) => {
            Value::String(rules.iter().fold(text.clone(), |acc, rule| rule(&acc)))
        }
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

fn envelope(text: String) -> Value {
    json!({
        "isError": false,
        "content": [{"type": "text", "text": text}],
        "structuredContent": {"result": text},
    })
}

// --- search_symbols golden ---------------------------------------------------

#[test]
fn search_symbols_serves_the_golden_fixture() {
    let root = repo_root();
    let fixture_path = root.join("rust/golden/mcp/search_symbols.json");
    let fixture: Value = serde_json::from_str(
        &fs::read_to_string(&fixture_path)
            .unwrap_or_else(|e| panic!("read fixture {}: {e}", fixture_path.display())),
    )
    .expect("fixture json");
    let entries = fixture["spike_queries"]
        .as_array()
        .unwrap_or_else(|| panic!("spike_queries missing in {}", fixture_path.display()));
    assert!(entries.len() >= 5, "expected >=5 spike queries");
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

    let store_src = fixture_store(&root, &fixture, "rust/golden/spike/search-store.kg");
    let temp = TempStore::from_fixture(&store_src, "lex");
    let conn =
        cairn_store::conn::open_read_only(&temp.0.join(store_src.file_name().expect("store name")))
            .expect("open store");

    for entry in entries {
        let pattern = entry["pattern"].as_str().expect("pattern");
        let actual = envelope(ts::search_symbols_response(&conn, pattern, None));
        let expected = normalize_value(&entry["response"], &rules);
        let actual = normalize_value(&actual, &rules);
        assert_eq!(
            actual, expected,
            "search_symbols response differs from fixture for pattern {pattern:?}"
        );
    }
}

// --- semantic_search golden --------------------------------------------------

#[test]
fn semantic_search_serves_the_golden_fixture() {
    let root = repo_root();
    let fixture_path = root.join("rust/golden/mcp/semantic_search.json");
    let fixture: Value = serde_json::from_str(
        &fs::read_to_string(&fixture_path)
            .unwrap_or_else(|e| panic!("read fixture {}: {e}", fixture_path.display())),
    )
    .expect("fixture json");
    let entries = fixture["spike_queries"]
        .as_array()
        .unwrap_or_else(|| panic!("spike_queries missing in {}", fixture_path.display()));
    assert!(
        entries.len() >= 3,
        "expected >=3 semantic spike queries, found {}",
        entries.len()
    );
    assert_eq!(
        fixture["arguments"]["query"], entries[0]["query"],
        "fixture arguments drift from spike_queries[0]"
    );
    assert_eq!(
        fixture["response"], entries[0]["response"],
        "fixture response drift from spike_queries[0]"
    );
    // The replay env must encode the capture pins: hash backend, rerank off.
    assert_eq!(
        fixture["capture"]["env_pins"]["CAIRN_EMBED_BACKEND"],
        "hash"
    );
    assert_eq!(fixture["capture"]["env_pins"]["CAIRN_RERANK"], "0");
    let rules = normalization_rules(&fixture);

    let env = ts::SemanticEnv {
        fusion_enabled: true,
        backend: ts::EffectiveBackend::Hash,
        hash_fallback: false,
        model: ts::HASH_MODEL.to_string(),
        rerank_on: false,
        rerank_override: None,
        reranker_available: false,
        ann_enabled: true,
        min_margin: 0.45,
        server_base_url: None,
        server_api_key: None,
    };

    let store_src = fixture_store(&root, &fixture, "rust/golden/search/semantic-store.kg");
    let temp = TempStore::from_fixture(&store_src, "sem");
    let conn =
        cairn_store::conn::open_read_only(&temp.0.join(store_src.file_name().expect("store name")))
            .expect("open store");

    for entry in entries {
        let query = entry["query"].as_str().expect("query");
        let limit = entry["limit"].as_u64().expect("limit") as usize;
        let matches = ts::semantic_search(&conn, query, limit, 0.3, &env);
        let actual = envelope(ts::render_semantic_search(
            query,
            &matches,
            env.fusion_enabled,
        ));
        let expected = normalize_value(&entry["response"], &rules);
        let actual = normalize_value(&actual, &rules);
        assert_eq!(
            actual, expected,
            "semantic_search response differs from fixture for query {query:?}"
        );
    }
}
