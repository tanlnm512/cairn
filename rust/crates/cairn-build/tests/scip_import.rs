//! SCIP import contract: prost decodes the Python-runtime fixture, the
//! overlay writes exactly the position-joined edges, and generation
//! classification matches the Python registry semantics.

use std::fs;
use std::path::{Path, PathBuf};

use cairn_build::scip::importer::import_scip_file;
use cairn_build::scip::proto;
use cairn_store::schema::open_initialized;
use prost::Message as _;
use rusqlite::Connection;

const FIXTURE_DIR: &str = "tests/fixtures/scip";

fn fixture_path(name: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join(FIXTURE_DIR)
        .join(name)
}

fn temp_ws(tag: &str) -> PathBuf {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock")
        .as_nanos();
    let dir = std::env::temp_dir().join(format!("cairn-scip-{tag}-{}-{nanos}", std::process::id()));
    fs::create_dir_all(dir.join("pkg")).expect("temp dir");
    fs::create_dir_all(dir.join(".git")).expect("git marker");
    fs::write(
        dir.join("pkg").join("util.py"),
        "def add(a, b):\n    return a + b\n",
    )
    .expect("util");
    fs::write(
        dir.join("pkg").join("app.py"),
        "def main():\n    return add(1, 2)\n",
    )
    .expect("app");
    dir
}

fn open_store(ws: &Path) -> Connection {
    let db = ws.join("store.kg");
    open_initialized(&db).expect("open store")
}

/// Seed files/symbols matching `cargo xtask scip-fixture`'s scenario:
/// spans are 1-based; Klass(8..12) contains method(9..9) for the innermost join.
fn seed_graph(conn: &Connection, repo_id: &str) {
    conn.execute_batch(&format!(
        "INSERT INTO repos (id, name, path) VALUES ('{repo_id}', '{repo_id}', '.');
         INSERT INTO files (id, repo_id, path, language) VALUES
             ('f-util', '{repo_id}', 'pkg/util.py', 'python'),
             ('f-app',  '{repo_id}', 'pkg/app.py',  'python');
         INSERT INTO symbols (id, file_id, name, kind, line_start, line_end, column_start, column_end) VALUES
             ('s-add',    'f-util', 'add',    'function', 2, 3,  0, 100),
             ('s-helper', 'f-util', 'helper', 'function', 6, 7,  0, 100),
             ('s-main',   'f-app',  'main',   'function', 4, 5,  0, 100),
             ('s-klass',  'f-app',  'Klass',  'class',    8, 12, 0, 100),
             ('s-method', 'f-app',  'method', 'method',   9, 9,  4, 10);"
    ))
    .expect("seed graph");
}

fn discovered_repo_id(ws: &Path) -> String {
    cairn_build::scanner::discover_repo_records(ws.to_str().expect("ws"))
        .expect("discover")
        .into_iter()
        .next()
        .expect("one repo")
        .repo_id
}

/// (source, target, name, kind, line, column, resolution) of one scip edge row.
type EdgeRow = (String, Option<String>, String, String, i64, i64, String);

/// Every scip edge, in (line, column) order.
fn scip_edges(conn: &Connection) -> Vec<EdgeRow> {
    let mut stmt = conn
        .prepare(
            "SELECT source_id, target_id, target_name, kind, line, column, resolution
             FROM edges WHERE source = 'scip' ORDER BY line, column",
        )
        .expect("prepare");
    let rows = stmt
        .query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, Option<String>>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
                row.get::<_, i64>(4)?,
                row.get::<_, i64>(5)?,
                row.get::<_, String>(6)?,
            ))
        })
        .expect("query");
    rows.map(Result::unwrap).collect()
}

fn skip_rows(conn: &Connection) -> Vec<(String, String, String)> {
    let mut stmt = conn
        .prepare("SELECT repo_id, path, reason FROM skipped_files ORDER BY path, reason")
        .expect("prepare");
    let rows = stmt
        .query_map([], |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)))
        .expect("query");
    rows.map(Result::unwrap).collect()
}

fn single_line(line: i32, start: i32, end: i32) -> Option<proto::occurrence::TypedRange> {
    Some(proto::occurrence::TypedRange::SingleLineRange(
        proto::SingleLineRange {
            line,
            start_character: start,
            end_character: end,
        },
    ))
}

fn occ(
    symbol: &str,
    range: Option<proto::occurrence::TypedRange>,
    roles: i32,
) -> proto::Occurrence {
    proto::Occurrence {
        symbol: symbol.to_string(),
        symbol_roles: roles,
        typed_range: range,
        ..proto::Occurrence::default()
    }
}

