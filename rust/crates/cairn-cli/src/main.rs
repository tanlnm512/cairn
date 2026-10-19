//! The `cairn` binary: migration subcommand shims + the clap CLI port.

use std::process::ExitCode;

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    // Migration-harness shims keep their hand-rolled argparse-compatible
    // parsing; they are not part of the Python CLI surface clap ports.
    match args.first().map(String::as_str) {
        Some("canonical-dump") => return cairn_cli::canonical_dump::run(&args[1..]),
        Some("build-graph") => return cairn_cli::build_graph::run(&args[1..]),
        Some("closure") => return cairn_cli::closure::run(&args[1..]),
        _ => {}
    }
    cairn_cli::cli::run(&args)
}
