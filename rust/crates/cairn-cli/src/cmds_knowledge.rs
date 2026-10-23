//! Knowledge-layer commands: knowledge (+workflow), memory, compass, wiki.

use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::time::Instant;

use clap::Subcommand;
use rusqlite::Connection;
use serde_json::{Value, json};
use yaml_rust2::Yaml;

use cairn_query::ingest;
use cairn_query::skillgen;
use cairn_query::tasks::{self, DanglingPointer};
use cairn_query::tools_km::{self, Bundle, Concept, KnowledgeHit, RelatedDoc, WorkflowStep};
use cairn_query::tools_search::{self, EffectiveBackend};
use cairn_query::tools_wiki::{self, PipelineError};
use cairn_store::{conn as store_conn, schema as store_schema};

use crate::display;
use crate::py_json;
use crate::store_paths::resolve_store;

const IMPORT_MAX_FILE_SIZE: u64 = 10 * 1024 * 1024;
const WRITE_FAILED_MSG: &str = "failed to write the wiki manifest; queued tasks are not recorded";
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
    Validate {
        /// Knowledge bundle (default: CAIRN_KNOWLEDGE or the store-adjacent .knowledge).
        #[arg(long)]
        knowledge: Option<String>,
    },
    /// Check all concepts for stale file/symbol references.
    #[command(name = "validate-paths")]
    ValidatePaths {
        /// SQLite DB path (default: the resolved store).
        #[arg(long)]
        db: Option<String>,
        /// Knowledge bundle (default: CAIRN_KNOWLEDGE or the store-adjacent .knowledge).
        #[arg(long)]
        knowledge: Option<String>,
        /// Mark stale concepts (set stale=true in extensions).
        #[arg(long)]
        mark: bool,
    },
    /// Run the deterministic critic on one knowledge document.
    Verify {
        /// Concept id relative to .knowledge/ without the .md suffix.
        doc_path: String,
        /// SQLite DB path (default: the resolved store).
        #[arg(long)]
        db: Option<String>,
        /// Knowledge bundle (default: CAIRN_KNOWLEDGE or the store-adjacent .knowledge).
        #[arg(long)]
        knowledge: Option<String>,
    },
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
    Ingest {
        /// Fed markdown file (repeatable).
        #[arg(long = "file", value_name = "FILE")]
        files: Vec<String>,
        /// Fed markdown directory (repeatable).
        #[arg(long = "dir", value_name = "DIRECTORY")]
        dirs: Vec<String>,
        /// Repository root to scan for docs (repeatable).
        #[arg(long = "repo", value_name = "PATH")]
        repos: Vec<String>,
        /// Approve: write staged manifest rows into the store, then embed.
        #[arg(long = "ingest")]
        do_ingest: bool,
        /// Ingest drafts tagged 'draft' instead of skipping.
        #[arg(long = "include-drafts")]
        include_drafts: bool,
        /// Staging directory (default: <workspace>/.cairn/ingest-outbox).
        #[arg(long)]
        outbox: Option<String>,
    },
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
        #[arg(long = "max-steps", default_value_t = 20,
              value_parser = clap::value_parser!(i64).range(1..))]
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
    Generate {
        /// Repo name (all repos if omitted).
        #[arg(long)]
        repo: Option<String>,
        #[arg(long, default_value = None)]
        db: Option<String>,
        #[arg(long, default_value = None)]
        knowledge: Option<String>,
        /// Run generation + the critic and print each verdict; write nothing.
        #[arg(long = "dry-run")]
        dry_run: bool,
        /// Print the critic errors/warnings for each generated concept.
        #[arg(long = "show-rejections")]
        show_rejections: bool,
        /// Queue one wiki-page task per planned page for agent-decoupled writing.
        #[arg(long = "llm")]
        llm: bool,
        /// With --llm, max pages to plan, overview included.
        #[arg(long, default_value_t = 10)]
        pages: i64,
        /// With --llm, instruct writers to include Mermaid diagrams.
        #[arg(long)]
        diagrams: bool,
        /// With --llm, queue a wiki-catalog refinement task first.
        #[arg(long = "refine-catalog")]
        refine_catalog: bool,
        /// With --llm, re-queue every planned page even when unchanged and promoted.
        #[arg(long)]
        force: bool,
    },
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
    /// Generate a SKILL.md from SELECTORS (module, directory prefix, or symbol list).
    Generate {
        /// Module, directory prefix, or symbol list.
        #[arg(required = true)]
        selectors: Vec<String>,
        /// Target directory (default: <workspace>/.agents/skills/cairn-<slug>/).
        #[arg(long)]
        output: Option<String>,
        /// Maximum symbols packaged.
        #[arg(long = "top-k", default_value_t = 20, value_parser = clap::value_parser!(i64).range(1..))]
        top_k: i64,
        /// Maximum memories packaged.
        #[arg(long = "top-n", default_value_t = 5, value_parser = clap::value_parser!(i64).range(1..))]
        top_n: i64,
        /// SQLite DB path (default: resolved store).
        #[arg(long)]
        db: Option<String>,
        /// Queue a polish task through the LLM task queue; the completed
        /// result replaces SKILL.md only after the critic gate accepts it.
        #[arg(long)]
        polish: bool,
        /// Knowledge bundle backing the --polish task queue.
        #[arg(long)]
        knowledge: Option<String>,
    },
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
        KnowledgeCommand::Skill { command } => match command {
            SkillGroup::Generate {
                selectors,
                output,
                top_k,
                top_n,
                db,
                polish,
                knowledge,
            } => skill_generate(
                &selectors,
                output.as_deref(),
                top_k,
                top_n,
                db.as_deref(),
                polish,
                knowledge.as_deref(),
            ),
        },
        KnowledgeCommand::Task { command } => run_task(command),
        KnowledgeCommand::Validate { knowledge } => validate_cmd(knowledge.as_deref()),
        KnowledgeCommand::ValidatePaths {
            db,
            knowledge,
            mark,
        } => validate_paths_cmd(db.as_deref(), knowledge.as_deref(), mark),
        KnowledgeCommand::Verify {
            doc_path,
            db,
            knowledge,
        } => verify_cmd(&doc_path, db.as_deref(), knowledge.as_deref()),
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
        KnowledgeGroup::Ingest {
            files,
            dirs,
            repos,
            do_ingest,
            include_drafts,
            outbox,
        } => knowledge_ingest(
            &files,
            &dirs,
            &repos,
            do_ingest,
            include_drafts,
            outbox.as_deref(),
        ),
        KnowledgeGroup::Rebuild { db } => knowledge_rebuild(db.as_deref()),
        KnowledgeGroup::Embed { db, batch_size } => knowledge_embed(db.as_deref(), batch_size),
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
        WorkflowGroup::Sync {
            reference,
            all,
            dry_run,
            max_steps,
            db,
            knowledge,
        } => workflow_sync(
            reference.as_deref(),
            all,
            dry_run,
            max_steps,
            db.as_deref(),
            knowledge.as_deref(),
        ),
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
        MemoryGroup::Reflect { db, knowledge } => {
            memory_reflect(db.as_deref(), knowledge.as_deref())
        }
        MemoryGroup::Evolve {
            memory_path,
            title,
            body,
            db,
            knowledge,
        } => memory_evolve(
            &memory_path,
            title.as_deref(),
            body.as_deref(),
            db.as_deref(),
            knowledge.as_deref(),
        ),
        MemoryGroup::Capture {
            session_transcript,
            session_transcript_stdin,
            session_id,
            db,
            knowledge,
        } => memory_capture(
            session_transcript.as_deref(),
            session_transcript_stdin,
            &session_id,
            db.as_deref(),
            knowledge.as_deref(),
        ),
        MemoryGroup::Embed {
            db,
            knowledge,
            batch_size,
            reap,
        } => memory_embed(db.as_deref(), knowledge.as_deref(), batch_size, reap),
        MemoryGroup::BatchCritic { db, knowledge } => {
            memory_batch_critic(db.as_deref(), knowledge.as_deref())
        }
        MemoryGroup::Consolidate { db, knowledge } => {
            memory_consolidate(db.as_deref(), knowledge.as_deref())
        }
    }
}

fn run_wiki(group: WikiGroup) -> ExitCode {
    match group {
        WikiGroup::Generate {
            repo,
            db,
            knowledge,
            dry_run,
            show_rejections,
            llm,
            pages,
            diagrams,
            refine_catalog,
            force,
        } => wiki_generate(
            repo.as_deref(),
            db.as_deref(),
            knowledge.as_deref(),
            dry_run,
            show_rejections,
            llm,
            pages,
            diagrams,
            refine_catalog,
            force,
        ),
        WikiGroup::Search { query, knowledge } => wiki_search(&query, knowledge.as_deref()),
        WikiGroup::Status { repo, knowledge } => wiki_status(repo.as_deref(), knowledge.as_deref()),
        WikiGroup::Retry { repo, knowledge } => wiki_retry(repo.as_deref(), knowledge.as_deref()),
        WikiGroup::Export {
            out_dir,
            force,
            knowledge,
        } => wiki_export(&out_dir, force, knowledge.as_deref()),
        WikiGroup::Enrich {
            page_id,
            repo,
            all,
            knowledge,
        } => wiki_enrich(
            page_id.as_deref(),
            repo.as_deref(),
            all,
            knowledge.as_deref(),
        ),
    }
}

