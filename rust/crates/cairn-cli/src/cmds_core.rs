//! Core commands: init/build/update/import-scip/stats/checkpoint/config plus
//! the graph-read surface (def/callers/callees/search/impact/deps/tree/path/
//! taint/federated-search/pack).

use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::time::Instant;

use clap::Subcommand;
use rusqlite::Connection;
use serde_json::{Map, Value};

use crate::cli_rows;
use crate::display;
use crate::pack;
use crate::py_json;
use crate::rail::{self, Rail};
use crate::store_paths;

const TRAVERSAL_MAX_DEPTH: i64 = 100;
const CLOSURE_MAX_DEPTH: i64 = cairn_query::graph::CLOSURE_MAX_DEPTH;
const DEFAULT_PATH_LIMIT: i64 = 50;

#[derive(Subcommand)]
pub enum CoreCommand {
    /// Register this workspace with cairn's central store.
    Init(InitArgs),
    /// Show resolved store paths for this workspace (or all registered ones).
    Config(ConfigArgs),
    /// Build (or rebuild) the code graph.
    Build(BuildArgs),
    /// Import a SCIP index into an already-built graph as an edges-only overlay.
    #[command(name = "import-scip")]
    ImportScip(ImportScipArgs),
    /// Show graph statistics.
    Stats(StatsArgs),
    /// Checkpoint the graph DB's WAL into the main file (TRUNCATE).
    Checkpoint(CheckpointArgs),
    /// Incremental graph update from git diff (or a single changed file).
    Update(UpdateArgs),
    /// Find where a SYMBOL is defined.
    #[command(name = "def")]
    Def(DefArgs),
    /// Find callers of SYMBOL; precise by default, --fuzzy adds noisier name-matched sites.
    Callers(CallersArgs),
    /// Search symbols by PATTERN (supports * wildcards).
    Search(SearchArgs),
    /// Find what a SYMBOL calls.
    Callees(CalleesArgs),
    /// Recursive impact analysis: what breaks if SYMBOL changes.
    Impact(ImpactArgs),
    /// Cross-repo dependencies for REPO.
    Deps(DepsArgs),
    /// Search every registered store as one merged ranking.
    #[command(name = "federated-search")]
    FederatedSearch(FederatedSearchArgs),
    /// Semantic (concept) search over symbols.
    Semantic {
        /// Query text.
        query: String,
    },
    /// Build the semantic embedding index over the symbol graph.
    Embed,
    /// Search indexed files and group matches by enclosing symbol.
    Grep {
        /// Pattern to search for.
        pattern: String,
    },
    /// Show directory clusters, hubs, and workspace hotspots.
    Map,
    /// Directory/package structure of REPO with symbol counts.
    Tree(TreeArgs),
    /// Build a deterministic, token-budgeted context pack for a task.
    Pack(pack::PackArgs),
    /// Print shortest structural paths between two symbols.
    #[command(name = "path")]
    Path(PathArgs),
    /// Natural-language question across all layers.
    Ask {
        /// Question text.
        question: String,
    },
    /// Load relevant context (compass + memory + wiki) for a file.
    Context {
        /// File to load context for.
        file_path: String,
    },
    /// Trace inter-procedural taint paths from a source to a sink.
    Taint(TaintArgs),
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

/// Run a core command; unported surfaces keep the Phase 3a stub diagnostic.
pub fn run(command: CoreCommand) -> ExitCode {
    match command {
        CoreCommand::Init(args) => init(args),
        CoreCommand::Config(args) => config(args),
        CoreCommand::Build(args) => build(args),
        CoreCommand::ImportScip(args) => import_scip(args),
        CoreCommand::Stats(args) => stats(args),
        CoreCommand::Checkpoint(args) => checkpoint(args),
        CoreCommand::Update(args) => update(args),
        CoreCommand::Def(args) => find_def(args),
        CoreCommand::Callers(args) => callers(args),
        CoreCommand::Search(args) => search(args),
        CoreCommand::Callees(args) => callees(args),
        CoreCommand::Impact(args) => impact(args),
        CoreCommand::Deps(args) => deps(args),
        CoreCommand::FederatedSearch(args) => federated_search(args),
        CoreCommand::Tree(args) => tree(args),
        CoreCommand::Pack(args) => pack::run(args),
        CoreCommand::Path(args) => path(args),
        CoreCommand::Taint(args) => taint(args),
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
            Self::Init { .. } => "init",
            Self::Config { .. } => "config",
            Self::Build { .. } => "build",
            Self::ImportScip { .. } => "import-scip",
            Self::Stats { .. } => "stats",
            Self::Checkpoint { .. } => "checkpoint",
            Self::Update { .. } => "update",
            Self::Def { .. } => "def",
            Self::Callers { .. } => "callers",
            Self::Search { .. } => "search",
            Self::Callees { .. } => "callees",
            Self::Impact { .. } => "impact",
            Self::Deps { .. } => "deps",
            Self::FederatedSearch { .. } => "federated-search",
            Self::Semantic { .. } => "semantic",
            Self::Embed => "embed",
            Self::Grep { .. } => "grep",
            Self::Map => "map",
            Self::Tree { .. } => "tree",
            Self::Pack(_) => "pack",
            Self::Path { .. } => "path",
            Self::Ask { .. } => "ask",
            Self::Context { .. } => "context",
            Self::Taint { .. } => "taint",
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

// --- shared formatting -------------------------------------------------------

/// `f"{n:,}"` — comma-grouped thousands.
pub fn fmt_int(n: i64) -> String {
    let raw = n.abs().to_string();
    let mut grouped = String::new();
    for (i, ch) in raw.chars().enumerate() {
        if i > 0 && (raw.len() - i).is_multiple_of(3) {
            grouped.push(',');
        }
        grouped.push(ch);
    }
    if n < 0 {
        format!("-{grouped}")
    } else {
        grouped
    }
}

/// `f"{share:.0%}"`.
fn fmt_pct(share: f64) -> String {
    format!("{:.0}%", share * 100.0)
}

/// `_shorten`: strip the workspace root for display.
fn shorten(path: &str) -> String {
    let ws = store_paths::resolve_workspace(None);
    let ws = ws.to_string_lossy().to_string();
    let prefix = format!("{ws}/");
    if path == ws {
        return path.to_string();
    }
    if let Some(rel) = path.strip_prefix(&prefix) {
        return rel.to_string();
    }
    path.to_string()
}

/// Click's UsageError shape: exit code 2 with an `Error:` line.
pub fn usage_error(message: &str) -> ExitCode {
    eprintln!("Error: {message}");
    ExitCode::from(2)
}

/// ClickException shape: exit code 1 with an `Error:` line.
pub fn command_error(message: &str) -> ExitCode {
    eprintln!("Error: {message}");
    ExitCode::FAILURE
}

/// The store DB path for the current context.
pub fn default_db_path() -> String {
    default_db()
}

/// Open the query store honoring CAIRN_READ_ONLY; refresh only when writable.
fn fresh_conn(db: &str, refresh: Option<bool>) -> Result<Connection, String> {
    let read_only = std::env::var("CAIRN_READ_ONLY")
        .map(|v| matches!(v.to_lowercase().as_str(), "1" | "true" | "yes"))
        .unwrap_or(false);
    let path = PathBuf::from(db);
    let mut conn = if read_only {
        cairn_store::conn::open_read_only(&path)
    } else {
        cairn_store::schema::open_initialized(&path)
    }
    .map_err(|e| e.to_string())?;
    let repair = if read_only { Some(false) } else { refresh };
    refresh_conn(&mut conn, db, repair)?;
    Ok(conn)
}

/// Probe indexed files and repair drift before a read (watcher.refresh_for_query).
fn refresh_conn(conn: &mut Connection, db: &str, repair: Option<bool>) -> Result<(), String> {
    let repair = repair.unwrap_or_else(|| {
        !std::env::var("CAIRN_NO_REFRESH")
            .map(|v| matches!(v.to_lowercase().as_str(), "1" | "true" | "yes"))
            .unwrap_or(false)
    });
    let workspace = conn_store_workspace(conn, db);
    let mut drifted = cairn_build::incremental::detect_changed(conn, &workspace);
    drifted.sort();
    drifted.dedup();
    if drifted.is_empty() || !repair {
        banner_if_unrepaired(&drifted, false);
        return Ok(());
    }
    cairn_build::incremental::repair_changed(conn, &workspace, &drifted, true).map(|_| ())
}

fn banner_if_unrepaired(drifted: &[String], repaired: bool) {
    if drifted.is_empty() || repaired {
        return;
    }
    let shown: Vec<String> = drifted.iter().take(3).cloned().collect();
    let mut line = shown.join(", ");
    if drifted.len() > 3 {
        line.push_str(&format!(", +{} more", drifted.len() - 3));
    }
    eprintln!(
        "Stale graph: {} file(s) not refreshed: {line}",
        drifted.len()
    );
}

/// Registered workspace whose canonical store backs this db, else resolve_workspace.
fn conn_store_workspace(conn: &Connection, db: &str) -> String {
    let Ok(resolved) = std::fs::canonicalize(db) else {
        return store_paths::resolve_workspace(None)
            .to_string_lossy()
            .into_owned();
    };
    for (workspace, key) in store_paths::load_registry() {
        if let Some(home) = store_paths::cairn_home() {
            let store_db = home.join(&key).join(".kg");
            if std::fs::canonicalize(&store_db).is_ok_and(|p| p == resolved) {
                return workspace;
            }
        }
    }
    let _ = conn;
    store_paths::resolve_workspace(None)
        .to_string_lossy()
        .into_owned()
}

fn default_db() -> String {
    store_paths::resolve_store(None)
        .db
        .to_string_lossy()
        .into_owned()
}

/// Strip non-option db arg precedence: explicit --db wins, else the store.
fn db_arg(db: &Option<String>) -> String {
    db.clone().unwrap_or_else(default_db)
}

// --- cairn init --------------------------------------------------------------

/// init handler: register + legacy migration + first build.
fn init(args: crate::cmds_core::InitArgs) -> ExitCode {
    let ws = match &args.ws_arg {
        Some(ws) => store_paths::resolve_path(Path::new(ws)),
        None => store_paths::resolve_workspace(None),
    };
    let store = match store_paths::register_workspace(&ws) {
        Ok(store) => store,
        Err(e) => return command_error(&format!("could not register workspace: {e}")),
    };

    ensure_api_key();

    let mut rail = Rail::new("Initializing cairn");
    rail.step("Initialized in", Some(&ws.to_string_lossy()));
    rail.step("Store", Some(&store.home.to_string_lossy()));
    rail.detail(".kg", &store.db.to_string_lossy());
    rail.detail(".knowledge", &store.knowledge.to_string_lossy());

    let mut migrated: Vec<String> = Vec::new();
    let legacy = match &args.legacy_dir {
        Some(dir) => store_paths::resolve_path(Path::new(dir)),
        None => ws.join("cairn"),
    };
    let legacy_db = legacy.join(".kg");
    let legacy_kn = legacy.join(".knowledge");
    if legacy_db.exists() && !store.db.exists() {
        let size_kb = std::fs::metadata(&legacy_db)
            .map(|m| m.len() / 1024)
            .unwrap_or(0);
        if let Some(parent) = store.db.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        let migrate_tmp = store.db.with_extension("kg.migrate-tmp");
        match std::fs::copy(&legacy_db, &migrate_tmp) {
            Ok(_) => match std::fs::rename(&migrate_tmp, &store.db) {
                Ok(()) => migrated.push(format!(".kg ({size_kb} KB)")),
                Err(e) => {
                    let _ = std::fs::remove_file(&migrate_tmp);
                    rail.warn(&format!("legacy .kg migration failed: {e}"));
                }
            },
            Err(e) => {
                let _ = std::fs::remove_file(&migrate_tmp);
                rail.warn(&format!("legacy .kg migration failed: {e}"));
            }
        }
    }
    if legacy_kn.is_dir() {
        let mut copied = 0usize;
        collect_knowledge_copies(&legacy_kn, &legacy_kn, &store.knowledge, &mut copied);
        if copied > 0 {
            migrated.push(format!(".knowledge/ ({copied} files)"));
        }
    }
    if !migrated.is_empty() {
        rail.step(
            &format!("Migrated from {}", legacy.display()),
            Some(&migrated.join(", ")),
        );
    }

    if !args.no_build && !store.db.exists() {
        rail.start("Scanning files");
        let started = Instant::now();
        let db = store.db.to_string_lossy().into_owned();
        let ws_str = ws.to_string_lossy().into_owned();
        match cairn_build::builder::build_graph(&ws_str, &db, None, None) {
            Ok((summary, _timings)) => {
                rail.finish(&format!("{} found", fmt_int(summary.files as i64)));
                rail.start("Parsing code");
                rail.finish("done");
                rail.start("Resolving refs");
                rail.finish("done");
                rail.start("Persisting graph");
                rail.finish("done");
                if summary.files == 0 {
                    rail.warn("No source files found");
                } else {
                    rail.step(
                        &format!("Indexed {} files", fmt_int(summary.files as i64)),
                        None,
                    );
                    rail.stat(&format!(
                        "{} nodes, {} edges in {:.1}s",
                        fmt_int(summary.symbols as i64),
                        fmt_int(summary.edges as i64),
                        started.elapsed().as_secs_f64()
                    ));
                }
            }
            Err(message) => {
                rail.fail_close();
                return command_error(&message);
            }
        }
    } else if store.db.exists() {
        rail.warn("Graph already present. Use `cairn build` to rebuild.");
    }

    if args.import_docs {
        rail.warn("docs/ ingestion is not implemented in the Rust CLI yet; run the Python CLI for --import-docs");
    }
    if args.with_hooks {
        rail.warn("git hook installation is not implemented in the Rust CLI yet; run the Python CLI for --with-hooks");
    }

    rail.close("Done");
    display::dim("`cairn config` to verify; `cairn serve` for the MCP server.");
    ExitCode::SUCCESS
}

/// Recursively copy unseen knowledge files (skip .DS_Store), counting copies.
fn collect_knowledge_copies(root: &Path, dir: &Path, dest_root: &Path, copied: &mut usize) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    let mut entries: Vec<_> = entries.filter_map(Result::ok).collect();
    entries.sort_by_key(|e| e.file_name());
    for entry in entries {
        let path = entry.path();
        let name = entry.file_name().to_string_lossy().into_owned();
        if path.is_dir() {
            collect_knowledge_copies(root, &path, dest_root, copied);
        } else if name != ".DS_Store" {
            let rel = path.strip_prefix(root).unwrap_or(&path);
            let dst = dest_root.join(rel);
            if let Some(parent) = dst.parent() {
                let _ = std::fs::create_dir_all(parent);
            }
            if !dst.exists() && std::fs::copy(&path, &dst).is_ok() {
                *copied += 1;
            }
        }
    }
}

/// Create the persisted API key once (0600, never overwrite).
fn ensure_api_key() {
    let key_file = store_paths::current_cairn_home().join("mcp-api-key");
    if key_file.exists() {
        return;
    }
    if let Some(parent) = key_file.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let mut bytes = [0u8; 32];
    if std::fs::File::open("/dev/urandom")
        .and_then(|mut f| f.read_exact(&mut bytes))
        .is_err()
    {
        return;
    }
    const ALPHABET: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    let mut token = String::with_capacity(43);
    for chunk in bytes.chunks(3) {
        let b0 = chunk[0] as u32;
        let b1 = *chunk.get(1).unwrap_or(&0) as u32;
        let b2 = *chunk.get(2).unwrap_or(&0) as u32;
        let n = (b0 << 16) | (b1 << 8) | b2;
        let (chars, keep) = match chunk.len() {
            3 => (4, 4),
            2 => (3, 3),
            _ => (2, 2),
        };
        for i in 0..chars {
            let idx = ((n >> (18 - 6 * i)) & 0x3F) as usize;
            token.push(ALPHABET[idx] as char);
        }
        let _ = keep;
    }
    use std::io::Write as _;
    use std::os::unix::fs::OpenOptionsExt as _;
    if let Ok(mut file) = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&key_file)
    {
        let _ = writeln!(file, "{token}");
    }
}

// --- cairn config ------------------------------------------------------------

/// config handler: resolved store paths, registry, and the machine probes.
fn config(args: crate::cmds_core::ConfigArgs) -> ExitCode {
    if args.db_only {
        println!("{}", store_paths::resolve_store(None).db.display());
        return ExitCode::SUCCESS;
    }

    if args.as_json {
        let store = store_paths::resolve_store(None);
        let mut object = Map::new();
        object.insert(
            "cairn_home".into(),
            Value::from(
                store
                    .home
                    .parent()
                    .unwrap_or(&store.home)
                    .to_string_lossy()
                    .to_string(),
            ),
        );
        object.insert(
            "workspace".into(),
            Value::from(store.workspace.to_string_lossy().to_string()),
        );
        object.insert(
            "db".into(),
            Value::from(store.db.to_string_lossy().to_string()),
        );
        object.insert(
            "knowledge".into(),
            Value::from(store.knowledge.to_string_lossy().to_string()),
        );
        object.insert(
            "scip".into(),
            load_config(store.workspace.clone()).scip_json(),
        );
        println!("{}", py_json::dumps_compact(&Value::Object(object)));
        return ExitCode::SUCCESS;
    }

    if args.mcp_config {
        let mut entry = Map::new();
        entry.insert("type".into(), Value::from("http"));
        entry.insert(
            "url".into(),
            Value::from(format!(
                "http://{}:{}/mcp",
                cairn_daemon::lifecycle::DEFAULT_HOST,
                cairn_daemon::lifecycle::DEFAULT_PORT
            )),
        );
        let key = cairn_daemon::auth::resolve_api_key(None)
            .or_else(cairn_daemon::auth::persisted_api_key);
        if let Some(key) = key {
            let mut headers = Map::new();
            headers.insert("Authorization".into(), Value::from(format!("Bearer {key}")));
            entry.insert("headers".into(), Value::Object(headers));
        }
        let mut server = Map::new();
        server.insert("cairn".into(), Value::Object(entry));
        let mut snippet = Map::new();
        snippet.insert("mcpServers".into(), Value::Object(server));
        println!("{}", py_json::dumps(&Value::Object(snippet)));
        eprintln!("Run `cairn connect` to start or reuse the daemon and print its URL.");
        return ExitCode::SUCCESS;
    }

    if args.list_all {
        let reg = store_paths::load_registry();
        if reg.is_empty() {
            println!("No workspaces registered. Run `cairn init` in a workspace root.");
            let registry = store_paths::cairn_home()
                .map(|home| home.join("workspaces.json"))
                .unwrap_or_default();
            println!("Registry would live at: {}", registry.display());
            return ExitCode::SUCCESS;
        }
        let registry = store_paths::cairn_home()
            .map(|home| home.join("workspaces.json"))
            .unwrap_or_default();
        println!("Registry: {}", registry.display());
        let cwd = store_paths::resolve_workspace(None)
            .to_string_lossy()
            .to_string();
        for (ws_path, key) in &reg {
            let mark = if *ws_path == cwd {
                " <- cwd context"
            } else {
                ""
            };
            println!("  {key}  {ws_path}{mark}");
        }
        return ExitCode::SUCCESS;
    }

    let store = store_paths::resolve_store(None);
    let ws = store.workspace.to_string_lossy().into_owned();
    let home = store.home.to_string_lossy().into_owned();
    let db = store.db.to_string_lossy().into_owned();
    let knowledge = store.knowledge.to_string_lossy().into_owned();
    let db_state = if store.db.exists() {
        " (exists)"
    } else {
        " (missing)"
    };
    println!("workspace:  {ws}");
    println!("store:      {home}");
    println!("  .kg:         {db}{db_state}");
    println!("  .knowledge:  {knowledge}");
    println!(
        "home:       {}  (override with CAIRN_HOME)",
        store.home.parent().unwrap_or(&store.home).display()
    );

    // Embedding backend line: real backend vs the hash fallback.
    let env = cairn_query::tools_search::semantic_env(None);
    match env.backend {
        cairn_query::tools_search::EffectiveBackend::Unavailable => {
            println!("embed:      unusable backend (embedding backend unavailable)");
        }
        backend => {
            if env.hash_fallback {
                println!(
                    "embed:      {}  ⚠ fallback (sentence-transformers not installed)",
                    env.model
                );
                println!(
                    "            for real embeddings: uv tool install 'cairn-intel[semantic]' --force"
                );
            } else if backend == cairn_query::tools_search::EffectiveBackend::Hash {
                println!("embed:      {}  (CAIRN_EMBED_BACKEND=hash)", env.model);
            } else {
                println!("embed:      {}  (backend: server)", env.model);
            }
        }
    }

    let cfg = load_config(store.workspace.clone());
    let env_raw = std::env::var("CAIRN_REPO_NAMESPACES").ok();
    let env_usable = env_raw.as_deref().is_some_and(|raw| {
        serde_json::from_str::<Value>(raw)
            .ok()
            .and_then(|v| {
                v.as_object().map(|o| {
                    o.iter().any(|(k, v)| {
                        !k.trim().is_empty() && v.as_str().is_some_and(|s| !s.trim().is_empty())
                    })
                })
            })
            .unwrap_or(false)
    });
    let source = if env_usable {
        "CAIRN_REPO_NAMESPACES".to_string()
    } else if cfg.repo_namespaces.is_some() {
        store
            .workspace
            .join("cairn.json")
            .to_string_lossy()
            .into_owned()
    } else {
        "built-in default".to_string()
    };
    let namespaces = cairn_query::graph::resolve_namespaces(&store.workspace, env_raw.as_deref());
    println!("repo_namespaces ({source}):");
    if namespaces.is_empty() {
        println!("  (empty — cross_repo_deps will find no cross-repo links)");
    } else {
        let mut sorted = namespaces.clone();
        sorted.sort();
        for (ns, owner) in sorted {
            println!("  {ns} -> {owner}");
        }
    }

    let scip_indexes = cfg.scip_indexes();
    println!("scip indexes:");
    if scip_indexes.is_empty() {
        println!("  (none configured — SCIP edges stay off)");
    } else {
        let mut sorted = scip_indexes.clone();
        sorted.sort();
        for (lang, index_path) in sorted {
            println!("  {lang} -> {index_path}");
        }
    }

    println!();
    println!("MCP config for an agent (URL + key, needs the daemon):");
    println!("  cairn config --mcp-config");
    ExitCode::SUCCESS
}

/// Parsed `cairn.json` (exclude/repo_namespaces/scip/taint) for CLI consumers.
pub struct WorkspaceConfig {
    pub repo_namespaces: Option<Vec<(String, String)>>,
    scip_indexes: Vec<(String, String)>,
    pub taint_sources: Vec<(String, Vec<String>)>,
    pub taint_sinks: Vec<(String, Vec<String>)>,
}

impl WorkspaceConfig {
    /// The `scip` JSON fragment for `config --json` ({} or {"indexes": {...}}).
    fn scip_json(&self) -> Value {
        if self.scip_indexes.is_empty() {
            return serde_json::json!({});
        }
        let mut indexes = Map::new();
        for (lang, path) in &self.scip_indexes {
            indexes.insert(lang.clone(), Value::from(path.clone()));
        }
        serde_json::json!({ "indexes": Value::Object(indexes) })
    }

