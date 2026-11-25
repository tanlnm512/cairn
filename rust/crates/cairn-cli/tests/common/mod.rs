//! Shared harness for cairn-cli integration tests: CLI runner and store fixture.
#![allow(dead_code)]

use std::path::{Path, PathBuf};
use std::process::Command;

/// Run the cairn binary with HOME/CAIRN_HOME at `home` and telemetry on; returns (exit ok, stdout+stderr).
pub fn cairn(
    home: &Path,
    workspace: Option<&Path>,
    cwd: Option<&Path>,
    args: &[&str],
) -> (bool, String) {
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_cairn"));
    cmd.args(args)
        .env("HOME", home)
        .env("CAIRN_HOME", home)
        .env("CAIRN_TELEMETRY", "on");
    if let Some(cwd) = cwd {
        cmd.current_dir(cwd);
    }
    match workspace {
        Some(ws) => {
            cmd.env("CAIRN_WORKSPACE", ws);
        }
        None => {
            cmd.env_remove("CAIRN_WORKSPACE");
        }
    }
    let out = cmd.output().expect("spawn cairn");
    let mut text = String::from_utf8_lossy(&out.stdout).into_owned();
    text.push_str(&String::from_utf8_lossy(&out.stderr));
    (out.status.success(), text)
}

/// A temp home with a one-file python repo at `ws/repo` and its store at `ws/store.kg`.
pub struct Fixture {
    pub home: PathBuf,
    pub ws: PathBuf,
    pub repo: PathBuf,
    pub db: String,
}

impl Fixture {
    /// Create the fixture directories and source file without building.
    pub fn new(tag: &str) -> Fixture {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        // Canonicalize the temp root: update --file strips the canonicalized
        // abs path against the discovered repo root.
        let home = cairn_store::paths::resolve_path(&std::env::temp_dir())
            .join(format!("cairn-{tag}-{}-{nanos}", std::process::id()));
        let ws = home.join("ws");
        let repo = ws.join("repo");
        std::fs::create_dir_all(repo.join(".git")).expect("repo marker");
        std::fs::write(repo.join("mod.py"), "def one():\n    return 1\n").expect("source");
        let db = ws.join("store.kg");
        Fixture {
            home,
            ws,
            repo,
            db: db.to_str().expect("utf8 db path").to_string(),
        }
    }

    /// Run `cairn build` over the fixture workspace; asserts success.
    pub fn build(&self) {
        let (ok, out) = cairn(
            &self.home,
            None,
            None,
            &[
                "build",
                "--workspace",
                self.ws.to_str().expect("utf8 ws"),
                "--db",
                &self.db,
            ],
        );
        assert!(ok, "build failed: {out}");
    }

    /// Run `cairn build` from inside the repository with no workspace given; returns (exit ok, output).
    pub fn build_from_repo(&self) -> (bool, String) {
        cairn(
            &self.home,
            None,
            Some(&self.repo),
            &["build", "--db", &self.db],
        )
    }

    /// The repository ids the files table is filed under.
    pub fn file_repo_ids(&self) -> Vec<String> {
        let conn = rusqlite::Connection::open(&self.db).expect("open store");
        let mut stmt = conn
            .prepare("SELECT DISTINCT repo_id FROM files ORDER BY repo_id")
            .expect("prepare");
        stmt.query_map([], |r| r.get(0))
            .expect("query")
            .map(|row| row.expect("row"))
            .collect()
    }

    /// Run `cairn update --file <path>` against the fixture store.
    pub fn update_file(&self, file: &Path) -> (bool, String) {
        cairn(
            &self.home,
            Some(&self.ws),
            None,
            &[
                "update",
                "--file",
                file.to_str().expect("utf8 file"),
                "--workspace",
                self.ws.to_str().expect("utf8 ws"),
                "--db",
                &self.db,
            ],
        )
    }

    /// `(kind, files)` per build_runs row, newest first.
    pub fn build_runs(&self) -> Vec<(String, Option<i64>)> {
        let conn = rusqlite::Connection::open(&self.db).expect("open store");
        let mut stmt = conn
            .prepare("SELECT kind, files FROM build_runs ORDER BY started_at DESC")
            .expect("prepare");
        stmt.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))
            .expect("query")
            .map(|row| row.expect("row"))
            .collect()
    }

    /// `(tool, status)` per tool_metrics row, oldest first.
    pub fn tool_metrics(&self) -> Vec<(String, String)> {
        let conn = rusqlite::Connection::open(&self.db).expect("open store");
        let mut stmt = conn
            .prepare("SELECT tool_name, status FROM tool_metrics ORDER BY rowid")
            .expect("prepare");
        stmt.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))
            .expect("query")
            .map(|row| row.expect("row"))
            .collect()
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.home);
    }
}
