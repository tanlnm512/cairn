//! `cargo xtask kg-dump`: forwards to `cairn canonical-dump`, the canonical dump implementation.

use std::path::PathBuf;
use std::process::{Command, ExitCode};

fn cairn_binary() -> PathBuf {
    if let Some(path) = std::env::var_os("CAIRN_RUST_BIN") {
        return PathBuf::from(path);
    }
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../target/release/cairn")
}

pub fn run(args: &[String]) -> ExitCode {
    let bin = cairn_binary();
    match Command::new(&bin).arg("canonical-dump").args(args).status() {
        Ok(status) => ExitCode::from(status.code().unwrap_or(1) as u8),
        Err(err) => {
            eprintln!("xtask kg-dump: cannot run {}: {err}", bin.display());
            ExitCode::from(2)
        }
    }
}