#[test]
fn fixture_decodes_with_prost() {
    let bytes = fs::read(fixture_path("sample.scip")).expect("fixture bytes");
    let index = proto::Index::decode(bytes.as_slice()).expect("decode");

    assert_eq!(
        index
            .metadata
            .as_ref()
            .expect("metadata")
            .tool_info
            .as_ref()
            .expect("tool")
            .name,
        "make_fixture.py"
    );
    let paths: Vec<&str> = index
        .documents
        .iter()
        .map(|d| d.relative_path.as_str())
        .collect();
    assert_eq!(paths, vec!["pkg/util.py", "pkg/app.py", "gen/extra.py"]);

    let app = &index.documents[1];
    assert_eq!(app.occurrences.len(), 13);
    assert_eq!(app.symbols.len(), 3);
    let kinds: Vec<i32> = app.symbols.iter().map(|s| s.kind).collect();
    assert_eq!(kinds, vec![17, 7, 26]); // Function, Class, Method

    // Typed single-line reference (the `add` call site).
    let add_ref = &app.occurrences[4];
    assert_eq!(add_ref.symbol_roles, 0);
    assert_eq!(add_ref.typed_range, single_line(4, 11, 14));
    // Deprecated repeated range [startLine, startChar, endLine, endChar].
    #[allow(deprecated)]
    {
        let deprecated = &app.occurrences[9];
        assert_eq!(deprecated.typed_range, None);
        assert_eq!(deprecated.range, vec![4, 8, 4, 30]);
        // Occurrence without any range.
        assert_eq!(app.occurrences[10].typed_range, None);
        assert!(app.occurrences[10].range.is_empty());
    }

    assert_eq!(index.external_symbols.len(), 1);
    assert_eq!(index.external_symbols[0].kind, 17);
}

#[test]
fn overlay_writes_exact_call_and_reference_edges() {
    let ws = temp_ws("overlay");
    let repo_id = discovered_repo_id(&ws);
    let conn = open_store(&ws);
    seed_graph(&conn, &repo_id);
    // A tree-sitter edge on util.py must survive: that document plans no rows.
    conn.execute(
        "INSERT INTO edges (id, source_id, target_id, target_name, kind, line, column) \
         VALUES ('ts-util', 's-add', 's-helper', 'helper', 'calls', 3, 0)",
        [],
    )
    .expect("seed ts edge");

    let record = import_scip_file(
        &conn,
        fixture_path("sample.scip").to_str().unwrap(),
        ws.to_str().unwrap(),
    )
    .expect("import");
    assert_eq!(record.documents, 3);
    assert_eq!(record.matched_documents, 2);
    assert_eq!(record.skipped_documents, 1);
    assert_eq!(record.edges, 7);
    assert_eq!(record.calls, 5);
    assert_eq!(record.references, 2);
    assert_eq!(record.exact, 5);
    assert_eq!(record.unresolved, 2);
    assert_eq!(record.joined_occurrences, 7);
    assert_eq!(record.unjoined_occurrences, 2);
    assert_eq!(record.join_anomalies, 0);
    assert_eq!(record.disagreements, 0);
    assert_eq!(record.upgrades, 0);

    // (source, target, name, kind, line, column, resolution), ordered by line/column.
    let edges = scip_edges(&conn);
    let expected: Vec<EdgeRow> = vec![
        (
            "s-main".into(),
            Some("s-add".into()),
            "f".into(),
            "calls".into(),
            5,
            8,
            "exact".into(),
        ),
        (
            "s-main".into(),
            Some("s-add".into()),
            "f".into(),
            "calls".into(),
            5,
            11,
            "exact".into(),
        ),
        (
            "s-main".into(),
            None,
            "f".into(),
            "references".into(),
            5,
            17,
            "unresolved".into(),
        ),
        (
            "s-main".into(),
            Some("s-main".into()),
            "py:4:9-4:14".into(),
            "references".into(),
            5,
            24,
            "exact".into(),
        ),
        (
            "s-method".into(),
            Some("s-method".into()),
            "self".into(),
            "calls".into(),
            9,
            0,
            "exact".into(),
        ),
        (
            "s-klass".into(),
            Some("s-helper".into()),
            "f".into(),
            "calls".into(),
            11,
            4,
            "exact".into(),
        ),
        (
            "s-klass".into(),
            None,
            "f".into(),
            "calls".into(),
            12,
            0,
            "unresolved".into(),
        ),
    ];
    assert_eq!(edges, expected, "\ngot: {edges:?}");
    // No skip rows for a clean import; util.py's tree-sitter edge survives.
    assert!(skip_rows(&conn).is_empty(), "{:?}", skip_rows(&conn));
    let ts_survivors: i64 = conn
        .query_row(
            "SELECT count(*) FROM edges WHERE source IS NOT 'scip'",
            [],
            |row| row.get(0),
        )
        .expect("count");
    assert_eq!(ts_survivors, 1);
    let _ = fs::remove_dir_all(&ws);
}

