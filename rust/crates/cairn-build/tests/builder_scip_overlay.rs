//! Builder-side SCIP overlay wiring: a full build applies the configured
//! indexes after edge resolution, and an index it cannot apply degrades to
//! tree-sitter edges with a skip row instead of failing the build.

use std::fs;
use std::path::{Path, PathBuf};

use cairn_build::builder::build_graph;

const IN_INDEX_CALLREF: &str = "SELECT count(*) FROM edges e JOIN symbols s ON e.source_id = s.id \
     JOIN files f ON s.file_id = f.id WHERE f.path LIKE '%in_index.py' \
     AND e.kind IN ('calls', 'references')";

fn fixture_dir(name: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../../tests/fixtures/scip-indexing")
        .join(name)
}

fn copy_dir_all(src: &Path, dst: &Path) {
    fs::create_dir_all(dst).expect("mkdir");
    for entry in fs::read_dir(src).expect("read_dir") {
        let entry = entry.expect("entry");
        let target = dst.join(entry.file_name());
        if entry.file_type().expect("file type").is_dir() {
            copy_dir_all(&entry.path(), &target);
        } else {
            fs::copy(entry.path(), &target).expect("copy");
        }
    }
}

/// Copy one scip-indexing fixture into a fresh temp workspace with a .git marker.
fn materialize(fixture: &str, tag: &str) -> PathBuf {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock")
        .as_nanos();
    let ws = std::env::temp_dir().join(format!(
        "cairn-scip-overlay-{tag}-{}-{nanos}",
        std::process::id()
    ));
    copy_dir_all(&fixture_dir(fixture), &ws);
    fs::create_dir_all(ws.join(".git")).expect("git marker");
    ws
}

fn build(ws: &Path, db: &Path) -> cairn_build::BuildSummary {
    build_graph(
        ws.to_str().expect("ws"),
        db.to_str().expect("db"),
        None,
        Some(1),
    )
    .expect("build")
    .0
}

fn count(db: &Path, sql: &str) -> i64 {
    let conn = cairn_store::conn::open_read_only(db).expect("open store");
    conn.query_row(sql, [], |r| r.get(0)).expect("count")
}

#[test]
fn build_applies_configured_index_and_reports_scip_summary() {
    let ws = materialize("covered", "covered");
    let db = ws.join("covered.kg");
    let summary = build(&ws, &db);

    let scip_edges = count(&db, "SELECT count(*) FROM edges WHERE source = 'scip'");
    assert!(scip_edges > 0, "configured index must land scip edges");
    let scip_report = summary.scip.expect("summary carries the scip report");
    assert_eq!(scip_report.edges as i64, scip_edges);
    assert!(
        count(
            &db,
            &format!("{IN_INDEX_CALLREF} AND e.source = 'scip' AND e.resolution = 'exact'")
        ) > 0
    );
    // Per-file authority: the covered file keeps no tree-sitter calls/references.
    assert_eq!(
        count(
            &db,
            &format!("{IN_INDEX_CALLREF} AND coalesce(e.source, '') != 'scip'")
        ),
        0
    );
    // The overlay touches calls/references only.
    assert_eq!(
        count(
            &db,
            "SELECT count(*) FROM edges WHERE kind IN ('contains', 'imports') \
             AND coalesce(source, '') != 'tree_sitter'"
        ),
        0
    );
    let _ = fs::remove_dir_all(&ws);
}

#[test]
fn corrupt_index_never_fails_the_build_or_writes_partial_edges() {
    let ws = materialize("covered", "corrupt");
    fs::write(ws.join("index.scip"), b"\x12\x05ab").expect("corrupt bytes");
    let db = ws.join("corrupt.kg");
    let summary = build(&ws, &db);

    assert_eq!(
        count(&db, "SELECT count(*) FROM edges WHERE source = 'scip'"),
        0
    );
    let report = summary.scip.expect("degradation still reports");
    assert_eq!(report.edges, 0);

    let off_ws = materialize("covered-off", "corrupt-off");
    let off_db = off_ws.join("covered-off.kg");
    let off_summary = build(&off_ws, &off_db);
    assert!(off_summary.scip.is_none(), "no scip key without config");
    assert_eq!(
        count(&db, "SELECT count(*) FROM edges"),
        count(&off_db, "SELECT count(*) FROM edges")
    );

    let repo_id: String = {
        let conn = cairn_store::conn::open_read_only(&db).expect("open store");
        conn.query_row("SELECT repo_id FROM files LIMIT 1", [], |r| r.get(0))
            .expect("repo id")
    };
    assert_eq!(
        count(
            &db,
            &format!(
                "SELECT count(*) FROM skipped_files \
                 WHERE repo_id = '{repo_id}' AND path = 'index.scip' \
                 AND reason = 'scip_import_failed'"
            )
        ),
        1
    );
    let _ = fs::remove_dir_all(&ws);
    let _ = fs::remove_dir_all(&off_ws);
}

#[test]
fn missing_index_without_generation_records_skip_and_touches_nothing() {
    // Configure the index only after the build so no generation is ever attempted.
    let ws = materialize("covered-off", "no-gen");
    let db = ws.join("no-gen.kg");
    build(&ws, &db);
    fs::copy(
        fixture_dir("covered").join("cairn.json"),
        ws.join("cairn.json"),
    )
    .expect("config");

    let conn = cairn_store::schema::open_initialized(&db).expect("open store");
    let repo_id: String = conn
        .query_row("SELECT repo_id FROM files LIMIT 1", [], |r| r.get(0))
        .expect("repo id");
    let report = cairn_build::builder::apply_scip_overlay(
        &conn,
        ws.to_str().expect("ws"),
        std::slice::from_ref(&repo_id),
        false,
        &mut |_| {},
    )
    .expect("configured overlay reports");

    assert_eq!(report, cairn_build::builder::ScipReport::default());
    assert!(
        !ws.join("index.scip").exists(),
        "generate_missing=false must not generate"
    );
    let row: (String, String, String) = conn
        .query_row(
            "SELECT repo_id, path, reason FROM skipped_files \
             WHERE reason = 'scip_index_missing'",
            [],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
        )
        .expect("skip row");
    assert_eq!(
        row,
        (
            repo_id,
            "index.scip".to_string(),
            "scip_index_missing".to_string()
        )
    );
    let _ = conn.close();
    let _ = fs::remove_dir_all(&ws);
}
