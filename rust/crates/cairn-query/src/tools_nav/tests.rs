//! Nav-tool semantics on a synthetic store: fallback chains, precise/fuzzy
//! resolution, DFS cycles/truncation, test detection, staleness, cross-repo.

use rusqlite::Connection;

use super::{
    CalleeRow, CallerRow, DefRow, callees_data, callers_data, cross_repo_dependents,
    find_definition, impact_analysis, is_test_symbol, render_callees, render_callers,
    render_find_definition, staleness_banner,
};

const SCHEMA: &str = r#"
CREATE TABLE repos (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL,
    language TEXT, git_remote TEXT, indexed_at TIMESTAMP);
CREATE TABLE files (
    id TEXT PRIMARY KEY, repo_id TEXT NOT NULL REFERENCES repos(id),
    path TEXT NOT NULL, language TEXT NOT NULL, hash TEXT,
    line_count INTEGER, indexed_at TIMESTAMP, UNIQUE(repo_id, path));
CREATE TABLE symbols (
    id TEXT PRIMARY KEY, file_id TEXT NOT NULL REFERENCES files(id),
    name TEXT NOT NULL, qualified_name TEXT, kind TEXT NOT NULL,
    line_start INTEGER, line_end INTEGER, column_start INTEGER,
    column_end INTEGER, docstring TEXT, modifiers TEXT);
CREATE TABLE edges (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES symbols(id),
    target_id TEXT REFERENCES symbols(id), target_name TEXT,
    kind TEXT NOT NULL, line INTEGER, "column" INTEGER);
ALTER TABLE edges ADD COLUMN resolution TEXT;
CREATE TABLE imports (
    id TEXT PRIMARY KEY, file_id TEXT NOT NULL REFERENCES files(id),
    imported_path TEXT NOT NULL, resolved_symbol_id TEXT REFERENCES symbols(id),
    line INTEGER);
"#;

fn store() -> Connection {
    let conn = Connection::open_in_memory().expect("in-memory db");
    conn.execute_batch(SCHEMA).expect("schema");
    conn.execute_batch(
        r#"
        INSERT INTO repos VALUES ('cairn', 'cairn', '/ws/cairn', 'python', NULL, NULL);
        INSERT INTO repos VALUES ('consumer', 'consumer', '/ws/consumer', 'kotlin', NULL, NULL);
        INSERT INTO repos VALUES ('other', 'other', '/ws/other', 'python', NULL, NULL);
        INSERT INTO files VALUES
            ('f1', 'cairn', 'src/app.py', 'python', NULL, 10, NULL),
            ('f2', 'cairn', 'src/main_pkg/main.py', 'python', NULL, 10, NULL),
            ('f3', 'cairn', 'tests/test_app.py', 'python', NULL, 10, NULL),
            ('f4', 'consumer', 'src/consumer/MainActivity.kt', 'kotlin', NULL, 10, NULL),
            ('f5', 'other', 'lib.py', 'python', NULL, 10, NULL);
        INSERT INTO symbols VALUES
            ('s_usecase', 'f1', 'UseCase', 'app.UseCase', 'class', 1, 20, NULL, NULL, NULL, NULL),
            ('s_invoke', 'f1', 'invoke', 'app.UseCase.invoke', 'method', 5, 9, NULL, NULL, NULL, NULL),
            ('s_caller', 'f1', 'caller_fn', 'caller_fn', 'function', 10, 15, NULL, NULL, NULL, NULL),
            ('s_helper', 'f1', 'helper', 'helper', 'function', 16, 20, NULL, NULL, NULL, NULL),
            ('s_worker', 'f2', 'worker', 'main_pkg.worker', 'function', 1, 9, NULL, NULL, NULL, NULL),
            ('s_test', 'f3', 'test_helper', 'test_helper', 'function', 1, 5, NULL, NULL, NULL, NULL),
            ('s_view', 'f4', 'onCreate', NULL, 'method', 1, 9, NULL, NULL, NULL, NULL),
            ('s_lib', 'f5', 'lib_fn', NULL, 'function', 1, 5, NULL, NULL, NULL, NULL);
        INSERT INTO edges VALUES
            ('e1', 's_caller', 's_invoke', NULL, 'calls', 10, NULL, 'exact'),
            ('e2', 's_caller', 's_usecase', NULL, 'calls', 11, NULL, 'exact'),
            ('e3', 's_caller', NULL, 'missing_thing', 'calls', 12, NULL, 'unresolved'),
            ('e4', 's_worker', 's_helper', NULL, 'calls', 20, NULL, 'exact'),
            ('e5', 's_caller', 's_helper', NULL, 'calls', 13, NULL, 'exact'),
            ('e6', 's_test', 's_caller', NULL, 'calls', 2, NULL, 'exact'),
            ('e7', 's_helper', 's_worker', NULL, 'calls', 18, NULL, 'exact');
        INSERT INTO imports VALUES
            ('i1', 'f4', 'com.acme.core.util', NULL, 1),
            ('i2', 'f4', 'com.acme.core', NULL, 2),
            ('i3', 'f4', 'com.acme.core_extra', NULL, 3),
            ('i4', 'f5', 'com.acme.core', NULL, 1),
            ('i5', 'f5', 'unrelated.lib', NULL, 2);
        "#,
    )
    .expect("fixture rows");
    conn
}