    fn scip_indexes(&self) -> Vec<(String, String)> {
        self.scip_indexes.clone()
    }
}

/// Load `cairn.json` from the workspace root; defaults when absent or invalid.
pub fn load_config(workspace: PathBuf) -> WorkspaceConfig {
    let path = workspace.join("cairn.json");
    let empty = WorkspaceConfig {
        repo_namespaces: None,
        scip_indexes: vec![],
        taint_sources: vec![],
        taint_sinks: vec![],
    };
    let Ok(text) = std::fs::read_to_string(&path) else {
        return empty;
    };
    let Ok(parsed) = serde_json::from_str::<Value>(&text) else {
        eprintln!("warning: {}: invalid JSON; ignoring config", path.display());
        return empty;
    };
    let Some(object) = parsed.as_object() else {
        eprintln!(
            "warning: {}: top-level JSON must be an object; ignoring config",
            path.display()
        );
        return empty;
    };
    let string_map = |key: &str| -> Vec<(String, String)> {
        object
            .get(key)
            .and_then(Value::as_object)
            .map(|map| {
                map.iter()
                    .filter_map(|(k, v)| {
                        let v = v.as_str()?;
                        if k.trim().is_empty() || v.trim().is_empty() {
                            None
                        } else {
                            Some((k.trim().to_string(), v.trim().to_string()))
                        }
                    })
                    .collect()
            })
            .unwrap_or_default()
    };
    let name_table = |key: &str| -> Vec<(String, Vec<String>)> {
        object
            .get("taint")
            .and_then(|taint| taint.get(key))
            .and_then(Value::as_object)
            .map(|map| {
                map.iter()
                    .filter_map(|(category, names)| {
                        let list: Vec<String> = names
                            .as_array()?
                            .iter()
                            .filter_map(|n| n.as_str().map(String::from))
                            .collect();
                        if category.trim().is_empty() {
                            None
                        } else {
                            Some((category.trim().to_string(), list))
                        }
                    })
                    .collect()
            })
            .unwrap_or_default()
    };
    let repo_namespaces = object.get("repo_namespaces").and_then(|_| {
        let map = string_map("repo_namespaces");
        if map.is_empty() { None } else { Some(map) }
    });
    let scip_indexes = string_map("scip")
        .into_iter()
        .filter(|(k, _)| k == "indexes")
        .flat_map(|(_, _)| string_map_nested(object, "scip", "indexes"))
        .collect::<Vec<_>>();
    WorkspaceConfig {
        repo_namespaces,
        scip_indexes,
        taint_sources: name_table("sources"),
        taint_sinks: name_table("sinks"),
    }
}

fn string_map_nested(
    object: &serde_json::Map<String, Value>,
    outer: &str,
    inner: &str,
) -> Vec<(String, String)> {
    object
        .get(outer)
        .and_then(|v| v.get(inner))
        .and_then(Value::as_object)
        .map(|map| {
            map.iter()
                .filter_map(|(k, v)| {
                    let v = v.as_str()?;
                    Some((k.clone(), v.to_string()))
                })
                .collect()
        })
        .unwrap_or_default()
}

// --- cairn build -------------------------------------------------------------

/// build handler: full rebuild into the store (staging swaps atomically).
fn build(args: crate::cmds_core::BuildArgs) -> ExitCode {
    if args.staging && args.repo.is_some() {
        return usage_error(
            "--staging cannot be combined with --repo: a staged single-repo \
             build swaps in a temp DB containing only that repo, silently \
             deleting every other repo's graph. Use `cairn build --repo X` \
             (updates the live DB in place) or `cairn build --staging` (full \
             rebuild including every repo).",
        );
    }
    let db = args.db.clone().unwrap_or_else(default_db);
    let workspace = args.workspace.clone().unwrap_or_else(|| ".".to_string());
    let target_db = if args.staging {
        format!("{db}.tmp")
    } else {
        db.clone()
    };
    let started = Instant::now();

    let mut rail = Rail::new("Building graph");
    rail.start("Scanning files");
    match cairn_build::builder::build_graph(&workspace, &target_db, args.repo.as_deref(), None) {
        Err(message) => {
            if args.staging {
                let _ = std::fs::remove_file(&target_db);
            }
            rail.fail_close();
            command_error(&message)
        }
        Ok((summary, _timings)) => {
            if args.staging && summary.files == 0 {
                let _ = std::fs::remove_file(&target_db);
                rail.fail_close();
                return command_error(&format!(
                    "no source files found under {workspace} — refusing to swap \
                     an empty build over {db}"
                ));
            }
            rail.tick(&format!("{} found", fmt_int(summary.files as i64)));
            rail.finish(&format!("{} found", fmt_int(summary.files as i64)));
            rail.start("Parsing code");
            rail.finish("done");
            rail.start("Resolving refs");
            rail.finish("done");
            rail.start("Persisting graph");
            rail.finish("done");

            if args.with_closure {
                rail.start("Transitive closure");
                match Connection::open(&target_db)
                    .map_err(|e| e.to_string())
                    .and_then(|mut conn| {
                        cairn_build::closure::build_transitive_closure(&mut conn, 4)
                            .map(|outcome| outcome.rows as i64)
                    }) {
                    Ok(rows) => rail.finish(&format!("{} edges", fmt_int(rows))),
                    Err(message) => {
                        rail.fail_close();
                        return command_error(&format!("closure build failed: {message}"));
                    }
                }
            }
            if let Ok(conn) = Connection::open(&target_db) {
                let _ = conn.execute_batch("PRAGMA wal_checkpoint(TRUNCATE);");
            }

            let mut kv_pairs: Vec<(String, String)> = vec![
                ("repos".into(), summary.repos.to_string()),
                ("files".into(), fmt_int(summary.files as i64)),
                ("symbols".into(), fmt_int(summary.symbols as i64)),
                ("edges".into(), fmt_int(summary.edges as i64)),
                ("imports".into(), fmt_int(summary.imports as i64)),
            ];
            if summary.skipped > 0 {
                kv_pairs.push(("skipped".into(), fmt_int(summary.skipped as i64)));
            }
            let resolution = &summary.resolution;
            let total_edges = i64::max(summary.edges as i64, 1);
            let exact = resolution.exact as i64;
            let mut subtitle_parts = vec![
                format!(
                    "edges resolved: {} exact ({}%)",
                    fmt_int(exact),
                    100 * exact / total_edges
                ),
                format!("{} ambiguous", fmt_int(resolution.ambiguous as i64)),
                format!("{} unresolved", fmt_int(resolution.unresolved as i64)),
            ];
            if args.staging {
                subtitle_parts.push(format!("atomic swap → {db}"));
            }
            let subtitle = subtitle_parts.join("  ·  ");
            rail.close("Done");
            rail::summary_panel(
                &format!("Built graph in {:.1}s", started.elapsed().as_secs_f64()),
                &kv_pairs
                    .iter()
                    .map(|(k, v)| (k.as_str(), v.clone()))
                    .collect::<Vec<_>>(),
                Some(&subtitle),
            );
            ExitCode::SUCCESS
        }
    }
}

// --- cairn import-scip -------------------------------------------------------

/// import-scip handler: edges-only overlay import.
fn import_scip(args: crate::cmds_core::ImportScipArgs) -> ExitCode {
    let db_path = args.db.clone().unwrap_or_else(|| {
        store_paths::resolve_store(None)
            .db
            .to_string_lossy()
            .into_owned()
    });
    if !Path::new(&db_path).exists() {
        return command_error(&format!(
            "no graph DB at {db_path} — run `cairn build` first"
        ));
    }
    let conn = match cairn_store::schema::open_initialized(Path::new(&db_path)) {
        Ok(conn) => conn,
        Err(e) => return command_error(&format!("cannot open {db_path}: {e}")),
    };
    let symbols: i64 = conn
        .query_row("SELECT COUNT(*) FROM symbols", [], |row| row.get(0))
        .unwrap_or(0);
    if symbols == 0 {
        return command_error(&format!(
            "graph DB at {db_path} holds no symbols — run `cairn build` first"
        ));
    }
    let workspace = args.workspace.clone().unwrap_or_else(|| ".".to_string());
    let record =
        match cairn_build::scip::importer::import_scip_file(&conn, &args.scip_file, &workspace) {
            Ok(record) => record,
            Err(message) => return command_error(&message),
        };
    if record.documents > 0 && record.matched_documents == 0 {
        return command_error(&format!(
            "none of {} documents in {} matched the graph — check that \
             --workspace/--db point at the workspace this index covers",
            record.documents, args.scip_file
        ));
    }
    drop(conn);
    if args.with_closure {
        let mut reopened = match Connection::open(&db_path) {
            Ok(reopened) => reopened,
            Err(e) => return command_error(&format!("closure rebuild failed: {e}")),
        };
        if let Err(message) = cairn_build::closure::build_transitive_closure(&mut reopened, 4) {
            return command_error(&format!("closure rebuild failed: {message}"));
        }
    }
    let skipped_note = if record.skipped_documents > 0 {
        format!(", {} skipped", record.skipped_documents)
    } else {
        String::new()
    };
    println!(
        "scip: {} edges ({} disagreements) matched {}/{} documents{} from {}",
        record.edges,
        record.join_anomalies,
        record.matched_documents,
        record.documents,
        skipped_note,
        args.scip_file
    );
    ExitCode::SUCCESS
}

// --- cairn stats -------------------------------------------------------------

/// stats handler: aggregate graph statistics.
fn stats(args: crate::cmds_core::StatsArgs) -> ExitCode {
    let db = db_arg(&args.db);
    let Ok(conn) = cairn_store::schema::open_initialized(Path::new(&db)) else {
        return command_error(&format!("cannot open {db}"));
    };
    let s = cli_rows::get_stats(&conn);
    display::kv("repos", &s.repos.to_string());
    display::kv("files", &fmt_int(s.files));
    display::kv("symbols", &fmt_int(s.symbols));
    display::kv(
        "edges",
        &format!(
            "{} ({} resolved)",
            fmt_int(s.edges),
            fmt_int(s.edges_resolved)
        ),
    );
    let (exact, ambiguous, unresolved) = s.resolution;
    display::kv(
        "resolution",
        &format!(
            "{} exact / {} ambiguous / {} unresolved ({} / {} of pool)",
            fmt_int(exact),
            fmt_int(ambiguous),
            fmt_int(unresolved),
            fmt_pct(s.exact_share),
            fmt_pct(s.ambiguous_share)
        ),
    );
    let scip_edges = s
        .edge_sources
        .iter()
        .find(|(src, _)| src == "scip")
        .map(|(_, n)| *n)
        .unwrap_or(0);
    if scip_edges > 0 {
        let tree_sitter = s
            .edge_sources
            .iter()
            .find(|(src, _)| src == "tree_sitter")
            .map(|(_, n)| *n)
            .unwrap_or(0);
        display::kv(
            "edge sources",
            &format!(
                "{} tree-sitter / {} scip",
                fmt_int(tree_sitter),
                fmt_int(scip_edges)
            ),
        );
        if !s.exact_share_by_language.is_empty() {
            let shares = s
                .exact_share_by_language
                .iter()
                .map(|(lang, share)| format!("{lang} {}", fmt_pct(*share)))
                .collect::<Vec<_>>()
                .join("  ");
            display::kv("exact share", &shares);
        }
    }
    display::kv("imports", &fmt_int(s.imports));
    if s.skipped_total > 0 {
        display::kv(
            "skipped",
            &format!("{} (not indexed)", fmt_int(s.skipped_total)),
        );
    }
    if !s.by_repo.is_empty() {
        rail::print_table(
            "By repo",
            ["repo", "count"],
            &s.by_repo
                .iter()
                .map(|(repo, count)| [repo.clone(), count.to_string()])
                .collect::<Vec<_>>(),
        );
    }
    if !s.by_kind.is_empty() {
        rail::print_table(
            "By kind",
            ["kind", "count"],
            &s.by_kind
                .iter()
                .map(|(kind, count)| [kind.clone(), count.to_string()])
                .collect::<Vec<_>>(),
        );
    }
    if !s.skipped_by_reason.is_empty() {
        rail::print_table(
            "Skipped by reason",
            ["reason", "count"],
            &s.skipped_by_reason
                .iter()
                .map(|(reason, count)| [reason.clone(), count.to_string()])
                .collect::<Vec<_>>(),
        );
    }
    ExitCode::SUCCESS
}

// --- cairn checkpoint --------------------------------------------------------

/// checkpoint handler: WAL TRUNCATE with before/after sizes.
fn checkpoint(args: crate::cmds_core::CheckpointArgs) -> ExitCode {
    let path = db_arg(&args.db);
    if !Path::new(&path).exists() {
        return command_error(&format!("no graph DB at {path} — run `cairn build` first"));
    }
    let conn = match Connection::open(&path) {
        Ok(conn) => conn,
        Err(e) => return command_error(&format!("cannot open {path}: {e}")),
    };
    if let Err(e) = conn.execute_batch("PRAGMA busy_timeout = 10000") {
        return command_error(&format!("checkpoint failed for {path}: {e}"));
    }
    let wal = PathBuf::from(format!("{path}-wal"));
    let wal_size = |wal: &Path| std::fs::metadata(wal).map(|m| m.len()).unwrap_or(0);
    let before = wal_size(&wal);
    let result: Result<(i64, i64, i64), _> =
        conn.query_row("PRAGMA wal_checkpoint(TRUNCATE)", [], |row| {
            Ok((row.get(0)?, row.get(1)?, row.get(2)?))
        });
    match result {
        Err(e) => command_error(&format!("checkpoint failed for {path}: {e}")),
        Ok((busy, log_frames, checkpointed)) => {
            drop(conn);
            let after = wal_size(&wal);
            display::kv(
                "checkpoint",
                &format!("busy={busy} log_frames={log_frames} checkpointed={checkpointed}"),
            );
            display::kv(
                "wal size",
                &format!(
                    "{} -> {}",
                    human_bytes(before as f64),
                    human_bytes(after as f64)
                ),
            );
            ExitCode::SUCCESS
        }
    }
}

/// `_human_bytes`: B/KB/MB/GB with no decimals, TB with one.
fn human_bytes(mut n: f64) -> String {
    for unit in ["B", "KB", "MB", "GB"] {
        if n < 1024.0 {
            return format!("{n:.0} {unit}");
        }
        n /= 1024.0;
    }
    format!("{n:.1} TB")
}

// --- cairn update ------------------------------------------------------------

/// update handler: incremental reindex from git diff or a single file.
fn update(args: crate::cmds_core::UpdateArgs) -> ExitCode {
    let db = db_arg(&args.db);
    let workspace = args.workspace.clone().unwrap_or_else(|| ".".to_string());
    let knowledge = args.knowledge.clone().unwrap_or_else(|| {
        store_paths::resolve_store(None)
            .knowledge
            .to_string_lossy()
            .into_owned()
    });

    if let Some(file_path) = &args.file_path {
        let abs = store_paths::resolve_path(Path::new(file_path));
        let repo = args
            .repo
            .clone()
            .or_else(|| cairn_build::scanner::infer_repo_for_path(&workspace, &abs));
        let Some(repo) = repo else {
            display::error(&format!("Could not infer repository for path: {file_path}"));
            return ExitCode::FAILURE;
        };
        let repo_path = match cairn_build::scanner::resolve_repo_path(&workspace, &repo) {
            Ok(path) => path,
            Err(message) => {
                display::error(&message);
                return ExitCode::FAILURE;
            }
        };
        let Some(rel_path) = abs
            .strip_prefix(&repo_path)
            .ok()
            .map(|p| p.to_string_lossy().into_owned())
        else {
            display::error(&format!(
                "File {file_path} is not under repository path {}",
                repo_path.display()
            ));
            return ExitCode::FAILURE;
        };
        let mut conn = match cairn_store::schema::open_initialized(Path::new(&db)) {
            Ok(conn) => conn,
            Err(e) => return command_error(&format!("cannot open {db}: {e}")),
        };
        let result = cairn_build::incremental::reindex_paths(
            &mut conn,
            &workspace,
            &[abs.to_string_lossy().into_owned()],
        );
        if result.reindexed + result.deleted == 0 {
            display::warning(&format!(
                "no reindexable change for {rel_path} in repo {repo}"
            ));
        }
        display::success(&format!("Reindexed {rel_path} in repo {repo}"));
        return ExitCode::SUCCESS;
    }

    if let Some(diff_ref) = &args.diff_ref {
        let sep = if diff_ref.contains("...") {
            "..."
        } else {
            ".."
        };
        let endpoints: Vec<&str> = diff_ref.split(sep).filter(|p| !p.is_empty()).collect();
        let repos: Vec<String> = match &args.repo {
            Some(repo) => vec![repo.clone()],
            None => cairn_build::scanner::discover_repo_records(&workspace)
                .unwrap_or_default()
                .into_iter()
                .map(|r| r.repo_id)
                .collect(),
        };
        for repo in &repos {
            let Ok(repo_path) = cairn_build::scanner::resolve_repo_path(&workspace, repo) else {
                continue;
            };
            for endpoint in &endpoints {
                let Ok(output) = std::process::Command::new("git")
                    .args(["rev-parse", "-q", "--verify", endpoint])
                    .current_dir(&repo_path)
                    .output()
                else {
                    display::error(&format!(
                        "--diff-ref endpoint '{endpoint}' does not resolve in {}",
                        repo_path.display()
                    ));
                    return ExitCode::FAILURE;
                };
                if !output.status.success() {
                    display::error(&format!(
                        "--diff-ref endpoint '{endpoint}' does not resolve in {}",
                        repo_path.display()
                    ));
                    return ExitCode::FAILURE;
                }
            }
        }
    }

    let summary = match cairn_build::incremental::incremental_update(
        &workspace,
        &db,
        args.repo.as_deref(),
        args.diff_ref.as_deref(),
    ) {
        Ok(summary) => summary,
        Err(message) => return command_error(&message),
    };
    let deleted = summary.files_deleted;
    if !summary.errors.is_empty() {
        display::warning(&format!(
            "Updated: reindexed {} files ({} deleted) across {} repos with {} error(s)",
            summary.files_reindexed,
            deleted,
            summary.repos_scanned,
            summary.errors.len()
        ));
        for msg in summary.errors.iter().take(5) {
            display::warning(&format!("  {msg}"));
        }
        if summary.errors.len() > 5 {
            display::warning(&format!("  ... and {} more", summary.errors.len() - 5));
        }
    } else {
        let deleted_part = if deleted > 0 {
            format!(", {deleted} deleted")
        } else {
            String::new()
        };
        display::success(&format!(
            "Updated: reindexed {} files{deleted_part} across {} repos",
            summary.files_reindexed, summary.repos_scanned
        ));
    }

    // Memory decay: archive stale raw/tribal memories after the reindex.
    let bundle = cairn_query::tools_km::Bundle::new(&knowledge);
    match Connection::open(&db) {
        Ok(reap_conn) => match cairn_query::tools_km::decay(&bundle, 7, 90, Some(&reap_conn)) {
            Ok((expired_raw, archived_tribal)) => {
                if expired_raw > 0 || archived_tribal > 0 {
                    display::success(&format!(
                        "Memory decay: archived {expired_raw} stale raw memories, \
                             {archived_tribal} stale tribal memories"
                    ));
                }
            }
            Err(e) => display::warning(&format!("Memory decay failed (non-critical): {}", e.0)),
        },
        Err(e) => display::warning(&format!("Memory decay failed (non-critical): {e}")),
    }

    if !summary.errors.is_empty() {
        return ExitCode::FAILURE;
    }
    ExitCode::SUCCESS
}

// --- graph-read commands -----------------------------------------------------

/// def handler.
fn find_def(args: crate::cmds_core::DefArgs) -> ExitCode {
    let db = db_arg(&args.db);
    let refresh = refresh_flag(args.refresh, args.no_refresh);
    let Ok(conn) = fresh_conn(&db, refresh) else {
        return command_error(&format!("cannot open {db}"));
    };
    let rows = match cli_rows::find_definition_rows(&conn, &args.symbol, 50) {
        Ok(rows) => rows,
        Err(e) => return command_error(&e.to_string()),
    };
    if rows.is_empty() {
        eprintln!("No definition found for '{}'.", args.symbol);
        return ExitCode::FAILURE;
    }
    if args.as_json {
        println!(
            "{}",
            py_json::dumps(&Value::Array(rows.into_iter().map(Value::Object).collect()))
        );
        return ExitCode::SUCCESS;
    }
    for row in &rows {
        let modifiers = row
            .get("modifiers")
            .and_then(Value::as_str)
            .and_then(mods_list)
            .unwrap_or_default();
        println!(
            "{}:{}  {} {}{}",
            str_of(row.get("file_path")),
            str_of(row.get("line_start")),
            str_of(row.get("kind")),
            row.get("qualified_name")
                .and_then(Value::as_str)
                .or_else(|| row.get("name").and_then(Value::as_str))
                .unwrap_or_default(),
            if modifiers.is_empty() {
                String::new()
            } else {
                format!("  [{}]", modifiers.join(", "))
            }
        );
    }
    ExitCode::SUCCESS
}

fn refresh_flag(refresh: bool, no_refresh: bool) -> Option<bool> {
    if refresh {
        Some(true)
    } else if no_refresh {
        Some(false)
    } else {
        None
    }
}

fn str_of(value: Option<&Value>) -> String {
    match value {
        Some(Value::Null) | None => "None".to_string(),
        Some(Value::String(s)) => s.clone(),
        Some(Value::Number(n)) => n.to_string(),
        Some(Value::Bool(b)) => b.to_string(),
        Some(other) => other.to_string(),
    }
}

/// `_mods`: parse the modifiers JSON column into a list.
fn mods_list(raw: &str) -> Option<Vec<String>> {
    if raw.is_empty() {
        return None;
    }
    serde_json::from_str::<Value>(raw).ok().and_then(|v| {
        v.as_array().map(|a| {
            a.iter()
                .filter_map(|i| i.as_str().map(String::from))
                .collect()
        })
    })
}

/// callers handler.
fn callers(args: crate::cmds_core::CallersArgs) -> ExitCode {
    let db = db_arg(&args.db);
    let refresh = refresh_flag(args.refresh, args.no_refresh);
    let Ok(conn) = fresh_conn(&db, refresh) else {
        return command_error(&format!("cannot open {db}"));
    };
    let rows = match cli_rows::get_callers_rows(&conn, &args.symbol, 200, args.fuzzy, None, None) {
        Ok(rows) => rows,
        Err(e) => return command_error(&e.to_string()),
    };
    if rows.is_empty() {
        eprintln!("No callers found for '{}'.", args.symbol);
        return ExitCode::FAILURE;
    }
    if args.as_json {
        println!(
            "{}",
            py_json::dumps(&Value::Array(rows.into_iter().map(Value::Object).collect()))
        );
        return ExitCode::SUCCESS;
    }
    for row in &rows {
        println!(
            "{}:{}  {} {}  ({})",
            str_of(row.get("file_path")),
            str_of(row.get("edge_line")),
            str_of(row.get("caller_kind")),
            str_of(row.get("caller_name")),
            str_of(row.get("repo")),
        );
    }
    ExitCode::SUCCESS
}

/// search handler.
fn search(args: crate::cmds_core::SearchArgs) -> ExitCode {
    let db = db_arg(&args.db);
    let refresh = refresh_flag(args.refresh, args.no_refresh);
    let Ok(conn) = fresh_conn(&db, refresh) else {
        return command_error(&format!("cannot open {db}"));
    };
    let rows = cli_rows::search_symbol_rows(&conn, &args.pattern, args.kind.as_deref(), 100);
    if rows.is_empty() {
        eprintln!("No symbols matching '{}'.", args.pattern);
        return ExitCode::FAILURE;
    }
    if args.as_json {
        println!(
            "{}",
            py_json::dumps(&Value::Array(rows.into_iter().map(Value::Object).collect()))
        );
        return ExitCode::SUCCESS;
    }
    for row in &rows {
        println!(
            "{:<10} {:<35} {}:{}  ({})",
            str_of(row.get("kind")),
            str_of(row.get("name")),
            str_of(row.get("file_path")),
            str_of(row.get("line_start")),
            str_of(row.get("repo")),
        );
    }
    ExitCode::SUCCESS
}

/// callees handler.
fn callees(args: crate::cmds_core::CalleesArgs) -> ExitCode {
    let db = db_arg(&args.db);
    let refresh = refresh_flag(args.refresh, args.no_refresh);
    let Ok(conn) = fresh_conn(&db, refresh) else {
        return command_error(&format!("cannot open {db}"));
    };
    let rows = match cli_rows::get_callees_rows(&conn, &args.symbol, 200, args.fuzzy, None, None) {
        Ok(rows) => rows,
        Err(e) => return command_error(&e.to_string()),
    };
    if rows.is_empty() {
        eprintln!("No callees found for '{}'.", args.symbol);
        return ExitCode::FAILURE;
    }
    if args.as_json {
        println!(
            "{}",
            py_json::dumps(&Value::Array(rows.into_iter().map(Value::Object).collect()))
        );
        return ExitCode::SUCCESS;
    }
    for row in &rows {
        let resolved = if row.get("resolved").map(|v| !v.is_null()).unwrap_or(false) {
            ""
        } else {
            "  (unresolved)"
        };
        println!(
            "{:<10} {:<35} {}:{}{}",
            str_of(row.get("callee_kind")),
            str_of(row.get("callee_name")),
            str_of(row.get("file_path")),
            str_of(row.get("edge_line")),
            resolved,
        );
    }
    ExitCode::SUCCESS
}

/// impact handler.
fn impact(args: crate::cmds_core::ImpactArgs) -> ExitCode {
    if args.depth < 1 || args.depth > TRAVERSAL_MAX_DEPTH {
        return usage_error(&format!(
            "--depth must be between 1 and {TRAVERSAL_MAX_DEPTH}."
        ));
    }
    let db = db_arg(&args.db);
    let refresh = refresh_flag(args.refresh, args.no_refresh);
    let Ok(conn) = fresh_conn(&db, refresh) else {
        return command_error(&format!("cannot open {db}"));
    };
    let result =
        match cairn_query::graph::impact_analysis(&conn, &args.symbol, args.depth, args.fuzzy, 500)
        {
            Ok(result) => result,
            Err(e) => return command_error(&e.to_string()),
        };
    let impacted: Vec<Value> = result
        .impacted
        .iter()
        .map(|entry| {
            let mut map = Map::new();
            map.insert("symbol".into(), Value::from(entry.symbol.clone()));
            map.insert("file".into(), Value::from(entry.file.clone()));
            map.insert("repo".into(), Value::from(entry.repo.clone()));
            map.insert("depth".into(), Value::from(entry.depth));
            Value::Object(map)
        })
        .collect();
    let cycles: Vec<Value> = result
        .cycles
        .iter()
        .map(|(symbol, depth)| serde_json::json!({"symbol": symbol, "depth": depth}))
        .collect();
    let total = result.impacted.len();
    let empty = total == 0;
    if args.as_json {
        let mut object = Map::new();
        object.insert("impacted".into(), Value::Array(impacted));
        object.insert("cycles".into(), Value::Array(cycles));
        object.insert("total".into(), Value::from(total as i64));
        object.insert("truncated".into(), Value::from(result.truncated));
        println!("{}", py_json::dumps(&Value::Object(object)));
        if empty {
            return ExitCode::FAILURE;
        }
        return ExitCode::SUCCESS;
    }
    if !result.cycles.is_empty() {
        let names: Vec<String> = result
            .cycles
            .iter()
            .map(|(s, _)| format!("'{}'", s))
            .collect();
        if names.len() <= 15 {
            println!("Cycles detected: [{}]", names.join(", "));
        } else {
            let head: Vec<String> = names.iter().take(15).cloned().collect();
            println!(
                "Cycles detected ({}): [{}] ...",
                names.len(),
                head.join(", ")
            );
        }
    }
    if empty {
        eprintln!("No impacted symbols for '{}'.", args.symbol);
        return ExitCode::FAILURE;
    }
    println!("Total impacted: {}", fmt_int(total as i64));
    if result.truncated {
        eprintln!("Truncated: hit the symbol cap; narrow the query for full coverage.");
    }
    let mut depths: Vec<i64> = result.impacted.iter().map(|e| e.depth).collect();
    depths.sort();
    depths.dedup();
    for d in depths {
        let group: Vec<&cairn_query::graph::ImpactEntry> =
            result.impacted.iter().filter(|e| e.depth == d).collect();
        println!("\nDepth {d} ({}):", group.len());
        for entry in group.iter().take(20) {
            println!(
                "  {:<30} {}  ({})",
                entry.symbol,
                shorten(&entry.file),
                entry.repo
            );
        }
        if group.len() > 20 {
            println!("  ... and {} more", group.len() - 20);
        }
    }
    ExitCode::SUCCESS
}

/// deps handler.
fn deps(args: crate::cmds_core::DepsArgs) -> ExitCode {
    let db = db_arg(&args.db);
    let refresh = refresh_flag(args.refresh, args.no_refresh);
    let Ok(conn) = fresh_conn(&db, refresh) else {
        return command_error(&format!("cannot open {db}"));
    };
    let namespaces = namespace_table();
    let result = match cairn_query::graph::cross_repo_deps(&conn, &args.repo, &namespaces) {
        Ok(result) => result,
        Err(e) => return command_error(&e.to_string()),
    };
    let dependencies_json: Vec<Value> = result
        .dependencies
        .iter()
        .map(|dep| {
            serde_json::json!({
                "repo": dep.repo,
                "type": "import",
                "evidence": dep.evidence,
                "count": dep.count,
            })
        })
        .collect();
    let dependents_json: Vec<Value> = result
        .dependents
        .iter()
        .map(|dep| {
            serde_json::json!({
                "repo": dep.repo,
                "type": "import",
                "count": dep.count,
            })
        })
        .collect();
    if args.as_json {
        let mut object = Map::new();
        object.insert("dependencies".into(), Value::Array(dependencies_json));
        object.insert("dependents".into(), Value::Array(dependents_json));
        println!("{}", py_json::dumps(&Value::Object(object)));
        if result.dependencies.is_empty() && result.dependents.is_empty() {
            return ExitCode::FAILURE;
        }
        return ExitCode::SUCCESS;
    }
    println!("=== {} depends on ===", args.repo);
    if result.dependencies.is_empty() {
        println!("  (none)");
    } else {
        for dep in &result.dependencies {
            println!(
                "  {:<18} (import: {}) x{}",
                dep.repo, dep.evidence, dep.count
            );
        }
    }
    println!("\n=== Dependents of {} ===", args.repo);
    if result.dependents.is_empty() {
        println!("  (none)");
    } else {
        for dep in &result.dependents {
            println!("  {:<18} x{}", dep.repo, dep.count);
        }
    }
    if result.dependencies.is_empty() && result.dependents.is_empty() {
        return ExitCode::FAILURE;
    }
    ExitCode::SUCCESS
}

/// Resolved namespace table for cross-repo matching (env > cairn.json > default).
fn namespace_table() -> Vec<(String, String)> {
    let env_raw = std::env::var("CAIRN_REPO_NAMESPACES").ok();
    cairn_query::graph::resolve_namespaces(
        &store_paths::resolve_workspace(None),
        env_raw.as_deref(),
    )
}

/// federated-search handler.
fn federated_search(args: crate::cmds_core::FederatedSearchArgs) -> ExitCode {
    if args.limit < 1 {
        return usage_error("--limit must be at least 1");
    }
    if args.query.trim().is_empty() {
        return usage_error("query must not be empty");
    }
    let limit = args.limit.clamp(1, 1000) as usize;
    let env = cairn_query::tools_search::semantic_env(None);
    let mut hits: Vec<(String, Value)> = Vec::new();
    let mut dropped: Vec<String> = Vec::new();
    let mut states: Vec<(String, String)> = Vec::new();
    let mut rankings: Vec<Vec<String>> = Vec::new();
    let registry = store_paths::load_registry();
    let home = store_paths::cairn_home();
    for (ws_path, key) in &registry {
        let Some(home) = &home else { break };
        let store_home = home.join(key);
        let db = store_home.join(".kg");
        let state = classify_store(&store_home, &db);
        if state != "ok" {
            states.push((ws_path.clone(), state.to_string()));
            dropped.push(ws_path.clone());
            continue;
        }
        let Ok(conn) = cairn_store::conn::open_read_only(&db) else {
            let state = "error".to_string();
            states.push((ws_path.clone(), state.clone()));
            dropped.push(ws_path.clone());
            continue;
        };
        let store_hits = search_store_hits(&conn, &args.query, limit, &env);
        let store_hits = match store_hits {
            Ok(hits) => hits,
            Err(message) => {
                let state = if message.contains("locked") || message.contains("busy") {
                    "locked"
                } else {
                    "error"
                };
                states.push((ws_path.clone(), state.to_string()));
                dropped.push(ws_path.clone());
                continue;
            }
        };
        states.push((ws_path.clone(), "ok".to_string()));
        let mut ranking = Vec::new();
        for hit in store_hits {
            let dir_name = store_home
                .file_name()
                .map(|n| n.to_string_lossy().into_owned())
                .unwrap_or_default();
            let doc_id = format!("{dir_name}:{}", hit["id"].as_str().unwrap_or_default());
            let mut hit = hit;
            if let Some(obj) = hit.as_object_mut() {
                obj.insert("workspace".into(), Value::from(ws_path.clone()));
            }
            ranking.push(doc_id.clone());
            hits.push((doc_id, hit));
        }
        rankings.push(ranking);
    }
    // RRF-fuse the per-store rankings, keeping the python rounding.
    let hits_by_doc = hits;
    let fused = cairn_query::tools_search::rrf_fuse(&rankings, 60);
    let mut fused_hits: Vec<Value> = Vec::new();
    for (doc_id, score) in &fused {
        if let Some((_, hit)) = hits_by_doc.iter().find(|(id, _)| id == doc_id) {
            let mut hit = hit.clone();
            let rounded = (score * 10_000.0).round() / 10_000.0;
            if let Some(existing) = hit.get_mut("score")
                && let Some(v) = serde_json::Number::from_f64(rounded)
            {
                *existing = Value::Number(v);
            }
            fused_hits.push(hit);
        }
    }
    fused_hits.truncate(limit);

    if args.as_json {
        let mut object = Map::new();
        object.insert("query".into(), Value::from(args.query.clone()));
        object.insert("hits".into(), Value::Array(fused_hits.clone()));
        object.insert(
            "dropped".into(),
            Value::Array(dropped.iter().map(|d| Value::from(d.clone())).collect()),
        );
        let mut states_map = Map::new();
        for (ws, state) in &states {
            states_map.insert(ws.clone(), Value::from(state.clone()));
        }
        object.insert("states".into(), Value::Object(states_map));
        println!("{}", py_json::dumps(&Value::Object(object)));
        if fused_hits.is_empty() {
            return ExitCode::FAILURE;
        }
        return ExitCode::SUCCESS;
    }
    if states.is_empty() {
        eprintln!("No stores are registered.");
        return ExitCode::FAILURE;
    }
    for hit in &fused_hits {
        println!(
            "{:<10} {:<35} {}  ({})  [{}]  {}  {}",
            str_of(hit.get("kind")),
            str_of(hit.get("name")),
            str_of(hit.get("file_path")),
            str_of(hit.get("repo")),
            str_of(hit.get("workspace")),
            format_g(str_of(hit.get("score")).parse::<f64>().unwrap_or(0.0)),
            str_of(hit.get("provenance")),
        );
    }
    if !dropped.is_empty() {
        println!();
        println!("Dropped stores ({}):", dropped.len());
        for ws in &dropped {
            let state = states
                .iter()
                .find(|(w, _)| w == ws)
                .map(|(_, s)| s.as_str())
                .unwrap_or("unknown");
            println!("  {state:<10} {ws}");
        }
    }
    if fused_hits.is_empty() {
        eprintln!("No hits for '{}'.", args.query);
        return ExitCode::FAILURE;
    }
    ExitCode::SUCCESS
}

/// `format(value, "g")` for the score column (6 significant digits).
fn format_g(value: f64) -> String {
    if value == 0.0 {
        return "0".to_string();
    }
    let exp = value.abs().log10().floor() as i32;
    if !(-4..6).contains(&exp) {
        return format!("{value:e}");
    }
    let decimals = (5 - exp).max(0) as usize;
    format!("{value:.decimals$}")
        .trim_end_matches('0')
        .trim_end_matches('.')
        .to_string()
}

fn classify_store(home: &Path, db: &Path) -> &'static str {
    if !home.is_dir() {
        return "missing";
    }
    if !db.is_file() {
        return "unindexed";
    }
    "ok"
}

