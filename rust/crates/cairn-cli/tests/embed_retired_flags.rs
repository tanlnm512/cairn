//! `cairn embed`: the retired local-inference flags and the hash-fallback hint
//! name only the backends this build ships.

use std::path::PathBuf;
use std::process::Command;

const STALE: [&str; 5] = [
    "[semantic]",
    "PyPI CLI",
    "sentence-transformers",
    "torch",
    "not implemented in the Rust CLI yet",
];

fn scratch_home(tag: &str) -> PathBuf {
    let home = std::env::temp_dir().join(format!("cairn-embed-{tag}-{}", std::process::id()));
    std::fs::create_dir_all(&home).expect("scratch home");
    home
}

fn run(home: &PathBuf, args: &[&str]) -> (Option<i32>, String) {
    let out = Command::new(env!("CARGO_BIN_EXE_cairn"))
        .args(args)
        .env("HOME", home)
        .env("CAIRN_HOME", home)
        .env_remove("CAIRN_EMBED_BACKEND")
        .output()
        .expect("spawn cairn");
    let mut text = String::from_utf8_lossy(&out.stdout).into_owned();
    text.push_str(&String::from_utf8_lossy(&out.stderr));
    // The console wraps long lines; compare on whitespace-normalized text.
    let flat = text.split_whitespace().collect::<Vec<_>>().join(" ");
    (out.status.code(), flat)
}

#[test]
fn retired_flags_exit_1_and_point_at_the_shipped_backends() {
    let home = scratch_home("flags");
    for flag in ["--install-deps", "--download-model", "--adopt-server-model"] {
        let (code, text) = run(&home, &["embed", flag]);
        assert_eq!(code, Some(1), "{flag}: {text}");
        assert!(text.contains("CAIRN_EMBED_BACKEND"), "{flag}: {text}");
        for stale in STALE {
            assert!(!text.contains(stale), "{flag} still says {stale:?}: {text}");
        }
    }
    let _ = std::fs::remove_dir_all(&home);
}

#[test]
fn hash_fallback_hint_recommends_a_shipped_backend() {
    let home = scratch_home("hint");
    let missing = home.join("missing.kg");
    let (_, text) = run(
        &home,
        &["embed", "--db", missing.to_str().expect("utf8 path")],
    );
    assert!(text.contains("hash embedder"), "{text}");
    assert!(text.contains("CAIRN_EMBED_BACKEND"), "{text}");
    for stale in STALE.iter().chain(["--install-deps"].iter()) {
        assert!(!text.contains(stale), "hint still says {stale:?}: {text}");
    }
    let _ = std::fs::remove_dir_all(&home);
}
