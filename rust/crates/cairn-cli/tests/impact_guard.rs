//! `cairn impact --guard` suppresses cycle-carrying blowups above the threshold.

use std::path::Path;
use std::process::Command;

fn cairn(home: &Path, args: &[&str]) -> (bool, String) {
    let out = Command::new(env!("CARGO_BIN_EXE_cairn"))
        .args(args)
        .env("HOME", home)
        .env("CAIRN_HOME", home)
        .env_remove("CAIRN_WORKSPACE")
        .output()
        .expect("spawn cairn");
    (
        out.status.success(),
        String::from_utf8_lossy(&out.stdout).into_owned(),
    )
}

#[test]
fn guard_replaces_a_collision_dump_with_the_dataflow_fallback() {
    let home = std::env::temp_dir().join(format!("cairn-impact-guard-{}", std::process::id()));
    let repo = home.join("ws").join("repo");
    std::fs::create_dir_all(repo.join(".git")).expect("scratch repo");
    std::fs::write(
        repo.join("mod.py"),
        "def a():\n    b()\n\n\ndef b():\n    a()\n\n\ndef c():\n    a()\n",
    )
    .expect("fixture");
    let db = home.join("store.kg");
    let db = db.to_str().expect("utf8 path");
    let ws = home.join("ws");
    let (built, _) = cairn(
        &home,
        &[
            "build",
            "--workspace",
            ws.to_str().expect("utf8"),
            "--db",
            db,
        ],
    );
    assert!(built);

    let impact = |extra: &[&str]| {
        let mut args = vec!["impact", "a", "--db", db, "--no-refresh"];
        args.extend_from_slice(extra);
        cairn(&home, &args)
    };
    let (ok, plain) = impact(&[]);
    assert!(ok);
    let (ok, below) = impact(&["--guard"]);
    assert!(ok);
    assert_eq!(
        below, plain,
        "below the threshold --guard must not change output"
    );

    let (ok, tripped) = impact(&["--guard", "--threshold", "1"]);
    assert!(ok);
    assert!(tripped.contains("name-collision artifact"), "{tripped}");
    assert!(tripped.contains("No dataflow entry for 'a'"), "{tripped}");
    assert!(!tripped.contains("Depth "), "raw dump leaked: {tripped}");
    let _ = std::fs::remove_dir_all(&home);
}
