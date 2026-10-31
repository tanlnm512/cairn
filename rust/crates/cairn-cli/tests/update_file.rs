//! `cairn update --file` warns without claiming a reindex on a zero-change path.

use std::path::Path;
use std::process::Command;

fn cairn(home: &Path, workspace: Option<&Path>, args: &[&str]) -> (bool, String) {
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_cairn"));
    cmd.args(args).env("HOME", home).env("CAIRN_HOME", home);
    match workspace {
        Some(ws) => {
            cmd.env("CAIRN_WORKSPACE", ws);
        }
        None => {
            cmd.env_remove("CAIRN_WORKSPACE");
        }
    }
    let out = cmd.output().expect("spawn cairn");
    (
        out.status.success(),
        String::from_utf8_lossy(&out.stdout).into_owned(),
    )
}

#[test]
fn update_file_zero_change_warns_without_a_success_line() {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .expect("clock")
        .as_nanos();
    // update --file strips the canonicalized abs path against the discovered
    // repo root; resolve_path canonicalizes without the Windows verbatim
    // prefix raw canonicalize() would leave in the fixture paths.
    let home = cairn_store::paths::resolve_path(&std::env::temp_dir())
        .join(format!("cairn-update-file-{}-{nanos}", std::process::id()));
    let ws = home.join("ws");
    let repo = ws.join("repo");
    std::fs::create_dir_all(repo.join(".git")).expect("repo marker");
    std::fs::write(repo.join("mod.py"), "def one():\n    return 1\n").expect("source");
    std::fs::write(repo.join("NOTES.md"), "# notes\n").expect("non-source");
    let db = ws.join("store.kg");
    let db = db.to_str().expect("utf8 db path").to_string();
    let ws_arg = ws.to_str().expect("utf8 ws").to_string();
    let (built, out) = cairn(&home, None, &["build", "--workspace", &ws_arg, "--db", &db]);
    assert!(built, "build failed: {out}");

    // A non-source edit is the hook's zero-change path: warning only.
    let (ok, out) = cairn(
        &home,
        Some(&ws),
        &[
            "update",
            "--file",
            repo.join("NOTES.md").to_str().expect("utf8 notes"),
            "--workspace",
            &ws_arg,
            "--db",
            &db,
        ],
    );
    assert!(ok, "{out}");
    assert!(out.contains("no reindexable change for NOTES.md"), "{out}");
    assert!(!out.contains("Reindexed"), "{out}");

    std::fs::write(
        repo.join("mod.py"),
        "def one():\n    return 1\n\n\ndef two():\n    return 2\n",
    )
    .expect("edit source");
    let (ok, out) = cairn(
        &home,
        Some(&ws),
        &[
            "update",
            "--file",
            repo.join("mod.py").to_str().expect("utf8 mod"),
            "--workspace",
            &ws_arg,
            "--db",
            &db,
        ],
    );
    assert!(ok, "{out}");
    assert!(out.contains("Reindexed mod.py"), "{out}");

    let _ = std::fs::remove_dir_all(&home);
}
