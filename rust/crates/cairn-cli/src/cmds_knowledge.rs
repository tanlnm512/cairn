//! Knowledge-layer commands: knowledge (+workflow), memory, compass, wiki.

use std::path::{Path, PathBuf};
use std::process::ExitCode;

use clap::Subcommand;
use rusqlite::Connection;
use serde_json::{Value, json};
use yaml_rust2::Yaml;

use cairn_query::tasks;
use cairn_query::tools_km::{self, Bundle, Concept, KnowledgeHit, RelatedDoc, WorkflowStep};
use cairn_store::{conn as store_conn, schema as store_schema};

use crate::display;
use crate::py_json;
use crate::store_paths::resolve_store;

const IMPORT_MAX_FILE_SIZE: u64 = 10 * 1024 * 1024;
const MEMORY_TYPES: &[&str] = &["decision", "pattern", "mistake", "workaround"];

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
    Add {
        /// Read body from file.
        #[arg(long = "file")]
        file: Option<String>,
        /// Inline body text.
        #[arg(long)]
        body: Option<String>,
        /// Document title.
        #[arg(long)]
        title: String,
        /// business-rule|spec|decision
        #[arg(long = "type", default_value = "spec")]
        doc_type: String,
        /// Comma-separated tags.
        #[arg(long, default_value = "")]
        tags: String,
        /// Comma-separated repos (graph bridge).
        #[arg(long, default_value = "")]
        affects: String,
        /// Comma-separated module paths.
        #[arg(long = "affects-modules", default_value = "")]
        affects_modules: String,
        /// Jira epic link.
        #[arg(long, default_value = "")]
        epic: String,
        /// Canonical URI (Jira/Confluence).
        #[arg(long, default_value = "")]
        resource: String,
        /// One-line summary (defaults to title).
        #[arg(long)]
        description: Option<String>,
    },
    /// Import documents from a directory.
    Import {
        /// Directory of .md files.
        dir_path: String,
        #[arg(long = "type", default_value = "spec")]
        doc_type: String,
        #[arg(long, default_value = "")]
        tags: String,
        #[arg(long, default_value = "")]
        affects: String,
    },
    /// Ingest sources into the knowledge bundle.
    Ingest,
    /// Rebuild the knowledge index tables.
    Rebuild {
        #[arg(long)]
        db: Option<String>,
    },
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
    Related {
        /// Knowledge document id.
        doc_id: String,
        /// Emit JSON.
        #[arg(long = "json")]
        as_json: bool,
        #[arg(long = "db")]
        db: Option<String>,
    },
    /// Print the doc's upstream/downstream chain.
    Chain {
        /// Knowledge document id.
        doc_id: String,
        /// Emit JSON.
        #[arg(long = "json")]
        as_json: bool,
        #[arg(long = "db")]
        db: Option<String>,
    },
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
    Embed {
        #[arg(long)]
        db: Option<String>,
        #[arg(long = "batch-size", default_value_t = 64)]
        batch_size: i64,
    },
    /// Rank docs by graph impact for a query.
    Impact {
        /// Query text.
        query: String,
        #[arg(long)]
        db: Option<String>,
        #[arg(long, default_value_t = 10)]
        limit: i64,
    },
    /// Remove a knowledge document.
    Remove {
        /// Knowledge document id.
        doc_id: String,
        #[arg(long)]
        db: Option<String>,
    },
    /// Update a document's lifecycle status.
    Status {
        /// Knowledge document id.
        doc_id: String,
        /// active|superseded|archived
        new_status: String,
    },
    /// Export every doc as markdown.
    Export {
        /// Destination directory or tarball path.
        #[arg(long)]
        out: String,
    },
    /// Workflow documents: ordered, editable procedural knowledge.
    Workflow {
        #[command(subcommand)]
        command: WorkflowGroup,
    },
}

#[derive(Subcommand)]
pub enum WorkflowGroup {
    /// Add a workflow document.
    Add {
        /// Workflow title.
        #[arg(long)]
        title: String,
        /// Repeatable. Format: 'name::description[::symbol[::file]]'.
        #[arg(long = "step")]
        steps_raw: Vec<String>,
        /// Path to a YAML/JSON file containing a list of step dicts.
        #[arg(long = "steps-file")]
        steps_file: Option<String>,
        /// Comma-separated tags.
        #[arg(long, default_value = "")]
        tags: String,
        /// Comma-separated repos (graph bridge).
        #[arg(long, default_value = "")]
        affects: String,
        /// Comma-separated module paths.
        #[arg(long = "affects-modules", default_value = "")]
        affects_modules: String,
        /// Canonical URI (Jira/Confluence/runbook).
        #[arg(long, default_value = "")]
        resource: String,
    },
    /// Trace a workflow's ordered steps into the code graph.
    Trace {
        /// Title, slug, or concept id.
        reference: String,
        /// Emit JSON.
        #[arg(long = "json")]
        as_json: bool,
    },
    /// Re-check a workflow's steps against the graph.
    Sync {
        /// Workflow ref (or --all).
        reference: Option<String>,
        /// Sync all workflows.
        #[arg(long)]
        all: bool,
        /// Show staleness report without writing.
        #[arg(long = "dry-run")]
        dry_run: bool,
        /// Cap workflow steps.
        #[arg(long = "max-steps", default_value_t = 20)]
        max_steps: i64,
        #[arg(long)]
        db: Option<String>,
        /// Knowledge bundle override.
        #[arg(long)]
        knowledge: Option<String>,
    },
}

#[derive(Subcommand)]
pub enum MemoryGroup {
    /// Capture a learning.
    Record {
        /// decision|pattern|mistake|workaround
        mtype: String,
        /// Memory title.
        title: String,
        /// Memory body text.
        #[arg(long, default_value = "")]
        body: String,
        /// Related file path.
        #[arg(long)]
        resource: Option<String>,
        /// Agent confidence 0.0-1.0.
        #[arg(long, default_value_t = 0.7)]
        confidence: f64,
        /// Failure signature gate: capture only on the second occurrence.
        #[arg(long = "recurrence-key")]
        recurrence_key: Option<String>,
        /// Record-time prior stance.
        #[arg(long)]
        stance: Option<String>,
        #[arg(long, default_value = None)]
        db: Option<String>,
        #[arg(long, default_value = None)]
        knowledge: Option<String>,
    },
    /// Reflect raw captures into drafts.
    Reflect {
        #[arg(long, default_value = None)]
        db: Option<String>,
        #[arg(long, default_value = None)]
        knowledge: Option<String>,
    },
    /// Evolve drafts into tribal memories.
    Evolve {
        /// Memory path.
        memory_path: String,
        #[arg(long)]
        title: Option<String>,
        #[arg(long)]
        body: Option<String>,
        #[arg(long, default_value = None)]
        db: Option<String>,
        #[arg(long, default_value = None)]
        knowledge: Option<String>,
    },
    /// Search past decisions, patterns, mistakes.
    Search {
        /// Query text.
        query: String,
        #[arg(long)]
        tier: Option<String>,
        /// Only memories valid at this ISO-8601 date/time.
        #[arg(long = "as-of")]
        as_of: Option<String>,
        /// Agent id doing the search; merges shared memories.
        #[arg(long)]
        agent: Option<String>,
        #[arg(long, default_value = None)]
        db: Option<String>,
        #[arg(long, default_value = None)]
        knowledge: Option<String>,
    },
    /// Per-symbol history of memories.
    Timeline {
        /// Symbol cited by the memories.
        symbol: String,
        #[arg(long, default_value = None)]
        db: Option<String>,
        #[arg(long, default_value = None)]
        knowledge: Option<String>,
    },
    /// Capture from a review or session event file.
    Capture {
        /// JSON transcript of the session.
        #[arg(long = "session-transcript")]
        session_transcript: Option<String>,
        /// Read the JSON transcript from stdin.
        #[arg(long = "session-transcript-stdin")]
        session_transcript_stdin: bool,
        /// Origin session id.
        #[arg(long = "session-id", default_value = "hook")]
        session_id: String,
        #[arg(long, default_value = None)]
        db: Option<String>,
        #[arg(long, default_value = None)]
        knowledge: Option<String>,
    },
    /// List memories.
    List {
        #[arg(long)]
        tier: Option<String>,
        #[arg(long)]
        tag: Option<String>,
        #[arg(long, default_value = None)]
        db: Option<String>,
        #[arg(long, default_value = None)]
        knowledge: Option<String>,
    },
    /// Memory store statistics.
    Stats {
        #[arg(long, default_value = None)]
        knowledge: Option<String>,
    },
    /// Digest memories for a context window.
    Digest {
        /// Max memories to show.
        #[arg(long, default_value_t = 10)]
        limit: i64,
        #[arg(long, default_value = None)]
        db: Option<String>,
        #[arg(long, default_value = None)]
        knowledge: Option<String>,
    },
    /// Promote a draft to tribal memory.
    Promote {
        /// Memory path.
        path: String,
        #[arg(long, default_value = None)]
        db: Option<String>,
        #[arg(long, default_value = None)]
        knowledge: Option<String>,
    },
    /// Run the decay pass.
    Decay {
        #[arg(long, default_value = None)]
        db: Option<String>,
        #[arg(long, default_value = None)]
        knowledge: Option<String>,
    },
    /// Embed memories for semantic recall.
    Embed {
        #[arg(long, default_value = None)]
        db: Option<String>,
        #[arg(long, default_value = None)]
        knowledge: Option<String>,
        #[arg(long = "batch-size", default_value_t = 64)]
        batch_size: i64,
        #[arg(long = "reap", default_value_t = true)]
        reap: bool,
    },
    /// Batch quality review of drafts.
    #[command(name = "batch-critic")]
    BatchCritic {
        #[arg(long, default_value = None)]
        db: Option<String>,
        #[arg(long, default_value = None)]
        knowledge: Option<String>,
    },
    /// Forget a memory.
    Forget {
        /// Memory path.
        memory_path: String,
        #[arg(long, default_value = None)]
        db: Option<String>,
        #[arg(long, default_value = None)]
        knowledge: Option<String>,
    },
    /// Demote a tribal memory back to draft.
    Demote {
        /// Memory path.
        memory_path: String,
        /// Target tier (raw or archived).
        #[arg(long, default_value = "raw")]
        tier: String,
        #[arg(long, default_value = None)]
        db: Option<String>,
        #[arg(long, default_value = None)]
        knowledge: Option<String>,
    },
    /// Purge expired memories.
    Purge {
        /// Delete archived memories older than this.
        #[arg(long = "max-days", default_value_t = 90)]
        max_days: i64,
        /// List candidates without deleting.
        #[arg(long = "dry-run")]
        dry_run: bool,
        #[arg(long, default_value = None)]
        db: Option<String>,
        #[arg(long, default_value = None)]
        knowledge: Option<String>,
    },
    /// Consolidate overlapping memories.
    Consolidate {
        #[arg(long, default_value = None)]
        db: Option<String>,
        #[arg(long, default_value = None)]
        knowledge: Option<String>,
    },
    /// Share memories across stores.
    Share {
        /// Memory id (optional).
        memory_id: Option<String>,
        /// Caller agent id: non-empty, <=64 chars, [A-Za-z0-9._-].
        #[arg(long)]
        agent: String,
        /// Comma-separated symbols the share covers.
        #[arg(long)]
        symbols: String,
        #[arg(long, default_value = None)]
        db: Option<String>,
    },
    /// Check memory-store overlap and health.
    Check {
        /// Caller agent id: non-empty, <=64 chars, [A-Za-z0-9._-].
        #[arg(long)]
        agent: String,
        /// Comma-separated symbols about to be edited.
        #[arg(long)]
        symbols: String,
        #[arg(long, default_value = None)]
        db: Option<String>,
    },
}