fn names(rows: &[DefRow]) -> Vec<String> {
    rows.iter().map(|r| r.name.clone()).collect()
}

#[test]
fn find_definition_falls_back_exact_qualified_substring() {
    let conn = store();
    // Exact name wins even with a qualified-name hit available.
    assert_eq!(
        names(&find_definition(&conn, "UseCase", 50).unwrap()),
        vec!["UseCase"]
    );
    // Qualified-name match for a bare name that never appears as `name`.
    assert_eq!(
        names(&find_definition(&conn, "main_pkg.worker", 50).unwrap()),
        vec!["worker"]
    );
    // Substring-LIKE fallback with LIKE metacharacters taken literally.
    assert_eq!(
        names(&find_definition(&conn, "ork_", 50).unwrap()),
        Vec::<String>::new()
    );
    assert_eq!(
        names(&find_definition(&conn, "orker", 50).unwrap()),
        vec!["worker"]
    );
    // Miss renders the search_symbols hint.
    let miss = render_find_definition("zzz", &[], 50, "");
    assert_eq!(
        miss,
        "No definition found for 'zzz'. The name may be misspelled, ambiguous, \
         or the symbol lives outside the indexed workspace. Try \
         search_symbols(\"zzz\") to find near matches."
    );
}

#[test]
fn callers_follow_resolved_class_targets_and_split_precise_fuzzy() {
    let conn = store();
    // Kotlin operator-invoke style: a bare call resolving to the class symbol
    // surfaces as a precise caller edge targeting `kind=class`.
    let precise = callers_data(&conn, "UseCase", false, 200, "").unwrap();
    assert!(!precise.used_fallback);
    let kinds: Vec<(String, String)> = precise
        .rows
        .iter()
        .map(|r: &CallerRow| (r.caller_kind.clone(), r.resolution.clone().unwrap()))
        .collect();
    assert_eq!(kinds, vec![("function".to_string(), "exact".to_string())]);

    // Unresolved name-only edge stays out of precise and enters via fallback;
    // an ambiguous edge (resolver declined) behaves the same way.
    conn.execute(
        "INSERT INTO edges VALUES ('e8', 's_view', NULL, 'missing_thing', \
         'calls', 4, NULL, 'ambiguous')",
        [],
    )
    .expect("ambiguous edge");
    let data = callers_data(&conn, "missing_thing", false, 200, "").unwrap();
    assert!(data.used_fallback);
    let labels: Vec<Option<&str>> = data.rows.iter().map(|r| r.resolution.as_deref()).collect();
    assert_eq!(labels, vec![Some("unresolved"), Some("ambiguous")]);
    assert_eq!(data.rows[0].caller_name, "caller_fn");
    let rendered = render_callers(&data);
    assert_eq!(
        rendered,
        "0 precise callers for 'missing_thing'; 2 fuzzy candidates (name-match \
         only -- verify each against actual code before treating it as a real \
         caller):\n  function caller_fn  src/app.py:12  (cairn)\n  method \
         onCreate  src/consumer/MainActivity.kt:4  (consumer)"
    );
    // Explicit fuzzy skips the fallback flag.
    let explicit = callers_data(&conn, "missing_thing", true, 200, "").unwrap();
    assert!(!explicit.used_fallback);
}

