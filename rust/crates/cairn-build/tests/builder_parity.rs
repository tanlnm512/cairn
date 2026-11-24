//! Builder contract: parse-order-independent output and stable counts.

use std::fs;
use std::path::{Path, PathBuf};

use cairn_build::builder::build_graph;
use cairn_store::dump::{DumpOptions, dump_connection};

fn temp_dir(tag: &str) -> PathBuf {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock")
        .as_nanos();
    let dir =
        std::env::temp_dir().join(format!("cairn-build-{tag}-{}-{nanos}", std::process::id()));
    fs::create_dir_all(dir.join("src").join("deep")).expect("temp dir");
    fs::create_dir_all(dir.join(".git")).expect("git marker");
    dir
}

fn write(dir: &Path, rel: &str, content: &str) {
    fs::write(dir.join(rel), content).expect("write fixture");
}

/// Build a deterministic multi-language corpus fixture; returns its root.
fn corpus(tag: &str) -> PathBuf {
    let dir = temp_dir(tag);
    write(
        &dir,
        "src/util.py",
        "\"\"\"Helpers.\"\"\"\n\ndef add(a, b):\n    return a + b\n\n\ndef spread(a, b, c):\n    return add(add(a, b), c)\n",
    );
    write(
        &dir,
        "src/deep/app.py",
        "from util import add\n\n\ndef main():\n    return add(1, 2)\n",
    );
    write(
        &dir,
        "src/worker.ts",
        "export function run(name: string): number {\n  return name.length;\n}\n",
    );
    write(
        &dir,
        "src/lib.rs",
        "pub fn compute(x: u64) -> u64 {\n    x + 1\n}\n",
    );
    // Skipped by the size cap, never indexed.
    write(
        &dir,
        "src/huge.py",
        &format!("x = 1\n{}", " ".repeat(1_000_001)),
    );
    dir
}

fn dump_tables(db: &Path, only: &[&str]) -> String {
    let conn = cairn_store::conn::open_read_only(db).expect("open store");
    let opts = DumpOptions {
        map_paths: vec![],
        only: Some(only.iter().map(|t| t.to_string()).collect()),
    };
    let (_, lines) = dump_connection(&conn, &opts).expect("dump");
    conn.close().expect("close");
    lines.join("\n")
}

#[test]
fn output_is_identical_across_parse_pool_sizes() {
    let dir = corpus("order");
    let db_a = dir.join("a.kg");
    let db_b = dir.join("b.kg");

    // 1 worker parses serially; 8 scrambles parse completion order on the
    // rayon pool. Insert order is restored by the stable sort, so the
    // structural tables must be byte-equal across the two runs.
    let (summary_a, _) = build_graph(
        dir.to_str().expect("ws"),
        db_a.to_str().expect("db"),
        None,
        Some(1),
    )
    .expect("serial build");
    let (summary_b, _) = build_graph(
        dir.to_str().expect("ws"),
        db_b.to_str().expect("db"),
        None,
        Some(8),
    )
    .expect("parallel build");

    assert_eq!(summary_a.files, 4); // src/huge.py is size-capped, not indexed
    assert_eq!(summary_a.skipped, 1);
    assert_eq!(summary_a.files, summary_b.files);
    assert_eq!(summary_a.symbols, summary_b.symbols);
    assert_eq!(summary_a.edges, summary_b.edges);
    assert_eq!(summary_a.resolution, summary_b.resolution);
    assert_eq!(summary_a.parse_errors, 0);

    let tables = ["symbols", "edges", "imports", "rationale"];
    for table in tables {
        let a = dump_tables(&db_a, &[table]);
        let b = dump_tables(&db_b, &[table]);
        assert_eq!(a, b, "table {table} diverged across parse orders");
    }

    // The imports edge from app.py's `from util import add` resolves to
    // util.py's module symbol.
    let edges = dump_tables(&db_a, &["edges"]);
    assert!(
        edges.contains("\timports\t"),
        "expected materialized imports edges:\n{edges}"
    );
    // add/spread/main resolved; the ts/rust files contribute their own edges.
    assert!(edges.contains("exact"), "expected resolved edges:\n{edges}");

    let _ = fs::remove_dir_all(&dir);
}