#[derive(Subcommand)]
pub enum WikiGroup {
    /// Plan the wiki and queue page-writing tasks.
    Generate,
    /// Search promoted wiki pages.
    Search {
        /// Query text.
        query: String,
        /// Knowledge bundle override.
        #[arg(long)]
        knowledge: Option<String>,
    },
    /// Per-page wiki state.
    Status {
        /// Only pages of this repo.
        #[arg(long)]
        repo: Option<String>,
        #[arg(long)]
        knowledge: Option<String>,
    },
    /// Re-queue failed pages.
    Retry {
        #[arg(long)]
        repo: Option<String>,
        #[arg(long)]
        knowledge: Option<String>,
    },
    /// Export promoted pages as markdown.
    Export {
        /// Directory to write the exported pages into.
        #[arg(long = "dir")]
        out_dir: String,
        /// Overwrite files in an existing non-empty target directory.
        #[arg(long)]
        force: bool,
        #[arg(long)]
        knowledge: Option<String>,
    },
    /// Queue an append-only extension of a promoted page.
    Enrich {
        /// Page id (or --all).
        page_id: Option<String>,
        /// Scope the enrichment to one repo.
        #[arg(long)]
        repo: Option<String>,
        /// Queue one enrichment per promoted page across repos.
        #[arg(long)]
        all: bool,
        #[arg(long)]
        knowledge: Option<String>,
    },
}

#[derive(Subcommand)]
pub enum CompassGroup {
    /// Generate a compass from a call-graph trace.
    Generate {
        /// Module path.
        module: String,
        /// Repo name (auto-inferred if omitted).
        #[arg(long)]
        repo: Option<String>,
        /// Agent-decoupled synthesis with revise loop.
        #[arg(long = "use-llm")]
        use_llm: bool,
        #[arg(long, default_value = None)]
        db: Option<String>,
        #[arg(long, default_value = None)]
        knowledge: Option<String>,
        /// Run generation + the critic and print the result; write nothing.
        #[arg(long = "dry-run")]
        dry_run: bool,
        /// With --use-llm, print every revise cycle's critic verdict.
        #[arg(long = "show-rejections")]
        show_rejections: bool,
    },
    /// List compass guides.
    List {
        #[arg(long, default_value = None)]
        knowledge: Option<String>,
    },
    /// Validate compass references against the graph.
    Validate {
        #[arg(long, default_value = None)]
        db: Option<String>,
        #[arg(long, default_value = None)]
        knowledge: Option<String>,
    },
    /// Report modules without compass coverage.
    Gaps {
        #[arg(long, default_value = None)]
        db: Option<String>,
        #[arg(long, default_value = None)]
        knowledge: Option<String>,
    },
    /// Generate a flow compass from a call-graph trace.
    Flow {
        /// Entry symbol.
        entry: String,
        #[arg(long, default_value = None)]
        db: Option<String>,
        #[arg(long, default_value = None)]
        knowledge: Option<String>,
        #[arg(long = "dry-run")]
        dry_run: bool,
        #[arg(long = "as-workflow")]
        as_workflow: bool,
        #[arg(long = "max-steps", default_value_t = 20)]
        max_steps: i64,
        #[arg(long = "use-llm")]
        use_llm: bool,
        /// Symbol database ID for collision-safe entry resolution.
        #[arg(long = "entry-id")]
        entry_id: Option<String>,
    },
    /// Report flow-compass gaps.
    #[command(name = "flow-gaps")]
    FlowGaps {
        /// Minimum resolved outgoing edges to qualify as a flow.
        #[arg(long = "min-edges", default_value_t = 5)]
        min_edges: i64,
        /// Generate a flow compass for each undocumented flow.
        #[arg(long)]
        generate: bool,
        /// With --generate, cap the number of flows generated.
        #[arg(long, default_value_t = 0)]
        limit: i64,
        #[arg(long = "dry-run")]
        dry_run: bool,
        #[arg(long, default_value = None)]
        db: Option<String>,
        #[arg(long, default_value = None)]
        knowledge: Option<String>,
    },
}

#[derive(Subcommand)]
pub enum SkillGroup {
    /// Generate skill files from the graph.
    Generate,
}

#[derive(Subcommand)]
pub enum TaskGroup {
    /// List queued tasks.
    List {
        /// pending|in-progress|done|failed|dropped
        #[arg(long)]
        status: Option<String>,
        /// Filter by task kind.
        #[arg(long)]
        kind: Option<String>,
        /// Filter by task kind prefix (e.g. wiki-page).
        #[arg(long = "kind-prefix")]
        kind_prefix: Option<String>,
        /// Knowledge bundle (default: CAIRN_KNOWLEDGE or the store-adjacent .knowledge).
        #[arg(long)]
        knowledge: Option<String>,
    },
    /// Show one task.
    Show {
        task_id: String,
        #[arg(long)]
        knowledge: Option<String>,
    },
    /// Claim a task.
    Claim {
        task_id: String,
        /// Who/what is claiming this task.
        #[arg(long = "as", default_value = "")]
        assigned_to: String,
        #[arg(long)]
        knowledge: Option<String>,
    },
    /// Complete a claimed task with a result file.
    Complete {
        task_id: String,
        /// Result text (alternative to --result-file).
        #[arg(long)]
        result: Option<String>,
        /// Read result from this file.
        #[arg(long = "result-file")]
        result_file: Option<String>,
        /// Claimer identity; must match the task's --as at claim time.
        #[arg(long = "as", default_value = "")]
        claimer: String,
        /// SQLite DB path (default: the resolved store).
        #[arg(long)]
        db: Option<String>,
        #[arg(long)]
        knowledge: Option<String>,
    },
    /// Drop a claimed task.
    Drop {
        task_id: String,
        #[arg(long)]
        knowledge: Option<String>,
    },
}

/// Run a knowledge-layer command.
pub fn run(command: KnowledgeCommand) -> ExitCode {
    match command {
        KnowledgeCommand::Knowledge { command } => run_knowledge(command),
        KnowledgeCommand::Memory { command } => run_memory(command),
        KnowledgeCommand::Wiki { command } => run_wiki(command),
        KnowledgeCommand::Compass { command } => run_compass(command),
        KnowledgeCommand::Skill { command } => not_ported(match command {
            SkillGroup::Generate => "skill generate",
        }),
        KnowledgeCommand::Task { command } => run_task(command),
        other => not_ported(other.name()),
    }
}

fn run_knowledge(group: KnowledgeGroup) -> ExitCode {
    match group {
        KnowledgeGroup::Add {
            file,
            body,
            title,
            doc_type,
            tags,
            affects,
            affects_modules,
            epic,
            resource,
            description,
        } => knowledge_add(
            file.as_deref(),
            body.as_deref(),
            &title,
            &doc_type,
            &tags,
            &affects,
            &affects_modules,
            &epic,
            &resource,
            description.as_deref(),
        ),
        KnowledgeGroup::Import {
            dir_path,
            doc_type,
            tags,
            affects,
        } => knowledge_import(&dir_path, &doc_type, &tags, &affects),
        KnowledgeGroup::Islands { as_json, db } => knowledge_islands(as_json, db.as_deref()),
        KnowledgeGroup::Related {
            doc_id,
            as_json,
            db,
        } => knowledge_related(&doc_id, as_json, db.as_deref()),
        KnowledgeGroup::Chain {
            doc_id,
            as_json,
            db,
        } => knowledge_chain(&doc_id, as_json, db.as_deref()),
        KnowledgeGroup::Search {
            query,
            limit,
            threshold: _,
            as_json,
            db,
        } => knowledge_search(&query, limit, as_json, db.as_deref()),
        KnowledgeGroup::List {
            doc_type,
            status,
            tag,
            as_json,
        } => knowledge_list(
            doc_type.as_deref(),
            status.as_deref(),
            tag.as_deref(),
            as_json,
        ),
        KnowledgeGroup::Impact { query, db, limit } => {
            knowledge_impact(&query, db.as_deref(), limit)
        }
        KnowledgeGroup::Remove { doc_id, db } => knowledge_remove(&doc_id, db.as_deref()),
        KnowledgeGroup::Status { doc_id, new_status } => knowledge_status(&doc_id, &new_status),
        KnowledgeGroup::Export { out } => knowledge_export(&out),
        KnowledgeGroup::Workflow { command } => run_workflow(command),
        other => not_ported(other.name()),
    }
}

fn run_workflow(group: WorkflowGroup) -> ExitCode {
    match group {
        WorkflowGroup::Add {
            title,
            steps_raw,
            steps_file,
            tags,
            affects,
            affects_modules,
            resource,
        } => workflow_add(
            &title,
            &steps_raw,
            steps_file.as_deref(),
            &tags,
            &affects,
            &affects_modules,
            &resource,
        ),
        WorkflowGroup::Trace { reference, as_json } => workflow_trace(&reference, as_json),
        WorkflowGroup::Sync { .. } => not_ported("knowledge workflow sync"),
    }
}

fn run_memory(group: MemoryGroup) -> ExitCode {
    match group {
        MemoryGroup::Record {
            mtype,
            title,
            body,
            resource,
            confidence,
            recurrence_key,
            stance,
            db,
            knowledge,
        } => {
            if !MEMORY_TYPES.contains(&mtype.as_str()) {
                return usage_error(
                    "memory record [OPTIONS] MTYPE TITLE",
                    &format!(
                        "Invalid value for 'MTYPE': '{mtype}' is not one of {}.",
                        MEMORY_TYPES
                            .iter()
                            .map(|t| format!("'{t}'"))
                            .collect::<Vec<_>>()
                            .join(", ")
                    ),
                );
            }
            memory_record(
                &mtype,
                &title,
                &body,
                resource.as_deref(),
                confidence,
                recurrence_key.as_deref(),
                stance.as_deref(),
                db.as_deref(),
                knowledge.as_deref(),
            )
        }
        MemoryGroup::Search {
            query,
            tier,
            as_of,
            agent,
            db,
            knowledge,
        } => memory_search(
            &query,
            tier.as_deref(),
            as_of.as_deref(),
            agent.as_deref(),
            db.as_deref(),
            knowledge.as_deref(),
        ),
        MemoryGroup::Timeline {
            symbol,
            db,
            knowledge,
        } => memory_timeline(&symbol, db.as_deref(), knowledge.as_deref()),
        MemoryGroup::List {
            tier,
            tag,
            db,
            knowledge,
        } => memory_list(
            tier.as_deref(),
            tag.as_deref(),
            db.as_deref(),
            knowledge.as_deref(),
        ),
        MemoryGroup::Stats { knowledge } => memory_stats(knowledge.as_deref()),
        MemoryGroup::Digest {
            limit,
            db,
            knowledge,
        } => memory_digest(limit, db.as_deref(), knowledge.as_deref()),
        MemoryGroup::Promote {
            path,
            db,
            knowledge,
        } => memory_promote(&path, db.as_deref(), knowledge.as_deref()),
        MemoryGroup::Decay { db, knowledge } => memory_decay(db.as_deref(), knowledge.as_deref()),
        MemoryGroup::Forget {
            memory_path,
            db,
            knowledge,
        } => memory_forget(&memory_path, db.as_deref(), knowledge.as_deref()),
        MemoryGroup::Demote {
            memory_path,
            tier,
            db,
            knowledge,
        } => memory_demote(&memory_path, &tier, db.as_deref(), knowledge.as_deref()),
        MemoryGroup::Purge {
            max_days,
            dry_run,
            db,
            knowledge,
        } => memory_purge(max_days, dry_run, db.as_deref(), knowledge.as_deref()),
        MemoryGroup::Share {
            memory_id,
            agent,
            symbols,
            db,
        } => memory_share(memory_id.as_deref(), &agent, &symbols, db.as_deref()),
        MemoryGroup::Check { agent, symbols, db } => memory_check(&agent, &symbols, db.as_deref()),
        other => not_ported(other.name()),
    }
}

