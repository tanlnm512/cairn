//! init flags and help text no longer reference the removed Python CLI.

use std::path::{Path, PathBuf};
use std::process::Command;

fn scratch(tag: &str) -> PathBuf {
    let home = std::env::temp_dir().join(format!("cairn-init-{tag}-{}", std::process::id()));
    std::fs::create_dir_all(&home).expect("scratch home");
    home
}

fn cairn(home: &Path, args: &[&str]) -> (Option<i32>, String) {
    let out = Command::new(env!("CARGO_BIN_EXE_cairn"))
        .args(args)
        .env("HOME", home)
        .env("CAIRN_HOME", home)
        .env_remove("CAIRN_DAEMON_IMPL")
        .output()
        .expect("spawn cairn");
    let mut text = String::from_utf8_lossy(&out.stdout).into_owned();
    text.push_str(&String::from_utf8_lossy(&out.stderr));
    // The console wraps long lines; compare on whitespace-normalized text.
    let flat = text.split_whitespace().collect::<Vec<_>>().join(" ");
    (out.status.code(), flat)
}

#[test]
fn init_with_hooks_installs_git_hooks() {
    let home = scratch("hooks");
    let ws = home.join("ws");
    std::fs::create_dir_all(ws.join(".git")).expect("scratch repo");
    let (code, text) = cairn(
        &home,
        &[
            "init",
            "--workspace",
            ws.to_str().expect("utf8 path"),
            "--no-build",
            "--with-hooks",
        ],
    );
    assert_eq!(code, Some(0), "{text}");
    assert!(
        ws.join(".git/hooks/post-commit").is_file(),
        "post-commit hook missing: {text}"
    );
    assert!(
        ws.join(".git/hooks/post-checkout").is_file(),
        "post-checkout hook missing: {text}"
    );
    assert!(!text.contains("Python CLI"), "{text}");
    let _ = std::fs::remove_dir_all(&home);
}

#[test]
fn init_import_docs_names_the_replacement() {
    let home = scratch("docs");
    let ws = home.join("ws");
    std::fs::create_dir_all(&ws).expect("scratch ws");
    let (code, text) = cairn(
        &home,
        &[
            "init",
            "--workspace",
            ws.to_str().expect("utf8 path"),
            "--no-build",
            "--import-docs",
        ],
    );
    assert_eq!(code, Some(0), "{text}");
    assert!(text.contains("cairn knowledge import"), "{text}");
    assert!(!text.contains("Python CLI"), "{text}");
    let _ = std::fs::remove_dir_all(&home);
}

#[test]
fn help_does_not_advertise_the_retired_transition() {
    let home = scratch("help");
    let (_, build_help) = cairn(&home, &["build", "--help"]);
    assert!(
        build_help.contains("pyright LSP pass is gone"),
        "{build_help}"
    );
    assert!(!build_help.contains("not yet ported"), "{build_help}");
    let (_, connect_help) = cairn(&home, &["connect", "--help"]);
    assert!(!connect_help.contains("else python"), "{connect_help}");
    let _ = std::fs::remove_dir_all(&home);
}