fn run_compass(group: CompassGroup) -> ExitCode {
    match group {
        CompassGroup::List { knowledge } => compass_list(knowledge.as_deref()),
        CompassGroup::Validate { db, knowledge } => {
            compass_validate(db.as_deref(), knowledge.as_deref())
        }
        CompassGroup::Generate {
            module,
            repo,
            use_llm,
            db,
            knowledge,
            dry_run,
            show_rejections,
        } => compass_generate(
            &module,
            repo.as_deref(),
            db.as_deref(),
            knowledge.as_deref(),
            use_llm,
            dry_run,
            show_rejections,
        ),
        CompassGroup::Gaps { db, knowledge } => compass_gaps(db.as_deref(), knowledge.as_deref()),
        CompassGroup::Flow {
            entry,
            db,
            knowledge,
            dry_run,
            as_workflow,
            max_steps,
            use_llm,
            entry_id,
        } => compass_flow(
            &entry,
            db.as_deref(),
            knowledge.as_deref(),
            dry_run,
            as_workflow,
            max_steps,
            use_llm,
            entry_id.as_deref(),
        ),
        CompassGroup::FlowGaps {
            min_edges,
            generate,
            limit,
            dry_run,
            db,
            knowledge,
        } => compass_flow_gaps(
            min_edges,
            generate,
            limit,
            dry_run,
            db.as_deref(),
            knowledge.as_deref(),
        ),
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
        let title = tools_km::py_title(&stem.replace(['-', '_'], " "));
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
            None,
            None,
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
        let dst = PathBuf::from(out_path);
        if let Some(parent) = dst.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        let written = if out_path.ends_with("gz") {
            crate::targz::write_tar_gz(&kn_dir, ".knowledge", &dst)
        } else {
            crate::targz::write_tar(&kn_dir, ".knowledge", &dst)
        };
        if written.is_err() {
            return cli_err("could not write the tarball");
        }
        println!("Exported knowledge bundle tarball to {}", dst.display());
        return ExitCode::SUCCESS;
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

// --- validate / validate-paths / verify (cli/validate.py port) -----------------

/// The --knowledge default: CAIRN_KNOWLEDGE-aware resolved store bundle.
fn knowledge_path_arg(knowledge: Option<&str>) -> PathBuf {
    knowledge.map_or_else(|| resolve_store(None).knowledge, PathBuf::from)
}

fn validate_cmd(knowledge: Option<&str>) -> ExitCode {
    let knowledge_path = knowledge_path_arg(knowledge);
    let errors = tools_km::check_bundle(&knowledge_path);
    if errors.is_empty() {
        println!(
            "OKF bundle at {} is conformant (0 errors).",
            knowledge_path.display()
        );
        return ExitCode::SUCCESS;
    }
    println!("{} conformance errors:", errors.len());
    for e in &errors {
        println!("  {e}");
    }
    // Exit non-zero so CI/scripts can detect conformance failure.
    ExitCode::FAILURE
}

fn set_extension(ext: &mut Vec<(String, Yaml)>, key: &str, value: Yaml) {
    match ext.iter_mut().find(|(k, _)| k == key) {
        Some(slot) => slot.1 = value,
        None => ext.push((key.to_string(), value)),
    }
}

fn validate_paths_cmd(db: Option<&str>, knowledge: Option<&str>, mark: bool) -> ExitCode {
    let bundle = resolve_bundle(&knowledge_path_arg(knowledge));
    let Ok(db_file) = require_db_dir(db) else {
        return ExitCode::FAILURE;
    };
    let Ok(conn) = open_db_writable(&db_file) else {
        return ExitCode::FAILURE;
    };
    let stale = tools_km::validate_paths_stale(&conn, &bundle);
    if stale.is_empty() {
        println!("All concepts have valid references (0 stale).");
        return ExitCode::SUCCESS;
    }

    let now = tools_km::iso_utc_now();
    let mut marked = 0usize;
    for entry in &stale {
        println!(
            "  [STALE] {}  (verified: {})",
            entry.concept_id,
            py_round3_text(entry.verified)
        );
        if !mark {
            continue;
        }
        // read_concept keys the concept by its resolved file path; restore the
        // bundle-relative id so the validity projection upserts the existing row.
        match (|| -> Result<(), String> {
            let mut concept = bundle.read_concept(&entry.concept_id).map_err(|e| e.0)?;
            concept.concept_id = entry.concept_id.clone();
            set_extension(&mut concept.extensions, "stale", Yaml::Boolean(true));
            if entry.concept_id.starts_with("memory/") {
                // Memory records auto-invalidate at build time; an existing
                // interval or successor link is preserved.
                let successor = concept
                    .ext_str("successor_symbol")
                    .map(str::to_string)
                    .or_else(|| tools_km::resolve_successor(&conn, &concept.body));
                let valid_from = concept
                    .ext_str("valid_from")
                    .filter(|v| !v.is_empty())
                    .map(str::to_string)
                    .or_else(|| concept.timestamp.clone())
                    .unwrap_or_else(|| now.clone());
                let valid_until = concept
                    .ext_str("valid_until")
                    .filter(|v| !v.is_empty())
                    .map(str::to_string)
                    .unwrap_or_else(|| now.clone());
                tools_km::write_validity(
                    &mut concept,
                    &valid_from,
                    Some(valid_until.as_str()),
                    successor.as_deref(),
                    Some(&bundle),
                    Some(&conn),
                )
                .map_err(|e| e.0)
            } else {
                bundle.write_concept(&mut concept).map_err(|e| e.0)
            }
        })() {
            Ok(()) => marked += 1,
            Err(e) => println!("    (mark failed: {e})"),
        }
    }

    let action = if mark { "marked" } else { "found" };
    let count = if mark { marked } else { stale.len() };
    println!("\n{action} {count} stale concept(s).");
    // Exit non-zero so CI/scripts can detect stale concepts.
    ExitCode::FAILURE
}

fn verify_cmd(doc_path: &str, db: Option<&str>, knowledge: Option<&str>) -> ExitCode {
    let bundle = resolve_bundle(&knowledge_path_arg(knowledge));
    let Ok(db_file) = require_db_dir(db) else {
        return ExitCode::FAILURE;
    };
    let Ok(conn) = open_db_writable(&db_file) else {
        return ExitCode::FAILURE;
    };
    let concept = match bundle.concept_file(doc_path) {
        Err(e) => {
            eprintln!("Could not read concept '{doc_path}': {}", e.0);
            return ExitCode::from(2);
        }
        Ok(path) => match std::fs::read_to_string(&path) {
            Err(e) => {
                eprintln!(
                    "Could not read concept '{doc_path}': {}",
                    py_oserror_text(&e, path.to_string_lossy().as_ref())
                );
                return ExitCode::from(2);
            }
            Ok(text) => {
                Concept::from_text(&text, path.with_extension("").to_string_lossy().to_string())
            }
        },
    };
    let result = match tools_km::critic_concept(&concept, &conn) {
        Ok(result) => result,
        Err(e) => return cli_err(&e.0),
    };
    let status = if result.passed { "OK" } else { "FAIL" };
    println!(
        "[{status}] {doc_path} (quality={:.2})",
        result.quality_score
    );
    for e in &result.errors {
        println!("  ERROR: {e}");
    }
    for w in &result.warnings {
        println!("  warn: {w}");
    }
    // Non-zero exit on blocking errors so scripts/CI can detect a failed verify.
    if !result.passed {
        return ExitCode::FAILURE;
    }
    ExitCode::SUCCESS
}

// --- knowledge rebuild / embed (cli/knowledge.py port) --------------------------

/// Python's dangling-pointer warning rendering (index/executor shared).
fn echo_dangling_warnings(items: &[DanglingPointer]) {
    for item in items {
        match &item.candidates {
            Some(candidates) => println!(
                "  warning: {} declares relates_to '{}' matching multiple knowledge \
                 documents ({}); ambiguous pointer, kept in frontmatter, not indexed.",
                item.doc_id,
                item.concept_id,
                candidates.join(", ")
            ),
            None => println!(
                "  warning: {} declares relates_to '{}' matching no knowledge document \
                 or resource path; kept in frontmatter, not indexed.",
                item.doc_id, item.concept_id
            ),
        }
    }
}

fn knowledge_rebuild(db: Option<&str>) -> ExitCode {
    let paths = resolve_store(None);
    if paths.ensure().is_err() {
        return cli_err("could not create the store directories");
    }
    let bundle = resolve_bundle(&paths.knowledge);
    let Ok(db_file) = require_db_dir(db) else {
        return ExitCode::FAILURE;
    };
    let Ok(conn) = open_db_writable(&db_file) else {
        return ExitCode::FAILURE;
    };
    let report = match tasks::rebuild_knowledge_index(&conn, &bundle) {
        Ok(report) => report,
        Err(e) => return cli_err(&e.0),
    };
    println!(
        "Rebuilt knowledge index: {} doc(s), {} edge(s) ({} derived), {} ref(s).",
        report.docs, report.edges, report.derived, report.doc_refs
    );
    echo_dangling_warnings(&report.dangling);
    let queued = tasks::queue_doc_link_tasks(&conn, &bundle);
    println!("Island detection: queued {queued} doc-link task(s).");
    ExitCode::SUCCESS
}

/// The plain-text progress bar's 0.5s draw throttle (display.py contract).
struct ThrottledBar {
    description: &'static str,
    unit: &'static str,
    t0: Instant,
    last_draw: Instant,
}

impl ThrottledBar {
    fn new(description: &'static str, unit: &'static str) -> Self {
        let now = Instant::now();
        Self {
            description,
            unit,
            t0: now,
            last_draw: now,
        }
    }

    fn draw(&mut self, completed: usize, total: usize) {
        crate::cmds_lifecycle::plain_progress_draw(
            self.description,
            completed,
            total,
            self.t0.elapsed().as_secs_f64(),
            self.unit,
        );
    }

    fn maybe_draw(&mut self, completed: usize, total: usize) {
        if self.last_draw.elapsed().as_secs_f64() >= 0.5 {
            self.last_draw = Instant::now();
            self.draw(completed, total);
        }
    }
}

fn knowledge_embed(db: Option<&str>, batch_size: i64) -> ExitCode {
    let env = tools_search::semantic_env(None);
    if env.backend == EffectiveBackend::Unavailable {
        display::error("Semantic backend unavailable");
        display::dim(&tools_search::install_hint());
        return ExitCode::FAILURE;
    }
    let paths = resolve_store(None);
    let Ok(db_file) = require_db_dir(db) else {
        return ExitCode::FAILURE;
    };
    if paths.ensure().is_err() {
        return cli_err("could not create the store directories");
    }
    let bundle = resolve_bundle(&paths.knowledge);
    let Ok(conn) = open_db_writable(&db_file) else {
        return ExitCode::FAILURE;
    };
    let model = match cairn_build::embeddings::current_model() {
        Ok(model) => model,
        Err(e) => return cli_err(&e),
    };
    display::info(&format!("Embedding knowledge docs with {model}"));

    let timer = Instant::now();
    let mut bar = ThrottledBar::new("Embedding docs", "docs");
    bar.draw(0, 0);
    let mut state = (0usize, 0usize);
    let summary = match cairn_build::embeddings::embed_knowledge(
        &conn,
        &bundle,
        batch_size.max(1) as usize,
        |done, total| {
            state = (done, total);
            bar.maybe_draw(done, total);
        },
    ) {
        Ok(summary) => summary,
        Err(message) => {
            // A deliberate embed abort is a CLI error, not a traceback.
            display::error(&message);
            return ExitCode::FAILURE;
        }
    };
    let _ = state;
    bar.draw(state.0, state.1);
    println!();
    let elapsed = timer.elapsed().as_secs_f64();
    crate::rail::summary_panel(
        &format!(
            "Embedded {} docs in {elapsed:.1}s",
            crate::cmds_core::fmt_int(summary.embedded as i64)
        ),
        &[
            (
                "embedded",
                crate::cmds_core::fmt_int(summary.embedded as i64),
            ),
            ("skipped", crate::cmds_core::fmt_int(summary.skipped as i64)),
        ],
        None,
    );
    ExitCode::SUCCESS
}

// --- knowledge ingest (cli/knowledge.py port) -----------------------------------

/// A relationship entry's field as text.
fn rel_field(entry: &Yaml, key: &str) -> String {
    match entry {
        Yaml::Hash(h) => h
            .get(&tools_km::ys(key))
            .and_then(|v| match v {
                Yaml::String(s) => Some(s.clone()),
                _ => None,
            })
            .unwrap_or_default(),
        _ => String::new(),
    }
}

fn knowledge_ingest(
    files: &[String],
    dirs: &[String],
    repos: &[String],
    do_ingest: bool,
    include_drafts: bool,
    outbox: Option<&str>,
) -> ExitCode {
    if files.is_empty() && dirs.is_empty() && repos.is_empty() {
        eprintln!("Error: --file, --dir, or --repo required.");
        return ExitCode::FAILURE;
    }
    for repo in repos {
        if !Path::new(repo).exists() {
            return usage_error(
                "knowledge ingest [OPTIONS]",
                &format!("Invalid value for '--repo': Path '{repo}' does not exist."),
            );
        }
    }
    let workspace = crate::store_paths::resolve_workspace(None);
    let file_paths: Vec<PathBuf> = files.iter().map(PathBuf::from).collect();
    let dir_paths: Vec<PathBuf> = dirs.iter().map(PathBuf::from).collect();
    let repo_paths: Vec<PathBuf> = repos.iter().map(PathBuf::from).collect();
    let manifest = match ingest::run_ingest(
        &workspace,
        &file_paths,
        &dir_paths,
        &repo_paths,
        outbox.map(Path::new),
        include_drafts,
    ) {
        Ok(manifest) => manifest,
        Err(e) => {
            eprintln!("Error: {e}.");
            return ExitCode::FAILURE;
        }
    };

    let dangling: Vec<DanglingPointer> = if do_ingest {
        let paths = resolve_store(None);
        if paths.ensure().is_err() {
            return cli_err("could not create the store directories");
        }
        let Ok(conn) = open_db_writable(&paths.db) else {
            return ExitCode::FAILURE;
        };
        let bundle = resolve_bundle(&paths.knowledge);
        let embed_available =
            tools_search::semantic_env(None).backend != EffectiveBackend::Unavailable;
        let embed_pass = |conn: &Connection, bundle: &Bundle| -> Result<usize, String> {
            cairn_build::embeddings::embed_knowledge(conn, bundle, 32, |_, _| {})
                .map(|summary| summary.embedded)
        };
        let embed: Option<&ingest::EmbedPass> = embed_available.then_some(&embed_pass);
        let report = match ingest::execute_manifest(&manifest, &conn, &bundle, embed) {
            Ok(report) => report,
            Err(e) => return cli_err(&e.0),
        };
        let dangling = report.dangling_pointers.clone();
        let embed_note = report
            .embedded
            .map(|n| format!(", embedded {n}"))
            .unwrap_or_default();
        println!(
            "Wrote {} document(s) to the store{embed_note}.",
            report.written.len()
        );
        // Verify legs in the report's insertion order; any False leg fails.
        let legs: Vec<(&str, String, bool)> = vec![
            ("index_edges", report.index_edges.to_string(), false),
            ("index_doc_refs", report.index_doc_refs.to_string(), false),
            ("verified_refs", report.verified_refs.to_string(), false),
            ("doc_link_tasks", report.doc_link_tasks.to_string(), false),
            ("store_count", report.store_count.to_string(), false),
            ("expected_count", report.expected_count.to_string(), false),
            (
                "count_ok",
                py_bool(report.count_ok).to_string(),
                !report.count_ok,
            ),
            (
                "smoke_search_hit",
                py_bool(report.smoke_search_hit).to_string(),
                !report.smoke_search_hit,
            ),
            (
                "validate_ok",
                py_bool(report.validate_ok).to_string(),
                !report.validate_ok,
            ),
            ("validate_errors", report.validate_errors.to_string(), false),
            ("validate_message", report.validate_message.clone(), false),
        ];
        let mut legs = legs;
        if let Some(err) = &report.embed_error {
            legs.insert(4, ("embed_error", err.clone(), false));
        }
        let mut failed: Vec<&str> = Vec::new();
        for (key, value, is_false) in &legs {
            println!("  {key}: {value}");
            if *is_false {
                failed.push(key);
            }
        }
        if !failed.is_empty() {
            eprintln!(
                "Error: post-ingest verification failed ({}).",
                failed.join(", ")
            );
            return ExitCode::FAILURE;
        }
        dangling
    } else {
        // Dry run: predict pointers that will never index before writing.
        let bundle = knowledge_bundle();
        ingest::predict_dangling(&bundle, &manifest)
    };

    println!(
        "Staged {} document(s), skipped {}.",
        manifest.accepted, manifest.skipped
    );
    for row in &manifest.rows {
        match row {
            ingest::ManifestRow::Skipped(s) => {
                println!("  skipped: {} ({})", s.source_path, s.skip);
            }
            ingest::ManifestRow::Staged(r) => {
                println!("  staged: {} -> {}", r.source_path, r.concept_id);
                for rel in &r.relationships {
                    println!(
                        "    {}: {} ({})",
                        rel_field(rel, "relation"),
                        rel_field(rel, "concept_id"),
                        rel_field(rel, "kind")
                    );
                }
            }
        }
    }
    echo_dangling_warnings(&dangling);
    println!("Outbox: {}", manifest.workspace.display());
    ExitCode::SUCCESS
}

// --- skill generate (cli/skill.py port) -----------------------------------------

fn skill_generate(
    selectors: &[String],
    output: Option<&str>,
    top_k: i64,
    top_n: i64,
    db: Option<&str>,
    polish: bool,
    knowledge: Option<&str>,
) -> ExitCode {
    let Ok(db_file) = require_db_dir(db) else {
        return ExitCode::FAILURE;
    };
    let Ok(conn) = open_db_writable(&db_file) else {
        return ExitCode::FAILURE;
    };
    let resolution = skillgen::resolve_selector(&conn, &selectors.join(" "));
    if !resolution.unmatched.is_empty() || resolution.candidates.is_empty() {
        let listed = if resolution.unmatched.is_empty() {
            "no candidates resolved".to_string()
        } else {
            resolution.unmatched.join(", ")
        };
        eprintln!("Error: Unresolved selector: {listed}; no skill written.");
        return ExitCode::FAILURE;
    }
    let assembly_bundle = resolve_bundle(&skillgen::knowledge_path_for(
        &conn,
        &resolve_store(None).knowledge,
    ));
    let draft = match skillgen::assemble_draft(
        &conn,
        &assembly_bundle,
        &resolution,
        top_k as usize,
        top_n as usize,
    ) {
        Ok(draft) => draft,
        Err(e) => return cli_err(&e.0),
    };

    let stem = if draft.module.is_empty() {
        selectors[0].clone()
    } else {
        draft.module.clone()
    };
    let slug = {
        let slug = skillgen::slugify(&stem);
        if slug.is_empty() {
            "skill".to_string()
        } else {
            slug
        }
    };
    let target = match output {
        Some(output) => PathBuf::from(output),
        None => match skillgen::landing_dir(&crate::store_paths::resolve_workspace(None), &slug) {
            Ok(target) => target,
            Err(e) => return cli_err(&e),
        },
    };
    let mut sections: Vec<(&str, String)> = Vec::new();
    if !draft.compass_body.is_empty() {
        sections.push(("Module Compass", draft.compass_body.clone()));
    }
    if !draft.symbols.is_empty() {
        sections.push((
            "Key Symbols",
            draft
                .symbols
                .iter()
                .map(|s| format!("- {s}"))
                .collect::<Vec<_>>()
                .join("\n"),
        ));
    }
    if !draft.memories.is_empty() {
        sections.push(("Module Memory", draft.memories.join("\n")));
    }
    let section_refs: Vec<(&str, &str)> = sections
        .iter()
        .map(|(heading, markdown)| (*heading, markdown.as_str()))
        .collect();
    let (inline, references) =
        skillgen::split_references(&section_refs, skillgen::REFERENCE_SPLIT_CHARS);
    let description = format!(
        "Module context for {stem}. Load when asked about {stem} structure, call \
         graphs, or blast radius before editing its files."
    );
    let inline_refs: Vec<(&str, &str)> = inline
        .iter()
        .map(|(heading, markdown)| (heading.as_str(), markdown.as_str()))
        .collect();
    let rendered =
        match skillgen::render_skill(&format!("cairn-{slug}"), &description, &inline_refs) {
            Ok(rendered) => rendered,
            Err(e) => return cli_err(&e),
        };
    // Critic gate on the exact bytes destined for disk; rejection writes nothing.
    let gate = skillgen::verify_draft(&conn, &rendered);
    if !gate.ok {
        eprintln!(
            "Error: Unverified references: {}; no skill written.",
            gate.failing_refs.join(", ")
        );
        return ExitCode::FAILURE;
    }
    for (_, content) in &references {
        let gate = skillgen::verify_draft(&conn, content);
        if !gate.ok {
            eprintln!(
                "Error: Unverified references: {}; no skill written.",
                gate.failing_refs.join(", ")
            );
            return ExitCode::FAILURE;
        }
    }
    let mut polish_outcome = None;
    if polish {
        let polish_bundle =
            resolve_bundle(&knowledge.map_or_else(|| resolve_store(None).knowledge, PathBuf::from));
        match skillgen::run_polish_stage(
            &polish_bundle,
            &conn,
            &slug,
            &rendered,
            &target.join("SKILL.md").to_string_lossy(),
        ) {
            Ok(outcome) => polish_outcome = Some(outcome),
            Err(e) => {
                // Polish is optional; its failure must not suppress the write.
                eprintln!(
                    "Polish stage unavailable ({}); the deterministic skill stands.",
                    e.0
                );
            }
        }
    }

    if std::fs::create_dir_all(&target).is_err() {
        return cli_err("could not create the skill directory");
    }
    if std::fs::write(target.join("SKILL.md"), &rendered).is_err() {
        return cli_err("could not write SKILL.md");
    }
    for (filename, content) in &references {
        let path = target.join(filename);
        if let Some(parent) = path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        if std::fs::write(&path, content).is_err() {
            return cli_err("could not write the reference file");
        }
    }
    println!("Wrote SKILL.md: {}", target.join("SKILL.md").display());
    if let Some(outcome) = polish_outcome {
        settle_polish(outcome, &target);
    }
    ExitCode::SUCCESS
}

fn settle_polish(outcome: skillgen::PolishOutcome, target: &Path) {
    match outcome.state.as_str() {
        "applied" => {
            let _ = std::fs::write(target.join("SKILL.md"), &outcome.rendered);
            println!(
                "Applied polished SKILL.md (task {} passed the critic gate).",
                outcome.task_id
            );
        }
        "queued" => {
            println!(
                "Queued polish task {}; any agent with the cairn skill can process it:",
                outcome.task_id
            );
            println!("  cairn task list --kind skill-polish --status pending");
            println!("  cairn task claim <id> && cairn task complete <id> --result-file <path>");
            println!("Rerun 'cairn skill generate --polish' after completion to apply the result.");
        }
        "pending" => {
            println!(
                "Polish task {} is still in flight; rerun 'cairn skill generate --polish' once it \
                 completes.",
                outcome.task_id
            );
        }
        // rejected | unavailable: the deterministic skill stands.
        _ => {
            let mut detail = outcome.reason.clone();
            if !outcome.failing_refs.is_empty() {
                let listed = outcome.failing_refs.join(", ");
                detail = if detail.is_empty() {
                    listed
                } else {
                    format!("{detail}; unverified references: {listed}")
                };
            }
            eprintln!(
                "Polish task {} not applied ({detail}); the deterministic skill stands.",
                outcome.task_id
            );
        }
    }
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

fn stale_issues(d: &tools_km::StaleStep) -> String {
    let mut issues = Vec::new();
    if !d.symbol_ok {
        issues.push(format!("symbol '{}' not found", d.symbol));
    }
    if !d.file_ok {
        issues.push(format!("file '{}' not found", d.file));
    }
    issues.join(", ")
}

fn workflow_sync(
    reference: Option<&str>,
    all: bool,
    dry_run: bool,
    max_steps: i64,
    db: Option<&str>,
    knowledge: Option<&str>,
) -> ExitCode {
    if reference.is_some() && all {
        eprintln!("Error: pass a workflow ref or --all, not both.");
        return ExitCode::FAILURE;
    }
    if reference.is_none() && !all {
        eprintln!("Error: provide a workflow ref or use --all.");
        return ExitCode::FAILURE;
    }
    let store = resolve_store(None);
    let knowledge_path = knowledge.map_or_else(|| store.knowledge.clone(), PathBuf::from);
    let bundle = resolve_bundle(&knowledge_path);
    let db_file = db_path(db);
    // No workflows exist without the bundle; guard before the DB is opened.
    if !bundle.root.exists() || !db_file.parent().is_some_and(Path::exists) {
        println!("No workflows to sync.");
        return ExitCode::SUCCESS;
    }
    let Ok(conn) = open_db_writable(&db_file) else {
        return ExitCode::FAILURE;
    };
    let max_steps = max_steps.max(1) as usize;

    if dry_run {
        if all {
            let reports = match tools_km::check_all_workflows(&conn, &bundle) {
                Ok(reports) => reports,
                Err(e) => return cli_err(&e.0),
            };
            if reports.is_empty() {
                println!("All workflows are up to date (no stale steps found).");
                return ExitCode::SUCCESS;
            }
            println!("{} workflow(s) with stale steps:", reports.len());
            for r in &reports {
                println!();
                println!("  {} ({})", r.title, r.concept_id);
                println!("    {}/{} steps stale:", r.stale_count(), r.total_steps);
                for d in r.stale_details.iter().take(8) {
                    println!("      {}: {}", d.step, stale_issues(d));
                }
                if r.stale_details.len() > 8 {
                    println!("      ... and {} more", r.stale_details.len() - 8);
                }
            }
        } else {
            let reference = reference.expect("ref or --all validated");
            let report = match tools_km::check_workflow_staleness(&conn, &bundle, reference) {
                Ok(report) => report,
                Err(e) => return cli_err(&e.0),
            };
            let Some(report) = report else {
                eprintln!("No workflow found matching '{reference}'.");
                return ExitCode::FAILURE;
            };
            if report.stale_count() == 0 {
                println!(
                    "'{}' is up to date ({} steps, no staleness).",
                    report.title, report.total_steps
                );
                return ExitCode::SUCCESS;
            }
            println!("'{}' ({})", report.title, report.concept_id);
            println!(
                "  {}/{} steps stale:",
                report.stale_count(),
                report.total_steps
            );
            for d in &report.stale_details {
                println!("    {}: {}", d.step, stale_issues(d));
            }
            println!();
            println!("Run without --dry-run to sync.");
        }
        return ExitCode::SUCCESS;
    }

    if all {
        let workflows = tools_km::list_documents(&bundle, Some("workflow"), None, None);
        if workflows.is_empty() {
            println!("No workflows to sync.");
            return ExitCode::SUCCESS;
        }
        println!("Syncing {} workflow(s)...", workflows.len());
        let mut synced = 0usize;
        let mut errors = 0usize;
        for w in &workflows {
            let cid = tools_km::normalize_doc_id(&bundle, &w.concept_id);
            let result = match tools_km::sync_workflow(&conn, &bundle, &cid, max_steps) {
                Ok(result) => result,
                Err(e) => return cli_err(&e.0),
            };
            let Some(result) = result else {
                continue;
            };
            if let Some(error) = &result.error {
                println!("  SKIP  {}: {}", result.title, error);
                errors += 1;
            } else {
                println!(
                    "  OK    {}: {} -> {} steps (+{}, -{})",
                    result.title,
                    result.old_step_count,
                    result.new_step_count,
                    result.added.len(),
                    result.removed.len()
                );
                synced += 1;
            }
        }
        println!();
        println!("Sync complete: {synced} synced, {errors} skipped.");
    } else {
        let reference = reference.expect("ref or --all validated");
        let result = match tools_km::sync_workflow(&conn, &bundle, reference, max_steps) {
            Ok(result) => result,
            Err(e) => return cli_err(&e.0),
        };
        let Some(result) = result else {
            eprintln!("No workflow found matching '{reference}'.");
            return ExitCode::FAILURE;
        };
        if let Some(error) = &result.error {
            println!("Could not sync '{}': {}", result.title, error);
            return ExitCode::FAILURE;
        }
        println!("Synced '{}' ({})", result.title, result.concept_id);
        println!(
            "  steps: {} -> {}",
            result.old_step_count, result.new_step_count
        );
        if !result.added.is_empty() {
            let shown: Vec<&str> = result.added.iter().take(8).map(String::as_str).collect();
            println!("  added: {}", shown.join(", "));
        }
        if !result.removed.is_empty() {
            let shown: Vec<&str> = result.removed.iter().take(8).map(String::as_str).collect();
            println!("  removed: {}", shown.join(", "));
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
        None,
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
    let now = tools_km::iso_utc_micros();
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

/// Best-effort embed of freshly saved memories; failures defer to `memory embed`.
fn embed_saved_memories(conn: &Connection, bundle: &Bundle, paths: &[String]) {
    if cairn_build::embeddings::embed_memory_concepts(conn, bundle, paths).is_err() {
        eprintln!(
            "Embedding failed; memories saved without vectors \
             (run `cairn memory embed` to backfill)."
        );
    }
}

fn memory_reflect(db: Option<&str>, knowledge: Option<&str>) -> ExitCode {
    let bundle = memory_bundle(knowledge);
    let Ok(conn) = open_db_writable(&db_path(db)) else {
        return ExitCode::FAILURE;
    };
    match tools_km::reflect_store(&bundle, &conn) {
        Ok(summary) => {
            println!(
                "Reflected {} memory(ies): {} changed, {} unchanged, {} contested.",
                summary.changed + summary.unchanged,
                summary.changed,
                summary.unchanged,
                summary.contested
            );
            ExitCode::SUCCESS
        }
        Err(e) => cli_err(&format!("reflect failed: {}", e.0)),
    }
}

fn memory_evolve(
    memory_path: &str,
    title: Option<&str>,
    body: Option<&str>,
    db: Option<&str>,
    knowledge: Option<&str>,
) -> ExitCode {
    let bundle = memory_bundle(knowledge);
    let Ok(conn) = open_db_writable(&db_path(db)) else {
        return ExitCode::FAILURE;
    };
    let result = match tools_km::evolve_memory(&conn, &bundle, memory_path, title, body) {
        Ok(result) => result,
        Err(e) => return cli_err(&e.0),
    };
    let Some(result) = result else {
        eprintln!("Memory not found: '{memory_path}'.");
        return ExitCode::FAILURE;
    };
    embed_saved_memories(&conn, &bundle, std::slice::from_ref(&result.path));
    println!(
        "Evolved '{memory_path}' -> {} (score={}, tier={}, superseded {})",
        result.path,
        py_float_text(result.score),
        result.tier,
        result.superseded
    );
    ExitCode::SUCCESS
}

/// JSON-object lines from a backend transcript extraction (`_parse_extract_lines`).
fn extract_candidates(cli: &str, transcript: &str) -> Vec<serde_json::Value> {
    let transcript = tools_km::strip_private_data(transcript);
    let facts = serde_json::json!({ "transcript": transcript });
    let prompt = format!(
        "[cairn task: memory-extract]\n\nUse the cairn skill to process this. \
         Facts (graph-grounded):\n{}\n\n## Output spec\n{}\n\nOutput ONLY the \
         result content, nothing else.",
        serde_json::to_string_pretty(&facts).unwrap_or_default(),
        tasks::output_spec("memory-extract", &yaml_rust2::yaml::Hash::new()),
    );
    let argv: Vec<String> = match cli {
        "claude" => vec![
            cli.to_string(),
            "-p".into(),
            prompt,
            "--allowedTools".into(),
            "Read,Glob,Grep".into(),
        ],
        "droid" => vec![cli.to_string(), "exec".into(), prompt],
        _ => vec![cli.to_string(), "exec".into(), "--prompt".into(), prompt],
    };
    let Ok(output) = std::process::Command::new(&argv[0])
        .args(&argv[1..])
        .output()
    else {
        return Vec::new();
    };
    if !output.status.success() {
        return Vec::new();
    }
    String::from_utf8_lossy(&output.stdout)
        .lines()
        .filter_map(|line| {
            let line = line.trim();
            if !line.starts_with('{') {
                return None;
            }
            serde_json::from_str(line).ok()
        })
        .collect()
}

fn memory_capture(
    session_transcript: Option<&str>,
    session_transcript_stdin: bool,
    session_id: &str,
    db: Option<&str>,
    knowledge: Option<&str>,
) -> ExitCode {
    let Ok(conn) = open_db_writable(&db_path(db)) else {
        return ExitCode::FAILURE;
    };
    let bundle = memory_bundle(knowledge);
    let transcript = if session_transcript_stdin {
        use std::io::Read;
        let mut buf = String::new();
        if std::io::stdin().read_to_string(&mut buf).is_err() {
            return cli_err("could not read the session transcript from stdin");
        }
        buf
    } else {
        session_transcript
            .filter(|t| !t.is_empty())
            .unwrap_or("[]")
            .to_string()
    };

    let backend = std::env::var("CAIRN_LLM_BACKEND")
        .unwrap_or_default()
        .to_lowercase();
    let candidates = if !backend.is_empty() && crate::agent_install::which(&backend).is_none() {
        eprintln!("LLM backend '{backend}' not found on PATH; queueing memory-extract task.");
        Vec::new()
    } else if matches!(backend.as_str(), "droid" | "opencode" | "claude") {
        extract_candidates(&backend, &transcript)
    } else {
        Vec::new()
    };

    let mut recorded = 0usize;
    let mut failed = 0usize;
    let mut captured_paths: Vec<String> = Vec::new();
    for cand in &candidates {
        let cand_type = cand
            .get("type")
            .and_then(|v| v.as_str())
            .filter(|t| MEMORY_TYPES.contains(t))
            .unwrap_or("decision");
        let title = cand
            .get("title")
            .and_then(|v| v.as_str())
            .unwrap_or("untitled");
        let body = cand.get("body").and_then(|v| v.as_str()).unwrap_or("");
        let confidence = match cand.get("confidence") {
            None => Ok(0.6),
            Some(serde_json::Value::Number(n)) => n.as_f64().ok_or(()),
            Some(serde_json::Value::String(s)) => s.parse::<f64>().map_err(|_| ()),
            Some(_) => Err(()),
        };
        let Ok(confidence) = confidence else {
            failed += 1;
            continue;
        };
        match tools_km::capture_memory(
            &conn,
            &bundle,
            cand_type,
            title,
            body,
            None,
            confidence,
            Some(session_id),
            None,
        ) {
            Ok(result) => {
                captured_paths.push(result.path);
                recorded += 1;
            }
            Err(_) => failed += 1,
        }
    }
    if !captured_paths.is_empty() {
        embed_saved_memories(&conn, &bundle, &captured_paths);
    }
    if recorded > 0 {
        println!("Captured {recorded} memories from session {session_id}.");
    }
    if failed > 0 {
        eprintln!("Skipped {failed} unrecordable candidate(s) from session {session_id}.");
    }
    if recorded == 0 {
        let mut facts = yaml_rust2::yaml::Hash::new();
        let scrubbed = tools_km::strip_private_data(&transcript);
        let truncated: String = scrubbed.chars().take(8000).collect();
        facts.insert(Yaml::String("transcript".into()), Yaml::String(truncated));
        facts.insert(
            Yaml::String("session_id".into()),
            Yaml::String(session_id.to_string()),
        );
        match tasks::create_task(
            &bundle,
            "memory-extract",
            &format!("session-{session_id}"),
            facts,
            0,
        ) {
            Ok(task) => println!(
                "No agent available; queued memory-extract task {}.",
                task.id
            ),
            Err(e) => return cli_err(&e.0),
        }
    }
    ExitCode::SUCCESS
}

fn memory_embed(
    db: Option<&str>,
    knowledge: Option<&str>,
    batch_size: i64,
    reap: bool,
) -> ExitCode {
    let bundle = memory_bundle(knowledge);
    let Ok(conn) = open_db_writable(&db_path(db)) else {
        return ExitCode::FAILURE;
    };
    if reap {
        match cairn_build::embeddings::reap_orphaned_memory_embeddings(&conn, &bundle) {
            Ok(reaped) if reaped > 0 => println!("Reaped {reaped} orphaned embedding row(s)."),
            Ok(_) => {}
            Err(e) => return cli_err(&e),
        }
    }
    match cairn_build::embeddings::embed_memory(&conn, &bundle, batch_size.max(1) as usize) {
        Ok(summary) => {
            println!(
                "Embedded {} memory concept(s) with {} \
                 ({} already up to date, {} total).",
                summary.embedded, summary.model, summary.skipped, summary.total
            );
            ExitCode::SUCCESS
        }
        Err(e) => cli_err(&e),
    }
}

fn memory_batch_critic(db: Option<&str>, knowledge: Option<&str>) -> ExitCode {
    let bundle = memory_bundle(knowledge);
    let Ok(conn) = open_db_writable(&db_path(db)) else {
        return ExitCode::FAILURE;
    };
    match tools_km::batch_critic(&conn, &bundle) {
        Ok(result) => {
            println!(
                "Processed {} drafts: {} -> tribal, {} dropped, {} remain drafts.",
                result.processed, result.tribal, result.dropped, result.remaining_drafts
            );
            ExitCode::SUCCESS
        }
        Err(e) => cli_err(&e.0),
    }
}

fn memory_consolidate(db: Option<&str>, knowledge: Option<&str>) -> ExitCode {
    let bundle = memory_bundle(knowledge);
    let Ok(conn) = open_db_writable(&db_path(db)) else {
        return ExitCode::FAILURE;
    };
    match tools_km::consolidate_memories(&bundle, Some(&conn)) {
        Ok(count) => {
            println!("Consolidated {count} raw memories into tribal knowledge.");
            ExitCode::SUCCESS
        }
        Err(e) => cli_err(&e.0),
    }
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

fn critic_verdict_lines(result: &tools_km::CriticResult) {
    println!(
        "  passed: {}  quality: {:.2}  errors: {}  warnings: {}",
        py_bool(result.passed),
        result.quality_score,
        result.errors.len(),
        result.warnings.len()
    );
    for e in &result.errors {
        println!("  ERROR: {e}");
    }
    for w in &result.warnings {
        println!("  warn: {w}");
    }
}

fn module_error_exit(module: &str, e: &tools_km::ModuleResolutionError) -> ExitCode {
    println!("Cannot generate compass for '{module}': {}", e.0);
    ExitCode::FAILURE
}

/// SubprocessBackend exec: run the agent CLI, killed at the 120s timeout.
fn exec_agent(argv: &[String]) -> String {
    use std::io::Read;
    use std::process::Stdio;

    const EXEC_TIMEOUT_S: u64 = 120;
    let Ok(mut child) = std::process::Command::new(&argv[0])
        .args(&argv[1..])
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
    else {
        return String::new();
    };
    let mut stdout = child.stdout.take().expect("piped stdout");
    let reader = std::thread::spawn(move || {
        let mut buf = String::new();
        let _ = stdout.read_to_string(&mut buf);
        buf
    });
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(EXEC_TIMEOUT_S);
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                let out = reader.join().unwrap_or_default();
                return if status.success() { out } else { String::new() };
            }
            Ok(None) if std::time::Instant::now() < deadline => {
                std::thread::sleep(std::time::Duration::from_millis(100));
            }
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return String::new();
            }
        }
    }
}

/// FileQueueBackend fallback: queue the task and poll it for the result.
fn queue_wait(
    bundle: &Bundle,
    kind: &str,
    resource: &str,
    facts: &yaml_rust2::yaml::Hash,
    timeout_s: u64,
) -> String {
    const POLL_INTERVAL_S: u64 = 2;
    const MAX_WAIT_S: u64 = 600;
    let Ok(task) = tasks::create_task(bundle, kind, resource, facts.clone(), 0) else {
        return String::new();
    };
    let deadline =
        std::time::Instant::now() + std::time::Duration::from_secs(timeout_s.min(MAX_WAIT_S));
    loop {
        if let Ok(Some(t)) = tasks::get_task(bundle, &task.id) {
            if t.status == "done" {
                return tasks::read_result(bundle, &task.id).unwrap_or_default();
            }
            if t.status == "failed" || t.status == "dropped" {
                return String::new();
            }
        }
        if std::time::Instant::now() >= deadline {
            return String::new();
        }
        std::thread::sleep(std::time::Duration::from_secs(POLL_INTERVAL_S));
    }
}

/// SubprocessBackend synthesize/revise: exec the agent, then the queue fallback.
fn agent_backend_run(
    cli: &str,
    prompt_id: &str,
    facts: &yaml_rust2::yaml::Hash,
    bundle: &Bundle,
) -> String {
    let prompt = format!(
        "[cairn task: {prompt_id}]\n\nUse the cairn skill to process this. \
         Facts (graph-grounded):\n{}\n\n## Output spec\n{}\n\nOutput ONLY the \
         result content, nothing else.",
        py_json::dumps(&py_json::yaml_to_value(&Yaml::Hash(facts.clone()))),
        tasks::output_spec(prompt_id, &yaml_rust2::yaml::Hash::new()),
    );
    let argv: Vec<String> = match cli {
        "claude" => vec![
            cli.to_string(),
            "-p".into(),
            prompt,
            "--allowedTools".into(),
            "Read,Glob,Grep".into(),
        ],
        "droid" => vec![cli.to_string(), "exec".into(), prompt],
        _ => vec![cli.to_string(), "exec".into(), "--prompt".into(), prompt],
    };
    let out = exec_agent(&argv);
    if !out.is_empty() {
        return out;
    }
    let resource = match facts.get(&Yaml::String("resource".into())) {
        Some(Yaml::String(s)) => s.clone(),
        _ => prompt_id.to_string(),
    };
    queue_wait(bundle, prompt_id, &resource, facts, 120)
}

/// One generator->critic->revise outcome for the agent-sync path.
struct CompassLlmOutcome {
    concept: Concept,
    mode: String,
    cycles: usize,
    fact_errors: Vec<String>,
    trace: Vec<(usize, Vec<String>, f64)>,
}

fn generate_compass_with_llm(
    conn: &Connection,
    bundle: &Bundle,
    module: &str,
    repo: Option<&str>,
    cli: &str,
) -> Result<CompassLlmOutcome, tools_km::ModuleResolutionError> {
    let facts = tools_km::gather_module_facts(conn, module, repo)?;
    let resolved_repo = facts.repo.as_deref();
    let facts_yaml = tools_km::compass_facts_yaml(&facts);
    let mut trace: Vec<(usize, Vec<String>, f64)> = Vec::new();
    let mut draft = String::new();
    let mut fact_errors: Vec<String> = Vec::new();
    for cycle in 0..=(tasks::MAX_REVISE_CYCLES as usize) {
        draft = if cycle == 0 {
            agent_backend_run(cli, "compass-synthesize", &facts_yaml, bundle)
        } else {
            let mut revise = facts_yaml.clone();
            revise.insert(
                Yaml::String("previous_draft".into()),
                Yaml::String(draft.clone()),
            );
            revise.insert(
                Yaml::String("errors".into()),
                Yaml::Array(fact_errors.iter().map(tools_km::ys).collect()),
            );
            agent_backend_run(cli, "compass-revise", &revise, bundle)
        };
        if draft.is_empty() {
            let concept = tools_km::generate_compass(conn, bundle, &facts.module, resolved_repo)?;
            return Ok(CompassLlmOutcome {
                concept,
                mode: "deterministic-fallback".to_string(),
                cycles: cycle,
                fact_errors: vec!["llm unavailable".to_string()],
                trace,
            });
        }
        let tmp = Concept {
            type_: "Compass".to_string(),
            resource: Some(facts.module.clone()),
            body: draft.clone(),
            ..Default::default()
        };
        match tools_km::critic_concept(&tmp, conn) {
            Ok(result) => {
                fact_errors = result.errors.clone();
                trace.push((cycle, result.errors, result.quality_score));
            }
            Err(e) => return Err(tools_km::ModuleResolutionError(e.0)),
        }
        if fact_errors.is_empty() {
            break;
        }
    }
    let concept =
        tools_km::compass_concept_from_body(conn, bundle, &facts.module, resolved_repo, &draft)?;
    Ok(CompassLlmOutcome {
        concept,
        mode: "llm".to_string(),
        cycles: trace.len(),
        fact_errors,
        trace,
    })
}

#[allow(clippy::too_many_arguments)]
fn compass_generate(
    module: &str,
    repo: Option<&str>,
    db: Option<&str>,
    knowledge: Option<&str>,
    use_llm: bool,
    dry_run: bool,
    show_rejections: bool,
) -> ExitCode {
    let knowledge_path = knowledge.map_or_else(knowledge_default, PathBuf::from);
    let Ok(db_file) = require_db_dir(db) else {
        return ExitCode::FAILURE;
    };
    let Ok(conn) = open_db_writable(&db_file) else {
        return ExitCode::FAILURE;
    };
    let bundle = resolve_bundle(&knowledge_path);
    let backend = std::env::var("CAIRN_LLM_BACKEND")
        .unwrap_or_else(|_| "file-queue".to_string())
        .to_lowercase();

    if use_llm && matches!(backend.as_str(), "droid" | "opencode" | "claude") {
        let outcome = match generate_compass_with_llm(&conn, &bundle, module, repo, &backend) {
            Ok(outcome) => outcome,
            Err(e) => return module_error_exit(module, &e),
        };
        let concept = outcome.concept;
        let Ok(result) = tools_km::critic_concept(&concept, &conn) else {
            return cli_err("critic execution failed");
        };
        if show_rejections {
            println!("--- critic trace ({} cycle(s)) ---", outcome.trace.len());
            for (cycle, errors, quality) in &outcome.trace {
                let verdict = if errors.is_empty() { "ok" } else { "REJECTED" };
                println!(
                    "  cycle {cycle}: quality={quality:.2} ({} error(s)) [{verdict}]",
                    errors.len()
                );
                for e in errors.iter().take(3) {
                    println!("    - {e}");
                }
            }
            println!();
        }
        let fact_errors = &outcome.fact_errors;
        if dry_run {
            println!(
                "Would write compass ({}): {}",
                outcome.mode, concept.concept_id
            );
            println!("  revise cycles: {}", outcome.cycles);
            println!(
                "  critic verdict: passed={} quality={:.2} errors={} warnings={}",
                py_bool(result.passed),
                result.quality_score,
                result.errors.len(),
                result.warnings.len()
            );
            for e in fact_errors.iter().take(5) {
                println!("    {e}");
            }
            println!("\n--- body (not written) ---\n");
            println!("{}", concept.body);
            return ExitCode::SUCCESS;
        }
        if !fact_errors.is_empty() {
            println!(
                "compass {}: NOT written — {} unresolved fact error(s) after {} revise cycle(s):",
                concept.concept_id,
                fact_errors.len(),
                outcome.cycles
            );
            for e in fact_errors.iter().take(5) {
                println!("    {e}");
            }
            return ExitCode::FAILURE;
        }
        if !result.passed {
            println!(
                "Compass rejected by critic (quality={:.2}, {} error(s)) — not written.",
                result.quality_score,
                result.errors.len()
            );
            for e in &result.errors {
                println!("  ERROR: {e}");
            }
            for w in &result.warnings {
                println!("  warn: {w}");
            }
            return ExitCode::FAILURE;
        }
        let mut concept = concept;
        if let Err(e) = bundle.write_concept(&mut concept) {
            return cli_err(&e.0);
        }
        println!(
            "Generated compass ({}): {}",
            outcome.mode, concept.concept_id
        );
        println!("  revise cycles: {}", outcome.cycles);
        println!("  fact errors: {}", fact_errors.len());
        for e in fact_errors.iter().take(5) {
            println!("    {e}");
        }
        println!("\n--- body ---\n");
        println!("{}", concept.body);
        return ExitCode::SUCCESS;
    }

    if use_llm {
        let facts = match tools_km::gather_module_facts(&conn, module, repo) {
            Ok(facts) => facts,
            Err(e) => return module_error_exit(module, &e),
        };
        if dry_run {
            println!("--- dry-run: task not queued ---");
            println!(
                "Would queue compass-synthesize task for `{}` ({} symbols, {} key file(s)).",
                facts.module,
                facts.symbol_count,
                facts.key_files.len()
            );
            println!("Re-run without --dry-run to enqueue it.");
            return ExitCode::SUCCESS;
        }
        let task = match tasks::create_task(
            &bundle,
            "compass-synthesize",
            &facts.module,
            tools_km::compass_facts_yaml(&facts),
            0,
        ) {
            Ok(task) => task,
            Err(e) => return cli_err(&e.0),
        };
        println!("Queued compass task: {}", task.id);
        println!("Any agent with the cairn skill can process it:");
        println!(
            "  cairn task show {}        # view the task + facts",
            task.id
        );
        println!("  cairn task claim {}       # claim it", task.id);
        println!(
            "  cairn task complete {} --result-file <path>   # submit result",
            task.id
        );
        println!("On completion, run: cairn compass validate  (deterministic critic)");
        return ExitCode::SUCCESS;
    }

    let concept = match tools_km::generate_compass(&conn, &bundle, module, repo) {
        Ok(concept) => concept,
        Err(e) => return module_error_exit(module, &e),
    };
    let Ok(result) = tools_km::critic_concept(&concept, &conn) else {
        return cli_err("critic execution failed");
    };
    if dry_run {
        println!("--- critic verdict (dry-run; nothing written) ---");
        critic_verdict_lines(&result);
        println!("\n--- body (not written) ---\n");
        println!("{}", concept.body);
        return ExitCode::SUCCESS;
    }
    if !result.passed {
        println!(
            "Compass rejected by critic (quality={:.2}, {} error(s)) — not written.",
            result.quality_score,
            result.errors.len()
        );
        for e in &result.errors {
            println!("  ERROR: {e}");
        }
        for w in &result.warnings {
            println!("  warn: {w}");
        }
        println!(
            "\nThe deterministic generator is graph-sourced, so this usually means \
             a stale graph (run `cairn build`) or a generator bug."
        );
        return ExitCode::FAILURE;
    }
    let mut concept = concept;
    if let Err(e) = bundle.write_concept(&mut concept) {
        return cli_err(&e.0);
    }
    println!("Generated compass: {}", concept.concept_id);
    println!(
        "  quality: {:.2}, errors: {}",
        result.quality_score,
        result.errors.len()
    );
    for e in &result.errors {
        println!("  ERROR: {e}");
    }
    println!("\n--- body ---\n");
    println!("{}", concept.body);
    ExitCode::SUCCESS
}

fn compass_gaps(db: Option<&str>, knowledge: Option<&str>) -> ExitCode {
    let knowledge_path = knowledge.map_or_else(knowledge_default, PathBuf::from);
    let Ok(db_file) = require_db_dir(db) else {
        return ExitCode::FAILURE;
    };
    let Ok(conn) = open_db_writable(&db_file) else {
        return ExitCode::FAILURE;
    };
    let bundle = resolve_bundle(&knowledge_path);
    let gaps = tools_km::detect_gaps(&conn, &bundle);
    if gaps.is_empty() {
        println!("No coverage gaps (all modules have compass files).");
        return ExitCode::SUCCESS;
    }
    println!("{} modules without compass coverage:", gaps.len());
    for g in &gaps {
        println!("  {g}");
    }
    ExitCode::SUCCESS
}

#[allow(clippy::too_many_arguments)]
fn compass_flow(
    entry: &str,
    db: Option<&str>,
    knowledge: Option<&str>,
    dry_run: bool,
    as_workflow: bool,
    max_steps: i64,
    use_llm: bool,
    entry_id: Option<&str>,
) -> ExitCode {
    let knowledge_path = knowledge.map_or_else(knowledge_default, PathBuf::from);
    let Ok(db_file) = require_db_dir(db) else {
        return ExitCode::FAILURE;
    };
    let Ok(conn) = open_db_writable(&db_file) else {
        return ExitCode::FAILURE;
    };
    let bundle = resolve_bundle(&knowledge_path);

    if entry_id.is_none() {
        let defs: Vec<_> = cairn_query::graph::find_definition(&conn, entry, 50)
            .unwrap_or_default()
            .into_iter()
            .filter(|d| d.name == entry || d.qualified_name.as_deref() == Some(entry))
            .collect();
        if defs.len() > 1 {
            println!(
                "`{entry}` is ambiguous: {} symbols share the name.",
                defs.len()
            );
            for d in &defs {
                println!("  {}  {} ({})", d.id, d.file_path, d.repo);
            }
            println!(
                "Re-run with --entry-id <id>, or use `cairn compass flow-gaps --generate` \
                 for the batch path."
            );
            return ExitCode::FAILURE;
        }
    }

    let facts = match tools_km::gather_flow_facts(&conn, entry, entry_id) {
        Ok(facts) => facts,
        Err(e) => return cli_err(&e.to_string()),
    };
    if facts.total_steps <= 1 {
        println!("No outgoing calls traced from `{entry}` -- nothing to document.");
        println!("Verify the symbol exists and has resolved callees (run `cairn build` first).");
        return ExitCode::FAILURE;
    }

    if use_llm {
        if dry_run {
            println!("--- dry-run: task not queued ---");
            println!(
                "Would queue flow-synthesize task for `{entry}` ({} step(s)).",
                facts.total_steps
            );
            println!("Re-run without --dry-run to enqueue it.");
            return ExitCode::SUCCESS;
        }
        let key = match tools_km::flow_coverage_key(&conn, entry, entry_id, &facts) {
            Ok(key) => key,
            Err(e) => return cli_err(&e.to_string()),
        };
        let task = match tasks::create_task(
            &bundle,
            "flow-synthesize",
            &key,
            tools_km::flow_facts_yaml(&facts),
            0,
        ) {
            Ok(task) => task,
            Err(e) => return cli_err(&e.0),
        };
        println!(
            "Traced {} step(s) from `{entry}` across {} module(s).",
            facts.total_steps,
            facts.modules.len()
        );
        println!("Queued flow task: {}", task.id);
        println!("Any agent with the cairn skill can process it:");
        println!(
            "  cairn task show {}        # view the task + facts",
            task.id
        );
        println!("  cairn task claim {}       # claim it", task.id);
        println!(
            "  cairn task complete {} --result-file <path>   # submit result",
            task.id
        );
        let safe_id = key.replace(['/', '.', '#'], "-");
        println!("On completion, the critic auto-promotes to compass/flow-{safe_id}.");
        return ExitCode::SUCCESS;
    }

    println!(
        "Traced {} step(s) from `{entry}` across {} module(s).",
        facts.total_steps,
        facts.modules.len()
    );
    if !facts.branches.is_empty() {
        println!("  branch points: {}", facts.branches.len());
    }
    if !facts.leaves.is_empty() {
        println!("  terminal calls: {}", facts.leaves.len());
    }
    println!();

    let concept = match tools_km::generate_flow_compass(&conn, entry, entry_id, &facts, None, None)
    {
        Ok(concept) => concept,
        Err(e) => return cli_err(&e.to_string()),
    };
    let Ok(result) = tools_km::critic_concept(&concept, &conn) else {
        return cli_err("critic execution failed");
    };

    let mut steps = Vec::new();
    if as_workflow {
        steps = tools_km::flow_to_workflow(&facts, max_steps.max(0) as usize);
        if dry_run {
            println!("--- workflow steps (not written) ---");
            for (i, step) in steps.iter().enumerate() {
                println!("  {}. {}", i + 1, step.name.clone().unwrap_or_default());
                if let Some(desc) = &step.description
                    && !desc.is_empty()
                {
                    println!("     {desc}");
                }
            }
            println!();
        }
    }

    if dry_run {
        println!("--- flow trace ---");
        for line in &facts.chain_lines {
            println!("  {line}");
        }
        println!("\n--- critic verdict (dry-run; nothing written) ---");
        critic_verdict_lines(&result);
        println!("\n--- body (not written) ---\n");
        println!("{}", concept.body);
        return ExitCode::SUCCESS;
    }

    if !result.passed {
        println!(
            "Flow compass rejected by critic (quality={:.2}, {} error(s)) -- not written.",
            result.quality_score,
            result.errors.len()
        );
        for e in &result.errors {
            println!("  ERROR: {e}");
        }
        for w in &result.warnings {
            println!("  warn: {w}");
        }
        println!(
            "\nThe flow body is graph-sourced, so this usually means a stale graph \
             (run `cairn build`) or a generator bug."
        );
        return ExitCode::FAILURE;
    }

    let mut concept = concept;
    if let Err(e) = bundle.write_concept(&mut concept) {
        return cli_err(&e.0);
    }
    println!("Generated flow compass: {}", concept.concept_id);
    println!(
        "  quality: {:.2}, errors: {}",
        result.quality_score,
        result.errors.len()
    );
    println!("\n--- body ---\n");
    println!("{}", concept.body);

    if as_workflow {
        let workflow_cid = match tools_km::generate_flow_workflow(
            &bundle,
            entry,
            &facts,
            max_steps.max(0) as usize,
        ) {
            Ok(cid) => cid,
            Err(e) => return cli_err(&e.0),
        };
        println!(
            "Generated flow workflow: {workflow_cid} ({} steps)",
            steps.len()
        );
        println!("  Trace with: cairn knowledge workflow trace \"Flow: {entry}\"");
    }
    ExitCode::SUCCESS
}

/// pathlib `Path(suffix).parent/stem` rendering for a colliding flow title.
fn flow_gap_suffix_stem(resource: &str, fname: &str) -> String {
    let suffix = match resource.split_once('#') {
        Some((_, s)) => s,
        None => fname,
    };
    if suffix.contains('/') {
        let path = Path::new(suffix);
        let parent = path.parent().and_then(|p| p.to_str()).unwrap_or("");
        let stem = path.file_stem().and_then(|s| s.to_str()).unwrap_or(suffix);
        format!("{parent}/{stem}")
    } else {
        Path::new(suffix)
            .file_stem()
            .and_then(|s| s.to_str())
            .unwrap_or(suffix)
            .to_string()
    }
}

fn compass_flow_gaps(
    min_edges: i64,
    generate: bool,
    limit: i64,
    dry_run: bool,
    db: Option<&str>,
    knowledge: Option<&str>,
) -> ExitCode {
    let knowledge_path = knowledge.map_or_else(knowledge_default, PathBuf::from);
    let Ok(db_file) = require_db_dir(db) else {
        return ExitCode::FAILURE;
    };
    let Ok(conn) = open_db_writable(&db_file) else {
        return ExitCode::FAILURE;
    };
    let bundle = resolve_bundle(&knowledge_path);
    let result = match tools_km::detect_flow_gaps(&conn, &bundle, min_edges) {
        Ok(result) => result,
        Err(e) => return cli_err(&e.0),
    };
    for cid in &result.unreadable {
        println!("WARNING: unreadable flow compass file skipped: {cid}");
    }
    let uncovered = result.uncovered;
    let covered = result.covered;
    if uncovered.is_empty() && covered.is_empty() {
        println!(
            "No candidate flows found (min-edges={min_edges}). Run `cairn build` first, \
             or lower --min-edges."
        );
        return ExitCode::SUCCESS;
    }

    if generate {
        let targets: &[tools_km::FlowGapEntry] = if limit <= 0 {
            &uncovered
        } else {
            let n = (limit as usize).min(uncovered.len());
            &uncovered[..n]
        };
        println!(
            "Generating {} flow compass(es) (min-edges={}, {} already documented)...",
            targets.len(),
            min_edges,
            covered.len()
        );
        if dry_run {
            println!("(dry-run: nothing will be written)");
        }
        println!();

        let (mut generated, mut rejected, mut skipped, mut errors) =
            (0usize, 0usize, 0usize, 0usize);
        for entry in targets {
            let fname = entry.file.rsplit('/').next().unwrap_or(&entry.file);
            let display_title = if entry.colliding {
                format!(
                    "Flow: {} ({})",
                    entry.name,
                    flow_gap_suffix_stem(&entry.resource, fname)
                )
            } else {
                format!("Flow: {}", entry.name)
            };
            let entry_id = Some(entry.id.as_str());
            let facts = match tools_km::gather_flow_facts(&conn, &entry.name, entry_id) {
                Ok(facts) => facts,
                Err(e) => {
                    println!("  ERROR {display_title} ({e})");
                    errors += 1;
                    continue;
                }
            };
            if facts.total_steps <= 1 {
                println!("  SKIP  {:40} (no outgoing calls)", entry.name);
                skipped += 1;
                continue;
            }
            let concept = match tools_km::generate_flow_compass(
                &conn,
                &entry.name,
                entry_id,
                &facts,
                Some(&entry.resource),
                Some(&display_title),
            ) {
                Ok(concept) => concept,
                Err(e) => {
                    println!("  ERROR {display_title} ({e})");
                    errors += 1;
                    continue;
                }
            };
            let critic_result = match tools_km::critic_concept(&concept, &conn) {
                Ok(result) => result,
                Err(e) => {
                    println!("  ERROR {display_title} ({})", e.0);
                    errors += 1;
                    continue;
                }
            };
            if !critic_result.passed {
                println!(
                    "  REJECT {display_title} (quality={:.2})",
                    critic_result.quality_score
                );
                rejected += 1;
                continue;
            }
            if dry_run {
                println!("  WOULD  {display_title}");
                generated += 1;
                continue;
            }
            let mut concept = concept;
            if let Err(e) = bundle.write_concept(&mut concept) {
                println!("  ERROR {display_title} ({})", e.0);
                errors += 1;
                continue;
            }
            println!(
                "  OK    {display_title}  (steps={}, quality={:.2})",
                facts.total_steps, critic_result.quality_score
            );
            generated += 1;
        }

        println!();
        let action = if dry_run {
            "would generate"
        } else {
            "generated"
        };
        println!(
            "Batch complete: {action} {generated}, {rejected} rejected, {skipped} skipped, \
             {errors} error(s)."
        );
        if !dry_run && (rejected > 0 || errors > 0) {
            return ExitCode::FAILURE;
        }
        return ExitCode::SUCCESS;
    }

    if !uncovered.is_empty() {
        println!(
            "{} undocumented flow(s) (min-edges={}, {} already documented):",
            uncovered.len(),
            min_edges,
            covered.len()
        );
        for e in &uncovered {
            let fname = e.file.rsplit('/').next().unwrap_or(&e.file);
            println!("  {:40} out={:3}  {}", e.name, e.out_edges, fname);
        }
    } else {
        println!(
            "All candidate flows are documented ({} total).",
            covered.len()
        );
    }
    if !covered.is_empty() {
        println!("\nAlready documented ({}):", covered.len());
        for e in &covered {
            let fname = e.file.rsplit('/').next().unwrap_or(&e.file);
            println!("  {:40} out={:3}  {}  [DONE]", e.name, e.out_edges, fname);
        }
    }
    ExitCode::SUCCESS
}

// --- wiki group --------------------------------------------------------------

fn wiki_search(query: &str, knowledge: Option<&str>) -> ExitCode {
    let knowledge_path = knowledge.map_or_else(|| resolve_store(None).knowledge, PathBuf::from);
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

/// Python str() of a bool.
fn py_bool(b: bool) -> &'static str {
    if b { "True" } else { "False" }
}

fn manifest_error_exit(msg: &str) -> ExitCode {
    eprintln!("Wiki manifest error: {msg}");
    ExitCode::FAILURE
}

#[allow(clippy::too_many_arguments)]
fn wiki_generate(
    repo: Option<&str>,
    db: Option<&str>,
    knowledge: Option<&str>,
    dry_run: bool,
    show_rejections: bool,
    llm: bool,
    pages: i64,
    diagrams: bool,
    refine_catalog: bool,
    force: bool,
) -> ExitCode {
    let paths = resolve_store(None);
    let knowledge_path = knowledge.map_or_else(|| paths.knowledge.clone(), PathBuf::from);
    let bundle = resolve_bundle(&knowledge_path);
    let Ok(db_file) = require_db_dir(db) else {
        return ExitCode::FAILURE;
    };
    let Ok(conn) = open_db_writable(&db_file) else {
        return ExitCode::FAILURE;
    };
    let repos: Vec<String> = match repo {
        Some(repo) => vec![repo.to_string()],
        None => {
            let Ok(rows) = conn.prepare("SELECT id FROM repos").and_then(|mut stmt| {
                stmt.query_map([], |r| r.get::<_, String>(0))?
                    .collect::<Result<Vec<_>, _>>()
            }) else {
                return cli_err("failed to list repos");
            };
            rows
        }
    };
    if llm {
        if repos.is_empty() {
            eprintln!("No repos indexed; run 'cairn build' first.");
            return ExitCode::FAILURE;
        }
        let mut plans = Vec::new();
        for r in &repos {
            match tools_wiki::build_page_plan(&conn, r, pages.max(0) as usize) {
                Ok(plan) => plans.push((r.clone(), plan)),
                Err(PipelineError::Planner(msg)) => {
                    eprintln!("Cannot plan wiki pages: {msg}");
                    return ExitCode::FAILURE;
                }
                Err(PipelineError::Manifest(msg)) => return manifest_error_exit(&msg),
                Err(PipelineError::Write) => {
                    return manifest_error_exit(WRITE_FAILED_MSG);
                }
            }
        }
        if dry_run {
            for (r, plan) in &plans {
                println!("--- {r} (dry-run; nothing queued) ---");
                for page in plan {
                    println!(
                        "  {}: {} [module {}]",
                        page.page_id, page.title, page.module
                    );
                }
            }
            println!(
                "Dry-run: planned {} wiki page(s); queued nothing.",
                plans.iter().map(|(_, p)| p.len()).sum::<usize>()
            );
            return ExitCode::SUCCESS;
        }
        let live_ids = tools_wiki::live_generation_task_ids(&bundle);
        let mut queued_total = 0usize;
        let mut skipped_total = 0usize;
        let mut adopted_total = 0usize;
        let mut catalog_total = 0usize;
        for r in &repos {
            let result = match tools_wiki::run_wiki_generate(
                &conn,
                &bundle,
                r,
                pages.max(0) as usize,
                force,
                diagrams,
                refine_catalog,
            ) {
                Ok(result) => result,
                Err(PipelineError::Planner(msg)) => {
                    eprintln!("Cannot plan wiki pages: {msg}");
                    return ExitCode::FAILURE;
                }
                Err(PipelineError::Manifest(msg)) => return manifest_error_exit(&msg),
                Err(PipelineError::Write) => return manifest_error_exit(WRITE_FAILED_MSG),
            };
            if let Some(catalog_id) = &result.catalog_task_id {
                catalog_total += 1;
                if result.catalog_pending {
                    println!(
                        "wiki-catalog task {catalog_id} still pending ({r}); the page tasks queue once it completes:"
                    );
                } else {
                    println!(
                        "Queued wiki-catalog task {catalog_id}: refine the wiki outline ({r})"
                    );
                    println!("Any agent with the cairn skill can process it:");
                }
                println!("  cairn task show {catalog_id}        # view the task + facts");
                println!("  cairn task claim {catalog_id}       # claim it");
                println!(
                    "  cairn task complete {catalog_id} --result-file <path>   # submit result"
                );
                continue;
            }
            let queued_ids: std::collections::HashSet<&String> =
                result.queued_task_ids.iter().collect();
            let rows = match tools_wiki::load_manifest(&bundle) {
                Ok(manifest) => manifest.get("pages").cloned().unwrap_or(json!({})),
                Err(PipelineError::Manifest(msg)) => return manifest_error_exit(&msg),
                Err(_) => return manifest_error_exit(WRITE_FAILED_MSG),
            };
            for page in &result.plan {
                let key = format!("{}/{}", r, page.page_id);
                let task_id = rows
                    .get(&key)
                    .and_then(|row| row.get("task_id"))
                    .and_then(Value::as_str)
                    .map(str::to_string);
                if task_id.as_ref().is_some_and(|id| queued_ids.contains(id)) {
                    queued_total += 1;
                    println!(
                        "Queued wiki-page task {}: {} ({r})",
                        task_id.as_deref().unwrap_or_default(),
                        page.title
                    );
                } else if task_id.as_ref().is_some_and(|id| live_ids.contains(id))
                    && tools_wiki::read_page_concept(&bundle, r, &page.page_id).is_none()
                {
                    adopted_total += 1;
                    println!(
                        "Adopted live wiki-page task {}: {} ({r})",
                        task_id.as_deref().unwrap_or_default(),
                        page.title
                    );
                } else {
                    skipped_total += 1;
                    println!("Up to date, skipped: {} ({r})", page.title);
                }
            }
        }
        if catalog_total > 0 {
            println!(
                "Re-run 'cairn wiki generate --llm --refine-catalog' after the catalog task completes to queue the page tasks."
            );
            return ExitCode::SUCCESS;
        }
        let mut summary = format!("Queued {queued_total} new wiki-page task(s); ");
        if adopted_total > 0 {
            summary.push_str(&format!("{adopted_total} already in flight; "));
        }
        summary.push_str(&format!("{skipped_total} page(s) already up to date. "));
        println!("{summary}Any agent with the cairn skill can process them:");
        println!("  cairn task list --kind wiki-page --status pending");
        println!("  cairn task claim <id> && cairn task complete <id> --result-file <path>");
        return ExitCode::SUCCESS;
    }
    let mut total = 0usize;
    let mut seen = 0usize;
    for r in &repos {
        let reports =
            match tools_wiki::generate_wiki_with_critic(&conn, &bundle, r, &paths.workspace) {
                Ok(reports) => reports,
                Err(e) => return cli_err(&e.0),
            };
        for report in reports {
            seen += 1;
            let result = &report.critic;
            let concept = &report.concept;
            if dry_run {
                println!("--- {} (dry-run; not written) ---", concept.concept_id);
                println!(
                    "  passed: {}  quality: {:.2}  errors: {}  warnings: {}",
                    py_bool(result.passed),
                    result.quality_score,
                    result.errors.len(),
                    result.warnings.len()
                );
                for e in &result.errors {
                    println!("    ERROR: {e}");
                }
                for w in &result.warnings {
                    println!("    warn: {w}");
                }
                continue;
            }
            let mut concept = concept.clone();
            if show_rejections || !result.passed {
                println!(
                    "  generated wiki: {} (quality={:.2}, errors={}, warnings={})",
                    concept.concept_id,
                    result.quality_score,
                    result.errors.len(),
                    result.warnings.len()
                );
                if !result.passed {
                    for e in &result.errors {
                        println!("    ERROR: {e}");
                    }
                    for w in &result.warnings {
                        println!("    warn: {w}");
                    }
                }
                if let Err(e) = bundle.write_concept(&mut concept) {
                    return cli_err(&e.0);
                }
                total += 1;
            } else {
                if let Err(e) = bundle.write_concept(&mut concept) {
                    return cli_err(&e.0);
                }
                total += 1;
                println!("  generated wiki: {}", concept.concept_id);
            }
        }
    }
    if dry_run {
        println!("Dry-run: examined {seen} wiki concept(s); wrote nothing.");
    } else {
        println!("Generated {total} wiki concepts.");
    }
    ExitCode::SUCCESS
}

fn wiki_status(repo: Option<&str>, knowledge: Option<&str>) -> ExitCode {
    let paths = resolve_store(None);
    let knowledge_path = knowledge.map_or_else(|| paths.knowledge.clone(), PathBuf::from);
    let bundle = resolve_bundle(&knowledge_path);
    let manifest = match tools_wiki::load_manifest(&bundle) {
        Ok(manifest) => manifest,
        Err(PipelineError::Manifest(msg)) => return manifest_error_exit(&msg),
        Err(_) => return manifest_error_exit(WRITE_FAILED_MSG),
    };
    let pages = manifest.get("pages").cloned().unwrap_or(json!({}));
    if pages.as_object().is_none_or(|m| m.is_empty()) {
        println!("No wiki pages planned; run 'cairn wiki generate --llm' first.");
        return ExitCode::SUCCESS;
    }
    let chains = tools_wiki::page_chains(&bundle);
    let conn = open_db_read_only(&paths.db).ok();
    let mut counts = std::collections::BTreeMap::<String, usize>::new();
    let mut staleness_counts = std::collections::BTreeMap::<&str, usize>::new();
    let mut heads: std::collections::HashMap<String, Option<String>> =
        std::collections::HashMap::new();
    let mut shown = 0usize;
    let mut keys: Vec<&String> = pages
        .as_object()
        .map(|m| m.keys().collect())
        .unwrap_or_default();
    keys.sort();
    for key in keys {
        let (page_repo, page_id) = tools_wiki::split_page_key(key);
        if repo.is_some_and(|r| page_repo != r) {
            continue;
        }
        let row = &pages[key];
        let chain = chains.get(key).map(Vec::as_slice).unwrap_or(&[]);
        let state = tools_wiki::derived_state(&bundle, &page_repo, &page_id, chain);
        let head = match heads.get(&page_repo) {
            Some(head) => head.clone(),
            None => {
                let head = tasks::repo_head(conn.as_ref(), Some(&paths.workspace), &page_repo);
                heads.insert(page_repo.clone(), head.clone());
                head
            }
        };
        let page_staleness = tools_wiki::staleness(
            tools_wiki::recorded_sha(&bundle, &page_repo, &page_id).as_deref(),
            head.as_deref(),
        );
        shown += 1;
        *counts.entry(state.clone()).or_default() += 1;
        *staleness_counts.entry(page_staleness).or_default() += 1;
        println!(
            "  {:<36} {:<12} {:<8} queue_attempts={}",
            key,
            state,
            page_staleness,
            row.get("queue_attempts")
                .and_then(Value::as_i64)
                .unwrap_or(0)
        );
    }
    let totals = tools_wiki::DERIVED_STATES
        .iter()
        .map(|state| format!("{state}={}", counts.get(*state).copied().unwrap_or(0)))
        .collect::<Vec<_>>()
        .join("  ");
    let freshness = ["fresh", "stale", "unknown"]
        .iter()
        .map(|s| format!("{s}={}", staleness_counts.get(s).copied().unwrap_or(0)))
        .collect::<Vec<_>>()
        .join("  ");
    println!("Wiki pages: {shown}  {totals}  {freshness}");
    ExitCode::SUCCESS
}

fn wiki_retry(repo: Option<&str>, knowledge: Option<&str>) -> ExitCode {
    let knowledge_path = knowledge.map_or_else(|| resolve_store(None).knowledge, PathBuf::from);
    let bundle = resolve_bundle(&knowledge_path);
    let outcome = match tools_wiki::retry_failed_pages(&bundle, repo) {
        Ok(outcome) => outcome,
        Err(PipelineError::Manifest(msg)) => return manifest_error_exit(&msg),
        Err(PipelineError::Planner(msg)) => return manifest_error_exit(&msg),
        Err(PipelineError::Write) => return manifest_error_exit(WRITE_FAILED_MSG),
    };
    if outcome.no_pages {
        println!("No wiki pages planned; run 'cairn wiki generate --llm' first.");
        return ExitCode::SUCCESS;
    }
    if outcome.requeued.is_empty() {
        println!("Nothing to retry: no failed wiki pages.");
        return ExitCode::SUCCESS;
    }
    for page in &outcome.requeued {
        println!(
            "Re-queued wiki-page task {}: {} (attempt {})",
            page.task_id, page.key, page.attempt
        );
    }
    if outcome.write_failed {
        eprintln!(
            "Failed to write the wiki manifest; the {} re-queued task(s) above are already live in the task queue — inspect them with 'cairn task list --kind wiki-page --status pending'.",
            outcome.requeued.len()
        );
        return ExitCode::FAILURE;
    }
    println!(
        "Re-queued {} failed wiki page(s). Any agent with the cairn skill can process them:",
        outcome.requeued.len()
    );
    println!("  cairn task list --kind wiki-page --status pending");
    println!("  cairn task claim <id> && cairn task complete <id> --result-file <path>");
    ExitCode::SUCCESS
}

/// Python non-strict `Path.resolve()`.
fn resolve_lenient(path: &Path) -> PathBuf {
    if let Ok(c) = path.canonicalize() {
        return c;
    }
    let mut ancestor = path.parent();
    while let Some(a) = ancestor {
        if let Ok(base) = a.canonicalize() {
            let mut resolved = base;
            if let Ok(tail) = path.strip_prefix(a) {
                resolved.extend(tail.components());
            }
            return resolved;
        }
        ancestor = a.parent();
    }
    path.to_path_buf()
}

fn wiki_export(out_dir: &str, force: bool, knowledge: Option<&str>) -> ExitCode {
    let out_dir = Path::new(out_dir);
    if out_dir.exists() && !out_dir.is_dir() {
        eprintln!("Export target {} is not a directory.", out_dir.display());
        return ExitCode::FAILURE;
    }
    if out_dir.is_dir()
        && out_dir
            .read_dir()
            .is_ok_and(|mut entries| entries.next().is_some())
        && !force
    {
        eprintln!(
            "Refusing to export into non-empty directory {}; pass --force to overwrite.",
            out_dir.display()
        );
        return ExitCode::FAILURE;
    }
    let knowledge_path = knowledge.map_or_else(|| resolve_store(None).knowledge, PathBuf::from);
    let bundle = resolve_bundle(&knowledge_path);
    let manifest = match tools_wiki::load_manifest(&bundle) {
        Ok(manifest) => manifest,
        Err(PipelineError::Manifest(msg)) => return manifest_error_exit(&msg),
        Err(_) => return manifest_error_exit(WRITE_FAILED_MSG),
    };
    let out_root = resolve_lenient(out_dir);
    let mut exported = 0usize;
    let pages = manifest.get("pages").cloned().unwrap_or(json!({}));
    let mut keys: Vec<&String> = pages
        .as_object()
        .map(|m| m.keys().collect())
        .unwrap_or_default();
    keys.sort();
    for key in keys {
        let (page_repo, page_id) = tools_wiki::split_page_key(key);
        let Some(concept) = tools_wiki::read_page_concept(&bundle, &page_repo, &page_id) else {
            continue;
        };
        let target = resolve_lenient(&out_dir.join(&page_repo).join(format!("{page_id}.md")));
        if !target.starts_with(&out_root) {
            eprintln!("Skipped {key}: page key escapes the export directory.");
            continue;
        }
        if let Err(e) = concept.to_file(&target) {
            return cli_err(&format!("concept write failed: {e}"));
        }
        exported += 1;
    }
    println!("Exported {exported} page(s) to {}", out_dir.display());
    ExitCode::SUCCESS
}

fn wiki_enrich(
    page_id: Option<&str>,
    repo: Option<&str>,
    enrich_all: bool,
    knowledge: Option<&str>,
) -> ExitCode {
    if page_id.is_some() && enrich_all {
        eprintln!("Enrich one PAGE_ID or --all, not both.");
        return ExitCode::FAILURE;
    }
    if page_id.is_none() && !enrich_all {
        eprintln!("Enrich one PAGE_ID or --all.");
        return ExitCode::FAILURE;
    }
    let knowledge_path = knowledge.map_or_else(|| resolve_store(None).knowledge, PathBuf::from);
    let bundle = resolve_bundle(&knowledge_path);
    if let Err(PipelineError::Manifest(msg)) = tools_wiki::load_manifest(&bundle) {
        return manifest_error_exit(&msg);
    }
    let queued = match tools_wiki::queue_enrich_tasks(&bundle, repo, page_id) {
        Ok(queued) => queued,
        Err(e) => return cli_err(&e.0),
    };
    let scope = repo.map_or_else(String::new, |r| format!(" in repo '{r}'"));
    if queued.is_empty() && !enrich_all {
        eprintln!(
            "Cannot enrich '{}': no wiki page to enrich for it{scope}.",
            page_id.unwrap_or_default()
        );
        return ExitCode::FAILURE;
    }
    for task in &queued {
        println!(
            "Queued wiki-page-enrich task {}: {} ({})",
            task.task_id, task.resource, task.repo
        );
    }
    if queued.is_empty() {
        println!("Nothing to enrich: no wiki pages to enrich{scope}.");
        return ExitCode::SUCCESS;
    }
    println!(
        "Queued {} wiki-page-enrich task(s). Any agent with the cairn skill can process them:",
        queued.len()
    );
    println!("  cairn task list --kind wiki-page-enrich --status pending");
    println!("  cairn task claim <id> && cairn task complete <id> --result-file <path>");
    ExitCode::SUCCESS
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
    fn workflow_sync_requires_a_ref_or_all() {
        let code = run(KnowledgeCommand::Knowledge {
            command: KnowledgeGroup::Workflow {
                command: WorkflowGroup::Sync {
                    reference: None,
                    all: false,
                    dry_run: false,
                    max_steps: 20,
                    db: None,
                    knowledge: None,
                },
            },
        });
        assert_eq!(code, ExitCode::FAILURE);
    }

    #[test]
    fn py_title_and_word_match_track_python() {
        assert_eq!(
            tools_km::py_title("golden-capture_rule"),
            "Golden-Capture_Rule"
        );
        assert_eq!(tools_km::py_title("release notes v2"), "Release Notes V2");
        assert_eq!(tools_km::py_title("it's a title"), "It'S A Title");
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

    #[test]
    fn validate_paths_mark_closes_the_stale_memory_interval() {
        let env = crate::test_env::EnvScope::new("vp-mark");
        let home = env.dir().to_path_buf();
        let knowledge = home.join(".knowledge");
        let db = home.join("store.kg");
        // SAFETY: EnvScope holds ENV_LOCK and restores every pinned var on drop.
        unsafe {
            std::env::set_var("CAIRN_KNOWLEDGE", &knowledge);
            std::env::set_var("CAIRN_DB", &db);
        }
        let memory = knowledge.join("memory/tribal");
        std::fs::create_dir_all(&memory).expect("memory dir");
        std::fs::write(
            memory.join("dead-ref.md"),
            "---\ntype: Memory\ntitle: dead ref\ntags: []\n---\n\nCites `NoSuchSymbol`.\n",
        )
        .expect("memory file");
        {
            let conn = cairn_store::conn::open_writable(&db).expect("db");
            cairn_store::schema::apply_schema(&conn).expect("schema");
        }

        let code = validate_paths_cmd(None, None, true);
        assert_eq!(code, ExitCode::FAILURE, "one stale concept -> exit 1");

        let marked = std::fs::read_to_string(memory.join("dead-ref.md")).expect("reread");
        assert!(
            marked.contains("stale: true"),
            "stale flag missing:\n{marked}"
        );
        assert!(
            marked.contains("valid_until:"),
            "marking must close the interval with valid_until=now:\n{marked}"
        );
        let conn = rusqlite::Connection::open(&db).expect("reopen");
        let valid_until: Option<String> = conn
            .query_row(
                "SELECT valid_until FROM memory_validity WHERE concept_id = 'memory/tribal/dead-ref'",
                [],
                |r| r.get(0),
            )
            .expect("validity row");
        assert!(
            valid_until.is_some(),
            "memory_validity.valid_until must not be NULL"
        );
    }
}