fn run_wiki(group: WikiGroup) -> ExitCode {
    match group {
        WikiGroup::Search { query, knowledge } => wiki_search(&query, knowledge.as_deref()),
        other => not_ported(other.name()),
    }
}

fn run_compass(group: CompassGroup) -> ExitCode {
    match group {
        CompassGroup::List { knowledge } => compass_list(knowledge.as_deref()),
        CompassGroup::Validate { db, knowledge } => {
            compass_validate(db.as_deref(), knowledge.as_deref())
        }
        other => not_ported(other.name()),
    }
}

fn run_task(group: TaskGroup) -> ExitCode {
    match group {
        TaskGroup::List {
            status,
            kind,
            kind_prefix,
            knowledge,
        } => task_list(
            status.as_deref(),
            kind.as_deref(),
            kind_prefix.as_deref(),
            knowledge.as_deref(),
        ),
        TaskGroup::Show { task_id, knowledge } => task_show(&task_id, knowledge.as_deref()),
        TaskGroup::Claim {
            task_id,
            assigned_to,
            knowledge,
        } => task_claim(&task_id, &assigned_to, knowledge.as_deref()),
        TaskGroup::Complete {
            task_id,
            result,
            result_file,
            claimer,
            db,
            knowledge,
        } => task_complete(
            &task_id,
            result.as_deref(),
            result_file.as_deref(),
            &claimer,
            db.as_deref(),
            knowledge.as_deref(),
        ),
        TaskGroup::Drop { task_id, knowledge } => task_drop(&task_id, knowledge.as_deref()),
    }
}

// --- task queue (cli/task.py port) ------------------------------------------------

/// The --knowledge default for the task group: the resolved store's bundle.
fn task_bundle(knowledge: Option<&str>) -> Bundle {
    resolve_bundle(&knowledge.map_or_else(|| resolve_store(None).knowledge, PathBuf::from))
}

fn task_list(
    status: Option<&str>,
    kind: Option<&str>,
    kind_prefix: Option<&str>,
    knowledge: Option<&str>,
) -> ExitCode {
    let bundle = task_bundle(knowledge);
    let tasks = match tasks::list_tasks(&bundle, status, kind, kind_prefix) {
        Ok(tasks) => tasks,
        Err(e) => return cli_err(&e.0),
    };
    if tasks.is_empty() {
        println!("No tasks.");
        return ExitCode::SUCCESS;
    }
    for t in tasks {
        println!(
            "  [{:<11}] {:<20} {}  {}",
            t.status, t.task_kind, t.id, t.resource
        );
    }
    ExitCode::SUCCESS
}

fn task_show(task_id: &str, knowledge: Option<&str>) -> ExitCode {
    let bundle = task_bundle(knowledge);
    let task = match tasks::get_task(&bundle, task_id) {
        Ok(task) => task,
        Err(e) => return cli_err(&e.0),
    };
    let Some(t) = task else {
        eprintln!("No task '{task_id}'.");
        return ExitCode::FAILURE;
    };
    println!("# Task {}", t.id);
    println!(
        "kind: {}   status: {}   attempt: {}",
        t.task_kind, t.status, t.attempt
    );
    println!("resource: {}", t.resource);
    println!();
    if !t.facts.is_empty() {
        println!("## Facts");
        for (key, val) in &t.facts {
            println!("  {}: {}", tasks::py_display(key), tasks::py_display(val));
        }
    }
    println!();
    if t.status == "done" {
        if let Some(result) = tasks::read_result(&bundle, task_id) {
            println!("## Result");
            println!("{result}");
        }
    } else {
        println!("To process: `cairn task claim {task_id}`");
    }
    ExitCode::SUCCESS
}

fn task_claim(task_id: &str, assigned_to: &str, knowledge: Option<&str>) -> ExitCode {
    let bundle = task_bundle(knowledge);
    match tasks::claim_task(&bundle, task_id, assigned_to) {
        Ok(Some(_)) => {
            println!("Claimed {task_id}. Write your result, then:");
            let mut hint = format!("  cairn task complete {task_id} --result-file <path>");
            if !assigned_to.is_empty() {
                hint.push_str(&format!(" --as {assigned_to}"));
            }
            println!("{hint}");
            ExitCode::SUCCESS
        }
        Ok(None) => {
            eprintln!("Could not claim '{task_id}' (not pending or not found).");
            ExitCode::FAILURE
        }
        Err(e) => cli_err(&e.0),
    }
}

fn task_complete(
    task_id: &str,
    result: Option<&str>,
    result_file: Option<&str>,
    claimer: &str,
    db: Option<&str>,
    knowledge: Option<&str>,
) -> ExitCode {
    let bundle = task_bundle(knowledge);
    let result = if let Some(path) = result_file {
        match std::fs::read_to_string(path) {
            Ok(text) => text,
            Err(e) => {
                eprintln!("Cannot read result file: {}", py_oserror_text(&e, path));
                return ExitCode::FAILURE;
            }
        }
    } else {
        result.unwrap_or_default().to_string()
    };
    if result.is_empty() {
        eprintln!("No result provided. Use --result or --result-file.");
        return ExitCode::FAILURE;
    }
    let paths = resolve_store(None);
    // get_db refuses a missing store parent; never fabricate one
    let db_file = match require_db_dir(db) {
        Ok(path) => path,
        Err(code) => return code,
    };
    let Ok(conn) = open_db_writable(&db_file) else {
        return ExitCode::FAILURE;
    };
    let outcome = match tasks::complete_task(
        &bundle,
        task_id,
        &result,
        Some(&conn),
        Some(claimer),
        Some(&paths.workspace),
    ) {
        Ok(outcome) => outcome,
        Err(e) => return cli_err(&e.0),
    };
    if outcome.refused {
        let reasons = if outcome.errors.is_empty() {
            "unknown".to_string()
        } else {
            outcome.errors.join("; ")
        };
        eprintln!("Task {task_id} was not completed: {reasons}.");
        return ExitCode::FAILURE;
    }
    if outcome.dropped {
        eprintln!(
            "Task {task_id} dropped after {} failed attempts.",
            tasks::MAX_REVISE_CYCLES
        );
        for err in &outcome.errors {
            eprintln!("  Error: {err}");
        }
        return ExitCode::FAILURE;
    }
    if outcome.revised {
        println!("Task {task_id} had errors. A revise task has been spawned.");
        eprintln!("Errors:");
        for err in &outcome.errors {
            eprintln!("  - {err}");
        }
        println!("Quality score: {:.2}", outcome.quality);
    } else if outcome.promoted {
        println!("Task {task_id} completed and promoted.");
    } else if !outcome.errors.is_empty() {
        eprintln!(
            "Task {task_id} was not completed; it remains in-progress and can be completed again."
        );
        for err in &outcome.errors {
            eprintln!("  Error: {err}");
        }
        return ExitCode::FAILURE;
    } else {
        println!("Task {task_id} completed. Result stored.");
        println!("Quality score: {:.2}", outcome.quality);
    }
    ExitCode::SUCCESS
}

/// Python OSError str(): "[Errno <n>] <message>: '<path>'".
fn py_oserror_text(e: &std::io::Error, path: &str) -> String {
    let message = e
        .to_string()
        .trim_end_matches(&format!(
            " (os error {})",
            e.raw_os_error().unwrap_or_default()
        ))
        .to_string();
    match e.raw_os_error() {
        Some(code) => format!("[Errno {code}] {message}: '{path}'"),
        None => format!("{message}: '{path}'"),
    }
}

fn task_drop(task_id: &str, knowledge: Option<&str>) -> ExitCode {
    let bundle = task_bundle(knowledge);
    let outcome = match tasks::drop_task(&bundle, task_id) {
        Ok(outcome) => outcome,
        Err(e) => return cli_err(&e.0),
    };
    if !outcome.dropped {
        let joined = outcome.errors.join("; ");
        let reasons = if joined.is_empty() {
            "not droppable".to_string()
        } else {
            joined
        };
        eprintln!("Could not drop '{task_id}': {reasons}");
        return ExitCode::FAILURE;
    }
    println!("Dropped {task_id}.");
    ExitCode::SUCCESS
}

// --- shared output/store helpers -------------------------------------------------

fn not_ported(name: &str) -> ExitCode {
    display::error(&format!(
        "cairn {name} is not implemented in the Rust CLI yet"
    ));
    ExitCode::FAILURE
}

fn cli_err(msg: &str) -> ExitCode {
    eprintln!("Error: {msg}");
    ExitCode::FAILURE
}

/// click.UsageError rendering: usage line + hint + blank + Error, exit 2.
fn usage_error(usage: &str, msg: &str) -> ExitCode {
    eprintln!("Usage: cairn {usage}");
    eprintln!("Try 'cairn {usage} --help' for help.");
    eprintln!();
    eprintln!("Error: {msg}");
    ExitCode::from(2)
}

/// Python str() of a float ("0.62", "1.0").
fn py_float_text(f: f64) -> String {
    let s = format!("{f}");
    if s.contains('.') || s.contains('e') || s.contains("inf") || s.contains("NaN") {
        s
    } else {
        format!("{s}.0")
    }
}

/// YAML scalar as Python str() would render it; None for null/composite.
fn yaml_scalar_text(v: &Yaml) -> Option<String> {
    match v {
        Yaml::String(s) | Yaml::Real(s) => Some(s.clone()),
        Yaml::Integer(i) => Some(i.to_string()),
        Yaml::Boolean(true) => Some("True".to_string()),
        Yaml::Boolean(false) => Some("False".to_string()),
        _ => None,
    }
}

fn resolve_bundle(knowledge: &Path) -> Bundle {
    Bundle::new(knowledge)
}

/// The knowledge bundle for the knowledge group (CAIRN_KNOWLEDGE-aware).
fn knowledge_bundle() -> Bundle {
    resolve_bundle(&resolve_store(None).knowledge)
}

