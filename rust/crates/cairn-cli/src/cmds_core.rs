//! Core command stubs: workspace/build surface plus the graph query commands.

use std::process::ExitCode;

use clap::Subcommand;

use crate::display;

#[derive(Subcommand)]
pub enum CoreCommand {
    /// Register this workspace with cairn's central store.
    Init,
    /// Show resolved store paths for this workspace (or all registered ones).
    Config {
        /// List all registered workspaces.
        #[arg(long = "list")]
        list_all: bool,
        /// Print a path-free .mcp.json snippet for agents.
        #[arg(long = "mcp-config")]
        mcp_config: bool,
        /// Print only the resolved graph DB path (machine-readable; for scripting).
        #[arg(long = "db")]
        db_only: bool,
        /// Emit the resolved store paths as a single JSON object (machine-readable probe).
        #[arg(long = "json")]
        as_json: bool,
    },
    /// Build (or rebuild) the code graph.
    Build,
    /// Import a SCIP index into an already-built graph.
    #[command(name = "import-scip")]
    ImportScip,
    /// Show graph statistics.
    Stats,
    /// Checkpoint the graph DB's WAL into the main file.
    Checkpoint,
    /// Find where a SYMBOL is defined.
    #[command(name = "def")]
    Def,
    /// Find callers of SYMBOL; precise by default, --fuzzy widens the net.
    Callers,
    /// Find what a SYMBOL calls.
    Callees,
    /// Search symbols by PATTERN (supports * wildcards).
    Search,
    /// Recursive impact analysis: what breaks if SYMBOL changes.
    Impact,
    /// Cross-repo dependencies for REPO.
    Deps,
    /// Search every registered store as one merged ranking.
    #[command(name = "federated-search")]
    FederatedSearch,
    /// Semantic (concept) search over symbols.
    Semantic,
    /// Build the semantic embedding index over the symbol graph.
    Embed,
    /// Search indexed files and group matches by enclosing symbol.
    Grep,
    /// Show directory clusters, hubs, and workspace hotspots.
    Map,
    /// Directory/package structure of REPO with symbol counts.
    Tree,
    /// Build a deterministic, token-budgeted context pack for a task.
    Pack,
    /// Print shortest structural paths between two symbols.
    #[command(name = "path")]
    Path,
    /// Natural-language question across all layers.
    Ask,
    /// Load relevant context (compass + memory + wiki) for a file.
    Context,
    /// Trace inter-procedural taint paths from a source to a sink.
    Taint,
    /// Show reverse dependents for symbols touched by a git diff.
    Blast,
    /// Compute Louvain communities over structural edges.
    Communities,
    /// Precomputed dataflow index for public symbols.
    Dataflow {
        #[command(subcommand)]
        command: DataflowCommand,
    },
    /// Print rationale comments (NOTE/WHY/HACK), ordered by hotness.
    Rationale,
    /// Review-context pack and review-loop hooks for a change.
    Review,
    /// List open PRs with CI and review state from gh.
    Prs,
    /// Run performance, scalability, or agent-effort benchmarks.
    Bench,
    /// Run retrieval evaluation harness across corpora.
    Eval,
    /// Generate visual diagrams from the graph.
    Viz,
}

#[derive(Subcommand)]
pub enum DataflowCommand {
    /// Build the dataflow index.
    Build,
    /// Look up a symbol's dataflow facts.
    Lookup,
}

/// Run a core command; every handler is a Phase 3a stub until ported.
pub fn run(command: CoreCommand) -> ExitCode {
    match command {
        CoreCommand::Config { .. } => not_ported("config"),
        CoreCommand::Dataflow { command } => not_ported(match command {
            DataflowCommand::Build => "dataflow build",
            DataflowCommand::Lookup => "dataflow lookup",
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

impl CoreCommand {
    /// The python-CLI command name for diagnostics.
    fn name(&self) -> &'static str {
        match self {
            Self::Init => "init",
            Self::Config { .. } => "config",
            Self::Build => "build",
            Self::ImportScip => "import-scip",
            Self::Stats => "stats",
            Self::Checkpoint => "checkpoint",
            Self::Def => "def",
            Self::Callers => "callers",
            Self::Callees => "callees",
            Self::Search => "search",
            Self::Impact => "impact",
            Self::Deps => "deps",
            Self::FederatedSearch => "federated-search",
            Self::Semantic => "semantic",
            Self::Embed => "embed",
            Self::Grep => "grep",
            Self::Map => "map",
            Self::Tree => "tree",
            Self::Pack => "pack",
            Self::Path => "path",
            Self::Ask => "ask",
            Self::Context => "context",
            Self::Taint => "taint",
            Self::Blast => "blast",
            Self::Communities => "communities",
            Self::Dataflow { .. } => "dataflow",
            Self::Rationale => "rationale",
            Self::Review => "review",
            Self::Prs => "prs",
            Self::Bench => "bench",
            Self::Eval => "eval",
            Self::Viz => "viz",
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
        tree::<CoreCommand>().debug_assert();
        tree::<DataflowCommand>().debug_assert();
    }

    #[test]
    fn stub_exit_is_failure_with_diagnostic() {
        let code = run(CoreCommand::Def);
        assert_eq!(code, ExitCode::FAILURE);
    }
}
