//! SCIP import contract: prost decodes the committed fixture, the
//! overlay writes exactly the position-joined edges, and generation
//! classification matches the registry semantics.

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
    assert_eq!(record.external, 2);
    assert_eq!(record.unresolved, 0);
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
            "external".into(),
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
            "external".into(),
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
fn oversized_index_records_skip_without_reading() {
    let ws = temp_ws("too-large");
    let repo_id = discovered_repo_id(&ws);
    let conn = open_store(&ws);
    seed_graph(&conn, &repo_id);
    let index_path = ws.join("huge.scip");
    // Sparse: set_len reserves the length without writing the bytes.
    std::fs::File::create(&index_path)
        .expect("create")
        .set_len(256 * 1024 * 1024 + 1)
        .expect("sparse length");

    let err = import_scip_file(&conn, index_path.to_str().unwrap(), ws.to_str().unwrap())
        .expect_err("oversized index must fail");
    assert!(err.contains("exceeds the 256 MiB read cap"), "{err}");
    assert_eq!(
        skip_rows(&conn),
        vec![(
            "".to_string(),
            index_path.to_string_lossy().to_string(),
            "scip_index_too_large".to_string()
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

#[test]
fn external_symbol_references_classified_as_external() {
    let ws = temp_ws("external");
    let repo_id = discovered_repo_id(&ws);
    let conn = open_store(&ws);
    seed_graph(&conn, &repo_id);

    let mut doc = proto::Document {
        relative_path: "pkg/app.py".to_string(),
        ..proto::Document::default()
    };
    doc.symbols.push(proto::SymbolInformation {
        symbol: "scip-fixture a 1.0.0/a( f).".to_string(),
        kind: 17,
        ..proto::SymbolInformation::default()
    });
    // Definition of the local call target: joins to s_main.
    doc.occurrences.push(occ(
        "scip-fixture a 1.0.0/a( f).",
        single_line(4, 10, 16),
        1,
    ));
    // Exact call into the local target.
    doc.occurrences.push(occ(
        "scip-fixture a 1.0.0/a( f).",
        single_line(4, 20, 24),
        0,
    ));
    // Reference to a dependency symbol declared in external_symbols: no
    // workspace definition exists by design, so it must classify as
    // 'external', not 'unresolved'.
    doc.occurrences
        .push(occ("pip numpy 1.0.0/numpy( f).", single_line(4, 30, 35), 0));
    let index_path = ws.join("external.scip");
    fs::write(
        &index_path,
        proto::Index {
            documents: vec![doc],
            external_symbols: vec![proto::SymbolInformation {
                symbol: "pip numpy 1.0.0/numpy( f).".to_string(),
                kind: 17,
                ..proto::SymbolInformation::default()
            }],
            ..proto::Index::default()
        }
        .encode_to_vec(),
    )
    .expect("write index");

    let record = import_scip_file(&conn, index_path.to_str().unwrap(), ws.to_str().unwrap())
        .expect("import");
    assert_eq!(record.edges, 2);
    assert_eq!(record.exact, 1);
    assert_eq!(record.external, 1);
    assert_eq!(record.unresolved, 0);
    let external_rows: Vec<String> = conn
        .prepare("SELECT resolution FROM edges WHERE source = 'scip'")
        .expect("stmt")
        .query_map([], |row| row.get::<_, String>(0))
        .expect("query")
        .filter_map(Result::ok)
        .collect();
    assert!(
        external_rows.contains(&"external".to_string()),
        "{external_rows:?}"
    );
    let _ = fs::remove_dir_all(&ws);
}

#[test]
fn stale_guard_keeps_tree_sitter_edges_for_files_newer_than_index() {
    let ws = temp_ws("stale");
    let repo_id = discovered_repo_id(&ws);
    let conn = open_store(&ws);
    seed_graph(&conn, &repo_id);
    // Incumbent tree-sitter edge for app.py that the guarded import must keep.
    conn.execute(
        "INSERT INTO edges (id, source_id, target_id, target_name, kind, line, column, resolution) \
         VALUES ('ts-1', 's-main', 's-add', 'add', 'calls', 5, 0, 'exact')",
        [],
    )
    .expect("seed ts edge");
    let index_path = ws.join("stale.scip");
    fs::copy(fixture_path("sample.scip"), &index_path).expect("fixture index");
    // app.py edited after the index was generated.
    let newer = future_mtime(100.0);
    conn.execute("UPDATE files SET mtime = ? WHERE id = 'f-app'", [newer])
        .expect("bump mtime");

    let record = cairn_build::scip::importer::import_scip_file_opts(
        &conn,
        index_path.to_str().unwrap(),
        ws.to_str().unwrap(),
        true,
    )
    .expect("guarded import");
    assert_eq!(record.stale_documents, 1);
    // The stale file keeps its tree-sitter edges: no scip rows for app.py.
    let app_scip: i64 = conn
        .query_row(
            "SELECT count(*) FROM edges e JOIN symbols s ON e.source_id = s.id \
             WHERE s.file_id = 'f-app' AND e.source = 'scip'",
            [],
            |row| row.get(0),
        )
        .expect("count");
    assert_eq!(app_scip, 0);
    let ts_kept: i64 = conn
        .query_row("SELECT count(*) FROM edges WHERE id = 'ts-1'", [], |row| {
            row.get(0)
        })
        .expect("count");
    assert_eq!(ts_kept, 1);

    // The unguarded explicit import still covers the file (user intent).
    let record = import_scip_file(&conn, index_path.to_str().unwrap(), ws.to_str().unwrap())
        .expect("unguarded import");
    assert_eq!(record.stale_documents, 0);
    let _ = fs::remove_dir_all(&ws);
}

/// Unix-seconds mtime `offset` seconds from now (files.mtime is REAL seconds).
fn future_mtime(offset: f64) -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock")
        .as_secs_f64()
        + offset
}

#[test]
fn stale_document_remains_a_join_target() {
    let ws = temp_ws("stale-join");
    let repo_id = discovered_repo_id(&ws);
    let conn = open_store(&ws);
    seed_graph(&conn, &repo_id);
    // A scheme-prefixed symbol defined in util.py and referenced from app.py:
    // util.py is stale, but its current spans must still join app.py's call.
    let mut util = proto::Document {
        relative_path: "pkg/util.py".to_string(),
        ..proto::Document::default()
    };
    util.occurrences
        .push(occ("scip-x pkg 1.0.0/util.add().", single_line(1, 0, 3), 1));
    let mut app = proto::Document {
        relative_path: "pkg/app.py".to_string(),
        ..proto::Document::default()
    };
    app.occurrences
        .push(occ("scip-x pkg 1.0.0/util.add().", single_line(4, 0, 3), 0));
    let index_path = ws.join("stale-join.scip");
    fs::write(
        &index_path,
        proto::Index {
            documents: vec![util, app],
            ..proto::Index::default()
        }
        .encode_to_vec(),
    )
    .expect("write index");
    conn.execute(
        "UPDATE files SET mtime = ? WHERE id = 'f-util'",
        [future_mtime(100.0)],
    )
    .expect("bump mtime");

    let record = cairn_build::scip::importer::import_scip_file_opts(
        &conn,
        index_path.to_str().unwrap(),
        ws.to_str().unwrap(),
        true,
    )
    .expect("guarded import");
    assert_eq!(record.stale_documents, 1);
    assert_eq!(record.exact, 1);
    assert_eq!(record.external, 0);
    // The stale document plans no rows of its own; the fresh document's
    // reference resolves exactly into it.
    let edges = scip_edges(&conn);
    assert_eq!(edges.len(), 1, "{edges:?}");
    assert_eq!(edges[0].0, "s-main");
    assert_eq!(edges[0].1.as_deref(), Some("s-add"));
    assert_eq!(edges[0].6, "exact");
    let _ = fs::remove_dir_all(&ws);
}

#[test]
fn scheme_symbol_with_in_index_definition_is_unresolved_not_external() {
    let ws = temp_ws("scheme-def");
    let repo_id = discovered_repo_id(&ws);
    let conn = open_store(&ws);
    seed_graph(&conn, &repo_id);
    // app.py references a scheme-prefixed symbol whose only definition site
    // sits in a document matching no workspace file: a workspace symbol that
    // failed to join, not a dependency.
    let symbol = "scip-x pkg 1.0.0/util.add().";
    let mut app = proto::Document {
        relative_path: "pkg/app.py".to_string(),
        ..proto::Document::default()
    };
    app.occurrences.push(occ(symbol, single_line(4, 0, 3), 0));
    let mut ghost = proto::Document {
        relative_path: "pkg/ghost.py".to_string(),
        ..proto::Document::default()
    };
    ghost.occurrences.push(occ(symbol, single_line(1, 0, 3), 1));
    let index_path = ws.join("scheme-def.scip");
    fs::write(
        &index_path,
        proto::Index {
            documents: vec![app, ghost],
            ..proto::Index::default()
        }
        .encode_to_vec(),
    )
    .expect("write index");

    let record = import_scip_file(&conn, index_path.to_str().unwrap(), ws.to_str().unwrap())
        .expect("import");
    assert_eq!(record.skipped_documents, 1);
    assert_eq!(record.unresolved, 1);
    assert_eq!(record.external, 0);
    let edges = scip_edges(&conn);
    assert_eq!(edges.len(), 1, "{edges:?}");
    assert_eq!(edges[0].1, None);
    assert_eq!(edges[0].6, "unresolved");
    let _ = fs::remove_dir_all(&ws);
}

#[test]
fn committed_index_overlays_without_the_stale_guard() {
    let ws = temp_ws("committed");
    let repo_id = discovered_repo_id(&ws);
    fs::write(
        ws.join("cairn.json"),
        r#"{"scip": {"indexes": {"python": "index.scip"}}}"#,
    )
    .expect("config");
    fs::copy(fixture_path("sample.scip"), ws.join("index.scip")).expect("committed index");
    let conn = open_store(&ws);
    seed_graph(&conn, &repo_id);
    // A checked-out file is newer than the committed index; committed
    // indexes are trusted regardless of checkout-order mtimes.
    conn.execute(
        "UPDATE files SET mtime = ? WHERE id = 'f-app'",
        [future_mtime(100.0)],
    )
    .expect("bump mtime");

    let report = cairn_build::builder::apply_scip_overlay(
        &conn,
        ws.to_str().unwrap(),
        std::slice::from_ref(&repo_id),
        false,
        &mut |_| {},
    )
    .expect("overlay report");
    assert_eq!(report.edges, 7);
    assert_eq!(report.stale_files, 0);
    assert_eq!(scip_edges(&conn).len(), 7);
    let _ = fs::remove_dir_all(&ws);
}

#[test]
#[cfg(unix)]
fn stale_store_index_is_regenerated_by_the_overlay() {
    static ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
    let _guard = ENV_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());

    let ws = temp_ws("store-regen");
    let repo_id = discovered_repo_id(&ws);
    // The index the configured command installs into the store slot.
    let mut fresh = proto::Document {
        relative_path: "pkg/app.py".to_string(),
        ..proto::Document::default()
    };
    fresh.symbols.push(proto::SymbolInformation {
        symbol: "scip-fixture a 1.0.0/a( f).".to_string(),
        kind: 17,
        ..proto::SymbolInformation::default()
    });
    fresh.occurrences.push(occ(
        "scip-fixture a 1.0.0/a( f).",
        single_line(4, 10, 16),
        1,
    ));
    fresh.occurrences.push(occ(
        "scip-fixture a 1.0.0/a( f).",
        single_line(4, 20, 24),
        0,
    ));
    let fresh_path = ws.join("fresh.scip");
    let fresh_bytes = proto::Index {
        documents: vec![fresh],
        ..proto::Index::default()
    }
    .encode_to_vec();
    fs::write(&fresh_path, &fresh_bytes).expect("write fresh index");

    // Register the workspace in an isolated cairn home.
    let home = ws.join("cairn-home");
    let canonical = fs::canonicalize(&ws).expect("canonical ws");
    fs::create_dir_all(&home).expect("home dir");
    fs::write(
        home.join("workspaces.json"),
        format!(r#"{{"{}": "0123456789abcdef"}}"#, canonical.display()),
    )
    .expect("registry");
    let config = serde_json::json!({
        "scip": {
            "generated": {"python": true},
            "commands": {"python": ["cp", fresh_path.to_string_lossy(), "$out"]},
        }
    });
    fs::write(ws.join("cairn.json"), config.to_string()).expect("config");
    let saved_home = std::env::var_os("CAIRN_HOME");
    let saved_commands = std::env::var_os("CAIRN_SCIP_COMMANDS");
    // SAFETY: ENV_LOCK serializes env-mutating tests; restored before returning.
    unsafe {
        std::env::set_var("CAIRN_HOME", &home);
        std::env::set_var("CAIRN_SCIP_COMMANDS", "1");
    }

    // A store index older than every indexed file: cairn owns it, so the
    // overlay must rebuild it instead of guarding against it forever.
    let store_index = home
        .join("0123456789abcdef")
        .join("scip")
        .join("python.scip");
    fs::create_dir_all(store_index.parent().expect("parent")).expect("store scip dir");
    fs::write(&store_index, b"stale bytes").expect("seed store index");
    let old = std::time::SystemTime::now() - std::time::Duration::from_secs(200);
    std::fs::File::options()
        .write(true)
        .open(&store_index)
        .expect("open store index")
        .set_times(std::fs::FileTimes::new().set_modified(old))
        .expect("backdate mtime");

    let conn = open_store(&ws);
    seed_graph(&conn, &repo_id);
    let file_mtime = future_mtime(-100.0);
    conn.execute("UPDATE files SET mtime = ?", [file_mtime])
        .expect("set file mtimes");

    let report = cairn_build::builder::apply_scip_overlay(
        &conn,
        ws.to_str().unwrap(),
        std::slice::from_ref(&repo_id),
        true,
        &mut |_| {},
    )
    .expect("overlay report");

    assert_eq!(
        fs::read(&store_index).expect("regenerated store index"),
        fresh_bytes,
        "the stale store index must be rebuilt from the configured command"
    );
    assert_eq!(report.edges, 1);
    assert_eq!(report.stale_files, 0);
    assert_eq!(scip_edges(&conn).len(), 1);

    // SAFETY: still holding ENV_LOCK.
    unsafe {
        match saved_home {
            Some(v) => std::env::set_var("CAIRN_HOME", v),
            None => std::env::remove_var("CAIRN_HOME"),
        }
        match saved_commands {
            Some(v) => std::env::set_var("CAIRN_SCIP_COMMANDS", v),
            None => std::env::remove_var("CAIRN_SCIP_COMMANDS"),
        }
    }
    let _ = fs::remove_dir_all(&ws);
}