/// The --knowledge default for memory/compass/wiki: the resolved db's sibling.
fn knowledge_default() -> PathBuf {
    resolve_store(None)
        .db
        .parent()
        .map(|p| p.join(".knowledge"))
        .unwrap_or_default()
}

fn memory_bundle(knowledge: Option<&str>) -> Bundle {
    resolve_bundle(&knowledge.map_or_else(knowledge_default, PathBuf::from))
}

/// Effective db path: explicit > the resolved store.
fn db_path(db: Option<&str>) -> PathBuf {
    db.map_or_else(|| resolve_store(None).db, PathBuf::from)
}

/// _require_db_dir: exit cleanly when the db parent is missing.
fn require_db_dir(db: Option<&str>) -> Result<PathBuf, ExitCode> {
    let path = db_path(db);
    match path.parent() {
        Some(parent) if parent.exists() => Ok(path),
        _ => Err(cli_err(&format!(
            "store directory does not exist: {}. \
             Run `cairn init && cairn build` first.",
            path.parent()
                .map_or_else(Default::default, |p| p.display().to_string())
        ))),
    }
}

fn open_db_writable(path: &Path) -> Result<Connection, rusqlite::Error> {
    store_schema::open_initialized(path)
}

fn open_db_read_only(path: &Path) -> Result<Connection, rusqlite::Error> {
    store_conn::open_read_only(path)
}

/// One memory-listing line: '[<tier> ]<score>, refs-verified=<refs>[, stance=...] title [id]'.
/// Python str(round(x, 3)): correct decimal rounding, half to even.
fn py_round3_text(x: f64) -> String {
    let fixed = format!("{x:.3}");
    py_float_text(fixed.parse().unwrap_or(x))
}

fn memory_line(
    c: &Concept,
    conn: Option<&Connection>,
    with_tier: bool,
    with_concept_id: bool,
) -> String {
    let refs = match conn {
        None => "?".to_string(),
        Some(conn) => tools_km::graph_verification(c, conn)
            .map(py_round3_text)
            .unwrap_or_else(|_| "?".to_string()),
    };
    let tier = if with_tier {
        format!("{} ", c.ext_str("memory_tier").unwrap_or("?"))
    } else {
        String::new()
    };
    let cid = if with_concept_id {
        format!("  ({})", c.concept_id)
    } else {
        String::new()
    };
    let score = c
        .ext("memory_score")
        .and_then(yaml_scalar_text)
        .unwrap_or_else(|| "?".to_string());
    let mut stance_seg = String::new();
    if let Some(stance) = c.ext_str("memory_stance") {
        stance_seg = format!(", stance={stance}");
        if stance == "contested"
            && let Some(peer) = c.ext_str("memory_stance_peer")
        {
            stance_seg.push_str(&format!(" peer={peer}"));
        }
    }
    let title = c.title.as_deref().unwrap_or("None");
    format!("  [{tier}{score}, refs-verified={refs}{stance_seg}] {title}{cid}")
}

/// Python \b<symbol>\b containment on a lowercased haystack.
fn has_word(hay_lower: &str, symbol_lower: &str) -> bool {
    if symbol_lower.is_empty() {
        return false;
    }
    let is_word = |c: char| c.is_alphanumeric() || c == '_';
    let mut start = 0;
    while let Some(pos) = hay_lower[start..].find(symbol_lower) {
        let at = start + pos;
        let end = at + symbol_lower.len();
        let before = hay_lower[..at].chars().next_back();
        let after = hay_lower[end..].chars().next();
        if !before.is_some_and(is_word) && !after.is_some_and(is_word) {
            return true;
        }
        start = at + symbol_lower.len().max(1);
    }
    false
}

/// Python str.title(): alpha chars after any non-alpha boundary capitalize.
fn py_title(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut prev_alpha = false;
    for c in s.chars() {
        if c.is_alphabetic() {
            if prev_alpha {
                out.extend(c.to_lowercase());
            } else {
                out.extend(c.to_uppercase());
            }
        } else {
            out.push(c);
        }
        prev_alpha = c.is_alphabetic();
    }
    out
}

/// Import-collision slug: "(repo)" style suffix, numbered past the first.
fn unique_slug(base: &str, repo: &str, seen: &mut std::collections::HashSet<String>) -> String {
    const SLUG_MAX: usize = 60;
    let collision = |counter: usize| -> String {
        let mut parts = vec![tools_km::slugify(repo)];
        if counter > 1 {
            parts.push(counter.to_string());
        }
        let suffix = parts
            .into_iter()
            .filter(|p| !p.is_empty())
            .collect::<Vec<_>>()
            .join("-");
        let room = SLUG_MAX as i64 - suffix.len() as i64 - 1;
        if room <= 0 {
            return suffix;
        }
        let trimmed = base
            .chars()
            .take(room as usize)
            .collect::<String>()
            .trim_end_matches('-')
            .to_string();
        if trimmed.is_empty() {
            suffix
        } else {
            format!("{trimmed}-{suffix}")
        }
    };
    let mut counter = 1;
    let mut candidate = collision(counter);
    while seen.contains(&candidate) {
        counter += 1;
        candidate = collision(counter);
    }
    candidate
}

// --- knowledge group ---------------------------------------------------------

#[allow(clippy::too_many_arguments)]
fn knowledge_add(
    file: Option<&str>,
    body_text: Option<&str>,
    title: &str,
    doc_type: &str,
    tags: &str,
    affects: &str,
    affects_modules: &str,
    epic: &str,
    resource: &str,
    description: Option<&str>,
) -> ExitCode {
    let body = if let Some(path) = file {
        match std::fs::read_to_string(path) {
            Ok(text) => text,
            Err(e) => return cli_err(&e.to_string()),
        }
    } else if let Some(text) = body_text {
        text.to_string()
    } else {
        return cli_err("--file or --body required.");
    };
    let paths = resolve_store(None);
    if paths.ensure().is_err() {
        return cli_err("could not create the store directories");
    }
    let bundle = resolve_bundle(&paths.knowledge);
    let replaced = matches!(
        tools_km::get_document(&bundle, &tools_km::doc_concept_id(title, doc_type, None)),
        Ok(Some(_))
    );
    let cid = match tools_km::add_document(
        &bundle,
        title,
        &body,
        doc_type,
        tools_km::split_csv(tags),
        tools_km::split_csv(affects_modules),
        tools_km::split_csv(affects),
        if resource.is_empty() {
            None
        } else {
            Some(resource)
        },
        None,
        if epic.is_empty() { None } else { Some(epic) },
        None,
        description,
        "manual",
    ) {
        Ok(cid) => cid,
        Err(e) => return cli_err(&e.0),
    };
    let note = if replaced {
        " (replaced an existing document with the same title and doc_type)"
    } else {
        ""
    };
    println!("Stored: {cid}{note}");
    ExitCode::SUCCESS
}

fn knowledge_import(dir_path: &str, doc_type: &str, tags: &str, affects: &str) -> ExitCode {
    let root = PathBuf::from(dir_path);
    if !root.is_dir() {
        return cli_err(&format!("Import directory does not exist: {dir_path}"));
    }
    let paths = resolve_store(None);
    if paths.ensure().is_err() {
        return cli_err("could not create the store directories");
    }
    let bundle = resolve_bundle(&paths.knowledge);
    let mut imported: Vec<String> = Vec::new();
    let mut seen_slugs: std::collections::HashSet<String> = std::collections::HashSet::new();
    let mut md_files: Vec<PathBuf> = walk_md(&root);
    md_files.sort();
    for md_file in md_files {
        let Ok(meta) = std::fs::metadata(&md_file) else {
            continue;
        };
        if meta.len() > IMPORT_MAX_FILE_SIZE {
            continue;
        }
        let Ok(text) = std::fs::read_to_string(&md_file) else {
            continue;
        };
        let stem = md_file
            .file_stem()
            .map(|s| s.to_string_lossy().to_string())
            .unwrap_or_default();
        let title = py_title(&stem.replace(['-', '_'], " "));
        let slug = unique_slug(
            &tools_km::slugify(&title),
            dir_name(&md_file).as_str(),
            &mut seen_slugs,
        );
        seen_slugs.insert(slug.clone());
        match tools_km::add_document_with_slug(
            &bundle,
            &title,
            &text,
            doc_type,
            tools_km::split_csv(tags),
            Vec::new(),
            tools_km::split_csv(affects),
            None,
            None,
            None,
            None,
            None,
            "imported",
            Some(&slug),
        ) {
            Ok(cid) => imported.push(cid),
            Err(_) => continue,
        }
    }
    println!("Imported {} document(s).", imported.len());
    for cid in &imported {
        println!("  {cid}");
    }
    ExitCode::SUCCESS
}

fn walk_md(root: &Path) -> Vec<PathBuf> {
    let mut out = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let Ok(entries) = std::fs::read_dir(&dir) else {
            continue;
        };
        for entry in entries.flatten() {
            let p = entry.path();
            if p.is_dir() {
                stack.push(p);
            } else if p.extension().is_some_and(|e| e == "md") {
                out.push(p);
            }
        }
    }
    out
}

fn dir_name(path: &Path) -> String {
    path.parent()
        .map(|p| {
            p.file_name()
                .map(|n| n.to_string_lossy().to_string())
                .unwrap_or_default()
        })
        .unwrap_or_default()
}

fn knowledge_list(
    doc_type: Option<&str>,
    status: Option<&str>,
    tag: Option<&str>,
    as_json: bool,
) -> ExitCode {
    let bundle = knowledge_bundle();
    let docs = tools_km::list_documents(&bundle, doc_type, status, tag);
    if as_json {
        let rows: Vec<Value> = docs
            .iter()
            .map(|c| {
                let concept_id = tools_km::normalize_doc_id(&bundle, &c.concept_id);
                let parts: Vec<&str> = concept_id.split('/').collect();
                json!({
                    "concept_id": concept_id,
                    "title": c.title,
                    "doc_type": if parts.len() > 2 { parts[1].to_string() } else { String::new() },
                    "doc_status": c.ext("doc_status").map(py_json::yaml_to_value).unwrap_or(Value::Null),
                })
            })
            .collect();
        println!("{}", py_json::dumps(&Value::Array(rows)));
        return ExitCode::SUCCESS;
    }
    if docs.is_empty() {
        println!("No knowledge documents found.");
        return ExitCode::SUCCESS;
    }
    for c in &docs {
        let st = c.ext_str("doc_status").unwrap_or("?");
        let concept_id = tools_km::normalize_doc_id(&bundle, &c.concept_id);
        let title = c.title.as_deref().unwrap_or("None");
        println!("  [{st}] {title}  ({concept_id})");
    }
    ExitCode::SUCCESS
}

fn hit_json(r: &KnowledgeHit) -> Value {
    let score = if r.provenance == "lexical_knowledge" {
        json!(r.score as i64)
    } else {
        json!(r.score)
    };
    let mut row = serde_json::Map::new();
    row.insert("concept_id".into(), json!(r.concept_id));
    row.insert("title".into(), json!(r.title));
    row.insert("doc_type".into(), json!(r.doc_type));
    row.insert("score".into(), score);
    row.insert("provenance".into(), json!(r.provenance));
    row.insert("affects_modules".into(), json!(r.affects_modules));
    row.insert("affects_repos".into(), json!(r.affects_repos));
    row.insert("chunk".into(), json!(r.chunk));
    if !r.affects_repos.is_empty() {
        let deps: serde_json::Map<String, Value> = r
            .graph_deps
            .iter()
            .map(|(repo, y)| (repo.clone(), py_json::yaml_to_value(y)))
            .collect();
        row.insert("graph_deps".into(), Value::Object(deps));
    }
    Value::Object(row)
}

