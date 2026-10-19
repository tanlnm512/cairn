//! Knowledge-layer command stubs: knowledge, memory, wiki, compass, skill, task, OKF validators.

use std::process::ExitCode;

use clap::Subcommand;

use crate::display;

#[derive(Subcommand)]
pub enum KnowledgeCommand {
    /// Business knowledge document ingestion and search.
    Knowledge {
        #[command(subcommand)]
        command: KnowledgeGroup,
    },
    /// Agent memory: decisions, patterns, mistakes, workarounds.
    Memory {
        #[command(subcommand)]
        command: MemoryGroup,
    },
    /// Architectural wiki.
    Wiki {
        #[command(subcommand)]
        command: WikiGroup,
    },
    /// Compass module navigation files.
    Compass {
        #[command(subcommand)]
        command: CompassGroup,
    },
    /// Generated codebase skills.
    Skill {
        #[command(subcommand)]
        command: SkillGroup,
    },
    /// LLM task queue: any agent with the skill processes queued work.
    Task {
        #[command(subcommand)]
        command: TaskGroup,
    },
    /// Check OKF conformance of the .knowledge/ bundle.
    Validate,
    /// Check all concepts for stale file/symbol references.
    #[command(name = "validate-paths")]
    ValidatePaths,
    /// Run the deterministic critic on one knowledge document.
    Verify,
}

#[derive(Subcommand)]
pub enum KnowledgeGroup {
    /// Store a document as knowledge.
    Add,
    /// Import documents from a directory.
    Import,
    /// Ingest sources into the knowledge bundle.
    Ingest,
    /// Rebuild the knowledge index tables.
    Rebuild,
    /// List isolated doc components and their doc-link tasks.
    Islands {
        /// Emit JSON.
        #[arg(long = "json")]
        as_json: bool,
        /// Graph DB path (default: the resolved store).
        #[arg(long = "db")]
        db: Option<String>,
    },
    /// List a doc's relationship neighbors in both index directions.
    Related,
    /// Print the doc's upstream/downstream chain.
    Chain,
    /// Search knowledge docs (lexical + semantic + graph bridge).
    Search {
        /// Query text.
        query: String,
        /// Max results.
        #[arg(long, default_value_t = 20)]
        limit: i64,
        /// Semantic score threshold.
        #[arg(long, default_value_t = 0.3)]
        threshold: f64,
        /// Emit JSON.
        #[arg(long = "json")]
        as_json: bool,
        /// Graph DB path (default: the resolved store).
        #[arg(long = "db")]
        db: Option<String>,
    },
    /// List knowledge documents.
    List {
        /// Filter by doc type.
        #[arg(long = "type")]
        doc_type: Option<String>,
        /// Filter by doc status.
        #[arg(long)]
        status: Option<String>,
        /// Filter by tag.
        #[arg(long)]
        tag: Option<String>,
        /// Emit JSON rows (concept_id, title, doc_type, doc_status).
        #[arg(long = "json")]
        as_json: bool,
    },
    /// Embed knowledge docs into the per-model vector tables.
    Embed,
    /// Rank docs by graph impact for a query.
    Impact,
    /// Remove a knowledge document.
    Remove,
    /// Update a document's lifecycle status.
    Status,
    /// Export every doc as markdown.
    Export,
    /// Workflow documents: ordered, editable procedural knowledge.
    Workflow {
        #[command(subcommand)]
        command: WorkflowGroup,
    },
}

#[derive(Subcommand)]
pub enum WorkflowGroup {
    /// Add a workflow document.
    Add,
    /// Trace a workflow's ordered steps into the code graph.
    Trace,
    /// Re-check a workflow's steps against the graph.
    Sync,
}

#[derive(Subcommand)]
pub enum MemoryGroup {
    /// Capture a learning.
    Record,
    /// Reflect raw captures into drafts.
    Reflect,
    /// Evolve drafts into tribal memories.
    Evolve,
    /// Search past decisions, patterns, mistakes.
    Search,
    /// Per-symbol history of memories.
    Timeline,
    /// Capture from a review or session event file.
    Capture,
    /// List memories.
    List,
    /// Memory store statistics.
    Stats,
    /// Digest memories for a context window.
    Digest,
    /// Promote a draft to tribal memory.
    Promote,
    /// Run the decay pass.
    Decay,
    /// Embed memories for semantic recall.
    Embed,
    /// Batch quality review of drafts.
    #[command(name = "batch-critic")]
    BatchCritic,
    /// Forget a memory.
    Forget,
    /// Demote a tribal memory back to draft.
    Demote,
    /// Purge expired memories.
    Purge,
    /// Consolidate overlapping memories.
    Consolidate,
    /// Share memories across stores.
    Share,
    /// Check memory-store overlap and health.
    Check,
}

#[derive(Subcommand)]
pub enum WikiGroup {
    /// Plan the wiki and queue page-writing tasks.
    Generate,
    /// Search promoted wiki pages.
    Search,
    /// Per-page wiki state.
    Status,
    /// Re-queue failed pages.
    Retry,
    /// Export promoted pages as markdown.
    Export,
    /// Queue an append-only extension of a promoted page.
    Enrich,
}