/// A store holding a tiny graph; the seeded rows both refusal paths rely on.
fn seed_populated_store(db: &Path) {
    let conn = cairn_store::schema::open_initialized(db).expect("open");
    conn.execute_batch(
        "INSERT INTO repos (id, name, path) VALUES ('r', 'r', '.');
         INSERT INTO files (id, repo_id, path, language) VALUES ('f', 'r', 'x.py', 'python');
         INSERT INTO symbols (id, file_id, name, kind) VALUES ('s', 'f', 'x', 'function');",
    )
    .expect("seed");
}

#[test]
fn all_skipped_scan_refuses_to_replace_a_populated_store() {
    let dir = temp_dir("empty");
    // One source file, filtered out by gitignore: skip_count > 0, files == 0.
    fs::write(dir.join(".gitignore"), "skipme.py\n").expect("gitignore");
    fs::write(dir.join("skipme.py"), "x = 1\n").expect("skipped file");
    let db = dir.join("store.kg");
    seed_populated_store(&db);
    let err = build_graph(
        dir.to_str().expect("ws"),
        db.to_str().expect("db"),
        None,
        None,
    )
    .expect_err("must refuse");
    assert!(err.contains("refusing to replace"), "{err}");
    let _ = fs::remove_dir_all(&dir);
}

/// A populated store must not keep serving its graph when the scanned
/// workspace holds no repository at all.
#[test]
fn zero_repo_scan_refuses_to_keep_serving_a_populated_store() {
    let dir = temp_dir("norepo");
    let db = dir.join("store.kg");
    seed_populated_store(&db);
    let missing = dir.join("missing").to_string_lossy().into_owned();
    let err = build_graph(&missing, db.to_str().expect("db"), None, None).expect_err("must refuse");
    assert!(err.contains("no repositories discovered"), "{err}");
    assert!(
        err.contains(&missing),
        "error must name the workspace: {err}"
    );

    // A store that does not exist yet still builds nothing quietly (init on
    // an empty workspace).
    let fresh = dir.join("fresh.kg");
    let (summary, _) =
        build_graph(&missing, fresh.to_str().expect("db"), None, None).expect("empty build");
    assert_eq!(summary.files, 0);
    assert!(!fresh.exists());
    let _ = fs::remove_dir_all(&dir);
}

/// A file that turns unreadable between classify and hash is a skip row,
/// never an aborted build.
#[test]
#[cfg(unix)]
fn unreadable_source_file_becomes_a_skip_row_not_a_failed_build() {
    let dir = temp_dir("unreadable");
    write(&dir, "src/app.py", "def main():\n    return 1\n");
    let secret = dir.join("src/secret.py");
    fs::write(&secret, "def hidden():\n    return 1\n").expect("write");
    {
        use std::os::unix::fs::PermissionsExt as _;
        fs::set_permissions(&secret, fs::Permissions::from_mode(0o000)).expect("chmod");
    }
    // Root and mode-ignoring filesystems still read the file; the failure
    // path cannot be exercised there.
    if fs::read(&secret).is_ok() {
        let _ = fs::remove_dir_all(&dir);
        return;
    }
    let db = dir.join("store.kg");
    let (summary, _) = build_graph(
        dir.to_str().expect("ws"),
        db.to_str().expect("db"),
        None,
        Some(1),
    )
    .expect("build");
    assert_eq!(summary.files, 1);
    assert_eq!(
        count(
            &db,
            "SELECT COUNT(*) FROM skipped_files WHERE reason = 'stat_error' \
             AND path LIKE '%secret.py'"
        ),
        1
    );
    let _ = fs::remove_dir_all(&dir);
}