fn knowledge_search(query: &str, limit: i64, as_json: bool, db: Option<&str>) -> ExitCode {
    let paths = resolve_store(None);
    let bundle = resolve_bundle(&paths.knowledge);
    if !bundle.root.exists() || !db_path(db).parent().is_some_and(|p| p.exists()) {
        println!("No knowledge documents matching '{query}'.");
        return ExitCode::SUCCESS;
    }
    let Ok(conn) = open_db_writable(&db_path(db)) else {
        return ExitCode::FAILURE;
    };
    let limit = limit.clamp(0, i64::MAX) as usize;
    let results = match tools_km::search_knowledge_docs(&conn, &bundle, query, limit) {
        Ok(rows) => rows,
        Err(e) => return cli_err(&e.0),
    };
    if as_json {
        let rows: Vec<Value> = results.iter().map(hit_json).collect();
        println!("{}", py_json::dumps(&Value::Array(rows)));
        return ExitCode::SUCCESS;
    }
    if results.is_empty() {
        println!("No knowledge documents matching '{query}'.");
        return ExitCode::SUCCESS;
    }
    println!("{} knowledge doc(s) for '{query}':", results.len());
    for r in &results {
        println!("  [{}] {} ({})", r.provenance, r.title, r.doc_type);
    }
    ExitCode::SUCCESS
}

fn knowledge_impact(query: &str, db: Option<&str>, limit: i64) -> ExitCode {
    let paths = resolve_store(None);
    let bundle = resolve_bundle(&paths.knowledge);
    if !bundle.root.exists() || !db_path(db).parent().is_some_and(|p| p.exists()) {
        println!("No knowledge documents matching '{query}'.");
        return ExitCode::SUCCESS;
    }
    let Ok(conn) = open_db_writable(&db_path(db)) else {
        return ExitCode::FAILURE;
    };
    let results =
        match tools_km::search_knowledge_docs(&conn, &bundle, query, limit.max(0) as usize) {
            Ok(rows) => rows,
            Err(e) => return cli_err(&e.0),
        };
    if results.is_empty() {
        println!("No knowledge documents matching '{query}'.");
        return ExitCode::SUCCESS;
    }
    println!("=== Knowledge impact: '{query}' ===");
    for r in &results {
        println!("\n[{}] {}", r.provenance, r.title);
        println!("  doc: {}", r.concept_id);
        if !r.affects_repos.is_empty() {
            println!("  affects_repos: {}", r.affects_repos.join(", "));
        }
        for (repo, deps) in &r.graph_deps {
            let Yaml::Hash(hash) = deps else {
                continue;
            };
            if let Some(err) = hash.get(&Yaml::String("error".into())) {
                println!(
                    "  {repo} → graph lookup failed: {}",
                    yaml_scalar_text(err).unwrap_or_default()
                );
                continue;
            }
            let dep_repos: Vec<String> = match hash.get(&Yaml::String("dependencies".into())) {
                Some(Yaml::Array(items)) => items
                    .iter()
                    .filter_map(|d| match d {
                        Yaml::Hash(dh) => dh
                            .get(&Yaml::String("repo".into()))
                            .and_then(yaml_scalar_text)
                            .filter(|s| !s.is_empty()),
                        _ => None,
                    })
                    .collect(),
                _ => Vec::new(),
            };
            if !dep_repos.is_empty() {
                println!("  {repo} → depends on: {}", dep_repos.join(", "));
            } else {
                println!("  {repo} → no cross-repo dependencies in the graph.");
            }
        }
    }
    ExitCode::SUCCESS
}

fn knowledge_islands(as_json: bool, db: Option<&str>) -> ExitCode {
    let paths = resolve_store(None);
    let bundle = resolve_bundle(&paths.knowledge);
    if !bundle.root.exists() {
        println!("No knowledge store; nothing to inspect.");
        return ExitCode::SUCCESS;
    }
    let Ok(db_file) = require_db_dir(db) else {
        return ExitCode::FAILURE;
    };
    let Ok(conn) = open_db_writable(&db_file) else {
        return ExitCode::FAILURE;
    };
    let islands = tools_km::doc_islands(&conn, &bundle);
    let mut queued: std::collections::HashMap<Vec<String>, (String, String)> =
        std::collections::HashMap::new();
    for (id, status, members) in tools_km::doc_link_tasks(&bundle) {
        let mut key = members;
        key.sort();
        key.dedup();
        queued.insert(key, (id, status));
    }
    let lookup = |members: &[String]| -> Option<&(String, String)> {
        let mut key = members.to_vec();
        key.sort();
        key.dedup();
        queued.get(&key)
    };
    if as_json {
        let rows: Vec<Value> = islands
            .iter()
            .map(|members| {
                let task = lookup(members);
                json!({
                    "members": members,
                    "task_id": task.map(|(id, _)| id.clone()),
                    "task_status": task.map(|(_, status)| status.clone()),
                })
            })
            .collect();
        println!("{}", py_json::dumps(&Value::Array(rows)));
        return ExitCode::SUCCESS;
    }
    if islands.is_empty() {
        println!("No islands: every knowledge doc is connected.");
        return ExitCode::SUCCESS;
    }
    println!("{} island(s) worth linking:", islands.len());
    for members in &islands {
        let state = match lookup(members) {
            Some((id, status)) => format!("doc-link task {id} ({status})"),
            None => "no doc-link task yet".to_string(),
        };
        println!("  {}  [{state}]", members.join(" + "));
    }
    ExitCode::SUCCESS
}

fn resolve_or_exit(bundle: &Bundle, doc_id: &str) -> Result<Concept, ExitCode> {
    if !bundle.root.exists() {
        return Err(cli_err(&format!(
            "unknown knowledge document: '{doc_id}'. No knowledge store at {} -- \
             ingest (`cairn knowledge ingest --ingest`) or add documents first.",
            bundle.root.display()
        )));
    }
    match tools_km::resolve_knowledge_doc(bundle, doc_id) {
        Ok(Some(concept)) => Ok(concept),
        Ok(None) => Err(cli_err(&format!(
            "Unknown knowledge document: '{doc_id}'. \
             Run `cairn knowledge list` for stored doc ids."
        ))),
        Err(e) => Err(cli_err(&e.0)),
    }
}

fn knowledge_related(doc_id: &str, as_json: bool, db: Option<&str>) -> ExitCode {
    let bundle = knowledge_bundle();
    if resolve_or_exit(&bundle, doc_id).is_err() {
        return ExitCode::FAILURE;
    }
    let Ok(db_file) = require_db_dir(db) else {
        return ExitCode::FAILURE;
    };
    let Ok(conn) = open_db_writable(&db_file) else {
        return ExitCode::FAILURE;
    };
    let neighbors = match tools_km::related_docs(&conn, &bundle, doc_id) {
        Ok(rows) => rows,
        Err(e) => return cli_err(&e.0),
    };
    if as_json {
        let rows: Vec<Value> = neighbors
            .iter()
            .map(|n: &RelatedDoc| {
                json!({
                    "doc_id": n.doc_id,
                    "title": n.title,
                    "relation": n.relation,
                    "kind": n.kind,
                    "provenance": n.provenance,
                    "created_at": n.created_at.and_then(serde_json::Number::from_f64),
                    "direction": n.direction,
                })
            })
            .collect();
        println!("{}", py_json::dumps(&Value::Array(rows)));
        return ExitCode::SUCCESS;
    }
    if neighbors.is_empty() {
        println!("No stored relationships for '{doc_id}'.");
        return ExitCode::SUCCESS;
    }
    println!("{} related doc(s) for '{doc_id}':", neighbors.len());
    for n in &neighbors {
        let suffix = match &n.title {
            Some(title) if !title.is_empty() => format!(" ({title})"),
            _ => String::new(),
        };
        println!(
            "  {} ({}) [{}] {}{suffix}",
            n.relation, n.kind, n.direction, n.doc_id
        );
    }
    ExitCode::SUCCESS
}

fn knowledge_chain(doc_id: &str, as_json: bool, db: Option<&str>) -> ExitCode {
    let bundle = knowledge_bundle();
    if resolve_or_exit(&bundle, doc_id).is_err() {
        return ExitCode::FAILURE;
    }
    let Ok(db_file) = require_db_dir(db) else {
        return ExitCode::FAILURE;
    };
    let Ok(conn) = open_db_writable(&db_file) else {
        return ExitCode::FAILURE;
    };
    let chain = match tools_km::supersede_chain(&conn, &bundle, doc_id) {
        Ok(Some(chain)) => chain,
        Ok(None) => {
            return cli_err(&format!(
                "Unknown knowledge document: '{doc_id}'. \
                 Run `cairn knowledge list` for stored doc ids."
            ));
        }
        Err(e) => return cli_err(&e.0),
    };
    if as_json {
        let rows: Vec<Value> = chain
            .iter()
            .map(|m| {
                json!({
                    "concept_id": m.concept_id,
                    "title": m.title,
                    "doc_status": m.doc_status,
                    "relation": m.relation,
                })
            })
            .collect();
        println!("{}", py_json::dumps(&Value::Array(rows)));
        return ExitCode::SUCCESS;
    }
    println!(
        "Supersede chain for '{doc_id}' ({} doc(s), oldest -> newest):",
        chain.len()
    );
    for (pos, member) in chain.iter().enumerate() {
        let suffix = match &member.title {
            Some(title) if !title.is_empty() => format!(" ({title})"),
            _ => String::new(),
        };
        println!("  {}. {}{suffix}", pos + 1, member.concept_id);
        if let Some(relation) = member.relation {
            println!("     -> {relation}");
        }
    }
    ExitCode::SUCCESS
}

fn knowledge_remove(doc_id: &str, db: Option<&str>) -> ExitCode {
    let paths = resolve_store(None);
    let bundle = resolve_bundle(&paths.knowledge);
    if !bundle.root.exists() || !db_path(db).parent().is_some_and(|p| p.exists()) {
        return cli_err(&format!("Not found: '{doc_id}'."));
    }
    let Ok(conn) = open_db_writable(&db_path(db)) else {
        return ExitCode::FAILURE;
    };
    match tools_km::delete_document(&bundle, doc_id, Some(&conn), true) {
        Ok(true) => {
            println!("Deleted knowledge document: {doc_id}");
            ExitCode::SUCCESS
        }
        Ok(false) => cli_err(&format!("Not found: '{doc_id}'.")),
        Err(e) => cli_err(&e.0),
    }
}