/// One store's ranked hits: semantic when embeddings exist, bm25 otherwise.
fn search_store_hits(
    conn: &Connection,
    query: &str,
    limit: usize,
    env: &cairn_query::tools_search::SemanticEnv,
) -> Result<Vec<Value>, String> {
    if env.backend == cairn_query::tools_search::EffectiveBackend::Unavailable {
        return Err("embedding backend unavailable".to_string());
    }
    let has_embeddings: bool = conn
        .query_row(
            "SELECT 1 FROM embeddings WHERE model = ? LIMIT 1",
            [&env.model],
            |_| Ok(()),
        )
        .is_ok();
    if has_embeddings {
        let matches = cairn_query::tools_search::semantic_search(conn, query, limit, 0.3, env);
        return Ok(matches
            .into_iter()
            .map(|m| {
                serde_json::json!({
                    "id": m.id,
                    "name": m.name,
                    "kind": m.kind,
                    "qualified_name": m.qualified_name,
                    "file_path": m.file_path,
                    "repo": m.repo,
                    "score": m.score,
                    "chunk": m.chunk,
                    "provenance": m.provenance,
                    "reranked": m.reranked,
                })
            })
            .collect());
    }
    let rows = cairn_query::graph::lexical_search_symbols(conn, query, None, limit as i64);
    Ok(rows
        .into_iter()
        .map(|row| {
            serde_json::json!({
                "id": row.id,
                "name": row.name,
                "kind": row.kind,
                "qualified_name": row.qualified_name,
                "file_path": row.file_path,
                "repo": row.repo,
                "score": 0.0,
                "chunk": "",
                "provenance": "bm25",
                "reranked": false,
            })
        })
        .collect())
}