/// `include` reaches files inside default skip dirs, per classify_file's
/// include-override contract.
#[test]
fn include_pattern_rescues_files_under_skip_dirs() {
    let dir = temp_dir("include");
    fs::create_dir_all(dir.join("vendor")).expect("vendor dir");
    write(&dir, "vendor/lib.py", "def vendored():\n    return 1\n");
    write(&dir, "cairn.json", r#"{"include": ["vendor/**"]}"#);
    let db = dir.join("store.kg");
    build_graph(
        dir.to_str().expect("ws"),
        db.to_str().expect("db"),
        None,
        Some(1),
    )
    .expect("build");
    assert_eq!(
        count(
            &db,
            "SELECT COUNT(*) FROM files WHERE path = 'vendor/lib.py'"
        ),
        1
    );
    let _ = fs::remove_dir_all(&dir);
}

/// A submodule (.git file) must not hide the root repo it belongs to.
#[test]
fn submodule_checkout_keeps_the_root_repo_indexed() {
    let dir = temp_dir("submodule");
    write(&dir, "src/app.py", "def main():\n    return 1\n");
    fs::create_dir_all(dir.join("vendored")).expect("submodule dir");
    fs::write(
        dir.join("vendored").join(".git"),
        "gitdir: ../.git/modules/vendored\n",
    )
    .expect("submodule pointer");
    let ws = dir.to_str().expect("ws");
    assert!(cairn_build::scanner::is_single_repo_workspace(ws));

    let db = dir.join("store.kg");
    let (summary, _) = build_graph(ws, db.to_str().expect("db"), None, Some(1)).expect("build");
    assert_eq!(summary.repos, 1);
    assert_eq!(
        count(&db, "SELECT COUNT(*) FROM files WHERE path = 'src/app.py'"),
        1
    );
    let _ = fs::remove_dir_all(&dir);
}

/// Two-repo workspace (`alpha`, `beta`), each with one python module.
fn two_repo_workspace(tag: &str) -> PathBuf {
    let dir = temp_dir(tag);
    for (repo, body) in [
        (
            "alpha",
            "def a():\n    return b()\n\n\ndef b():\n    return 1\n",
        ),
        ("beta", "def c():\n    return 2\n"),
    ] {
        fs::create_dir_all(dir.join(repo).join(".git")).expect("repo marker");
        write(&dir, &format!("{repo}/mod.py"), body);
    }
    dir
}

fn count(db: &Path, sql: &str) -> i64 {
    let conn = cairn_store::conn::open_read_only(db).expect("open store");
    conn.query_row(sql, [], |r| r.get(0)).expect("count")
}

/// A store filed under the empty repo id (a build from a relative workspace)
/// keeps working for absolute-workspace callers: drift is seen, nothing is
/// duplicated.
#[test]
fn legacy_root_repo_store_syncs_without_duplicating() {
    let dir = temp_dir("legacy-root");
    write(&dir, "a.py", "def one():\n    return 1\n");
    write(&dir, "b.py", "def two():\n    return 2\n");
    let ws = dir.to_str().expect("ws");
    let db = dir.join("store.kg");
    build_graph(ws, db.to_str().expect("db"), None, Some(1)).expect("build");
    let mut conn = cairn_store::schema::open_initialized(&db).expect("store");
    conn.execute_batch(
        "INSERT INTO repos (id, name, path) VALUES ('', '', '.');
         UPDATE files SET repo_id = '';
         UPDATE parse_errors SET repo_id = '';
         DELETE FROM repos WHERE id <> '';",
    )
    .expect("legacy layout");
    write(
        &dir,
        "b.py",
        "def two():\n    return 2\n\n\ndef three():\n    return 3\n",
    );
    write(&dir, "c.py", "def four():\n    return 4\n");

    let changed = cairn_build::incremental::detect_changed(&conn, ws);
    assert_eq!(
        changed.len(),
        2,
        "the drifted and the new file: {changed:?}"
    );
    let result = cairn_build::incremental::reindex_paths(&mut conn, ws, &changed);
    assert!(result.errors.is_empty(), "{:?}", result.errors);
    drop(conn);

    assert_eq!(count(&db, "SELECT COUNT(*) FROM files"), 3);
    assert_eq!(
        count(&db, "SELECT COUNT(*) FROM files WHERE repo_id = ''"),
        3
    );
    assert_eq!(count(&db, "SELECT COUNT(*) FROM repos"), 1);
    assert_eq!(
        count(&db, "SELECT COUNT(*) FROM symbols WHERE name = 'two'"),
        1
    );
    assert_eq!(
        count(&db, "SELECT COUNT(*) FROM symbols WHERE name = 'four'"),
        1
    );
    let _ = fs::remove_dir_all(&dir);
}

/// Re-file a built store the way a relative build did: the root repository
/// under the empty id, each nested one under its `/`-prefixed path.
fn legacy_layout(dir: &Path) {
    let root = dir.file_name().and_then(|n| n.to_str()).expect("dir name");
    let mut conn = cairn_store::schema::open_initialized(&dir.join("store.kg")).expect("store");
    let ids: Vec<String> = {
        let mut stmt = conn.prepare("SELECT id FROM repos").expect("stmt");
        let rows = stmt.query_map([], |r| r.get::<_, String>(0)).expect("rows");
        rows.map(Result::unwrap).collect()
    };
    let tx = conn.transaction().expect("tx");
    tx.execute(
        "INSERT INTO repos (id, name, path) VALUES ('', '', '.')",
        [],
    )
    .expect("legacy root row");
    for id in ids {
        let legacy = match id.strip_prefix(&format!("{root}/")) {
            Some(rest) => format!("/{rest}"),
            None if id == root => String::new(),
            None => continue,
        };
        tx.execute(
            "INSERT OR IGNORE INTO repos (id, name, path)
             SELECT ?2, ?2, path FROM repos WHERE id = ?1",
            rusqlite::params![id, legacy],
        )
        .expect("legacy repo row");
        for table in ["files", "parse_errors", "skipped_files"] {
            tx.execute(
                &format!("UPDATE {table} SET repo_id = ?2 WHERE repo_id = ?1"),
                rusqlite::params![id, legacy],
            )
            .expect("refile");
        }
        tx.execute("DELETE FROM repos WHERE id = ?1", [&id])
            .expect("scanned repo row");
    }
    tx.commit().expect("commit");
}

/// Sorted `(repo_id, path)` rows of the files table.
fn filed_rows(db: &Path) -> Vec<(String, String)> {
    let conn = cairn_store::conn::open_read_only(db).expect("open store");
    let mut stmt = conn
        .prepare("SELECT repo_id, path FROM files ORDER BY 1, 2")
        .expect("stmt");
    let rows = stmt
        .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))
        .expect("rows");
    rows.map(Result::unwrap).collect()
}