#[derive(Subcommand)]
pub enum CompassGroup {
    /// Generate a compass from a call-graph trace.
    Generate,
    /// List compass guides.
    List,
    /// Validate compass references against the graph.
    Validate,
    /// Report modules without compass coverage.
    Gaps,
    /// Generate a flow compass from a call-graph trace.
    Flow,
    /// Report flow-compass gaps.
    #[command(name = "flow-gaps")]
    FlowGaps,
}

#[derive(Subcommand)]
pub enum SkillGroup {
    /// Generate skill files from the graph.
    Generate,
}

#[derive(Subcommand)]
pub enum TaskGroup {
    /// List queued tasks.
    List,
    /// Show one task.
    Show,
    /// Claim a task.
    Claim,
    /// Complete a claimed task with a result file.
    Complete,
    /// Drop a claimed task.
    Drop,
}

/// Run a knowledge-layer command; every handler is a Phase 3a stub until ported.
pub fn run(command: KnowledgeCommand) -> ExitCode {
    match command {
        KnowledgeCommand::Knowledge { command } => not_ported(match command {
            KnowledgeGroup::Workflow { command } => match command {
                WorkflowGroup::Add => "knowledge workflow add",
                WorkflowGroup::Trace => "knowledge workflow trace",
                WorkflowGroup::Sync => "knowledge workflow sync",
            },
            other => other.name(),
        }),
        KnowledgeCommand::Memory { command } => not_ported(command.name()),
        KnowledgeCommand::Wiki { command } => not_ported(match command {
            WikiGroup::Generate => "wiki generate",
            WikiGroup::Search => "wiki search",
            WikiGroup::Status => "wiki status",
            WikiGroup::Retry => "wiki retry",
            WikiGroup::Export => "wiki export",
            WikiGroup::Enrich => "wiki enrich",
        }),
        KnowledgeCommand::Compass { command } => not_ported(match command {
            CompassGroup::Generate => "compass generate",
            CompassGroup::List => "compass list",
            CompassGroup::Validate => "compass validate",
            CompassGroup::Gaps => "compass gaps",
            CompassGroup::Flow => "compass flow",
            CompassGroup::FlowGaps => "compass flow-gaps",
        }),
        KnowledgeCommand::Skill { command } => not_ported(match command {
            SkillGroup::Generate => "skill generate",
        }),
        KnowledgeCommand::Task { command } => not_ported(match command {
            TaskGroup::List => "task list",
            TaskGroup::Show => "task show",
            TaskGroup::Claim => "task claim",
            TaskGroup::Complete => "task complete",
            TaskGroup::Drop => "task drop",
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

impl KnowledgeCommand {
    /// The python-CLI command name for diagnostics.
    fn name(&self) -> &'static str {
        match self {
            Self::Knowledge { .. } => "knowledge",
            Self::Memory { .. } => "memory",
            Self::Wiki { .. } => "wiki",
            Self::Compass { .. } => "compass",
            Self::Skill { .. } => "skill",
            Self::Task { .. } => "task",
            Self::Validate => "validate",
            Self::ValidatePaths => "validate-paths",
            Self::Verify => "verify",
        }
    }
}

impl MemoryGroup {
    fn name(&self) -> &'static str {
        match self {
            Self::Record => "memory record",
            Self::Reflect => "memory reflect",
            Self::Evolve => "memory evolve",
            Self::Search => "memory search",
            Self::Timeline => "memory timeline",
            Self::Capture => "memory capture",
            Self::List => "memory list",
            Self::Stats => "memory stats",
            Self::Digest => "memory digest",
            Self::Promote => "memory promote",
            Self::Decay => "memory decay",
            Self::Embed => "memory embed",
            Self::BatchCritic => "memory batch-critic",
            Self::Forget => "memory forget",
            Self::Demote => "memory demote",
            Self::Purge => "memory purge",
            Self::Consolidate => "memory consolidate",
            Self::Share => "memory share",
            Self::Check => "memory check",
        }
    }
}

impl KnowledgeGroup {
    fn name(&self) -> &'static str {
        match self {
            Self::Add => "knowledge add",
            Self::Import => "knowledge import",
            Self::Ingest => "knowledge ingest",
            Self::Rebuild => "knowledge rebuild",
            Self::Islands { .. } => "knowledge islands",
            Self::Related => "knowledge related",
            Self::Chain => "knowledge chain",
            Self::Search { .. } => "knowledge search",
            Self::List { .. } => "knowledge list",
            Self::Embed => "knowledge embed",
            Self::Impact => "knowledge impact",
            Self::Remove => "knowledge remove",
            Self::Status => "knowledge status",
            Self::Export => "knowledge export",
            Self::Workflow { .. } => "knowledge workflow",
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
        tree::<KnowledgeCommand>().debug_assert();
        tree::<KnowledgeGroup>().debug_assert();
        tree::<MemoryGroup>().debug_assert();
        tree::<WikiGroup>().debug_assert();
        tree::<CompassGroup>().debug_assert();
        tree::<SkillGroup>().debug_assert();
        tree::<TaskGroup>().debug_assert();
        tree::<WorkflowGroup>().debug_assert();
    }

    #[test]
    fn stub_exit_is_failure_with_diagnostic() {
        assert_eq!(run(KnowledgeCommand::Verify), ExitCode::FAILURE);
    }
}
