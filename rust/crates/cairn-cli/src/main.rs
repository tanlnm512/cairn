//! Placeholder entry point; the real CLI lands with the clap port.

use std::process::ExitCode;

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        Some("canonical-dump") => cairn_cli::canonical_dump::run(&args[1..]),
        Some("build-graph") => cairn_cli::build_graph::run(&args[1..]),
        _ => {
            println!("{}", cairn_cli::crate_name());
            ExitCode::SUCCESS
        }
    }
}