/// tree handler.
fn tree(args: crate::cmds_core::TreeArgs) -> ExitCode {
    let db = db_arg(&args.db);
    let Ok(conn) = cairn_store::schema::open_initialized(Path::new(&db)) else {
        return command_error(&format!("cannot open {db}"));
    };
    let buckets = match cli_rows::get_tree_rows(&conn, &args.repo) {
        Ok(buckets) => buckets,
        Err(e) => return command_error(&e.to_string()),
    };
    if buckets.is_empty() {
        eprintln!("No data for repo '{}'.", args.repo);
        return ExitCode::FAILURE;
    }
    for (key, count) in buckets.iter().take(30) {
        println!("  {:>6}  {key}", count);
    }
    if buckets.len() > 30 {
        println!("  ... and {} more directories", buckets.len() - 30);
    }
    ExitCode::SUCCESS
}

/// path handler.
#[allow(clippy::too_many_arguments)]
fn path(args: crate::cmds_core::PathArgs) -> ExitCode {
    if args.from_pattern.trim().is_empty() || args.to_pattern.trim().is_empty() {
        return usage_error("--from/--to patterns must be non-empty.");
    }
    if let Some(max_depth) = args.max_depth
        && max_depth < 1
    {
        return usage_error("--max-depth must be >= 1.");
    }
    if args.limit < 1 {
        return usage_error("--limit must be >= 1.");
    }
    let effective_depth = args.max_depth.unwrap_or(CLOSURE_MAX_DEPTH);
    let db = db_arg(&args.db);
    let Ok(conn) = cairn_store::schema::open_initialized(Path::new(&db)) else {
        return command_error(&format!("cannot open {db}"));
    };
    let from_syms = match cairn_query::graph::resolve_pattern_symbols(&conn, &args.from_pattern) {
        Ok(rows) => rows,
        Err(e) => return command_error(&e.to_string()),
    };
    let to_syms = match cairn_query::graph::resolve_pattern_symbols(&conn, &args.to_pattern) {
        Ok(rows) => rows,
        Err(e) => return command_error(&e.to_string()),
    };
    if from_syms.is_empty() || to_syms.is_empty() {
        for (side, pattern, syms) in [
            ("from", &args.from_pattern, &from_syms),
            ("to", &args.to_pattern, &to_syms),
        ] {
            if syms.is_empty() {
                println!("no symbols match {side} pattern '{pattern}'");
            }
        }
        return ExitCode::SUCCESS;
    }
    let from_ids: std::collections::HashSet<String> = from_syms.into_iter().map(|s| s.id).collect();
    let to_ids: std::collections::HashSet<String> = to_syms.into_iter().map(|s| s.id).collect();
    let paths = match cairn_query::graph::find_symbol_paths(
        &conn,
        &from_ids,
        &to_ids,
        args.fuzzy,
        effective_depth,
    ) {
        Ok(paths) => paths,
        Err(e) => return command_error(&e.to_string()),
    };
    if paths.is_empty() {
        println!(
            "no path within {effective_depth} hops from '{}' to '{}'",
            args.from_pattern, args.to_pattern
        );
        return ExitCode::SUCCESS;
    }
    for (index, symbol_path) in paths.iter().take(args.limit as usize).enumerate() {
        println!(
            "path {} ({} -> {}, {} hops):",
            index + 1,
            args.from_pattern,
            args.to_pattern,
            symbol_path.hops.len()
        );
        for hop in &symbol_path.hops {
            let line = hop
                .line
                .map(|l| l.to_string())
                .unwrap_or_else(|| "?".into());
            println!(
                "  {}:{} {} [{}]",
                hop.file, line, hop.symbol, hop.resolution
            );
        }
    }
    ExitCode::SUCCESS
}