fn knowledge_status(doc_id: &str, new_status: &str) -> ExitCode {
    let paths = resolve_store(None);
    let bundle = resolve_bundle(&paths.knowledge);
    match tools_km::update_status(&bundle, doc_id, new_status) {
        Ok(true) => {
            println!("Updated '{doc_id}' status -> {new_status}");
            ExitCode::SUCCESS
        }
        Ok(false) => cli_err(&format!(
            "Not found, unknown status, or backward transition rejected: '{doc_id}' -> '{new_status}'."
        )),
        Err(e) => cli_err(&e.0),
    }
}

fn knowledge_export(out_path: &str) -> ExitCode {
    let kn_dir = resolve_store(None).knowledge;
    if !kn_dir.exists() {
        println!("Knowledge bundle directory does not exist.");
        return ExitCode::SUCCESS;
    }
    if out_path.ends_with(".tar.gz") || out_path.ends_with(".tgz") || out_path.ends_with(".tar") {
        return not_ported("knowledge export --out <tarball>");
    }
    let dst = PathBuf::from(out_path);
    if std::fs::create_dir_all(&dst).is_err() {
        return cli_err("could not create the export directory");
    }
    let target = dst.join(".knowledge");
    if target.exists() {
        let _ = std::fs::remove_dir_all(&target);
    }
    if copy_tree(&kn_dir, &target).is_err() {
        return cli_err("could not copy the knowledge bundle");
    }
    println!("Exported knowledge bundle directory to {}", dst.display());
    ExitCode::SUCCESS
}

fn copy_tree(src: &Path, dst: &Path) -> std::io::Result<()> {
    std::fs::create_dir_all(dst)?;
    for entry in std::fs::read_dir(src)? {
        let entry = entry?;
        let target = dst.join(entry.file_name());
        if entry.file_type()?.is_dir() {
            copy_tree(&entry.path(), &target)?;
        } else {
            std::fs::copy(entry.path(), &target)?;
        }
    }
    Ok(())
}

// --- knowledge workflow subgroup ---------------------------------------------

fn parse_step(raw: &str) -> WorkflowStep {
    let parts: Vec<&str> = raw.split("::").collect();
    let field = |i: usize| {
        parts
            .get(i)
            .filter(|s| !s.is_empty())
            .map(|s| s.to_string())
    };
    WorkflowStep {
        name: Some(parts.first().map_or_else(String::new, |s| s.to_string())),
        description: field(1),
        symbol: field(2),
        file: field(3),
    }
}

fn steps_from_yaml(value: &Yaml) -> Option<Vec<WorkflowStep>> {
    let Yaml::Array(items) = value else {
        return None;
    };
    let mut steps = Vec::new();
    for item in items {
        let Yaml::Hash(hash) = item else {
            return None;
        };
        let get = |key: &str| {
            hash.get(&Yaml::String(key.to_string()))
                .and_then(yaml_scalar_text)
        };
        steps.push(WorkflowStep {
            name: get("name"),
            description: get("description"),
            symbol: get("symbol"),
            file: get("file"),
        });
    }
    Some(steps)
}

fn workflow_add(
    title: &str,
    steps_raw: &[String],
    steps_file: Option<&str>,
    tags: &str,
    affects: &str,
    affects_modules: &str,
    resource: &str,
) -> ExitCode {
    let steps = if let Some(path) = steps_file {
        let text = match std::fs::read_to_string(path) {
            Ok(text) => text,
            Err(e) => return cli_err(&e.to_string()),
        };
        let docs = yaml_rust2::YamlLoader::load_from_str(&text);
        match docs.ok().and_then(|docs| steps_from_yaml(&docs[0])) {
            Some(steps) => steps,
            None => {
                return cli_err("--steps-file must contain a YAML/JSON list of step dicts.");
            }
        }
    } else if !steps_raw.is_empty() {
        steps_raw.iter().map(|raw| parse_step(raw)).collect()
    } else {
        return cli_err("--step (repeatable) or --steps-file required.");
    };
    let paths = resolve_store(None);
    if paths.ensure().is_err() {
        return cli_err("could not create the store directories");
    }
    let bundle = resolve_bundle(&paths.knowledge);
    let replaced = matches!(
        tools_km::get_document(&bundle, &tools_km::doc_concept_id(title, "workflow", None)),
        Ok(Some(_))
    );
    let count = steps.len();
    match tools_km::add_workflow(
        &bundle,
        title,
        steps,
        tools_km::split_csv(tags),
        tools_km::split_csv(affects_modules),
        tools_km::split_csv(affects),
        if resource.is_empty() {
            None
        } else {
            Some(resource)
        },
    ) {
        Ok(cid) => {
            let note = if replaced {
                " (replaced an existing workflow with the same title)"
            } else {
                ""
            };
            println!("Stored: {cid}{note} ({count} step(s))");
            ExitCode::SUCCESS
        }
        Err(e) => cli_err(&e.0),
    }
}

fn workflow_trace(reference: &str, as_json: bool) -> ExitCode {
    let bundle = knowledge_bundle();
    let concept = match tools_km::resolve_workflow(&bundle, reference) {
        Ok(Some(concept)) => concept,
        Ok(None) => {
            eprintln!(
                "No workflow found matching '{reference}'. Try `cairn knowledge search {reference}` \
                 or `cairn knowledge list --type workflow`."
            );
            return ExitCode::FAILURE;
        }
        Err(e) => return cli_err(&e.0),
    };
    if as_json {
        let obj = json!({
            "concept_id": tools_km::normalize_doc_id(&bundle, &concept.concept_id),
            "title": concept.title,
            "doc_status": concept
                .ext("doc_status")
                .map(py_json::yaml_to_value)
                .unwrap_or(json!("active")),
            "steps": concept
                .ext("steps")
                .map(py_json::yaml_to_value)
                .unwrap_or(json!([])),
        });
        println!("{}", py_json::dumps(&obj));
        return ExitCode::SUCCESS;
    }
    let doc_status = concept.ext_str("doc_status").unwrap_or("active");
    let status_note = if doc_status != "active" {
        format!(" [{doc_status}]")
    } else {
        String::new()
    };
    let title = concept.title.as_deref().unwrap_or("None");
    let concept_id = tools_km::normalize_doc_id(&bundle, &concept.concept_id);
    println!("{title}{status_note} ({concept_id})");
    let steps = match concept.ext("steps") {
        Some(Yaml::Array(items)) => items,
        _ => &Vec::new(),
    };
    for (i, step) in steps.iter().enumerate() {
        let n = i + 1;
        let field = |key: &str| -> Option<String> {
            match step {
                Yaml::Hash(hash) => hash
                    .get(&Yaml::String(key.to_string()))
                    .and_then(yaml_scalar_text),
                _ => None,
            }
        };
        let name = field("name").unwrap_or_else(|| format!("Step {n}"));
        println!("  {n}. {name}");
        if let Some(desc) = field("description").filter(|d| !d.is_empty()) {
            println!("     {desc}");
        }
        if let Some(sym) = field("symbol").filter(|s| !s.is_empty()) {
            println!("     symbol: {sym}");
        }
        if let Some(file) = field("file").filter(|f| !f.is_empty()) {
            println!("     file: {file}");
        }
    }
    ExitCode::SUCCESS
}

// --- memory group ------------------------------------------------------------

#[allow(clippy::too_many_arguments)]
fn memory_record(
    mtype: &str,
    title: &str,
    body: &str,
    resource: Option<&str>,
    confidence: f64,
    recurrence_key: Option<&str>,
    stance: Option<&str>,
    db: Option<&str>,
    knowledge: Option<&str>,
) -> ExitCode {
    let paths = resolve_store(None);
    if paths.ensure().is_err() {
        return cli_err("could not create the store directories");
    }
    let db_file = db_path(db);
    if let Some(parent) = db_file.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let Ok(conn) = open_db_writable(&db_file) else {
        return ExitCode::FAILURE;
    };
    if let Some(sig) = recurrence_key {
        let tool_name = title
            .strip_prefix("Tool failure: ")
            .unwrap_or(mtype)
            .to_string();
        if note_failure_signature(&conn, sig, &tool_name) == 0 {
            return ExitCode::SUCCESS;
        }
    }
    let bundle = memory_bundle(knowledge);
    let result = match tools_km::capture_memory(
        &conn,
        &bundle,
        mtype,
        title,
        if body.is_empty() { title } else { body },
        resource,
        confidence,
        stance,
    ) {
        Ok(result) => result,
        Err(e) => return cli_err(&e.0),
    };
    println!(
        "Recorded {mtype} '{title}' -> {} (score={}, tier={})",
        result.path,
        py_float_text(result.score),
        result.tier
    );
    ExitCode::SUCCESS
}

/// note_failure_signature: prior occurrence count (0 = first, capture skipped).
fn note_failure_signature(conn: &Connection, sig: &str, tool_name: &str) -> i64 {
    let now = iso_utc_micros_offset();
    let _ = conn.execute(
        "INSERT INTO memory_failure_signatures (sig, tool_name, occurrences, first_seen, last_seen) \
         VALUES (?, ?, 1, ?, ?) \
         ON CONFLICT(sig) DO UPDATE SET \
         occurrences = occurrences + 1, last_seen = excluded.last_seen",
        rusqlite::params![sig, tool_name, now, now],
    );
    conn.query_row(
        "SELECT occurrences FROM memory_failure_signatures WHERE sig = ?",
        [sig],
        |r| r.get::<_, i64>(0),
    )
    .map(|n| n - 1)
    .unwrap_or(0)
}

fn iso_utc_micros_offset() -> String {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default();
    let secs = now.as_secs();
    let micros = now.subsec_micros();
    let days = secs / 86_400;
    let rem = secs % 86_400;
    let (h, m, s) = (rem / 3600, (rem % 3600) / 60, rem % 60);
    let civil = civil_from_days(days as i64);
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}.{:06}+00:00",
        civil.0, civil.1, civil.2, h, m, s, micros
    )
}

fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = (z - era * 146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (if m <= 2 { y + 1 } else { y }, m, d)
}

fn parse_symbol_list(symbols: &str) -> Result<Vec<String>, ExitCode> {
    let list: Vec<String> = symbols.split(',').map(|s| s.trim().to_string()).collect();
    if list.iter().any(String::is_empty) {
        return Err(cli_err(
            "--symbols requires a non-empty comma-separated list.",
        ));
    }
    Ok(list)
}

fn require_agent_id(agent_id: &str) -> Result<String, ExitCode> {
    tools_km::validate_agent_id(agent_id)
        .map_err(|e| usage_error("memory share [MEMORY_ID] [OPTIONS]", &e.0))
}

