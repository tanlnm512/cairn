//! Closure contract: BFS distances, name hops, caps, and streaming row parity.

use std::fs;
use std::path::{Path, PathBuf};

use cairn_build::closure::{CLOSURE_MAX_DEPTH, build_transitive_closure};
use rusqlite::Connection;

fn temp_db(tag: &str) -> PathBuf {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock")
        .as_nanos();
    let dir = std::env::temp_dir().join(format!(
        "cairn-build-closure-{tag}-{}-{nanos}",
        std::process::id()
    ));
    fs::create_dir_all(&dir).expect("temp dir");
    dir.join("t.kg")
}

/// Seed a repo/file backbone plus symbols and edges with explicit ids:
/// symbols as (id, name), edges as (source_id, target_id, target_name, kind).
fn seed(
    conn: &Connection,
    symbols: &[(&str, &str)],
    edges: &[(&str, Option<&str>, Option<&str>, &str)],
) {
    conn.execute(
        "INSERT INTO repos (id, name, path) VALUES ('r', 'r', '.')",
        [],
    )
    .expect("repo");
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES ('f', 'r', 'x.py', 'python')",
        [],
    )
    .expect("file");
    for (id, name) in symbols {
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind) VALUES (?, 'f', ?, 'function')",
            rusqlite::params![id, name],
        )
        .expect("symbol");
    }
    for (source, target, target_name, kind) in edges {
        conn.execute(
            "INSERT INTO edges (id, source_id, target_id, target_name, kind)
             VALUES ('e' || random(), ?, ?, ?, ?)",
            rusqlite::params![source, target, target_name, kind],
        )
        .expect("edge");
    }
}

/// All closure rows as sorted (source_id, target_name, target_id, distance) strings.
fn closure_rows(db: &Path) -> Vec<String> {
    let conn = Connection::open(db).expect("open");
    let mut stmt = conn
        .prepare(
            "SELECT source_id, COALESCE(target_name, '<NULL>'),
                    COALESCE(target_id, '<NULL>'), distance
             FROM transitive_edges",
        )
        .expect("prepare");
    let mut rows: Vec<String> = stmt
        .query_map([], |r| {
            Ok(format!(
                "{}\t{}\t{}\t{}",
                r.get::<_, String>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, String>(2)?,
                r.get::<_, i64>(3)?,
            ))
        })
        .expect("query")
        .map(Result::unwrap)
        .collect();
    rows.sort();
    rows
}

#[test]
fn chain_distances_and_target_ids_follow_the_bfs() {
    let db = temp_db("chain");
    let mut conn = cairn_store::schema::open_initialized(&db).expect("open");
    seed(
        &conn,
        &[
            ("s1", "entry"),
            ("a1", "mid_a"),
            ("b1", "mid_b"),
            ("c1", "mid_c"),
            ("d1", "exit"),
        ],
        &[
            ("s1", Some("a1"), None, "calls"),
            ("a1", Some("b1"), None, "calls"),
            ("b1", Some("c1"), None, "calls"),
            ("c1", Some("d1"), None, "calls"),
        ],
    );
    let outcome = build_transitive_closure(&mut conn, CLOSURE_MAX_DEPTH).expect("closure");
    // Every frontier key re-emits per distance, so a 4-hop chain yields
    // 4 + 3 + 2 + 1 rows; depth caps at CLOSURE_MAX_DEPTH.
    assert_eq!(outcome.rows, 10);
    assert_eq!(outcome.levels, 4);
    assert_eq!(outcome.peak_frontier, 4);
    assert_eq!(
        closure_rows(&db),
        vec![
            "a1\texit\td1\t3".to_string(),
            "a1\tmid_b\tb1\t1".to_string(),
            "a1\tmid_c\tc1\t2".to_string(),
            "b1\texit\td1\t2".to_string(),
            "b1\tmid_c\tc1\t1".to_string(),
            "c1\texit\td1\t1".to_string(),
            "s1\texit\td1\t4".to_string(),
            "s1\tmid_a\ta1\t1".to_string(),
            "s1\tmid_b\tb1\t2".to_string(),
            "s1\tmid_c\tc1\t3".to_string(),
        ]
    );
    let _ = fs::remove_dir_all(db.parent().expect("parent"));
}

#[test]
fn non_structural_kinds_are_ignored() {
    let db = temp_db("kinds");
    let mut conn = cairn_store::schema::open_initialized(&db).expect("open");
    seed(
        &conn,
        &[("s1", "a"), ("t1", "b")],
        &[
            ("s1", Some("t1"), None, "imports"),
            ("s1", Some("t1"), None, "references"),
            ("s1", Some("t1"), None, "calls"),
        ],
    );
    let outcome = build_transitive_closure(&mut conn, CLOSURE_MAX_DEPTH).expect("closure");
    assert_eq!(outcome.rows, 1);
    assert_eq!(closure_rows(&db), vec!["s1\tb\tt1\t1".to_string()]);
    let _ = fs::remove_dir_all(db.parent().expect("parent"));
}

