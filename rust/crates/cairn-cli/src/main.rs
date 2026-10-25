//! The `cairn` binary: migration-harness shims + the clap CLI.

use std::process::ExitCode;

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    // Migration-harness shims keep their hand-rolled argparse-compatible
    // parsing, outside the clap tree.
    match args.first().map(String::as_str) {
        Some("canonical-dump") => return cairn_cli::canonical_dump::run(&args[1..]),
        Some("build-graph") => return cairn_cli::build_graph::run(&args[1..]),
        Some("closure") => return cairn_cli::closure::run(&args[1..]),
        _ => {}
    }
    cairn_cli::cli::run(&args)
}