#[test]
fn join_anomaly_keeps_tree_sitter_edges() {
    let ws = temp_ws("anomaly");
    let repo_id = discovered_repo_id(&ws);
    let conn = open_store(&ws);
    seed_graph(&conn, &repo_id);
    conn.execute(
        "INSERT INTO edges (id, source_id, target_id, target_name, kind, line, column) \
         VALUES ('ts-app', 's-main', 's-helper', 'helper', 'calls', 5, 0)",
        [],
    )
    .expect("seed ts edge");

    // One joinable reference against three unjoinable ones: yield 0.25 < 0.5.
    let mut doc = proto::Document {
        relative_path: "pkg/app.py".to_string(),
        ..proto::Document::default()
    };
    doc.occurrences
        .push(occ("local app.py:4:1-4:2", single_line(4, 0, 4), 0));
    doc.occurrences
        .push(occ("scip-x ghost a 1.0.0/a( f).", single_line(20, 0, 4), 0));
    doc.occurrences
        .push(occ("scip-x ghost a 1.0.0/b( f).", single_line(21, 0, 4), 0));
    doc.occurrences
        .push(occ("scip-x ghost a 1.0.0/c( f).", single_line(22, 0, 4), 0));
    let bytes = proto::Index {
        documents: vec![doc],
        ..proto::Index::default()
    }
    .encode_to_vec();
    let index_path = ws.join("anomaly.scip");
    fs::write(&index_path, bytes).expect("write index");

    let record = import_scip_file(&conn, index_path.to_str().unwrap(), ws.to_str().unwrap())
        .expect("import");
    assert_eq!(record.edges, 0);
    assert_eq!(record.join_anomalies, 1);
    // The anomalous row names the matched file's repo; ts edges stay put.
    assert_eq!(
        skip_rows(&conn),
        vec![(
            repo_id.clone(),
            "pkg/app.py".to_string(),
            "scip_join_anomaly".to_string()
        )]
    );
    let ts_survivors: i64 = conn
        .query_row("SELECT count(*) FROM edges", [], |row| row.get(0))
        .expect("count");
    assert_eq!(ts_survivors, 1);
    let _ = fs::remove_dir_all(&ws);
}

#[test]
fn authority_delete_replaces_tree_sitter_edges_with_disagreement_counts() {
    let ws = temp_ws("authority");
    let repo_id = discovered_repo_id(&ws);
    let conn = open_store(&ws);
    seed_graph(&conn, &repo_id);
    // Incumbents on app.py: one target-disagreement candidate, one
    // unresolved-resolution candidate for the upgrade counter.
    conn.execute_batch(
        "INSERT INTO edges (id, source_id, target_id, target_name, kind, line, column, resolution) VALUES
             ('ts-1', 's-main', 's-helper', 'helper', 'calls', 5, 0, NULL),
             ('ts-2', 's-main', NULL, 'x', 'references', 5, 1, 'unresolved');",
    )
    .expect("seed ts edges");

    let mut doc = proto::Document {
        relative_path: "pkg/app.py".to_string(),
        ..proto::Document::default()
    };
    doc.symbols.push(proto::SymbolInformation {
        symbol: "scip-fixture a 1.0.0/a( f).".to_string(),
        kind: 17,
        ..proto::SymbolInformation::default()
    });
    // Definition of the call target inside app.py: joins to s_main.
    doc.occurrences.push(occ(
        "scip-fixture a 1.0.0/a( f).",
        single_line(4, 10, 16),
        1,
    ));
    // Exact call at (calls, line 5): disagrees with ts-1's s_helper target.
    doc.occurrences.push(occ(
        "scip-fixture a 1.0.0/a( f).",
        single_line(4, 20, 24),
        0,
    ));
    // Class-kind reference (references, exact) at (references, line 5): upgrades ts-2.
    doc.symbols.push(proto::SymbolInformation {
        symbol: "scip-fixture a 1.0.0/Klass.".to_string(),
        kind: 7,
        ..proto::SymbolInformation::default()
    });
    doc.occurrences
        .push(occ("scip-fixture a 1.0.0/Klass.", single_line(7, 0, 5), 1));
    doc.occurrences.push(occ(
        "scip-fixture a 1.0.0/Klass.",
        single_line(4, 30, 35),
        0,
    ));
    let index_path = ws.join("authority.scip");
    fs::write(
        &index_path,
        proto::Index {
            documents: vec![doc],
            ..proto::Index::default()
        }
        .encode_to_vec(),
    )
    .expect("write index");

    let record = import_scip_file(&conn, index_path.to_str().unwrap(), ws.to_str().unwrap())
        .expect("import");
    assert_eq!(record.edges, 2);
    assert_eq!(record.disagreements, 1);
    assert_eq!(record.upgrades, 1);
    // Per-file authority: both incumbents deleted, exactly the scip rows remain.
    let edges = scip_edges(&conn);
    assert_eq!(edges.len(), 2, "{edges:?}");
    let other_rows: i64 = conn
        .query_row(
            "SELECT count(*) FROM edges WHERE source IS NOT 'scip'",
            [],
            |row| row.get(0),
        )
        .expect("count");
    assert_eq!(other_rows, 0);
    let _ = fs::remove_dir_all(&ws);
}

