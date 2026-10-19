//! System command stubs: health/lifecycle surface (doctor, serve, connect, installers).

use std::process::ExitCode;

use clap::Subcommand;

use crate::display;

#[derive(Subcommand)]
pub enum SystemCommand {
    /// Run system health checks; exits 0 on PASS/WARN-only.
    Doctor,
    /// System status and health across all layers.
    Status,
    /// Manually re-index size/mtime-drifted files.
    Sync,
    /// Report tool metrics.
    Metrics,
    /// Print a redacted diagnostic bundle.
    Report,
    /// Start or reuse the shared HTTP daemon and print its URL.
    Connect,
    /// Start the loopback-only read-only web dashboard.
    Dashboard,
    /// Wire cairn into AI coding clients.
    #[command(name = "install-agents")]
    InstallAgents,
    /// Remove cairn entries from AI client configs.
    #[command(name = "uninstall-agents")]
    UninstallAgents,
    /// Uninstall cairn: agent wiring, hooks, graph store.
    Uninstall,
    /// Incremental graph update from git diff.
    Update,
    /// Upgrade cairn.
    Upgrade,
    /// Print the installed cairn version.
    Version,
    /// Git hook management.
    Hooks {
        #[command(subcommand)]
        command: HooksGroup,
    },
    /// Download the reranker model and enable reranking.
    #[command(name = "download-reranker")]
    DownloadReranker,
    /// HTTP MCP server daemon lifecycle.
    Serve {
        #[command(subcommand)]
        command: ServeGroup,
    },
}

#[derive(Subcommand)]
pub enum HooksGroup {
    /// Install git hooks.
    Install,
    /// Uninstall git hooks.
    Uninstall,
}

#[derive(Subcommand)]
pub enum ServeGroup {
    /// Run the server in the foreground.
    Run,
    /// Start the daemon in the background.
    Start,
    /// Stop the daemon.
    Stop,
    /// Print daemon status.
    Status,
    /// Restart the daemon.
    Restart,
}

/// Run a system command; every handler is a Phase 3a stub until ported.
pub fn run(command: SystemCommand) -> ExitCode {
    match command {
        SystemCommand::Hooks { command } => not_ported(match command {
            HooksGroup::Install => "hooks install",
            HooksGroup::Uninstall => "hooks uninstall",
        }),
        SystemCommand::Serve { command } => not_ported(match command {
            ServeGroup::Run => "serve run",
            ServeGroup::Start => "serve start",
            ServeGroup::Stop => "serve stop",
            ServeGroup::Status => "serve status",
            ServeGroup::Restart => "serve restart",
        }),
        other => not_ported(other.name()),
    }
}

fn not_ported(name: &str) -> ExitCode {
    display::error(&format!(
        "cairn {name} is not implemented in the Rust CLI yet"
    ));
    ExitCode::FAILURE
}

impl SystemCommand {
    /// The python-CLI command name for diagnostics.
    fn name(&self) -> &'static str {
        match self {
            Self::Doctor => "doctor",
            Self::Status => "status",
            Self::Sync => "sync",
            Self::Metrics => "metrics",
            Self::Report => "report",
            Self::Connect => "connect",
            Self::Dashboard => "dashboard",
            Self::InstallAgents => "install-agents",
            Self::UninstallAgents => "uninstall-agents",
            Self::Uninstall => "uninstall",
            Self::Update => "update",
            Self::Upgrade => "upgrade",
            Self::Version => "version",
            Self::Hooks { .. } => "hooks",
            Self::DownloadReranker => "download-reranker",
            Self::Serve { .. } => "serve",
        }
    }
}

#[cfg(test)]
mod tests {
    use clap::{Command, Subcommand};

    use super::*;

    fn tree<T: Subcommand>() -> Command {
        T::augment_subcommands(Command::new("cairn"))
    }

    #[test]
    fn command_tree_parses() {
        tree::<SystemCommand>().debug_assert();
        tree::<HooksGroup>().debug_assert();
        tree::<ServeGroup>().debug_assert();
    }

    #[test]
    fn stub_exit_is_failure_with_diagnostic() {
        assert_eq!(run(SystemCommand::Doctor), ExitCode::FAILURE);
    }
}
