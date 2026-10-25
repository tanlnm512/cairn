//! `download-reranker` exits 2 without advertising an engine: this build has
//! no cross-encoder reranker.

use std::process::Command;

#[test]
fn download_reranker_reports_the_missing_native_reranker() {
    let out = Command::new(env!("CARGO_BIN_EXE_cairn"))
        .args(["download-reranker", "--model", "BAAI/bge-reranker-base"])
        .output()
        .expect("spawn cairn");
    let text = String::from_utf8_lossy(&out.stderr).replace('\n', " ");
    assert_eq!(out.status.code(), Some(2), "{text}");
    assert!(out.stdout.is_empty(), "stdout must stay empty");
    assert!(text.contains("no native cross-encoder reranker"), "{text}");
    assert!(!text.contains("cairn-intel[semantic]"), "{text}");
}