#[test]
fn cycles_terminate_and_reemit_rows_at_deeper_distances() {
    let db = temp_db("cycle");
    let mut conn = cairn_store::schema::open_initialized(&db).expect("open");
    seed(
        &conn,
        &[("a1", "alpha"), ("b1", "beta")],
        &[
            ("a1", Some("b1"), None, "calls"),
            ("b1", Some("a1"), None, "calls"),
        ],
    );
    let outcome = build_transitive_closure(&mut conn, CLOSURE_MAX_DEPTH).expect("closure");
    // d1: both direct edges; d2: self-rows; d3/d4: the pair swaps again
    // (the PK carries distance, so re-emitted rows coexist).
    assert_eq!(outcome.rows, 8);
    let rows = closure_rows(&db);
    assert_eq!(rows.len(), 8, "rows: {rows:?}");
    assert!(rows.contains(&"a1\talpha\ta1\t2".to_string()), "{rows:?}");
    assert!(rows.contains(&"a1\tbeta\tb1\t3".to_string()), "{rows:?}");
    let _ = fs::remove_dir_all(db.parent().expect("parent"));
}

#[test]
fn unresolved_targets_hop_through_globally_unique_names_only() {
    let db = temp_db("case2");
    let mut conn = cairn_store::schema::open_initialized(&db).expect("open");
    // helper is unique: the unresolved edge hops through it to reach t2.
    // dup_name is ambiguous: the same shape must NOT hop through it.
    seed(
        &conn,
        &[
            ("s1", "starter"),
            ("h1", "helper"),
            ("t2", "target_two"),
            ("u1", "dup_name"),
            ("u2", "dup_name"),
            ("t3", "target_three"),
        ],
        &[
            ("s1", None, Some("helper"), "calls"),
            ("s1", None, Some("dup_name"), "calls"),
            ("h1", Some("t2"), None, "calls"),
            ("u1", Some("t3"), None, "calls"),
            ("u2", Some("t3"), None, "calls"),
        ],
    );
    build_transitive_closure(&mut conn, CLOSURE_MAX_DEPTH).expect("closure");
    let rows = closure_rows(&db);
    // Seed row for the unresolved edge carries a NULL target_id.
    assert!(
        rows.contains(&"s1\thelper\t<NULL>\t1".to_string()),
        "seed row: {rows:?}"
    );
    assert!(
        rows.contains(&"s1\ttarget_two\tt2\t2".to_string()),
        "unique-name hop: {rows:?}"
    );
    assert!(
        !rows.iter().any(|r| r.starts_with("s1\ttarget_three")),
        "ambiguous name must not hop: {rows:?}"
    );
    let _ = fs::remove_dir_all(db.parent().expect("parent"));
}

#[test]
fn dangling_target_ids_extend_through_their_own_edges() {
    let db = temp_db("dangling");
    let mut conn = cairn_store::schema::open_initialized(&db).expect("open");
    // ghost is referenced by id but its symbol row is gone (the FK is relaxed
    // only to fabricate that production shape); its NULL target_name is
    // unstorable (NOT NULL drops the row) yet the frontier entry still
    // extends through ghost's own outgoing edge.
    conn.execute_batch("PRAGMA foreign_keys = OFF;")
        .expect("fk off");
    seed(
        &conn,
        &[("s1", "starter"), ("t3", "target_three")],
        &[
            ("s1", Some("ghost"), None, "calls"),
            ("ghost", Some("t3"), None, "calls"),
        ],
    );
    conn.execute_batch("PRAGMA foreign_keys = ON;")
        .expect("fk on");
    let outcome = build_transitive_closure(&mut conn, CLOSURE_MAX_DEPTH).expect("closure");
    // Generated rows: (s1,NULL,ghost)@1 [dropped by NOT NULL], plus the
    // ghost-sourced seed (ghost,target_three,t3)@1 and (s1,target_three,t3)@2.
    assert_eq!(outcome.rows, 3);
    assert_eq!(
        closure_rows(&db),
        vec![
            "ghost\ttarget_three\tt3\t1".to_string(),
            "s1\ttarget_three\tt3\t2".to_string(),
        ]
    );
    let _ = fs::remove_dir_all(db.parent().expect("parent"));
}

#[test]
fn rebuilds_are_idempotent_and_shortest_distance_coexists_with_deeper_rows() {
    let db = temp_db("idem");
    let mut conn = cairn_store::schema::open_initialized(&db).expect("open");
    seed(
        &conn,
        &[("s1", "a"), ("t1", "b"), ("u1", "c"), ("v1", "d")],
        &[
            ("s1", Some("t1"), None, "calls"),
            ("t1", Some("u1"), None, "calls"),
            ("u1", Some("v1"), None, "calls"),
            ("s1", Some("v1"), None, "calls"),
        ],
    );
    let first = build_transitive_closure(&mut conn, CLOSURE_MAX_DEPTH).expect("first");
    let rows_first = closure_rows(&db);
    let second = build_transitive_closure(&mut conn, CLOSURE_MAX_DEPTH).expect("second");
    assert_eq!(first.rows, 7);
    assert_eq!(first.rows, second.rows);
    assert_eq!(rows_first, closure_rows(&db));
    // The direct edge keeps distance 1 alongside the distance-3 chain row.
    assert!(
        rows_first.contains(&"s1\td\tv1\t1".to_string()),
        "{rows_first:?}"
    );
    assert!(
        rows_first.contains(&"s1\td\tv1\t3".to_string()),
        "{rows_first:?}"
    );
    let _ = fs::remove_dir_all(db.parent().expect("parent"));
}

#[test]
fn empty_store_yields_no_rows() {
    let db = temp_db("empty");
    let mut conn = cairn_store::schema::open_initialized(&db).expect("open");
    let outcome = build_transitive_closure(&mut conn, CLOSURE_MAX_DEPTH).expect("closure");
    assert_eq!(outcome.rows, 0);
    assert_eq!(outcome.levels, 0);
    assert_eq!(closure_rows(&db), Vec::<String>::new());
    let _ = fs::remove_dir_all(db.parent().expect("parent"));
}