#[test]
fn callees_omit_unresolved_precise_and_tag_them_fuzzy() {
    let conn = store();
    let precise = callees_data(&conn, "caller_fn", false, 200, "").unwrap();
    let names: Vec<(String, bool)> = precise
        .rows
        .iter()
        .map(|r: &CalleeRow| (r.name.clone().unwrap(), r.resolved))
        .collect();
    assert_eq!(
        names,
        vec![
            ("invoke".to_string(), true),
            ("UseCase".to_string(), true),
            ("helper".to_string(), true),
        ]
    );
    assert_eq!(
        render_callees(&precise),
        "3 callees of 'caller_fn':\n  invoke  src/app.py:10\n  UseCase  \
         src/app.py:11\n  helper  src/app.py:13"
    );
    let fuzzy = callees_data(&conn, "caller_fn", true, 200, "").unwrap();
    let unresolved = &fuzzy.rows[2];
    assert_eq!(unresolved.name.as_deref(), Some("missing_thing"));
    assert!(!unresolved.resolved);
    assert!(render_callees(&fuzzy).contains("  missing_thing (unresolved)  src/app.py:12"));
}

#[test]
fn impact_walks_depths_marks_cycles_and_truncates() {
    let conn = store();
    let result = impact_analysis(&conn, "helper", 5, false, 500, false, None).unwrap();
    let rows: Vec<(String, i64)> = result
        .impacted
        .iter()
        .map(|r| (r.symbol.clone(), r.depth))
        .collect();
    assert_eq!(
        rows,
        vec![
            ("caller_fn".to_string(), 0),
            ("test_helper".to_string(), 1),
            ("worker".to_string(), 0),
            // The seed re-enters through the worker back-edge.
            ("helper".to_string(), 1),
        ]
    );
    assert_eq!(result.total, 4);
    assert!(!result.truncated);
    assert_eq!(result.cycles, vec![("helper".to_string(), 2)]);

    // Limit cap stops discovery mid-walk and flips the truncation flag.
    let capped = impact_analysis(&conn, "helper", 5, false, 2, false, None).unwrap();
    assert_eq!(capped.total, 2);
    assert!(capped.truncated);

    // Depth grouping reads the final (possibly back-edge-relaxed) depths.
    let data = super::impact_data(&result, Vec::new(), "helper", false);
    assert_eq!(data.by_depth, vec![(0, 2), (1, 2)]);
    assert_eq!(data.cycles, vec!["helper"]);
}

#[test]
fn impact_renders_tests_cycles_and_zero_caller_note() {
    let conn = store();
    let result = impact_analysis(&conn, "helper", 5, false, 500, false, None).unwrap();
    let data = super::impact_data(&result, Vec::new(), "helper", false);
    let text = super::render_impact(&data, 500);
    assert!(text.contains("Affected tests (1 — run these to verify the change):"));
    assert!(text.contains("  test_helper  tests/test_app.py  (cairn, path)"));
    assert!(text.contains("Cycles: ['helper']"));
    assert!(text.contains("  Depth 0: 2 callers"));
    assert!(text.contains("  Depth 1: 2 callers"));

    // No callers, no tests: the fuzzy-retry note, then the cross-repo line.
    let empty = impact_analysis(&conn, "zzz", 3, false, 500, false, None).unwrap();
    let empty_data = super::impact_data(
        &empty,
        vec![super::Dependent {
            repo: "be-sdk".to_string(),
            count: 3,
        }],
        "zzz",
        false,
    );
    let text = super::render_impact(&empty_data, 500);
    assert!(
        text.contains(
            "(0 precise within-repo callers — retry fuzzy=True before concluding unused.)"
        )
    );
    assert!(text.contains("Cross-repo: 1 repo(s) depend — be-sdk (x3)"));
}

