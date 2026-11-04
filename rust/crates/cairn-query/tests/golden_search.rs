//! Golden parity: served `search_symbols` and `semantic_search` responses match
//! the recorded fixtures over the checked-in store fixtures.

mod common;

use std::fs;

use cairn_query::tools_search as ts;
use serde_json::Value;

use common::{TempStore, envelope, fixture_store, normalization_rules, normalize_value, repo_root};

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
    let conn = cairn_store::conn::open_read_only(&temp.db(&store_src)).expect("open store");

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
        native_cold: false,
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
    let conn = cairn_store::conn::open_read_only(&temp.db(&store_src)).expect("open store");

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
