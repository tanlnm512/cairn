//! clap command tree for the `cairn` CLI; dispatch routes to the cmds_* modules.

use std::io::IsTerminal;
use std::process::ExitCode;

use clap::{CommandFactory, FromArgMatches, Parser, Subcommand};

use crate::cmds_core::{self, CoreCommand};
use crate::cmds_knowledge::{self, KnowledgeCommand};
use crate::cmds_system::{self, SystemCommand};
use crate::display;
use crate::help;

#[derive(Parser)]
#[command(
    name = "cairn",
    version,
    about = "cairn: local codebase intelligence system.",
    arg_required_else_help = true
)]
pub struct Cli {
    /// Enable DEBUG logging for the cairn namespace (overrides CAIRN_LOG_LEVEL).
    #[arg(short, long, global = true)]
    pub verbose: bool,
    /// Color output mode: auto, always, or never (always/never override env detection).
    #[arg(
        long,
        value_enum,
        ignore_case = true,
        global = true,
        default_value = "auto",
        value_name = "WHEN"
    )]
    pub color: display::ColorMode,
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
    let mode = scan_color(args).unwrap_or(display::ColorMode::Auto);
    let color = display::resolve_color_mode(mode, std::io::stdout().is_terminal());
    display::init_color_mode(color);

    let mut cmd = Cli::command();
    cmd = cmd
        .color(if color {
            clap::ColorChoice::Always
        } else {
            clap::ColorChoice::Never
        })
        .styles(help::STYLES)
        .help_template(help::ROOT_TEMPLATE);
    let after = help::root_after_help(&cmd, color);
    cmd = cmd.after_help(after);
    help::apply_examples(&mut cmd);
    let matches =
        cmd.get_matches_from(std::iter::once("cairn".to_string()).chain(args.iter().cloned()));
    let cli = Cli::from_arg_matches(&matches).expect("clap validated the parse");
    match cli.command {
        Command::Core(command) => cmds_core::run(command),
        Command::Knowledge(command) => cmds_knowledge::run(command),
        Command::System(command) => cmds_system::run(command),
    }
}

/// Extract the last explicit `--color` occurrence for clap's stream coloring; stops at `--`.
fn scan_color(args: &[String]) -> Option<display::ColorMode> {
    let mut found = None;
    let mut values = args.iter().map(String::as_str);
    while let Some(arg) = values.next() {
        if arg == "--" {
            return found;
        }
        if let Some(value) = arg.strip_prefix("--color=") {
            found = display::ColorMode::parse_value(value);
        } else if arg == "--color" {
            let value = values.next()?;
            found = display::ColorMode::parse_value(value);
        }
    }
    found
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

    #[test]
    fn dashboard_defaults_to_the_documented_port() {
        let cli = Cli::try_parse_from(["cairn", "dashboard"]).expect("parses");
        let Command::System(SystemCommand::Dashboard { port, .. }) = cli.command else {
            panic!("dashboard did not route to the system family");
        };
        assert_eq!(port, 8765);
    }

    #[test]
    fn scan_color_finds_the_flag() {
        let args = |v: &[&str]| -> Vec<String> { v.iter().map(|s| s.to_string()).collect() };
        use display::ColorMode;
        assert_eq!(
            scan_color(&args(&["--color", "never"])),
            Some(ColorMode::Never)
        );
        assert_eq!(
            scan_color(&args(&["--color=always"])),
            Some(ColorMode::Always)
        );
        assert_eq!(
            scan_color(&args(&["build", "--color", "never"])),
            Some(ColorMode::Never)
        );
        assert_eq!(
            scan_color(&args(&["--color", "never", "--color", "always"])),
            Some(ColorMode::Always)
        );
        assert_eq!(
            scan_color(&args(&["--color", "never", "--color=always"])),
            Some(ColorMode::Always)
        );
        assert_eq!(scan_color(&args(&["ask", "--", "--color", "always"])), None);
        assert_eq!(scan_color(&args(&["--color"])), None);
        assert_eq!(scan_color(&args(&["--color", "bogus"])), None);
        assert_eq!(scan_color(&args(&[])), None);
    }

    #[test]
    fn color_flag_parses_globally() {
        let cli = Cli::try_parse_from(["cairn", "--color", "always", "version"]).expect("parses");
        assert_eq!(cli.color, display::ColorMode::Always);
        let cli = Cli::try_parse_from(["cairn", "version", "--color", "never"]).expect("parses");
        assert_eq!(cli.color, display::ColorMode::Never);
        let cli = Cli::try_parse_from(["cairn", "version"]).expect("parses");
        assert_eq!(cli.color, display::ColorMode::Auto);
    }

    #[test]
    fn bare_invocation_keeps_exit_2() {
        let err = match Cli::try_parse_from(["cairn"]) {
            Ok(_) => panic!("bare invocation must not parse"),
            Err(err) => err,
        };
        assert_eq!(
            err.kind(),
            clap::error::ErrorKind::DisplayHelpOnMissingArgumentOrSubcommand
        );
        assert!(err.use_stderr());
    }
}