fn memory_search(
    query: &str,
    tier: Option<&str>,
    as_of: Option<&str>,
    agent: Option<&str>,
    db: Option<&str>,
    knowledge: Option<&str>,
) -> ExitCode {
    if let Err(code) = (|| -> Result<(), ExitCode> {
        if let Some(agent) = agent {
            require_agent_id(agent)?;
        }
        let conn = match open_db_writable(&db_path(db)) {
            Ok(conn) => conn,
            Err(e) => return Err(cli_err(&e.to_string())),
        };
        let bundle = memory_bundle(knowledge);
        let mut results =
            match tools_km::search_memory(&conn, &bundle, query, tier, Some("cli"), false, as_of) {
                Ok(rows) => rows,
                Err(e) => {
                    return Err(usage_error("memory search [OPTIONS] QUERY", &e.0));
                }
            };
        let mut attribution: std::collections::HashMap<String, String> =
            std::collections::HashMap::new();
        if let Some(agent) = agent {
            let result_ids: std::collections::HashSet<String> =
                results.iter().map(|c| c.concept_id.clone()).collect();
            match tools_km::shared_recall_entries(
                &conn,
                &bundle,
                agent,
                query,
                tier,
                false,
                as_of,
                &result_ids,
            ) {
                Ok((extra, shared)) => {
                    attribution = shared;
                    results.extend(extra);
                }
                Err(e) => return Err(usage_error("memory search [OPTIONS] QUERY", &e.0)),
            }
        }
        if results.is_empty() {
            println!("No memories matching '{query}'.");
            return Ok(());
        }
        for c in &results {
            println!("{}", memory_line(c, Some(&conn), true, true));
            if let Some(line) = attribution.get(&c.concept_id) {
                println!("    {line}");
            }
        }
        if let Ok(hint) = tools_km::unembedded_memory_hint(&conn, &bundle)
            && !hint.is_empty()
        {
            println!("{hint}");
        }
        Ok(())
    })() {
        return code;
    }
    ExitCode::SUCCESS
}

fn memory_timeline(symbol: &str, db: Option<&str>, knowledge: Option<&str>) -> ExitCode {
    let _ = db;
    let bundle = memory_bundle(knowledge);
    let symbol_lower = symbol.to_lowercase();
    let mut matches: Vec<Concept> = Vec::new();
    for c in tools_km::list_memories(&bundle, None, None) {
        let refs: Vec<String> = tools_km::extract_symbol_refs(&c.body)
            .into_iter()
            .map(|r| r.strip_suffix("()").map(|s| s.to_string()).unwrap_or(r))
            .collect();
        let cited = refs
            .iter()
            .any(|r| r.as_str() == symbol || r.ends_with(&format!(".{symbol}")));
        if !cited {
            let hay = format!(
                "{} {} {}",
                c.title.as_deref().unwrap_or(""),
                c.description.as_deref().unwrap_or(""),
                c.body
            )
            .to_lowercase();
            if !has_word(&hay, &symbol_lower) {
                continue;
            }
        }
        matches.push(c);
    }
    matches.sort_by_key(|c| {
        c.ext_str("valid_from")
            .map(str::to_string)
            .or_else(|| c.timestamp.clone())
            .unwrap_or_default()
    });
    if matches.is_empty() {
        println!("No memories citing '{symbol}'.");
        return ExitCode::SUCCESS;
    }
    println!("Timeline for '{symbol}' ({} memory(ies)):", matches.len());
    for c in &matches {
        let start = c
            .ext_str("valid_from")
            .map(str::to_string)
            .or_else(|| c.timestamp.clone())
            .unwrap_or_else(|| "unknown".to_string());
        let start = start.chars().take(10).collect::<String>();
        let until = c.ext_str("valid_until").map(str::to_string);
        let window = match &until {
            Some(u) => format!("{start} -> {}", u.chars().take(10).collect::<String>()),
            None => format!("{start} -> present"),
        };
        let mut line = format!(
            "  {window}  [{} {}] {}  ({})",
            c.ext_str("memory_tier").unwrap_or("?"),
            c.ext("memory_score")
                .and_then(yaml_scalar_text)
                .unwrap_or_else(|| "?".to_string()),
            c.title.as_deref().unwrap_or("None"),
            c.concept_id
        );
        if let Some(successor) = c.ext_str("successor_symbol") {
            line.push_str(&format!("  successor: {successor}"));
        }
        println!("{line}");
    }
    ExitCode::SUCCESS
}

fn memory_list(
    tier: Option<&str>,
    tag: Option<&str>,
    db: Option<&str>,
    knowledge: Option<&str>,
) -> ExitCode {
    if let Some(tier) = tier
        && !tools_km::TIERS.contains(&tier)
    {
        return usage_error(
            "memory list [OPTIONS]",
            &format!(
                "--tier must be one of {}, got '{tier}'.",
                tools_km::TIERS.join(", ")
            ),
        );
    }
    let bundle = memory_bundle(knowledge);
    let mems = tools_km::list_memories(&bundle, tier, tag);
    if mems.is_empty() {
        println!("No memories found.");
        return ExitCode::SUCCESS;
    }
    let db_file = db_path(db);
    let conn = if db_file.exists() {
        match open_db_read_only(&db_file) {
            Ok(conn) => Some(conn),
            Err(e) => {
                return cli_err(&format!(
                    "--db '{}' is not a valid cairn database: {e}",
                    db_file.display()
                ));
            }
        }
    } else {
        None
    };
    for c in &mems {
        println!("{}", memory_line(c, conn.as_ref(), true, false));
    }
    ExitCode::SUCCESS
}

/// CPython 3.12+ built-in sum(): Neumaier compensated summation over floats.
fn python_sum(values: &[f64]) -> f64 {
    let mut sum = 0.0f64;
    let mut comp = 0.0f64;
    for &x in values {
        let t = sum + x;
        if sum.abs() >= x.abs() {
            comp += (sum - t) + x;
        } else {
            comp += (x - t) + sum;
        }
        sum = t;
    }
    sum + comp
}

fn memory_stats(knowledge: Option<&str>) -> ExitCode {
    let bundle = memory_bundle(knowledge);
    for tier in tools_km::TIERS {
        let mems = tools_km::list_memories(&bundle, Some(tier), None);
        let scores: Vec<f64> = mems
            .iter()
            .map(|m| m.ext_f64("memory_score").unwrap_or(0.0))
            .collect();
        let avg = if scores.is_empty() {
            "0".to_string()
        } else {
            let mean = python_sum(&scores) / scores.len() as f64;
            py_round3_text(mean)
        };
        println!("  {tier:10} {:>4} memories (avg score {avg})", mems.len());
    }
    ExitCode::SUCCESS
}