/// A legacy store's nested repository keeps its own id: an update leaves an
/// unchanged workspace alone and reindexes a nested edit in place.
#[test]
fn legacy_store_keeps_nested_repositories_under_their_own_ids() {
    let dir = temp_dir("legacy-nested");
    write(&dir, "a.py", "def alpha():\n    return 1\n");
    write(&dir, "n.py", "def top():\n    return 2\n");
    fs::create_dir_all(dir.join("vendored")).expect("nested repo dir");
    write(&dir, "vendored/n.py", "def nested():\n    return 3\n");
    fs::write(
        dir.join("vendored").join(".git"),
        "gitdir: ../.git/modules/vendored\n",
    )
    .expect("submodule pointer");
    write(&dir, "cairn.json", r#"{"include_nested_repos": true}"#);
    let ws = dir.to_str().expect("ws");
    let db = dir.join("store.kg");
    let db_str = db.to_str().expect("db");
    build_graph(ws, db_str, None, Some(1)).expect("build");
    legacy_layout(&dir);
    let filed = vec![
        (String::new(), "a.py".to_string()),
        (String::new(), "n.py".to_string()),
        ("/vendored".to_string(), "n.py".to_string()),
    ];
    assert_eq!(filed_rows(&db), filed, "the legacy layout");

    let idle =
        cairn_build::incremental::incremental_update(ws, db_str, None, None).expect("update");
    assert_eq!(
        (idle.files_reindexed, idle.files_deleted),
        (0, 0),
        "nothing changed on disk"
    );
    assert_eq!(filed_rows(&db), filed);

    write(
        &dir,
        "vendored/n.py",
        "def nested():\n    return 3\n\n\ndef inner():\n    return 4\n",
    );
    let mut conn = cairn_store::schema::open_initialized(&db).expect("store");
    let changed = cairn_build::incremental::detect_changed(&conn, ws);
    assert_eq!(
        changed,
        [dir.join("vendored/n.py").to_string_lossy().into_owned()],
        "only the nested edit"
    );
    let result = cairn_build::incremental::reindex_paths(&mut conn, ws, &changed);
    assert!(result.errors.is_empty(), "{:?}", result.errors);
    assert_eq!((result.reindexed, result.deleted), (1, 0));
    assert_eq!(filed_rows(&db), filed);
    drop(conn);
    assert_eq!(
        cairn_build::scanner::resolve_file_path(ws, "/vendored", "n.py"),
        dir.join("vendored").join("n.py"),
        "a stored legacy id resolves into its own repository"
    );
    assert_eq!(
        count(
            &db,
            "SELECT COUNT(*) FROM symbols WHERE name IN ('alpha', 'top', 'nested', 'inner')"
        ),
        4,
        "each function indexed once, under its own repository"
    );
    let _ = fs::remove_dir_all(&dir);
}

/// A second filing of every file and symbol under the empty repo id.
fn second_filing_under_empty_id(db: &Path) {
    let conn = cairn_store::schema::open_initialized(db).expect("store");
    conn.execute_batch(
        "INSERT INTO repos (id, name, path) VALUES ('', '', '.');
         INSERT INTO files (id, repo_id, path, language, hash, line_count, indexed_at, size, mtime)
             SELECT 'c' || id, '', path, language, hash, line_count, indexed_at, size, mtime
             FROM files;
         INSERT INTO symbols (id, file_id, name, kind, line_start, line_end)
             SELECT 'c' || id, 'c' || file_id, name, kind, line_start, line_end FROM symbols;",
    )
    .expect("second filing");
}

/// `--repo` on a store holding the repository under the empty id, alone or
/// beside the directory name, leaves a single filing under the directory name.
#[test]
fn repo_build_replaces_the_rows_of_a_legacy_root_repository() {
    for (tag, twice_filed) in [
        ("legacy-repo-build", false),
        ("twice-filed-repo-build", true),
    ] {
        let dir = temp_dir(tag);
        write(&dir, "a.py", "def one():\n    return 1\n");
        write(&dir, "b.py", "def two():\n    return 2\n");
        let ws = dir.to_str().expect("ws");
        let db = dir.join("store.kg");
        let db_str = db.to_str().expect("db");
        build_graph(ws, db_str, None, Some(1)).expect("build");
        if twice_filed {
            second_filing_under_empty_id(&db);
        } else {
            legacy_layout(&dir);
        }
        let repo = dir.file_name().and_then(|n| n.to_str()).expect("dir name");

        let (summary, _) = build_graph(ws, db_str, Some(repo), Some(1)).expect("repo build");

        assert_eq!((summary.files, summary.parse_errors), (2, 0));
        assert_eq!(
            filed_rows(&db),
            vec![
                (repo.to_string(), "a.py".to_string()),
                (repo.to_string(), "b.py".to_string())
            ],
            "twice filed: {twice_filed}"
        );
        assert_eq!(count(&db, "SELECT COUNT(*) FROM repos"), 1);
        assert_eq!(
            count(
                &db,
                "SELECT COUNT(*) FROM symbols WHERE name IN ('one', 'two')"
            ),
            2
        );
        let _ = fs::remove_dir_all(&dir);
    }
}

/// A store holding one repository under two ids keeps both copies current.
#[test]
fn store_with_two_filings_of_a_repository_refreshes_both() {
    let dir = temp_dir("twice-filed");
    write(&dir, "a.py", "def one():\n    return 1\n");
    write(&dir, "b.py", "def two():\n    return 2\n");
    let ws = dir.to_str().expect("ws");
    let db = dir.join("store.kg");
    let db_str = db.to_str().expect("db");
    build_graph(ws, db_str, None, Some(1)).expect("build");
    second_filing_under_empty_id(&db);

    write(
        &dir,
        "b.py",
        "def two():\n    return 2\n\n\ndef three():\n    return 3\n",
    );
    let mut conn = cairn_store::schema::open_initialized(&db).expect("store");
    let changed = cairn_build::incremental::detect_changed(&conn, ws);
    assert_eq!(changed.len(), 1, "{changed:?}");
    let result = cairn_build::incremental::reindex_paths(&mut conn, ws, &changed);
    assert!(result.errors.is_empty(), "{:?}", result.errors);
    assert_eq!((result.reindexed, result.deleted), (1, 0));
    drop(conn);
    assert_eq!(
        count(
            &db,
            "SELECT COUNT(*) FROM symbols s JOIN files f ON s.file_id = f.id \
             WHERE f.path = 'b.py' AND f.repo_id = '' AND s.name = 'three'"
        ),
        1,
        "the legacy copy must see the edit"
    );
    assert_eq!(
        count(
            &db,
            "SELECT COUNT(*) FROM symbols s JOIN files f ON s.file_id = f.id \
             WHERE f.path = 'b.py' AND f.repo_id <> '' AND s.name = 'three'"
        ),
        1,
        "the copy under the directory name must see the edit"
    );

    fs::remove_file(dir.join("a.py")).expect("delete a.py");
    let mut conn = cairn_store::schema::open_initialized(&db).expect("store");
    let changed = cairn_build::incremental::detect_changed(&conn, ws);
    assert_eq!(changed.len(), 1, "{changed:?}");
    let result = cairn_build::incremental::reindex_paths(&mut conn, ws, &changed);
    assert_eq!((result.reindexed, result.deleted), (0, 1));
    drop(conn);
    assert_eq!(
        count(&db, "SELECT COUNT(*) FROM files WHERE path = 'a.py'"),
        0,
        "the deletion must reach both copies"
    );
    assert_eq!(
        count(&db, "SELECT COUNT(*) FROM symbols WHERE name = 'one'"),
        0
    );
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn full_rebuild_replaces_a_populated_store() {
    let dir = two_repo_workspace("rebuild");
    let ws = dir.to_str().expect("ws");
    let db = dir.join("store.kg");
    let db_str = db.to_str().expect("db");
    build_graph(ws, db_str, None, Some(1)).expect("first build");
    let graph_tables = ["symbols", "edges", "imports"];
    let first = dump_tables(&db, &graph_tables);
    {
        let conn = cairn_store::schema::open_initialized(&db).expect("open");
        conn.execute(
            "INSERT INTO build_runs (kind, started_at) VALUES ('build', 1.0)",
            [],
        )
        .expect("seed telemetry");
    }

    let (rebuilt, _) =
        build_graph(ws, db_str, None, Some(1)).expect("rebuild over a populated store");
    assert_eq!(rebuilt.parse_errors, 0);
    assert_eq!(dump_tables(&db, &graph_tables), first);
    assert_eq!(count(&db, "SELECT COUNT(*) FROM build_runs"), 1);

    fs::remove_dir_all(dir.join("beta")).expect("drop repo");
    build_graph(ws, db_str, None, Some(1)).expect("rebuild without beta");
    assert_eq!(
        count(&db, "SELECT COUNT(*) FROM files WHERE repo_id = 'beta'"),
        0
    );
    assert!(!dir.join("store.kg.tmp").exists());
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn repo_rebuild_replaces_only_that_repo_in_place() {
    let dir = two_repo_workspace("repo-rebuild");
    let ws = dir.to_str().expect("ws");
    let db = dir.join("store.kg");
    let db_str = db.to_str().expect("db");
    build_graph(ws, db_str, None, Some(1)).expect("full build");
    let beta_before = count(&db, "SELECT COUNT(*) FROM files WHERE repo_id = 'beta'");

    write(&dir, "alpha/extra.py", "def d():\n    return 3\n");
    let (summary, _) = build_graph(ws, db_str, Some("alpha"), Some(1)).expect("repo rebuild");
    assert_eq!(summary.files, 2);
    assert_eq!(
        count(&db, "SELECT COUNT(*) FROM files WHERE repo_id = 'alpha'"),
        2
    );
    assert_eq!(
        count(&db, "SELECT COUNT(*) FROM files WHERE repo_id = 'beta'"),
        beta_before
    );
    assert_eq!(
        count(
            &db,
            "SELECT COUNT(*) FROM edges e JOIN symbols s ON e.target_id = s.id \
             WHERE e.kind = 'calls' AND e.resolution = 'exact' AND s.name = 'b'"
        ),
        1
    );
    assert_eq!(count(&db, "SELECT COUNT(*) FROM repo_build_state"), 0);
    let _ = fs::remove_dir_all(&dir);
}

/// A repo-filtered rebuild re-resolves the other repos' edges that
/// clear_repo demoted to bare names.
#[test]
fn repo_rebuild_repairs_other_repos_incoming_edges() {
    let dir = temp_dir("xrepo-repair");
    fs::create_dir_all(dir.join("alpha").join(".git")).expect("alpha marker");
    fs::create_dir_all(dir.join("beta").join(".git")).expect("beta marker");
    write(&dir, "alpha/mod.py", "def shared():\n    return 1\n");
    write(&dir, "beta/mod.py", "def c():\n    return shared()\n");
    let ws = dir.to_str().expect("ws");
    let db = dir.join("store.kg");
    let db_str = db.to_str().expect("db");
    build_graph(ws, db_str, None, Some(1)).expect("full build");

    write(&dir, "alpha/extra.py", "def other():\n    return 2\n");
    build_graph(ws, db_str, Some("alpha"), Some(1)).expect("repo rebuild");

    let exact_cross_repo = "SELECT COUNT(*) FROM edges e \
         JOIN symbols src ON e.source_id = src.id \
         JOIN files sf ON src.file_id = sf.id \
         JOIN symbols tgt ON e.target_id = tgt.id \
         JOIN files tf ON tgt.file_id = tf.id \
         WHERE sf.repo_id = 'beta' AND tf.repo_id = 'alpha' \
         AND e.kind = 'calls' AND e.resolution = 'exact'";
    assert_eq!(count(&db, exact_cross_repo), 1);
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn repo_rebuild_never_reuses_ids_of_the_rows_it_replaced() {
    let dir = two_repo_workspace("repo-ids");
    let ws = dir.to_str().expect("ws");
    let db = dir.join("store.kg");
    let db_str = db.to_str().expect("db");
    build_graph(ws, db_str, None, Some(1)).expect("full build");
    let top_beta_symbol: String = {
        let conn = cairn_store::schema::open_initialized(&db).expect("open");
        let id: String = conn
            .query_row(
                "SELECT MAX(s.id) FROM symbols s JOIN files f ON s.file_id = f.id \
                 WHERE f.repo_id = 'beta'",
                [],
                |r| r.get(0),
            )
            .expect("top beta symbol");
        conn.execute(
            "INSERT INTO transitive_edges (source_id, target_name, distance) VALUES (?, 'x', 1)",
            [&id],
        )
        .expect("seed closure row");
        id
    };

    build_graph(ws, db_str, Some("beta"), Some(1)).expect("repo rebuild");
    assert_eq!(
        count(
            &db,
            "SELECT COUNT(*) FROM transitive_edges t JOIN symbols s ON s.id = t.source_id"
        ),
        0,
        "stale closure row for {top_beta_symbol} resolves to a live symbol"
    );
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn edge_source_is_the_enclosing_same_named_owner() {
    let dir = temp_dir("owner");
    write(
        &dir,
        "src/jobs.py",
        concat!(
            "class A:\n    def run(self):\n        foo()\n\n",
            "class B:\n    def run(self):\n        bar()\n\n",
            "try:\n    class Impl:\n        @property\n        def value(self):\n            one()\n\n",
            "        @value.setter\n        def value(self, v):\n            two()\n",
            "except ImportError:\n    class Impl:\n        def run(self):\n            baz()\n",
            "\ndef combo():\n    @cache\n    def combo():\n        pass\n    return combo\n",
            "\ndef m():\n    @route\n    class C:\n        def m(self):\n            pass\n",
        ),
    );
    let db = dir.join("store.kg");
    build_graph(
        dir.to_str().expect("ws"),
        db.to_str().expect("db"),
        None,
        None,
    )
    .expect("build");
    let conn = cairn_store::conn::open_read_only(&db).expect("open store");
    let owner_line = |kind: &str, target: &str| -> i64 {
        conn.query_row(
            "SELECT src.line_start FROM edges e JOIN symbols src ON e.source_id = src.id
             LEFT JOIN symbols tgt ON e.kind = 'contains' AND e.target_id = tgt.id
             WHERE e.kind = ?1 AND COALESCE(CAST(tgt.line_start AS TEXT), e.target_name) = ?2",
            [kind, target],
            |r| r.get(0),
        )
        .unwrap_or_else(|e| panic!("{kind} {target}: {e}"))
    };
    let calls: Vec<i64> = ["foo", "bar", "one", "two", "baz"]
        .iter()
        .map(|t| owner_line("calls", t))
        .collect();
    assert_eq!(calls, vec![2, 6, 12, 16, 20]);
    assert_eq!(owner_line("contains", "20"), 19);
    assert_eq!(owner_line("decorates", "setter"), 16);
    // A decorated definition nested in a same-named outer one owns its
    // decorator, not the outer.
    assert_eq!(owner_line("decorates", "cache"), 25);
    // A decorator above a differently named definition binds to the
    // same-named definition that appears later, not the enclosing one.
    let route = conn
        .prepare(
            "SELECT src.line_start FROM edges e JOIN symbols src ON e.source_id = src.id
             WHERE e.kind = 'decorates' AND e.target_name = 'route' ORDER BY src.line_start",
        )
        .expect("route stmt")
        .query_map([], |r| r.get(0))
        .expect("route rows")
        .collect::<Result<Vec<i64>, _>>()
        .expect("route ids");
    assert_eq!(route, vec![31, 32]);
    drop(conn);
    let _ = fs::remove_dir_all(&dir);
}