/// taint handler.
fn taint(args: crate::cmds_core::TaintArgs) -> ExitCode {
    if let Some(max_depth) = args.max_depth
        && max_depth < 1
    {
        return usage_error("--max-depth must be >= 1.");
    }
    if args.limit < 1 {
        return usage_error("--limit must be >= 1.");
    }
    let ws = store_paths::resolve_workspace(None);
    let config = load_config(ws);
    let registry = cairn_query::graph::merged_registry(&config.taint_sources, &config.taint_sinks);
    let unknown: Vec<String> = [
        ("source", &args.from_pattern, &registry.0),
        ("sink", &args.to_pattern, &registry.1),
    ]
    .into_iter()
    .filter_map(|(kind, pattern, table)| {
        let categories: Vec<&str> = table.iter().map(|(c, _)| c.as_str()).collect();
        let names: Vec<&str> = table
            .iter()
            .flat_map(|(_, n)| n.iter().map(String::as_str))
            .collect();
        if categories.contains(&pattern.as_str()) || names.contains(&pattern.as_str()) {
            None
        } else {
            let mut sorted_categories = categories.to_vec();
            sorted_categories.sort();
            Some(format!(
                "unknown {kind} pattern '{pattern}' (known {kind} categories: {})",
                if sorted_categories.is_empty() {
                    "none".to_string()
                } else {
                    sorted_categories.join(", ")
                }
            ))
        }
    })
    .collect();
    if !unknown.is_empty() {
        return command_error(&unknown.join("; "));
    }
    let db = db_arg(&args.db);
    let Ok(conn) = cairn_store::schema::open_initialized(Path::new(&db)) else {
        return command_error(&format!("cannot open {db}"));
    };
    let effective_depth = args.max_depth.unwrap_or(CLOSURE_MAX_DEPTH);
    let paths = match cairn_query::graph::find_paths_registry(
        &conn,
        &registry.0,
        &registry.1,
        &args.from_pattern,
        &args.to_pattern,
        args.fuzzy,
        effective_depth,
    ) {
        Ok(paths) => paths,
        Err(e) => return command_error(&e.to_string()),
    };
    if paths.is_empty() {
        eprintln!(
            "No taint paths from '{}' to '{}'.",
            args.from_pattern, args.to_pattern
        );
        return ExitCode::FAILURE;
    }
    for (index, taint_path) in paths.iter().take(args.limit as usize).enumerate() {
        println!(
            "taint path {} ({} -> {}, {} hops):",
            index + 1,
            args.from_pattern,
            args.to_pattern,
            taint_path.hops.len()
        );
        for hop in &taint_path.hops {
            println!("  {}:{} [{}]", hop.file, hop.symbol, hop.resolution);
        }
    }
    ExitCode::SUCCESS
}