fn memory_digest(limit: i64, db: Option<&str>, knowledge: Option<&str>) -> ExitCode {
    if limit < 0 {
        return usage_error(
            "memory digest [OPTIONS]",
            &format!("limit must be non-negative, got {limit}"),
        );
    }
    let bundle = memory_bundle(knowledge);
    let mut mems = tools_km::list_memories(&bundle, Some("tribal"), None);
    mems.sort_by(|a, b| {
        b.ext_f64("memory_score")
            .unwrap_or(0.0)
            .partial_cmp(&a.ext_f64("memory_score").unwrap_or(0.0))
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    mems.truncate(limit.max(0) as usize);
    if mems.is_empty() {
        println!("No tribal memories yet.");
        return ExitCode::SUCCESS;
    }
    let db_file = db_path(db);
    let conn = if db_file.exists() {
        match open_db_read_only(&db_file) {
            Ok(conn) => Some(conn),
            Err(e) => {
                return cli_err(&format!(
                    "--db '{}' is not a valid cairn database: {e}",
                    db_file.display()
                ));
            }
        }
    } else {
        None
    };
    for c in &mems {
        println!("{}", memory_line(c, conn.as_ref(), false, false));
    }
    if let Some(conn) = &conn
        && let Ok(hint) = tools_km::unembedded_memory_hint(conn, &bundle)
        && !hint.is_empty()
    {
        println!("{hint}");
    }
    ExitCode::SUCCESS
}

fn memory_promote(path: &str, db: Option<&str>, knowledge: Option<&str>) -> ExitCode {
    let bundle = memory_bundle(knowledge);
    let Ok(conn) = open_db_writable(&db_path(db)) else {
        return ExitCode::FAILURE;
    };
    let new_id = match tools_km::promote_memory(&bundle, path, Some(&conn)) {
        Ok(new_id) => new_id,
        Err(e) => return cli_err(&e.0),
    };
    match new_id {
        Some(new_id) => {
            println!("Promoted to {new_id}");
            ExitCode::SUCCESS
        }
        None => cli_err(&format!("Could not find memory at '{path}'.")),
    }
}

fn memory_decay(db: Option<&str>, knowledge: Option<&str>) -> ExitCode {
    let bundle = memory_bundle(knowledge);
    let Ok(conn) = open_db_writable(&db_path(db)) else {
        return ExitCode::FAILURE;
    };
    match tools_km::decay(&bundle, 7, 90, Some(&conn)) {
        Ok((expired, archived)) => {
            println!("Expired raw: {expired}, archived tribal: {archived}");
            ExitCode::SUCCESS
        }
        Err(e) => cli_err(&e.0),
    }
}

fn memory_forget(memory_path: &str, db: Option<&str>, knowledge: Option<&str>) -> ExitCode {
    let bundle = memory_bundle(knowledge);
    let Ok(conn) = open_db_writable(&db_path(db)) else {
        return ExitCode::FAILURE;
    };
    match tools_km::delete_memory(&bundle, memory_path, Some(&conn)) {
        Ok(true) => {
            println!("Deleted memory: '{memory_path}'.");
            ExitCode::SUCCESS
        }
        Ok(false) => cli_err(&format!(
            "Memory not found (or outside the memory/ namespace): '{memory_path}'."
        )),
        Err(e) => cli_err(&e.0),
    }
}

fn memory_demote(
    memory_path: &str,
    target_tier: &str,
    db: Option<&str>,
    knowledge: Option<&str>,
) -> ExitCode {
    let bundle = memory_bundle(knowledge);
    let Ok(conn) = open_db_writable(&db_path(db)) else {
        return ExitCode::FAILURE;
    };
    match tools_km::demote_memory(&bundle, memory_path, target_tier, Some(&conn)) {
        Ok(Some(new_path)) => {
            println!("Demoted '{memory_path}' -> {new_path} (tier → {target_tier})");
            ExitCode::SUCCESS
        }
        Ok(None) => cli_err(&format!(
            "Cannot demote '{memory_path}' to '{target_tier}'. \
             Target must be strictly lower than current tier, or memory not found."
        )),
        Err(e) => cli_err(&e.0),
    }
}

fn memory_purge(
    max_days: i64,
    dry_run: bool,
    db: Option<&str>,
    knowledge: Option<&str>,
) -> ExitCode {
    let bundle = memory_bundle(knowledge);
    if dry_run {
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs() as i64)
            .unwrap_or(0);
        let mut candidates: Vec<(String, i64)> = Vec::new();
        for c in tools_km::list_memories(&bundle, Some("archived"), None) {
            let Some(ts) = c
                .timestamp
                .as_deref()
                .and_then(tools_km::parse_memory_timestamp)
            else {
                continue;
            };
            let age = ((now - ts) as f64 / 86_400.0).floor() as i64;
            if age > max_days {
                candidates.push((c.concept_id, age));
            }
        }
        if candidates.is_empty() {
            println!("No archived memories older than {max_days} days.");
            return ExitCode::SUCCESS;
        }
        println!(
            "{} archived memory(ies) older than {max_days} days:",
            candidates.len()
        );
        for (cid, age) in candidates {
            println!("  {cid}  ({age}d)");
        }
        return ExitCode::SUCCESS;
    }
    let Ok(conn) = open_db_writable(&db_path(db)) else {
        return ExitCode::FAILURE;
    };
    match tools_km::purge_archived(&bundle, max_days, Some(&conn)) {
        Ok(purged) => {
            println!("Purged {purged} archived memory(ies) older than {max_days} days.");
            ExitCode::SUCCESS
        }
        Err(e) => cli_err(&e.0),
    }
}

fn memory_share(
    memory_id: Option<&str>,
    agent_id: &str,
    symbols: &str,
    db: Option<&str>,
) -> ExitCode {
    let Ok(agent_id) = require_agent_id(agent_id) else {
        return ExitCode::FAILURE;
    };
    let Ok(symbol_list) = parse_symbol_list(symbols) else {
        return ExitCode::FAILURE;
    };
    let Ok(conn) = open_db_writable(&db_path(db)) else {
        return ExitCode::FAILURE;
    };
    match tools_km::share_memory(&agent_id, &symbol_list, memory_id, &conn) {
        Ok(inserted) if inserted > 0 => {
            println!(
                "Shared for agent '{agent_id}': {} ({inserted} new row(s)).",
                symbol_list.join(", ")
            );
            ExitCode::SUCCESS
        }
        Ok(_) => {
            println!("Nothing new recorded: agent '{agent_id}' already shares these symbols.");
            ExitCode::SUCCESS
        }
        Err(e) => cli_err(&e.0),
    }
}

fn memory_check(agent_id: &str, symbols: &str, db: Option<&str>) -> ExitCode {
    let Ok(agent_id) = require_agent_id(agent_id) else {
        return ExitCode::FAILURE;
    };
    let Ok(symbol_list) = parse_symbol_list(symbols) else {
        return ExitCode::FAILURE;
    };
    let db_file = db_path(db);
    let Ok(conn) = open_db_writable(&db_file) else {
        return ExitCode::FAILURE;
    };
    let ts = cairn_query::tools_km::iso_utc_now();
    for symbol in &symbol_list {
        let updated = conn
            .execute(
                "UPDATE agent_symbols SET ts = ? \
                 WHERE agent_id = ? AND symbol = ? AND kind = 'intent' AND memory_id IS NULL",
                rusqlite::params![ts, agent_id, symbol],
            )
            .unwrap_or(0);
        if updated == 0 {
            let _ = conn.execute(
                "INSERT INTO agent_symbols (agent_id, symbol, memory_id, kind, ts) \
                 VALUES (?, ?, NULL, 'intent', ?)",
                rusqlite::params![agent_id, symbol, ts],
            );
        }
    }
    let Ok(ro_conn) = open_db_read_only(&db_file) else {
        return ExitCode::FAILURE;
    };
    let conflicts = match tools_km::check_overlap(&agent_id, &symbol_list, &ro_conn, 7) {
        Ok(rows) => rows,
        Err(e) => return cli_err(&e.0),
    };
    if conflicts.is_empty() {
        println!(
            "No overlap: no other agent has recent activity on {}.",
            symbol_list.join(", ")
        );
        return ExitCode::SUCCESS;
    }
    let mut by_agent: Vec<(String, Vec<String>)> = Vec::new();
    for row in &conflicts {
        match by_agent
            .iter_mut()
            .find(|(agent, _)| *agent == row.agent_id)
        {
            Some((_, syms)) => {
                if !syms.contains(&row.symbol) {
                    syms.push(row.symbol.clone());
                }
            }
            None => by_agent.push((row.agent_id.clone(), vec![row.symbol.clone()])),
        }
    }
    for (other_agent, syms) in &by_agent {
        println!(
            "Overlap warning: agent '{other_agent}' has recent activity on {}",
            syms.join(", ")
        );
    }
    ExitCode::SUCCESS
}

// --- compass group -----------------------------------------------------------

fn compass_list(knowledge: Option<&str>) -> ExitCode {
    let bundle = memory_bundle(knowledge);
    let ids = bundle.list_concepts(Some("compass/"));
    if ids.is_empty() {
        println!("No compass files. Generate with: cairn compass generate <module>");
        return ExitCode::SUCCESS;
    }
    for cid in &ids {
        match bundle.read_concept(cid) {
            Ok(c) => {
                let title = c.title.unwrap_or_default();
                println!("  {:40} {cid}", title);
            }
            Err(e) => println!("  {cid} (read error: {e})"),
        }
    }
    ExitCode::SUCCESS
}

fn compass_validate(db: Option<&str>, knowledge: Option<&str>) -> ExitCode {
    let knowledge_path = knowledge.map_or_else(knowledge_default, PathBuf::from);
    let Ok(db_file) = require_db_dir(db) else {
        return ExitCode::FAILURE;
    };
    let Ok(conn) = open_db_writable(&db_file) else {
        return ExitCode::FAILURE;
    };
    let bundle = resolve_bundle(&knowledge_path);
    let ids = bundle.list_concepts(Some("compass/"));
    if ids.is_empty() {
        if !bundle.root.is_dir() {
            println!("Knowledge bundle not found: {}", knowledge_path.display());
            return ExitCode::FAILURE;
        }
        println!("No compass files to validate.");
        return ExitCode::SUCCESS;
    }
    let mut total_errors = 0i64;
    for cid in &ids {
        match bundle.read_concept(cid) {
            Err(e) => {
                println!("  [ERROR] {cid} (read error: {e})");
                total_errors += 1;
            }
            Ok(concept) => match tools_km::critic_concept(&concept, &conn) {
                Err(_) => {
                    total_errors += 1;
                }
                Ok(result) => {
                    let status = if result.passed { "OK" } else { "FAIL" };
                    println!("  [{status}] {cid} (quality={:.2})", result.quality_score);
                    if !result.passed {
                        total_errors += 1;
                    }
                    for e in &result.errors {
                        println!("      ERROR: {e}");
                        total_errors += 1;
                    }
                    for w in &result.warnings {
                        println!("      warn: {w}");
                    }
                }
            },
        }
    }
    println!(
        "\n{total_errors} total errors across {} compass files.",
        ids.len()
    );
    if total_errors > 0 {
        return ExitCode::FAILURE;
    }
    ExitCode::SUCCESS
}

// --- wiki group --------------------------------------------------------------

fn wiki_search(query: &str, knowledge: Option<&str>) -> ExitCode {
    let knowledge_path = knowledge.map_or_else(knowledge_default, PathBuf::from);
    let bundle = resolve_bundle(&knowledge_path);
    let Ok(results) = bundle.search(query, None) else {
        println!("No wiki matching '{query}'.");
        return ExitCode::SUCCESS;
    };
    let results: Vec<_> = results
        .into_iter()
        .filter(|c| {
            bundle
                .relativize_id(&c.concept_id)
                .starts_with("wiki/pages/")
        })
        .take(20)
        .collect();
    if results.is_empty() {
        println!("No wiki matching '{query}'.");
        return ExitCode::SUCCESS;
    }
    for c in &results {
        println!(
            "  {}  ({})",
            c.title.as_deref().unwrap_or("None"),
            bundle.relativize_id(&c.concept_id)
        );
        if let Some(desc) = c.description.as_deref().filter(|d| !d.is_empty()) {
            println!("      {desc}");
        }
    }
    ExitCode::SUCCESS
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
    /// The python-CLI command name for diagnostics.
    fn name(&self) -> &'static str {
        match self {
            Self::Record { .. } => "memory record",
            Self::Reflect { .. } => "memory reflect",
            Self::Evolve { .. } => "memory evolve",
            Self::Search { .. } => "memory search",
            Self::Timeline { .. } => "memory timeline",
            Self::Capture { .. } => "memory capture",
            Self::List { .. } => "memory list",
            Self::Stats { .. } => "memory stats",
            Self::Digest { .. } => "memory digest",
            Self::Promote { .. } => "memory promote",
            Self::Decay { .. } => "memory decay",
            Self::Embed { .. } => "memory embed",
            Self::BatchCritic { .. } => "memory batch-critic",
            Self::Forget { .. } => "memory forget",
            Self::Demote { .. } => "memory demote",
            Self::Purge { .. } => "memory purge",
            Self::Consolidate { .. } => "memory consolidate",
            Self::Share { .. } => "memory share",
            Self::Check { .. } => "memory check",
        }
    }
}

impl KnowledgeGroup {
    /// The python-CLI command name for diagnostics.
    fn name(&self) -> &'static str {
        match self {
            Self::Add { .. } => "knowledge add",
            Self::Import { .. } => "knowledge import",
            Self::Ingest => "knowledge ingest",
            Self::Rebuild { .. } => "knowledge rebuild",
            Self::Islands { .. } => "knowledge islands",
            Self::Related { .. } => "knowledge related",
            Self::Chain { .. } => "knowledge chain",
            Self::Search { .. } => "knowledge search",
            Self::List { .. } => "knowledge list",
            Self::Embed { .. } => "knowledge embed",
            Self::Impact { .. } => "knowledge impact",
            Self::Remove { .. } => "knowledge remove",
            Self::Status { .. } => "knowledge status",
            Self::Export { .. } => "knowledge export",
            Self::Workflow { .. } => "knowledge workflow",
        }
    }
}

impl WikiGroup {
    /// The python-CLI command name for diagnostics.
    fn name(&self) -> &'static str {
        match self {
            Self::Generate => "wiki generate",
            Self::Search { .. } => "wiki search",
            Self::Status { .. } => "wiki status",
            Self::Retry { .. } => "wiki retry",
            Self::Export { .. } => "wiki export",
            Self::Enrich { .. } => "wiki enrich",
        }
    }
}

impl CompassGroup {
    /// The python-CLI command name for diagnostics.
    fn name(&self) -> &'static str {
        match self {
            Self::Generate { .. } => "compass generate",
            Self::List { .. } => "compass list",
            Self::Validate { .. } => "compass validate",
            Self::Gaps { .. } => "compass gaps",
            Self::Flow { .. } => "compass flow",
            Self::FlowGaps { .. } => "compass flow-gaps",
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

    #[test]
    fn py_title_and_word_match_track_python() {
        assert_eq!(py_title("golden-capture_rule"), "Golden-Capture_Rule");
        assert_eq!(py_title("release notes v2"), "Release Notes V2");
        assert_eq!(py_title("it's a title"), "It'S A Title");
        assert!(has_word("call main() to start", "main"));
        assert!(!has_word("domain", "main"));
        assert!(!has_word("mainframe", "main"));
    }

    #[test]
    fn unique_slug_caps_and_numbers_collisions() {
        let mut seen = std::collections::HashSet::new();
        let first = unique_slug("release-notes", "cairn", &mut seen);
        assert_eq!(first, "release-notes-cairn");
        seen.insert(first.clone());
        assert_eq!(
            unique_slug("release-notes", "cairn", &mut seen),
            "release-notes-cairn-2"
        );
    }

    #[test]
    fn task_complete_refuses_a_missing_store_without_fabricating_one() {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let parent =
            std::env::temp_dir().join(format!("cairn-task-nostore-{}-{nanos}", std::process::id()));
        let db = parent.join("store.kg");
        let code = task_complete(
            "sometask",
            Some("result"),
            None,
            "",
            Some(&db.to_string_lossy()),
            None,
        );
        assert_eq!(code, std::process::ExitCode::FAILURE);
        assert!(!parent.exists(), "no store directory may be fabricated");
    }
}
