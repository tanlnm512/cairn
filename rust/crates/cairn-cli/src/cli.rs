//! clap command tree for the Python CLI surface; dispatch routes to the cmds_* stubs.

use std::process::ExitCode;

use clap::{Parser, Subcommand};

use crate::cmds_core::{self, CoreCommand};
use crate::cmds_knowledge::{self, KnowledgeCommand};
use crate::cmds_system::{self, SystemCommand};

#[derive(Parser)]
#[command(
    name = "cairn",
    version,
    about = "cairn-intel: local codebase intelligence system.",
    arg_required_else_help = true
)]
pub struct Cli {
    /// Enable DEBUG logging for the cairn namespace (overrides CAIRN_LOG_LEVEL).
    #[arg(short, long, global = true)]
    pub verbose: bool,
    #[command(subcommand)]
    pub command: Command,
}

#[derive(Subcommand)]
pub enum Command {
    #[command(flatten)]
    Core(CoreCommand),
    #[command(flatten)]
    Knowledge(KnowledgeCommand),
    #[command(flatten)]
    System(SystemCommand),
}

/// Parse and dispatch one invocation; clap owns usage errors and exit codes.
/// `args` excludes argv[0]; the bin name is re-supplied for clap's usage strings.
pub fn run(args: &[String]) -> ExitCode {
    let cli = Cli::parse_from(std::iter::once("cairn".to_string()).chain(args.iter().cloned()));
    match cli.command {
        Command::Core(command) => cmds_core::run(command),
        Command::Knowledge(command) => cmds_knowledge::run(command),
        Command::System(command) => cmds_system::run(command),
    }
}

#[cfg(test)]
mod tests {
    use clap::CommandFactory;

    use super::*;

    #[test]
    fn command_tree_parses() {
        Cli::command().debug_assert();
    }

    #[test]
    fn families_are_flattened_into_the_top_level() {
        let cmd = Cli::command();
        let names: Vec<&str> = cmd.get_subcommands().map(|c| c.get_name()).collect();
        for expected in ["config", "knowledge", "doctor", "memory", "serve"] {
            assert!(names.contains(&expected), "missing {expected} in {names:?}");
        }
    }
}