#[test]
fn corrupt_index_records_skip_and_fails_before_writes() {
    let ws = temp_ws("corrupt");
    let repo_id = discovered_repo_id(&ws);
    let conn = open_store(&ws);
    seed_graph(&conn, &repo_id);
    let index_path = ws.join("broken.scip");
    fs::write(&index_path, b"\xff\xff\xff").expect("corrupt bytes");

    let err = import_scip_file(&conn, index_path.to_str().unwrap(), ws.to_str().unwrap())
        .expect_err("corrupt index must fail");
    assert!(err.contains("corrupt SCIP index"), "{err}");
    // Index-scoped attribution: repo_id stays empty for parse errors.
    assert_eq!(
        skip_rows(&conn),
        vec![(
            "".to_string(),
            index_path.to_string_lossy().to_string(),
            "scip_parse_error".to_string()
        )]
    );

    // A successful re-import neither writes partial overlay state for the
    // failed path nor clears its parse-error row.
    fs::copy(fixture_path("sample.scip"), &index_path).expect("replace with valid index");
    let record = import_scip_file(&conn, index_path.to_str().unwrap(), ws.to_str().unwrap())
        .expect("valid re-import");
    assert_eq!(record.edges, 7);
    assert_eq!(
        skip_rows(&conn),
        vec![(
            "".to_string(),
            index_path.to_string_lossy().to_string(),
            "scip_parse_error".to_string()
        )]
    );
    let _ = fs::remove_dir_all(&ws);
}

#[test]
fn ambiguous_path_match_skips_every_document() {
    // Two sibling repos both holding pkg/util.py and pkg/app.py: the scanner
    // discovers both, so every fixture document path matches two files rows.
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock")
        .as_nanos();
    let ws = std::env::temp_dir().join(format!(
        "cairn-scip-ambiguous2-{}-{nanos}",
        std::process::id()
    ));
    for repo in ["repo-a", "repo-b"] {
        fs::create_dir_all(ws.join(repo).join("pkg")).expect("repo pkg dir");
        fs::create_dir_all(ws.join(repo).join(".git")).expect("git marker");
    }
    let conn = open_store(&ws);
    conn.execute_batch(
        "INSERT INTO repos (id, name, path) VALUES ('repo-a', 'repo-a', 'repo-a'),
                                          ('repo-b', 'repo-b', 'repo-b');
         INSERT INTO files (id, repo_id, path, language) VALUES
             ('f-util', 'repo-a', 'pkg/util.py', 'python'),
             ('f-app',  'repo-a', 'pkg/app.py',  'python'),
             ('b-util', 'repo-b', 'pkg/util.py', 'python'),
             ('b-app',  'repo-b', 'pkg/app.py',  'python');",
    )
    .expect("seed both repos");
    assert_eq!(
        cairn_build::scanner::discover_repo_records(ws.to_str().unwrap())
            .expect("discover")
            .len(),
        2,
        "both repos must be discovered for the ambiguity"
    );

    let record = import_scip_file(
        &conn,
        fixture_path("sample.scip").to_str().unwrap(),
        ws.to_str().unwrap(),
    )
    .expect("import");
    assert_eq!(record.matched_documents, 0);
    assert_eq!(record.skipped_documents, 3);
    assert_eq!(record.edges, 0);
    assert!(scip_edges(&conn).is_empty());
    let _ = fs::remove_dir_all(&ws);
}