#[test]
fn test_symbol_detection_uses_path_basename_and_name_signals() {
    assert_eq!(
        is_test_symbol("tests/test_app.py", "helper", ""),
        Some("path")
    );
    assert_eq!(is_test_symbol("src/main.py", "FooTest", ""), Some("name"));
    assert_eq!(is_test_symbol("src/app.py", "helper", ""), None);
    // Filename-only signals do not survive a production src/main root.
    assert_eq!(is_test_symbol("app/src/main/FooTest.kt", "", ""), None);
    assert_eq!(
        is_test_symbol("app/src/test/FooTest.kt", "", ""),
        Some("path")
    );
    // A test_ basename is a strong signal even under src/main.
    assert_eq!(
        is_test_symbol("app/src/main/test_util.py", "", ""),
        Some("path")
    );
    // Qualified-name final segment carries the name signal.
    assert_eq!(
        is_test_symbol("pkg/mod.py", "runner", "pkg.mod.LoginSpec"),
        Some("name")
    );
}

#[test]
fn staleness_banner_lists_pending_files_and_handles_missing_table() {
    let conn = store();
    // No pending_sync table: best-effort empty.
    assert_eq!(staleness_banner(&conn, &["src/app.py"]), "");
    conn.execute_batch(
        r#"
        CREATE TABLE pending_sync (path TEXT PRIMARY KEY, status TEXT);
        INSERT INTO pending_sync VALUES
            ('src/app.py', 'dirty'),
            ('src/main_pkg/main.py', 'dirty'),
            ('tests/test_app.py', 'dirty'),
            ('zz.py', 'dirty');
        "#,
    )
    .expect("pending_sync");
    let banner = staleness_banner(
        &conn,
        &[
            "src/app.py",
            "src/main_pkg/main.py",
            "tests/test_app.py",
            "zz.py",
        ],
    );
    assert_eq!(
        banner,
        "⚠ Stale graph: 4 file(s) in this result have unindexed edits pending \
         reindex (app.py, main.py, test_app.py (+1 more)). Results may be \
         incomplete until `cairn update` runs. "
    );
}

#[test]
fn cross_repo_dependents_match_namespace_dot_boundaries() {
    let conn = store();
    let ns = [("com.acme.core", "cairn")];
    let deps = cross_repo_dependents(&conn, "cairn", &ns).unwrap();
    // core_extra sits outside the dot boundary; counts sort desc.
    assert_eq!(
        deps,
        vec![
            super::Dependent {
                repo: "consumer".to_string(),
                count: 2,
            },
            super::Dependent {
                repo: "other".to_string(),
                count: 1,
            },
        ]
    );
    // A repo owning no namespace has no dependents.
    assert!(
        cross_repo_dependents(&conn, "other", &ns)
            .unwrap()
            .is_empty()
    );
}

#[test]
fn renderers_keep_the_python_fstring_line_formats() {
    let conn = store();
    let rows = find_definition(&conn, "UseCase", 50).unwrap();
    assert_eq!(
        render_find_definition("UseCase", &rows, 50, ""),
        "src/app.py:1  class app.UseCase  (cairn)"
    );
    // Limit cap line appears only when rows fill the limit.
    assert!(
        render_find_definition("UseCase", &rows, 1, "")
            .ends_with("  ... hit the limit cap; pass a higher limit for more.")
    );
    let callers = callers_data(&conn, "UseCase", false, 200, "").unwrap();
    assert_eq!(
        render_callers(&callers),
        "1 callers of 'UseCase':\n  function caller_fn  src/app.py:11  (cairn)"
    );
    // NULL lines render like a Python f-string's None.
    let mut null_line = callers;
    null_line.rows[0].edge_line = None;
    assert!(render_callers(&null_line).contains("src/app.py:None"));
}