// --- per-command argument structs --------------------------------------------

#[derive(clap::Args)]
pub struct InitArgs {
    /// Workspace root (default: cwd).
    #[arg(long = "workspace")]
    pub ws_arg: Option<String>,
    /// Migrate .kg + .knowledge from this cairn dir (e.g. ./cairn).
    #[arg(long = "from-legacy")]
    pub legacy_dir: Option<String>,
    /// Register without building the graph.
    #[arg(long = "no-build", default_value_t = false)]
    pub no_build: bool,
    /// Auto-discover and ingest docs/**/*.md as knowledge.
    #[arg(long = "import-docs", default_value_t = false)]
    pub import_docs: bool,
    /// Install cairn git hooks (post-commit, post-checkout) in discovered repos.
    #[arg(long = "with-hooks", default_value_t = false)]
    pub with_hooks: bool,
}

#[derive(clap::Args)]
pub struct ConfigArgs {
    /// List all registered workspaces.
    #[arg(long = "list")]
    pub list_all: bool,
    /// Print a path-free .mcp.json snippet for agents.
    #[arg(long = "mcp-config")]
    pub mcp_config: bool,
    /// Print only the resolved graph DB path (machine-readable; for scripting).
    #[arg(long = "db")]
    pub db_only: bool,
    /// Emit the resolved store paths as a single JSON object (machine-readable probe).
    #[arg(long = "json")]
    pub as_json: bool,
}

