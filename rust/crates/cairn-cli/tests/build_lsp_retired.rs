//! `cairn build --lsp`: the retired pyright upgrade pass must refuse, not no-op.

use std::path::PathBuf;
use std::process::Command;

fn scratch_home(tag: &str) -> PathBuf {
    let home = std::env::temp_dir().join(format!(
        "cairn-build-lsp-{tag}-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock")
            .as_nanos()
    ));
    std::fs::create_dir_all(&home).expect("scratch home");
    home
}

fn run(home: &PathBuf, args: &[&str]) -> (Option<i32>, String) {
    let out = Command::new(env!("CARGO_BIN_EXE_cairn"))
        .args(args)
        .env("HOME", home)
        .env("CAIRN_HOME", home)
        .env_remove("CAIRN_DB")
        .env_remove("CAIRN_WORKSPACE")
        .output()
        .expect("spawn cairn");
    let mut text = String::from_utf8_lossy(&out.stdout).into_owned();
    text.push_str(&String::from_utf8_lossy(&out.stderr));
    let flat = text.split_whitespace().collect::<Vec<_>>().join(" ");
    (out.status.code(), flat)
}

#[test]
fn build_lsp_refuses_and_names_the_retirement() {
    let home = scratch_home("flags");
    let workspace = home.join("corpus");
    std::fs::create_dir_all(&workspace).expect("workspace");
    let (code, text) = run(
        &home,
        &[
            "build",
            "--lsp",
            "--workspace",
            workspace.to_str().expect("utf8 workspace"),
            "--db",
            home.join("scratch.kg").to_str().expect("utf8 db"),
        ],
    );
    assert_eq!(code, Some(1), "expected exit 1, got {code:?}: {text}");
    assert!(
        text.contains("--lsp") && text.contains("retired"),
        "error must name the --lsp retirement: {text}"
    );
    assert!(
        !text.contains("Accepted for compatibility"),
        "the silent-compat wording must be gone: {text}"
    );
    let _ = std::fs::remove_dir_all(&home);
}
