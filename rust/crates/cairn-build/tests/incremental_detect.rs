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