#[derive(clap::Args)]
pub struct BuildArgs {
    /// Only build this repo.
    #[arg(long = "repo")]
    pub repo: Option<String>,
    /// Workspace root.
    #[arg(long = "workspace")]
    pub workspace: Option<String>,
    /// SQLite DB path.
    #[arg(long = "db")]
    pub db: Option<String>,
    /// Verbose per-file detail (parse errors, skip reasons).
    #[arg(short = 'v', long = "verbose", default_value_t = false)]
    pub verbose: bool,
    /// Build to temp DB and atomic-swap for zero downtime.
    #[arg(long = "staging", default_value_t = false)]
    pub staging: bool,
    /// Upgrade ambiguous Python calls with pyright (accepted; not yet ported).
    #[arg(long = "lsp", num_args = 0..=1, require_equals = false, default_missing_value = "true")]
    pub lsp: Option<bool>,
    /// Materialize the transitive closure (skipped by default).
    #[arg(long = "with-closure", default_value_t = false)]
    pub with_closure: bool,
}

#[derive(clap::Args)]
pub struct ImportScipArgs {
    /// Path to the SCIP index file.
    pub scip_file: String,
    /// Workspace root.
    #[arg(long = "workspace")]
    pub workspace: Option<String>,
    /// SQLite DB path (default: central store for this workspace).
    #[arg(long = "db")]
    pub db: Option<String>,
    /// Rebuild the transitive closure after import (skipped by default).
    #[arg(long = "with-closure", default_value_t = false)]
    pub with_closure: bool,
}

