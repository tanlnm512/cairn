//! Change-detection contract of `incremental_update` over a real git repo.

use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

use cairn_build::builder::build_graph;
use cairn_build::incremental::incremental_update;
use rusqlite::Connection;

fn git(repo: &Path, args: &[&str]) {
    let status = Command::new("git")
        .args(args)
        .current_dir(repo)
        .env("GIT_AUTHOR_NAME", "t")
        .env("GIT_AUTHOR_EMAIL", "t@example.com")
        .env("GIT_COMMITTER_NAME", "t")
        .env("GIT_COMMITTER_EMAIL", "t@example.com")
        .status()
        .expect("run git");
    assert!(status.success(), "git {args:?} failed");
}

/// A git repo whose first commit holds a.py + b.py, plus its built store.
fn committed_repo(tag: &str) -> (PathBuf, String) {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock")
        .as_nanos();
    let root =
        std::env::temp_dir().join(format!("cairn-detect-{tag}-{}-{nanos}", std::process::id()));
    let repo = root.join("gitrepo");
    fs::create_dir_all(&repo).expect("repo dir");
    git(&repo, &["init", "-q"]);
    fs::write(repo.join("a.py"), "def alpha():\n    return 1\n").expect("write a");
    fs::write(repo.join("b.py"), "def beta():\n    return 2\n").expect("write b");
    git(&repo, &["add", "."]);
    git(&repo, &["commit", "-qm", "first"]);
    let db = root.join("store.kg").to_string_lossy().into_owned();
    build_graph(repo.to_str().expect("ws"), &db, None, Some(1)).expect("build");
    (repo, db)
}

fn has_symbol(db: &str, name: &str) -> bool {
    let conn = Connection::open(db).expect("open store");
    conn.query_row("SELECT 1 FROM symbols WHERE name = ?", [name], |_| Ok(()))
        .is_ok()
}

#[test]
fn update_detects_changes_committed_since_last_index() {
    let (repo, db) = committed_repo("committed");
    fs::write(repo.join("a.py"), "def alpha_two():\n    return 2\n").expect("edit");
    git(&repo, &["commit", "-qam", "second"]);

    let summary = incremental_update(repo.to_str().expect("ws"), &db, None, None).expect("update");

    assert!(summary.errors.is_empty(), "{:?}", summary.errors);
    assert_eq!(summary.files_reindexed, 1);
    assert!(has_symbol(&db, "alpha_two"));
}

#[test]
fn range_update_reindexes_non_ascii_paths() {
    let (repo, db) = committed_repo("unicode");
    fs::write(repo.join("café.py"), "def gamma():\n    return 3\n").expect("write");
    git(&repo, &["add", "."]);
    git(&repo, &["commit", "-qm", "unicode"]);

    let summary = incremental_update(repo.to_str().expect("ws"), &db, None, Some("HEAD~1..HEAD"))
        .expect("update");

    assert!(summary.errors.is_empty(), "{:?}", summary.errors);
    assert_eq!(summary.files_reindexed, 1);
    assert!(has_symbol(&db, "gamma"));
}

#[test]
fn range_git_failure_surfaces_an_error_and_reindexes_nothing() {
    let (repo, db) = committed_repo("badrange");
    fs::write(repo.join("a.py"), "def alpha():\n    return 33\n").expect("edit");

    let summary = incremental_update(
        repo.to_str().expect("ws"),
        &db,
        None,
        Some("no-such-ref..HEAD"),
    )
    .expect("update");

    assert_eq!(summary.files_reindexed, 0);
    assert!(
        summary.errors.iter().any(|e| e.contains("no-such-ref")),
        "{:?}",
        summary.errors
    );
}

/// Age every stored mtime to the epoch: content-identical stat drift.
fn age_stored_mtimes(db: &str) {
    let conn = Connection::open(db).expect("open store");
    conn.execute("UPDATE files SET mtime = 0.0", [])
        .expect("age mtimes");
}

#[test]
fn mtime_only_drift_skips_reindex_when_hash_matches() {
    let (repo, db) = committed_repo("touchonly");
    age_stored_mtimes(&db);

    let summary = incremental_update(repo.to_str().expect("ws"), &db, None, None).expect("update");

    assert!(summary.errors.is_empty(), "{:?}", summary.errors);
    assert_eq!(summary.files_reindexed, 0);
}

#[test]
fn mtime_only_drift_does_not_trigger_freshness_repair() {
    let (repo, db) = committed_repo("freshprobe");
    let ws = repo.to_str().expect("ws").to_string();
    age_stored_mtimes(&db);

    let conn = Connection::open(&db).expect("open store");
    let drifted = cairn_build::incremental::detect_changed(&conn, &ws);
    assert!(drifted.is_empty(), "{drifted:?}");

    let stored: f64 = conn
        .query_row("SELECT mtime FROM files WHERE path = 'a.py'", [], |r| {
            r.get(0)
        })
        .expect("stored mtime");
    assert!(
        stored > 0.0,
        "stored mtime must converge to disk after a hash match"
    );
}

/// A repo added after the last build has no files-table rows; its files are
/// new, not invisible.
#[test]
fn detect_changed_enumerates_a_newly_added_repo() {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock")
        .as_nanos();
    let root = std::env::temp_dir().join(format!(
        "cairn-detect-newrepo-{}-{nanos}",
        std::process::id()
    ));
    for repo in ["alpha", "beta"] {
        fs::create_dir_all(root.join(repo).join(".git")).expect("repo marker");
        fs::write(root.join(repo).join("mod.py"), "def one():\n    return 1\n").expect("source");
    }
    let ws = root.to_str().expect("ws").to_string();
    let db = root.join("store.kg").to_string_lossy().into_owned();
    build_graph(&ws, &db, None, Some(1)).expect("build");

    fs::create_dir_all(root.join("gamma").join(".git")).expect("repo marker");
    fs::write(
        root.join("gamma").join("mod.py"),
        "def two():\n    return 2\n",
    )
    .expect("source");

    let conn = Connection::open(&db).expect("open store");
    let drifted = cairn_build::incremental::detect_changed(&conn, &ws);
    assert!(
        drifted.iter().any(|p| p.contains("gamma")),
        "a new repo's files must surface as drift: {drifted:?}"
    );
    let _ = fs::remove_dir_all(&root);
}