#[test]
fn trace_flow_rewalks_a_node_from_its_shortest_route() {
    let conn = Connection::open_in_memory().expect("in-memory db");
    conn.execute_batch(SCHEMA).expect("schema");
    conn.execute_batch(
        r#"
        INSERT INTO repos VALUES ('r', 'r', '/ws/r', 'python', NULL, NULL);
        INSERT INTO files VALUES ('f', 'r', 'main.py', 'python', NULL, 10, NULL);
        INSERT INTO symbols VALUES
            ('entry', 'f', 'entry', 'm.entry', 'function', 1, 10, NULL, NULL, NULL, NULL),
            ('left', 'f', 'left', 'm.left', 'function', 1, 10, NULL, NULL, NULL, NULL),
            ('mid', 'f', 'mid', 'm.mid', 'function', 1, 10, NULL, NULL, NULL, NULL),
            ('deep', 'f', 'deep', 'm.deep', 'function', 1, 10, NULL, NULL, NULL, NULL),
            ('tail', 'f', 'tail', 'm.tail', 'function', 1, 10, NULL, NULL, NULL, NULL),
            ('end', 'f', 'end', 'm.end', 'function', 1, 10, NULL, NULL, NULL, NULL),
            ('last', 'f', 'last', 'm.last', 'function', 1, 10, NULL, NULL, NULL, NULL),
            ('other', 'f', 'other', 'm.other', 'function', 1, 10, NULL, NULL, NULL, NULL);
        INSERT INTO edges VALUES
            ('e0', 'entry', 'left', 'left', 'call', 5, 0, 'exact'),
            ('e1', 'entry', 'mid', 'mid', 'call', 6, 0, 'exact'),
            ('e2', 'left', 'mid', 'mid', 'call', 5, 0, 'exact'),
            ('e3', 'mid', 'deep', 'deep', 'call', 5, 0, 'exact'),
            ('e4', 'mid', 'other', 'other', 'call', 6, 0, 'exact'),
            ('e5', 'deep', 'tail', 'tail', 'call', 5, 0, 'exact'),
            ('e6', 'deep', 'mid', 'mid', 'call', 6, 0, 'exact'),
            ('e7', 'tail', 'end', 'end', 'call', 5, 0, 'exact'),
            ('e8', 'end', 'last', 'last', 'call', 5, 0, 'exact');
        "#,
    )
    .expect("fixture rows");
    let flow = crate::graph::trace_flow(&conn, "entry", 5);
    let nodes: Vec<(String, i64, String)> = ["mid", "deep", "tail", "end", "last", "other"]
        .iter()
        .filter_map(|name| flow.chain.iter().find(|n| n.symbol == *name))
        .map(|n| {
            (
                n.symbol.clone(),
                n.depth,
                n.parent.clone().unwrap_or_default(),
            )
        })
        .collect();
    let expected = [
        ("mid", 1, "entry"),
        ("deep", 2, "mid"),
        ("tail", 3, "deep"),
        ("end", 4, "tail"),
        ("last", 5, "end"),
        ("other", 2, "mid"),
    ]
    .map(|(s, d, p)| (s.to_string(), d, p.to_string()));
    assert_eq!(nodes, expected);
    assert_eq!(flow.cycles, vec![("mid".to_string(), 3)]);
    let branch_owners: Vec<&str> = flow.branches.iter().map(|(s, _)| s.as_str()).collect();
    assert_eq!(branch_owners, vec!["deep", "mid", "entry"]);
}