#[derive(clap::Args)]
pub struct StatsArgs {
    /// SQLite DB path.
    #[arg(long = "db")]
    pub db: Option<String>,
}

#[derive(clap::Args)]
pub struct CheckpointArgs {
    /// SQLite DB path (default: central store for this workspace).
    #[arg(long = "db")]
    pub db: Option<String>,
}

#[derive(clap::Args)]
pub struct UpdateArgs {
    /// Specific repo to update.
    #[arg(long = "repo")]
    pub repo: Option<String>,
    /// Specific file changed (for PostToolUse hooks).
    #[arg(long = "file")]
    pub file_path: Option<String>,
    /// Git ref range A..B or A...B to scope changed-file detection to.
    #[arg(long = "diff-ref")]
    pub diff_ref: Option<String>,
    /// Workspace root.
    #[arg(long = "workspace")]
    pub workspace: Option<String>,
    /// SQLite DB path.
    #[arg(long = "db")]
    pub db: Option<String>,
    /// Knowledge bundle path (for the post-update memory staleness scan).
    #[arg(long = "knowledge")]
    pub knowledge: Option<String>,
}

#[derive(clap::Args)]
pub struct DefArgs {
    /// Symbol name to find.
    pub symbol: String,
    /// SQLite DB path.
    #[arg(long = "db")]
    pub db: Option<String>,
    /// Emit JSON.
    #[arg(long = "json")]
    pub as_json: bool,
    /// Reindex drifted files before answering.
    #[arg(long = "refresh")]
    pub refresh: bool,
    /// Answer from the current graph without reindexing.
    #[arg(long = "no-refresh")]
    pub no_refresh: bool,
}

#[derive(clap::Args)]
pub struct CallersArgs {
    /// Symbol name to look up.
    pub symbol: String,
    /// SQLite DB path.
    #[arg(long = "db")]
    pub db: Option<String>,
    /// Emit JSON.
    #[arg(long = "json")]
    pub as_json: bool,
    /// Also match unresolved edges by name.
    #[arg(long = "fuzzy", default_value_t = false)]
    pub fuzzy: bool,
    /// Reindex drifted files before answering.
    #[arg(long = "refresh")]
    pub refresh: bool,
    /// Answer from the current graph without reindexing.
    #[arg(long = "no-refresh")]
    pub no_refresh: bool,
}

#[derive(clap::Args)]
pub struct SearchArgs {
    /// Name pattern with optional * wildcards.
    pub pattern: String,
    /// Filter by kind: class|function|method|...
    #[arg(long = "kind")]
    pub kind: Option<String>,
    /// SQLite DB path.
    #[arg(long = "db")]
    pub db: Option<String>,
    /// Emit JSON.
    #[arg(long = "json")]
    pub as_json: bool,
    /// Reindex drifted files before answering.
    #[arg(long = "refresh")]
    pub refresh: bool,
    /// Answer from the current graph without reindexing.
    #[arg(long = "no-refresh")]
    pub no_refresh: bool,
}

#[derive(clap::Args)]
pub struct CalleesArgs {
    /// Symbol name to look up.
    pub symbol: String,
    /// SQLite DB path.
    #[arg(long = "db")]
    pub db: Option<String>,
    /// Emit JSON.
    #[arg(long = "json")]
    pub as_json: bool,
    /// Also include unresolved outgoing calls (named-only).
    #[arg(long = "fuzzy", default_value_t = false)]
    pub fuzzy: bool,
    /// Reindex drifted files before answering.
    #[arg(long = "refresh")]
    pub refresh: bool,
    /// Answer from the current graph without reindexing.
    #[arg(long = "no-refresh")]
    pub no_refresh: bool,
}

#[derive(clap::Args)]
pub struct ImpactArgs {
    /// Symbol name to analyze.
    pub symbol: String,
    /// Max traversal depth (1-100).
    #[arg(long = "depth", default_value_t = 10)]
    pub depth: i64,
    /// SQLite DB path.
    #[arg(long = "db")]
    pub db: Option<String>,
    /// Emit JSON.
    #[arg(long = "json")]
    pub as_json: bool,
    /// Reindex drifted files before answering.
    #[arg(long = "refresh")]
    pub refresh: bool,
    /// Answer from the current graph without reindexing.
    #[arg(long = "no-refresh")]
    pub no_refresh: bool,
    /// Also traverse unresolved name-only edges.
    #[arg(long = "fuzzy", default_value_t = false)]
    pub fuzzy: bool,
}

#[derive(clap::Args)]
pub struct DepsArgs {
    /// Repo id to map.
    pub repo: String,
    /// SQLite DB path.
    #[arg(long = "db")]
    pub db: Option<String>,
    /// Emit JSON.
    #[arg(long = "json")]
    pub as_json: bool,
    /// Reindex drifted files before answering.
    #[arg(long = "refresh")]
    pub refresh: bool,
    /// Answer from the current graph without reindexing.
    #[arg(long = "no-refresh")]
    pub no_refresh: bool,
}

#[derive(clap::Args)]
pub struct FederatedSearchArgs {
    /// Query text.
    pub query: String,
    /// Max merged hits.
    #[arg(long = "limit", default_value_t = 20)]
    pub limit: i64,
    /// Emit JSON.
    #[arg(long = "json")]
    pub as_json: bool,
    /// Opt into one shared embedding backend when every store's model stamp matches.
    #[arg(long = "shared-embed", default_value_t = false)]
    pub shared_embed: bool,
}

#[derive(clap::Args)]
pub struct TreeArgs {
    /// Repo id to summarize.
    pub repo: String,
    /// SQLite DB path.
    #[arg(long = "db")]
    pub db: Option<String>,
}

#[derive(clap::Args)]
pub struct PathArgs {
    /// SQLite DB path.
    #[arg(long = "db")]
    pub db: Option<String>,
    /// Origin pattern: substring of a symbol or qualified name.
    #[arg(long = "from")]
    pub from_pattern: String,
    /// Destination pattern: substring of a symbol or qualified name.
    #[arg(long = "to")]
    pub to_pattern: String,
    /// Also traverse ambiguous and unresolved edges.
    #[arg(long = "fuzzy", default_value_t = false)]
    pub fuzzy: bool,
    /// Edge budget between endpoints (default: closure depth).
    #[arg(long = "max-depth")]
    pub max_depth: Option<i64>,
    /// Maximum printed paths.
    #[arg(long = "limit", default_value_t = DEFAULT_PATH_LIMIT)]
    pub limit: i64,
}

#[derive(clap::Args)]
pub struct TaintArgs {
    /// SQLite DB path.
    #[arg(long = "db")]
    pub db: Option<String>,
    /// Source pattern: registry category token or exact call name.
    #[arg(long = "from")]
    pub from_pattern: String,
    /// Sink pattern: registry category token or exact call name.
    #[arg(long = "to")]
    pub to_pattern: String,
    /// Also traverse ambiguous and unresolved edges.
    #[arg(long = "fuzzy", default_value_t = false)]
    pub fuzzy: bool,
    /// Edge budget between entry and terminator (default: closure depth).
    #[arg(long = "max-depth")]
    pub max_depth: Option<i64>,
    /// Maximum printed paths.
    #[arg(long = "limit", default_value_t = DEFAULT_PATH_LIMIT)]
    pub limit: i64,
}
