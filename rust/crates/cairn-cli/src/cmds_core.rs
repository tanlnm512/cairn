//! Core commands: init/build/update/import-scip/stats/checkpoint/config plus
//! the graph-read surface (def/callers/callees/search/impact/deps/tree/path/
//! taint/federated-search/pack).

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::time::Instant;

use clap::Subcommand;
use rusqlite::Connection;
use serde_json::{Map, Value};

use crate::cli_paths::db_arg;
use crate::cli_rows;
use crate::display;
use crate::json_compat;
use crate::pack;
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
    Semantic(SemanticArgs),
    /// Build the semantic embedding index over the symbol graph.
    Embed(EmbedArgs),
    /// Search indexed files and group matches by enclosing symbol.
    Grep(GrepArgs),
    /// Show directory clusters, hubs, and workspace hotspots.
    Map(MapArgs),
    /// Directory/package structure of REPO with symbol counts.
    Tree(TreeArgs),
    /// Build a deterministic, token-budgeted context pack for a task.
    Pack(pack::PackArgs),
    /// Print shortest structural paths between two symbols.
    #[command(name = "path")]
    Path(PathArgs),
    /// Natural-language question across all layers.
    Ask(AskArgs),
    /// Load relevant context (compass + memory + wiki) for a file.
    Context(ContextArgs),
    /// Trace inter-procedural taint paths from a source to a sink.
    Taint(TaintArgs),
    /// Show reverse dependents for symbols touched by a git diff.
    Blast(BlastArgs),
    /// Compute Louvain communities over structural edges.
    Communities(CommunitiesArgs),
    /// Precomputed dataflow index for public symbols.
    Dataflow {
        #[command(subcommand)]
        command: DataflowCommand,
    },
    /// Print rationale comments (NOTE/WHY/HACK), ordered by hotness.
    Rationale(RationaleArgs),
    /// Review-context pack and review-loop hooks for a change.
    Review(ReviewArgs),
    /// Generate visual diagrams from the graph.
    Viz(VizArgs),
}

#[derive(Subcommand)]
pub enum DataflowCommand {
    /// Build the dataflow index.
    Build(DataflowBuildArgs),
    /// Look up a symbol's dataflow facts.
    Lookup(DataflowLookupArgs),
}

/// Run a core command.
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
        CoreCommand::Semantic(args) => semantic(args),
        CoreCommand::Embed(args) => embed(args),
        CoreCommand::Grep(args) => grep(args),
        CoreCommand::Map(args) => map(args),
        CoreCommand::Blast(args) => blast(args),
        CoreCommand::Communities(args) => communities(args),
        CoreCommand::Dataflow { command } => match command {
            DataflowCommand::Build(args) => dataflow_build(args),
            DataflowCommand::Lookup(args) => dataflow_lookup(args),
        },
        CoreCommand::Rationale(args) => rationale(args),
        CoreCommand::Viz(args) => viz(args),
        CoreCommand::Ask(args) => ask(args),
        CoreCommand::Context(args) => context(args),
        CoreCommand::Review(args) => review(args),
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
pub(crate) fn shorten(path: &str) -> String {
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

/// Drift still present after a probe: repair disabled or lock contention.
struct StaleDrift(String);

/// Probe indexed files and repair drift (watcher.refresh_for_query); a failed
/// strict repair is an error, unrepaired drift carries its banner text.
fn refresh_probe(
    conn: &mut Connection,
    db: &str,
    repair: Option<bool>,
) -> Result<Option<StaleDrift>, String> {
    let repair = repair.unwrap_or_else(|| {
        !std::env::var("CAIRN_NO_REFRESH")
            .map(|v| matches!(v.to_lowercase().as_str(), "1" | "true" | "yes"))
            .unwrap_or(false)
    });
    if !repair {
        let workspace = conn_store_workspace(db);
        let mut drifted = cairn_build::incremental::detect_changed(conn, &workspace);
        drifted.sort();
        drifted.dedup();
        return Ok(stale_banner(&drifted));
    }
    let _lock = match cairn_store::swap::build_lock(Path::new(db)) {
        Ok(lock) => lock,
        // Contention: a concurrent build owns the store; defer the repair and
        // let the read proceed against the stale graph.
        Err(_) => {
            let workspace = conn_store_workspace(db);
            let mut drifted = cairn_build::incremental::detect_changed(conn, &workspace);
            drifted.sort();
            drifted.dedup();
            return Ok(stale_banner(&drifted));
        }
    };
    // The connection must be opened under the lock: one opened earlier keeps
    // writing to the replaced file across a full-build swap.
    let mut conn = cairn_store::schema::open_initialized(Path::new(db))
        .map_err(|e| format!("open {db}: {e}"))?;
    let workspace = conn_store_workspace(db);
    let mut drifted = cairn_build::incremental::detect_changed(&conn, &workspace);
    drifted.sort();
    drifted.dedup();
    if drifted.is_empty() {
        return Ok(None);
    }
    cairn_build::incremental::repair_changed(&mut conn, &workspace, &drifted, true).map(|_| None)
}

fn stale_banner(drifted: &[String]) -> Option<StaleDrift> {
    if drifted.is_empty() {
        return None;
    }
    let shown: Vec<String> = drifted.iter().take(3).cloned().collect();
    let mut line = shown.join(", ");
    if drifted.len() > 3 {
        line.push_str(&format!(", +{} more", drifted.len() - 3));
    }
    Some(StaleDrift(format!(
        "Stale graph: {} file(s) not refreshed: {line}",
        drifted.len()
    )))
}

/// Probe indexed files and repair drift before a read (watcher.refresh_for_query).
fn refresh_conn(conn: &mut Connection, db: &str, repair: Option<bool>) -> Result<(), String> {
    match refresh_probe(conn, db, repair)? {
        Some(stale) => {
            banner_if_unrepaired(&stale.0);
            Ok(())
        }
        None => Ok(()),
    }
}

fn banner_if_unrepaired(banner: &str) {
    eprintln!("{banner}");
}

/// Registered workspace whose canonical store backs this db, else resolve_workspace.
fn conn_store_workspace(db: &str) -> String {
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

// --- cairn init --------------------------------------------------------------

/// init handler: register + first build.
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

    if args.with_hooks
        && crate::cmds_system::run_hooks_install(args.ws_arg.clone()) != ExitCode::SUCCESS
    {
        rail.warn("git hooks not installed; run `cairn hooks install` for details");
    }

    rail.close("Done");
    display::dim("`cairn config` to verify; `cairn serve` for the MCP server.");
    ExitCode::SUCCESS
}

/// OS CSPRNG bytes for key material; None when the source is unavailable.
fn os_random_bytes(out: &mut [u8]) -> Option<()> {
    #[cfg(unix)]
    {
        use std::io::Read as _;
        std::fs::File::open("/dev/urandom")
            .and_then(|mut f| f.read_exact(out))
            .ok()?;
        Some(())
    }
    #[cfg(windows)]
    {
        #[link(name = "bcrypt")]
        unsafe extern "system" {
            fn BCryptGenRandom(
                halgorithm: *mut core::ffi::c_void,
                pbbuffer: *mut u8,
                cbbuffer: u32,
                dwflags: u32,
            ) -> i32;
        }
        const BCRYPT_USE_SYSTEM_PREFERRED_RNG: u32 = 0x0000_0002;
        let status = unsafe {
            BCryptGenRandom(
                core::ptr::null_mut(),
                out.as_mut_ptr(),
                out.len() as u32,
                BCRYPT_USE_SYSTEM_PREFERRED_RNG,
            )
        };
        (status == 0).then_some(())
    }
}

/// Create the persisted API key once (0600 on Unix, never overwrite).
fn ensure_api_key() {
    let key_file = store_paths::current_cairn_home().join("mcp-api-key");
    if key_file.exists() {
        return;
    }
    if let Some(parent) = key_file.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let mut bytes = [0u8; 32];
    if os_random_bytes(&mut bytes).is_none() {
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
    #[cfg(unix)]
    let mut options = {
        use std::os::unix::fs::OpenOptionsExt as _;
        let mut o = std::fs::OpenOptions::new();
        o.mode(0o600);
        o
    };
    #[cfg(not(unix))]
    let mut options = std::fs::OpenOptions::new();
    if let Ok(mut file) = options.write(true).create_new(true).open(&key_file) {
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
        let scip = match cairn_build::config::load_config(&store.workspace) {
            Ok(cfg) => scip_config_json(&cfg),
            Err(message) => return command_error(&message),
        };
        object.insert("scip".into(), scip);
        println!("{}", json_compat::dumps_compact(&Value::Object(object)));
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
        println!("{}", json_compat::dumps(&Value::Object(snippet)));
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
                    "embed:      {}  ⚠ hash fallback (token-overlap vectors)",
                    env.model
                );
                println!(
                    "            for real vectors: CAIRN_EMBED_BACKEND=omlx | ollama | server (+ CAIRN_EMBED_BASE_URL)"
                );
            } else if backend == cairn_query::tools_search::EffectiveBackend::Hash {
                println!("embed:      {}  (CAIRN_EMBED_BACKEND=hash)", env.model);
            } else if backend == cairn_query::tools_search::EffectiveBackend::Native {
                println!("embed:      {}  (CAIRN_EMBED_BACKEND=native)", env.model);
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

    let scip_cfg = match cairn_build::config::load_config(&store.workspace) {
        Ok(cfg) => cfg,
        Err(message) => return command_error(&message),
    };
    for line in scip_config_lines(&scip_cfg) {
        println!("{line}");
    }

    println!();
    println!("MCP config for an agent (URL + key, needs the daemon):");
    println!("  cairn config --mcp-config");
    ExitCode::SUCCESS
}

/// The `scip` block of `cairn config --json`: {} or the configured
/// indexes/generated/commands fragments.
fn scip_config_json(cfg: &cairn_build::config::CairnConfig) -> Value {
    let mut scip = Map::new();
    if !cfg.scip_indexes.is_empty() {
        let mut indexes = Map::new();
        for (lang, path) in &cfg.scip_indexes {
            indexes.insert(lang.clone(), Value::from(path.clone()));
        }
        scip.insert("indexes".into(), Value::Object(indexes));
    }
    if !cfg.scip_generated.is_empty() {
        let mut langs = cfg.scip_generated.clone();
        langs.sort();
        scip.insert(
            "generated".into(),
            Value::Array(langs.into_iter().map(Value::from).collect()),
        );
    }
    if !cfg.scip_commands.is_empty() {
        let mut commands = Map::new();
        for (lang, argv) in &cfg.scip_commands {
            commands.insert(
                lang.clone(),
                Value::Array(argv.iter().map(|part| Value::from(part.clone())).collect()),
            );
        }
        scip.insert("commands".into(), Value::Object(commands));
    }
    Value::Object(scip)
}

/// The scip section lines of `cairn config` (text display).
fn scip_config_lines(cfg: &cairn_build::config::CairnConfig) -> Vec<String> {
    if cfg.scip_indexes.is_empty() && cfg.scip_generated.is_empty() && cfg.scip_commands.is_empty()
    {
        return vec![
            "scip indexes:".to_string(),
            "  (none configured — SCIP edges stay off)".to_string(),
        ];
    }
    let mut lines = Vec::new();
    if !cfg.scip_indexes.is_empty() {
        lines.push("scip indexes:".to_string());
        let mut sorted = cfg.scip_indexes.clone();
        sorted.sort();
        for (lang, index_path) in sorted {
            lines.push(format!("  {lang} -> {index_path}"));
        }
    }
    if !cfg.scip_generated.is_empty() {
        lines.push("scip generated:".to_string());
        let mut sorted = cfg.scip_generated.clone();
        sorted.sort();
        for lang in sorted {
            lines.push(format!("  {lang}"));
        }
    }
    if !cfg.scip_commands.is_empty() {
        lines.push("scip commands:".to_string());
        let mut sorted = cfg.scip_commands.clone();
        sorted.sort();
        for (lang, argv) in sorted {
            lines.push(format!("  {lang} -> {}", argv.join(" ")));
        }
    }
    lines
}

/// Parsed `cairn.json` (repo_namespaces/taint) for CLI consumers; scip keys
/// come from [`cairn_build::config::load_config`].
pub struct WorkspaceConfig {
    pub repo_namespaces: Option<Vec<(String, String)>>,
    pub taint_sources: Vec<(String, Vec<String>)>,
    pub taint_sinks: Vec<(String, Vec<String>)>,
}

/// Load `cairn.json` from the workspace root; defaults when absent or invalid.
pub fn load_config(workspace: PathBuf) -> WorkspaceConfig {
    let path = workspace.join("cairn.json");
    let empty = WorkspaceConfig {
        repo_namespaces: None,
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
    WorkspaceConfig {
        repo_namespaces,
        taint_sources: name_table("sources"),
        taint_sinks: name_table("sinks"),
    }
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
    let started = Instant::now();
    let checkpoint = |path: &Path| {
        if let Ok(conn) = Connection::open(path) {
            let _ = conn.execute_batch("PRAGMA wal_checkpoint(TRUNCATE);");
        }
    };
    let closure = |path: &Path| -> Result<i64, String> {
        Connection::open(path)
            .map_err(|e| e.to_string())
            .and_then(|mut conn| {
                cairn_build::closure::build_transitive_closure(&mut conn, 4)
                    .map(|outcome| outcome.rows as i64)
            })
            .map_err(|message| format!("closure build failed: {message}"))
    };
    let empty_refusal = format!(
        "no source files found under {workspace} — refusing to swap an empty build over {db}"
    );
    let mut staged_closure_rows: Option<i64> = None;

    let mut rail = Rail::new("Building graph");
    rail.start("Scanning files");
    let built = if args.staging {
        // Closure and checkpoint land in the staged store, under the lock, before the swap.
        cairn_build::builder::build_graph_with(&workspace, &db, None, None, |staged, summary| {
            if summary.files == 0 {
                return Err(empty_refusal.clone());
            }
            if args.with_closure {
                staged_closure_rows = Some(closure(staged)?);
            }
            checkpoint(staged);
            Ok(())
        })
    } else {
        cairn_build::builder::build_graph(&workspace, &db, args.repo.as_deref(), None)
    };
    match built {
        Err(message) => {
            rail.fail_close();
            command_error(&message)
        }
        Ok((summary, _timings)) => {
            if args.staging && summary.files == 0 {
                rail.fail_close();
                return command_error(&empty_refusal);
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
                let rows = match staged_closure_rows {
                    Some(rows) => Ok(rows),
                    None => closure(Path::new(&db)),
                };
                match rows {
                    Ok(rows) => rail.finish(&format!("{} edges", fmt_int(rows))),
                    Err(message) => {
                        rail.fail_close();
                        return command_error(&message);
                    }
                }
            }
            if !args.staging {
                checkpoint(Path::new(&db));
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
            if let Some(scip) = &summary.scip
                && (scip.edges > 0 || scip.stale_files > 0 || scip.join_anomalies > 0)
            {
                let mut scip_text = format!(
                    "{} edges ({} disagreements)",
                    fmt_int(scip.edges as i64),
                    fmt_int(scip.disagreements as i64)
                );
                if scip.stale_files > 0 {
                    scip_text.push_str(&format!(", {} stale", fmt_int(scip.stale_files as i64)));
                }
                kv_pairs.push(("scip".into(), scip_text));
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
        "scip: {} edges ({} disagreements, {} join anomalies) matched {}/{} documents{} from {}",
        record.edges,
        record.disagreements,
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
    let resolution = s.resolution;
    let external_note = if resolution.external > 0 {
        format!(" / {} external", fmt_int(resolution.external))
    } else {
        String::new()
    };
    display::kv(
        "resolution",
        &format!(
            "{} exact / {} ambiguous / {} unresolved{} ({} / {} of resolved)",
            fmt_int(resolution.exact),
            fmt_int(resolution.ambiguous),
            fmt_int(resolution.unresolved),
            external_note,
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
pub(crate) fn human_bytes(mut n: f64) -> String {
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
        } else {
            display::success(&format!("Reindexed {rel_path} in repo {repo}"));
        }
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
            json_compat::dumps(&Value::Array(rows.into_iter().map(Value::Object).collect()))
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
            json_compat::dumps(&Value::Array(rows.into_iter().map(Value::Object).collect()))
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
            json_compat::dumps(&Value::Array(rows.into_iter().map(Value::Object).collect()))
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
            json_compat::dumps(&Value::Array(rows.into_iter().map(Value::Object).collect()))
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
    let collision = args.guard && !result.cycles.is_empty() && total > args.threshold;
    let fallback = if collision {
        match cairn_build::dataflow::get_dataflow(&conn, &args.symbol) {
            Ok(row) => row,
            Err(message) => return command_error(&message),
        }
    } else {
        None
    };
    if args.as_json {
        let mut object = Map::new();
        if !collision {
            object.insert("impacted".into(), Value::Array(impacted));
        }
        object.insert("cycles".into(), Value::Array(cycles));
        object.insert("total".into(), Value::from(total as i64));
        object.insert("truncated".into(), Value::from(result.truncated));
        if args.guard {
            let mut guard = Map::new();
            guard.insert("collision".into(), Value::from(collision));
            guard.insert("threshold".into(), Value::from(args.threshold as i64));
            if collision {
                let dataflow = fallback.as_ref().map_or(Value::Null, dataflow_json);
                guard.insert("dataflow".into(), dataflow);
            }
            object.insert("guard".into(), Value::Object(guard));
        }
        println!("{}", json_compat::dumps(&Value::Object(object)));
        if empty {
            return ExitCode::FAILURE;
        }
        return ExitCode::SUCCESS;
    }
    if collision {
        print_collision_warning(
            &conn,
            &args.symbol,
            total,
            &result.cycles,
            fallback.as_ref(),
        );
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

fn print_collision_warning(
    conn: &Connection,
    symbol: &str,
    total: usize,
    cycles: &[(String, i64)],
    fallback: Option<&cairn_build::dataflow::DataflowRow>,
) {
    let shown: Vec<String> = cycles
        .iter()
        .take(8)
        .map(|(s, _)| format!("'{s}'"))
        .collect();
    let more = if cycles.len() > 8 {
        format!(" ... and {} more", cycles.len() - 8)
    } else {
        String::new()
    };
    println!(
        "Warning: {symbol}: impact reports {} impacted symbols, but the traversal passed \
         through common/colliding names [{}]{more} -- this reads as a name-collision \
         artifact, not real coupling (Golden Rule 6). Not printing the raw dump.",
        fmt_int(total as i64),
        shown.join(", ")
    );
    println!("\nFalling back to the precomputed dataflow index:");
    match fallback {
        Some(row) => print_dataflow(row),
        None => println!(
            "{} Or re-run with a more specific qualified name.",
            missing_dataflow_message(conn, symbol)
        ),
    }
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
        println!("{}", json_compat::dumps(&Value::Object(object)));
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
    // RRF-fuse the per-store rankings, keeping fixed-precision rounding.
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
        println!("{}", json_compat::dumps(&Value::Object(object)));
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

// --- cairn semantic / embed ----------------------------------------------------

use cairn_query::tools_search::{self, EffectiveBackend};

/// The `_exit_backend_unavailable` arms: server-family probes name the server;
/// local backends name the install hint.
fn exit_backend_unavailable() -> ExitCode {
    let server_family = matches!(
        tools_search::backend_name().as_str(),
        "server" | "omlx" | "ollama"
    );
    if !server_family {
        display::error("Semantic dependencies unavailable");
        display::dim(&tools_search::install_hint());
        return ExitCode::FAILURE;
    }
    display::error("Embedding server unavailable");
    match tools_search::server_base_url(&tools_search::backend_name()) {
        Ok(base) => {
            let base = base.trim_end_matches('/');
            display::dim(
                format!(
                "Availability probe failed: GET {base}/models must return 200 and list model '{}'.",
                tools_search::server_model()
            )
            .as_str(),
            );
            display::dim(
                format!(
                "Start the embedding server at {base} (verify with `curl {base}/models`), then re-run; `cairn doctor` checks probe, parity, and latency."
            )
            .as_str(),
            );
        }
        Err(message) => {
            display::dim(&message);
            display::dim("Run `cairn doctor` for embedding backend diagnostics.");
        }
    }
    ExitCode::FAILURE
}

fn embed_count(conn: &Connection, model: &str) -> i64 {
    conn.query_row(
        "SELECT COUNT(*) FROM embeddings WHERE model = ?1",
        rusqlite::params![model],
        |r| r.get(0),
    )
    .unwrap_or(0)
}

/// Pre-warm handler: download the native model and exit without embedding;
/// feature-off builds fail naming the cargo feature.
fn embed_download_model() -> ExitCode {
    match tools_search::native_warm_model() {
        Ok(()) => {
            display::info(
                "bge-m3 model weights cached; native embedding ready \
                 (CAIRN_EMBED_BACKEND=native)",
            );
            ExitCode::SUCCESS
        }
        Err(message) => {
            display::error("Model download unavailable");
            display::dim(&message);
            ExitCode::FAILURE
        }
    }
}

/// semantic handler.
fn semantic(args: crate::cmds_core::SemanticArgs) -> ExitCode {
    let env = tools_search::semantic_env(None);
    if env.backend == EffectiveBackend::Unavailable {
        return exit_backend_unavailable();
    }
    let db = db_arg(&args.db);
    let Ok(conn) = cairn_store::schema::open_initialized(Path::new(&db)) else {
        return command_error(&format!("cannot open {db}"));
    };
    if embed_count(&conn, &env.model) == 0 {
        display::warning(&format!(
            "Semantic index is empty under model '{}'. Run `cairn embed` once to index, then retry.",
            env.model
        ));
        return ExitCode::FAILURE;
    }
    let rows = tools_search::semantic_search(&conn, &args.query, args.limit, args.threshold, &env);
    let rows_json: Vec<Value> = rows
        .iter()
        .map(|row| {
            let mut map = Map::new();
            map.insert("id".into(), Value::from(row.id.clone()));
            map.insert("name".into(), Value::from(row.name.clone()));
            map.insert("kind".into(), Value::from(row.kind.clone()));
            map.insert(
                "qualified_name".into(),
                row.qualified_name
                    .clone()
                    .map(Value::from)
                    .unwrap_or(Value::Null),
            );
            map.insert("file_path".into(), Value::from(row.file_path.clone()));
            map.insert("repo".into(), Value::from(row.repo.clone()));
            map.insert("score".into(), number_value(row.score));
            map.insert("chunk".into(), Value::from(row.chunk.clone()));
            map.insert("provenance".into(), Value::from(row.provenance.clone()));
            map.insert("reranked".into(), Value::from(row.reranked));
            if row.reranked {
                map.insert("rerank_score".into(), number_value(row.score));
            }
            if args.include_callers {
                let callers = attach_neighbors(&conn, &row.name, true);
                let callees = attach_neighbors(&conn, &row.name, false);
                map.insert("callers".into(), Value::Array(callers));
                map.insert("callees".into(), Value::Array(callees));
            }
            Value::Object(map)
        })
        .collect();
    if args.as_json {
        println!("{}", json_compat::dumps(&Value::Array(rows_json)));
        return ExitCode::SUCCESS;
    }
    if rows.is_empty() {
        display::warning(&format!(
            "No semantic matches for '{}' (threshold {})",
            args.query, args.threshold
        ));
        return ExitCode::FAILURE;
    }
    let mut out = vec![format!(
        "{} semantic match(es) for '{}':",
        rows.len(),
        args.query
    )];
    for row in &rows {
        let short = row.file_path.rsplit('/').next().unwrap_or("");
        let score_label = if row.reranked {
            format!("rerank {:.2}", row.score)
        } else {
            format!("{:.2}", row.score)
        };
        out.push(format!(
            "  [{score_label}] {} {}  ({short})",
            row.kind,
            row.qualified_name.as_deref().unwrap_or(&row.name),
        ));
    }
    println!("{}", out.join("\n"));
    ExitCode::SUCCESS
}

/// Float json rendering (repr keeps shortest round-trip form).
fn number_value(value: f64) -> Value {
    serde_json::Number::from_f64(value)
        .map(Value::Number)
        .unwrap_or(Value::Null)
}

fn attach_neighbors(conn: &Connection, name: &str, callers: bool) -> Vec<Value> {
    let caller_rows = callers
        .then(|| cairn_query::graph::get_callers(conn, name, false, 5, None).unwrap_or_default());
    let callee_rows = (!callers)
        .then(|| cairn_query::graph::get_callees(conn, name, false, 5, None).unwrap_or_default());
    let mut out = Vec::new();
    if let Some(rows) = caller_rows {
        for row in rows.iter().take(5) {
            let mut map = Map::new();
            map.insert("name".into(), Value::from(row.caller_name.clone()));
            map.insert("kind".into(), Value::from(row.caller_kind.clone()));
            map.insert("file_path".into(), Value::from(row.file_path.clone()));
            out.push(Value::Object(map));
        }
    }
    if let Some(rows) = callee_rows {
        for row in rows.iter().take(5) {
            let Some(callee_name) = &row.callee_name else {
                continue;
            };
            let mut map = Map::new();
            map.insert("name".into(), Value::from(callee_name.clone()));
            map.insert("kind".into(), Value::from(row.callee_kind.clone()));
            map.insert("file_path".into(), Value::from(row.file_path.clone()));
            out.push(Value::Object(map));
        }
    }
    out
}

/// embed handler.
fn embed(args: crate::cmds_core::EmbedArgs) -> ExitCode {
    if args.download_model {
        return embed_download_model();
    }
    let env = tools_search::semantic_env(None);
    if env.backend == EffectiveBackend::Unavailable {
        return exit_backend_unavailable();
    }
    if env.native_cold {
        display::warning(
            "Using the hash embedder: the native model is not cached, so retrieval is degraded.",
        );
        display::dim(
            "For real vectors run: CAIRN_EMBED_BACKEND=native cairn embed --download-model, \
             then re-run cairn embed",
        );
    } else if env.hash_fallback {
        display::warning(
            "Using the hash embedder (dep-free): token-overlap vectors only, so retrieval is \
             degraded.",
        );
        display::dim(
            "For real vectors point CAIRN_EMBED_BACKEND at a local OpenAI-compatible embeddings \
             server:",
        );
        display::dim(
            "  CAIRN_EMBED_BACKEND=omlx | ollama | server (server needs CAIRN_EMBED_BASE_URL), \
             then re-run: cairn embed",
        );
        display::dim("Or set CAIRN_EMBED_BACKEND=hash explicitly to silence this warning.");
    }
    let db = db_arg(&args.db);
    let workspace = store_paths::resolve_workspace(None);
    let Ok(conn) = cairn_store::schema::open_initialized(Path::new(&db)) else {
        return command_error(&format!("cannot open {db}"));
    };
    let sym_count: i64 = conn
        .query_row("SELECT COUNT(*) FROM symbols", [], |r| r.get(0))
        .unwrap_or(0);
    display::info(&format!(
        "Embedding {} symbols with {}",
        fmt_int(sym_count),
        env.model
    ));
    let before = embed_count(&conn, &env.model);
    let multivector = !args.no_multivector;

    let build_ann = args.build_index || tools_search::ann_backend_enabled();
    let timer = Instant::now();
    crate::cmds_lifecycle::plain_progress_draw("Embedding", 0, 0, 0.0, "symbols");
    let summary = match cairn_build::embeddings::embed_all(
        &conn,
        &workspace.to_string_lossy(),
        args.batch_size,
        args.limit,
        !args.no_reap,
        multivector,
    ) {
        Ok(summary) => summary,
        Err(message) => return command_error(&message),
    };
    let ann_outcome = if build_ann {
        // The bar force-draws the description change, then stop() draws
        // again after the rebuild: two indeterminate `ANN index` segments.
        crate::cmds_lifecycle::plain_progress_draw(
            "ANN index",
            0,
            0,
            timer.elapsed().as_secs_f64(),
            "symbols",
        );
        let base = cairn_store::vec0::rebuild_index(
            &conn,
            &env.model,
            cairn_store::vec0::VecSource::Embeddings,
        )
        .ok();
        let mv = if multivector {
            cairn_store::vec0::rebuild_index(
                &conn,
                &env.model,
                cairn_store::vec0::VecSource::EmbeddingsMv,
            )
            .ok()
        } else {
            None
        };
        crate::cmds_lifecycle::plain_progress_draw(
            "ANN index",
            0,
            0,
            timer.elapsed().as_secs_f64(),
            "symbols",
        );
        println!();
        Some((base, mv))
    } else {
        crate::cmds_lifecycle::plain_progress_draw(
            "Embedding",
            summary.embedded,
            summary.attempted,
            timer.elapsed().as_secs_f64(),
            "symbols",
        );
        println!();
        None
    };
    let elapsed = timer.elapsed().as_secs_f64();
    let after = embed_count(&conn, &env.model);

    let mut kv_pairs: Vec<(&str, String)> = vec![
        ("embedded", fmt_int(summary.embedded as i64)),
        ("skipped", fmt_int(summary.skipped as i64)),
        ("reaped", fmt_int(summary.reaped as i64)),
        ("failed batches", fmt_int(summary.failed_batches as i64)),
        ("model", summary.model.clone()),
        (
            "vectors",
            format!("{} -> {}", fmt_int(before), fmt_int(after)),
        ),
    ];
    if multivector {
        kv_pairs.push((
            "mv vectors",
            fmt_int(summary.mv_embedded.unwrap_or(0) as i64),
        ));
    }
    rail::summary_panel(
        &format!(
            "Embedded {} symbols in {elapsed:.1}s",
            fmt_int(summary.embedded as i64)
        ),
        &kv_pairs,
        None,
    );

    if let Some((base, mv)) = ann_outcome {
        for (label, outcome) in [("ANN index", base), ("MV ANN index", mv)] {
            match outcome {
                Some(cairn_store::vec0::RebuildOutcome::Built {
                    indexed,
                    dim,
                    model,
                    ..
                }) => {
                    display::success(&format!(
                        "{label} rebuilt: {} vectors, dim={dim}, model='{model}'",
                        fmt_int(indexed)
                    ));
                }
                Some(cairn_store::vec0::RebuildOutcome::Skipped { reason, .. }) => {
                    display::warning(&format!("{label} not built: {reason}"));
                }
                None => {}
            }
        }
    }

    if std::env::var("CAIRN_TELEMETRY")
        .map(|v| !v.trim().eq_ignore_ascii_case("off"))
        .unwrap_or(true)
    {
        let started = std::time::SystemTime::now() - timer.elapsed();
        crate::cmds_lifecycle::record_build_run(
            &db,
            "embed",
            started,
            elapsed,
            None,
            Some(summary.embedded as i64),
            summary.skipped,
        );
    }
    ExitCode::SUCCESS
}

// --- cairn grep -----------------------------------------------------------------

/// grep handler.
fn grep(args: crate::cmds_core::GrepArgs) -> ExitCode {
    let db = db_arg(&args.db);
    let refresh = refresh_flag(args.refresh, args.no_refresh);
    let Ok(conn) = fresh_conn(&db, refresh) else {
        return command_error(&format!("cannot open {db}"));
    };
    let workspace = store_paths::resolve_workspace(None);
    let workspace_str = workspace.to_string_lossy().into_owned();
    let payload = match cairn_query::grep::graph_grep(
        &conn,
        &args.pattern,
        args.ignore_case,
        args.fixed,
        &args.path_prefix,
        args.max_hits,
        &|repo, stored| cairn_build::scanner::resolve_file_path(&workspace_str, repo, stored),
    ) {
        Ok(payload) => payload,
        Err(message) => return usage_error(&message),
    };
    if args.as_json {
        println!("{}", json_compat::dumps(&payload));
        return ExitCode::SUCCESS;
    }
    println!("{}", grep_text(&payload));
    ExitCode::SUCCESS
}

fn grep_text(payload: &Value) -> String {
    let mut lines = vec![format!(
        "searched files: {}",
        payload["searched_files"].as_i64().unwrap_or(0)
    )];
    let unreadable = payload["unreadable_files"].as_i64().unwrap_or(0);
    if unreadable > 0 {
        lines.push(format!("unreadable files: {unreadable}"));
    }
    for group in payload["groups"].as_array().unwrap_or(&Vec::new()) {
        let name = group["qualified_name"].as_str().unwrap_or("<file>");
        lines.push(format!(
            "{}:{} {} (in-degree {})",
            group["repo"].as_str().unwrap_or_default(),
            group["path"].as_str().unwrap_or_default(),
            name,
            group["incoming"].as_i64().unwrap_or(0)
        ));
        for hit in group["hits"].as_array().unwrap_or(&Vec::new()) {
            lines.push(format!(
                "  {}:{} {}",
                hit["line"].as_i64().unwrap_or(0),
                hit["column"].as_i64().unwrap_or(0),
                hit["text"].as_str().unwrap_or_default()
            ));
        }
    }
    lines.push(format!(
        "dropped hits: {}",
        payload["dropped_hits"].as_i64().unwrap_or(0)
    ));
    lines.push(format!(
        "dropped groups: {}",
        payload["dropped_groups"].as_i64().unwrap_or(0)
    ));
    if payload["truncated"].as_bool().unwrap_or(false) {
        lines.push("scan stopped at the hit cap; dropped counts are lower bounds".to_string());
    }
    lines.join("\n")
}

// --- cairn map ------------------------------------------------------------------

use cairn_query::tools_repo::{
    CLI_DEFAULT_CLUSTER_CAP, CLI_DEFAULT_HOTSPOT_CAP, CLI_DEFAULT_HUB_CAP,
};

/// map handler.
fn map(args: crate::cmds_core::MapArgs) -> ExitCode {
    let db = db_arg(&args.db);
    if !Path::new(&db).exists() {
        return command_error(&format!("no graph DB at {db} — run `cairn build` first"));
    }
    let refresh = refresh_flag(args.refresh, args.no_refresh);
    let Ok(conn) = fresh_conn(&db, refresh) else {
        return command_error(&format!("cannot open {db}"));
    };
    let payload = match cairn_query::tools_repo::repo_map_payload(
        &conn,
        args.max_clusters,
        args.max_hubs,
        args.max_hotspots,
    ) {
        Ok(payload) => payload,
        Err(e) => return command_error(&e.to_string()),
    };
    if args.as_json {
        println!("{}", json_compat::dumps(&payload));
        return ExitCode::SUCCESS;
    }
    println!("{}", map_text(&payload));
    ExitCode::SUCCESS
}

fn map_text(payload: &Value) -> String {
    let mut lines: Vec<String> = Vec::new();
    for scope in payload["repos"].as_array().unwrap_or(&Vec::new()) {
        lines.push(format!(
            "Repository {}",
            scope["repo"].as_str().unwrap_or_default()
        ));
        for cluster in scope["clusters"].as_array().unwrap_or(&Vec::new()) {
            lines.push(format!(
                "  {}: {} files, {} symbols, {} edges",
                cluster["path"].as_str().unwrap_or_default(),
                cluster["files"].as_i64().unwrap_or(0),
                cluster["symbols"].as_i64().unwrap_or(0),
                cluster["edges"].as_i64().unwrap_or(0),
            ));
            for hub in cluster["hubs"].as_array().unwrap_or(&Vec::new()) {
                lines.push(format!(
                    "    hub {} ({}, in-degree {})",
                    hub["qualified_name"].as_str().unwrap_or_default(),
                    hub["path"].as_str().unwrap_or_default(),
                    hub["incoming"].as_i64().unwrap_or(0),
                ));
            }
            lines.push(format!(
                "    dropped hubs: {}",
                cluster["dropped_hubs"].as_i64().unwrap_or(0)
            ));
        }
        lines.push(format!(
            "  dropped clusters: {}",
            scope["dropped_clusters"].as_i64().unwrap_or(0)
        ));
    }
    lines.push("Workspace hotspots".to_string());
    for hotspot in payload["hotspots"].as_array().unwrap_or(&Vec::new()) {
        lines.push(format!(
            "  {}:{} {} (in-degree {})",
            hotspot["repo"].as_str().unwrap_or_default(),
            hotspot["path"].as_str().unwrap_or_default(),
            hotspot["qualified_name"].as_str().unwrap_or_default(),
            hotspot["incoming"].as_i64().unwrap_or(0),
        ));
    }
    lines.push(format!(
        "  dropped hotspots: {}",
        payload["dropped_hotspots"].as_i64().unwrap_or(0)
    ));
    lines.join("\n")
}

// --- cairn rationale ---------------------------------------------------------------

/// rationale handler.
fn rationale(args: crate::cmds_core::RationaleArgs) -> ExitCode {
    let db = db_arg(&args.db);
    let conn = open_read_aware(&db);
    let Ok(conn) = conn else {
        return command_error(&format!("cannot open {db}"));
    };
    let records =
        match rationale_collect(&conn, args.symbol.as_deref(), args.file_pattern.as_deref()) {
            Ok(records) => records,
            Err(e) => return command_error(&e.to_string()),
        };
    if args.as_json {
        println!("{}", json_compat::dumps(&Value::Array(records)));
        return ExitCode::SUCCESS;
    }
    if records.is_empty() {
        println!("No rationale records found.");
        return ExitCode::SUCCESS;
    }
    let text = records
        .iter()
        .map(|r| {
            format!(
                "{}:{} [{}] {}",
                r["path"].as_str().unwrap_or_default(),
                r["line"].as_i64().unwrap_or(0),
                r["kind"].as_str().unwrap_or_default(),
                r["text"].as_str().unwrap_or_default()
            )
        })
        .collect::<Vec<_>>()
        .join("\n");
    println!("{text}");
    ExitCode::SUCCESS
}

/// Open honoring CAIRN_READ_ONLY without any refresh (get_db(read_only=...)).
fn open_read_aware(db: &str) -> Result<Connection, String> {
    let read_only = std::env::var("CAIRN_READ_ONLY")
        .map(|v| matches!(v.to_lowercase().as_str(), "1" | "true" | "yes"))
        .unwrap_or(false);
    if read_only {
        cairn_store::conn::open_read_only(Path::new(db)).map_err(|e| e.to_string())
    } else {
        cairn_store::schema::open_initialized(Path::new(db)).map_err(|e| e.to_string())
    }
}

#[derive(Clone)]
struct RationaleRecord {
    file_id: String,
    symbol_id: Option<String>,
    line: i64,
    kind: String,
    text: String,
    path: Option<String>,
}

impl RationaleRecord {
    fn json(&self) -> Value {
        let mut map = Map::new();
        map.insert("file_id".into(), Value::from(self.file_id.clone()));
        map.insert(
            "symbol_id".into(),
            self.symbol_id
                .clone()
                .map(Value::from)
                .unwrap_or(Value::Null),
        );
        map.insert("line".into(), Value::from(self.line));
        map.insert("kind".into(), Value::from(self.kind.clone()));
        map.insert("text".into(), Value::from(self.text.clone()));
        map.insert(
            "path".into(),
            self.path.clone().map(Value::from).unwrap_or(Value::Null),
        );
        Value::Object(map)
    }
}

fn rationale_collect(
    conn: &Connection,
    symbol: Option<&str>,
    file_pattern: Option<&str>,
) -> Result<Vec<Value>, String> {
    let file_ids = match_file_ids(conn, file_pattern)?;
    let mut records: Vec<RationaleRecord> = if let Some(symbol) = symbol {
        let symbol_ids: Vec<String> = cairn_query::graph::resolve_pattern_symbols(conn, symbol)
            .map_err(|e| e.to_string())?
            .into_iter()
            .map(|row| row.id)
            .collect();
        let mut records = records_for_symbol_ids(conn, &symbol_ids)?;
        if file_pattern.is_some() {
            records.retain(|r| file_ids.contains(&r.file_id));
        }
        records
    } else if let Some(_pattern) = file_pattern {
        let mut records = Vec::new();
        for fid in file_path_sorted_ids(conn, &file_ids)? {
            records.extend(records_for_file(conn, &fid)?);
        }
        records
    } else {
        let mut records = Vec::new();
        for fid in all_file_ids(conn)? {
            records.extend(records_for_file(conn, &fid)?);
        }
        records
    };
    let paths = file_paths(conn, &records)?;
    for record in &mut records {
        record.path = paths.get(&record.file_id).cloned();
    }
    records.sort_by(|a, b| {
        a.path
            .as_deref()
            .unwrap_or("")
            .cmp(b.path.as_deref().unwrap_or(""))
            .then_with(|| a.line.cmp(&b.line))
            .then_with(|| a.kind.cmp(&b.kind))
            .then_with(|| a.text.cmp(&b.text))
    });
    Ok(records.iter().map(RationaleRecord::json).collect())
}

fn match_file_ids(conn: &Connection, pattern: Option<&str>) -> Result<HashSet<String>, String> {
    let Some(pattern) = pattern else {
        return Ok(HashSet::new());
    };
    let mut stmt = conn
        .prepare("SELECT id FROM files WHERE instr(lower(path), lower(?1)) > 0 ORDER BY path")
        .map_err(|e| e.to_string())?;
    let mapped = stmt
        .query_map(rusqlite::params![pattern], |r| r.get::<_, String>(0))
        .map_err(|e| e.to_string())?;
    mapped
        .collect::<Result<HashSet<_>, _>>()
        .map_err(|e| e.to_string())
}

fn file_path_sorted_ids(conn: &Connection, ids: &HashSet<String>) -> Result<Vec<String>, String> {
    let mut pairs: Vec<(String, String)> = Vec::new();
    for chunk in ids.iter().collect::<Vec<_>>().chunks(400) {
        let placeholders = std::iter::once("?".to_string())
            .cycle()
            .take(chunk.len())
            .collect::<Vec<_>>()
            .join(",");
        let sql = format!("SELECT id, path FROM files WHERE id IN ({placeholders})");
        let params: Vec<&dyn rusqlite::ToSql> =
            chunk.iter().map(|id| id as &dyn rusqlite::ToSql).collect();
        let mut stmt = conn.prepare(&sql).map_err(|e| e.to_string())?;
        let mapped = stmt
            .query_map(params.as_slice(), |r| {
                Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?))
            })
            .map_err(|e| e.to_string())?;
        for row in mapped {
            pairs.push(row.map_err(|e| e.to_string())?);
        }
    }
    pairs.sort_by(|a, b| a.1.cmp(&b.1));
    Ok(pairs.into_iter().map(|(id, _)| id).collect())
}

fn all_file_ids(conn: &Connection) -> Result<Vec<String>, String> {
    let mut stmt = conn
        .prepare("SELECT id FROM files ORDER BY path")
        .map_err(|e| e.to_string())?;
    let mapped = stmt
        .query_map([], |r| r.get::<_, String>(0))
        .map_err(|e| e.to_string())?;
    mapped.collect::<Result<_, _>>().map_err(|e| e.to_string())
}

fn records_for_file(conn: &Connection, file_id: &str) -> Result<Vec<RationaleRecord>, String> {
    let mut stmt = conn
        .prepare(
            "SELECT file_id, symbol_id, line, kind, text FROM rationale \
             WHERE file_id = ?1 ORDER BY line, kind",
        )
        .map_err(|e| e.to_string())?;
    let mapped = stmt
        .query_map(rusqlite::params![file_id], rationale_row)
        .map_err(|e| e.to_string())?;
    mapped.collect::<Result<_, _>>().map_err(|e| e.to_string())
}

fn records_for_symbol_ids(
    conn: &Connection,
    ids: &[String],
) -> Result<Vec<RationaleRecord>, String> {
    let mut rows: Vec<RationaleRecord> = Vec::new();
    for chunk in ids.chunks(400) {
        let placeholders = std::iter::once("?".to_string())
            .cycle()
            .take(chunk.len())
            .collect::<Vec<_>>()
            .join(",");
        let sql = format!(
            "SELECT file_id, symbol_id, line, kind, text FROM rationale \
             WHERE symbol_id IN ({placeholders}) ORDER BY line, symbol_id, kind"
        );
        let params: Vec<&dyn rusqlite::ToSql> =
            chunk.iter().map(|id| id as &dyn rusqlite::ToSql).collect();
        let mut stmt = conn.prepare(&sql).map_err(|e| e.to_string())?;
        let mapped = stmt
            .query_map(params.as_slice(), rationale_row)
            .map_err(|e| e.to_string())?;
        for row in mapped {
            rows.push(row.map_err(|e| e.to_string())?);
        }
    }
    rows.sort_by(|a, b| {
        a.line
            .cmp(&b.line)
            .then_with(|| a.symbol_id.cmp(&b.symbol_id))
            .then_with(|| a.kind.cmp(&b.kind))
    });
    Ok(rows)
}

fn rationale_row(r: &rusqlite::Row<'_>) -> rusqlite::Result<RationaleRecord> {
    Ok(RationaleRecord {
        file_id: r.get(0)?,
        symbol_id: r.get(1)?,
        line: r.get(2)?,
        kind: r.get(3)?,
        text: r.get(4)?,
        path: None,
    })
}

fn file_paths(
    conn: &Connection,
    records: &[RationaleRecord],
) -> Result<HashMap<String, String>, String> {
    let ids: HashSet<String> = records.iter().map(|r| r.file_id.clone()).collect();
    let mut paths = HashMap::new();
    for chunk in ids.iter().collect::<Vec<_>>().chunks(400) {
        let placeholders = std::iter::once("?".to_string())
            .cycle()
            .take(chunk.len())
            .collect::<Vec<_>>()
            .join(",");
        let sql = format!("SELECT id, path FROM files WHERE id IN ({placeholders})");
        let params: Vec<&dyn rusqlite::ToSql> =
            chunk.iter().map(|id| id as &dyn rusqlite::ToSql).collect();
        let mut stmt = conn.prepare(&sql).map_err(|e| e.to_string())?;
        let mapped = stmt
            .query_map(params.as_slice(), |r| {
                Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?))
            })
            .map_err(|e| e.to_string())?;
        for row in mapped {
            let (id, path) = row.map_err(|e| e.to_string())?;
            paths.insert(id, path);
        }
    }
    Ok(paths)
}

// --- cairn blast -------------------------------------------------------------------

/// Refresh drift and return the stale-graph banner ("" when current).
/// Refresh between the deleted and live seed legs; returns the stale-graph
/// banner ("" when current) and errors on a failed strict repair.
fn refresh_with_banner(
    conn: &mut Connection,
    db: &str,
    refresh: Option<bool>,
) -> Result<String, String> {
    match refresh_probe(conn, db, refresh)? {
        Some(stale) => Ok(stale.0),
        None => Ok(String::new()),
    }
}

/// blast handler.
fn blast(args: crate::cmds_core::BlastArgs) -> ExitCode {
    let db = db_arg(&args.db);
    let refresh = refresh_flag(args.refresh, args.no_refresh);
    let ws = store_paths::resolve_workspace(None);
    let config = load_config(ws.clone());
    let registry = cairn_query::graph::merged_registry(&config.taint_sources, &config.taint_sinks);
    let Ok(mut conn) = cairn_store::schema::open_initialized(Path::new(&db)) else {
        return command_error(&format!("cannot read store {db}"));
    };
    let workspace_str = ws.to_string_lossy().into_owned();
    let mut refresh_fn = |conn: &mut Connection| refresh_with_banner(conn, &db, refresh);
    let result = match cairn_query::blast::compute_blast(
        &mut conn,
        &workspace_str,
        args.base.as_deref(),
        args.fuzzy,
        500,
        &registry,
        &mut refresh_fn,
    ) {
        Ok(result) => result,
        Err(message) => return command_error(&message),
    };
    let rendered = if matches!(args.output_format, BlastFormat::Json) {
        json_compat::dumps_sorted(&result.to_json())
    } else {
        let mut rendered = cairn_query::blast::render_blast(&result, args.output_format.as_str())
            .unwrap_or_default();
        if matches!(
            args.output_format,
            BlastFormat::Text | BlastFormat::Markdown
        ) {
            let warning = cairn_query::blast::taint_warning(&result.taint_paths);
            if !warning.is_empty() {
                rendered = format!("{rendered}\n{warning}\n");
            }
        }
        rendered
    };
    match &args.output {
        None => println!("{rendered}"),
        Some(path) => {
            if let Err(e) = std::fs::write(path, rendered.as_bytes()) {
                return command_error(&format!("cannot write {path}: {e}"));
            }
        }
    }
    ExitCode::SUCCESS
}

// --- cairn ask / context / review --------------------------------------------

/// Click's UsageError shape: usage block, help hint, blank line, message.
fn click_usage_error(usage: &str, command: &str, message: &str) -> ExitCode {
    eprintln!("Usage: {usage}");
    eprintln!("Try 'cairn {command} --help' for help.");
    eprintln!();
    eprintln!("Error: {message}");
    ExitCode::from(2)
}

/// The route_query dict as `json.dumps(result, indent=2)` renders it.
fn route_json(outcome: &cairn_query::tools_km::RouteOutcome) -> Value {
    let mut results = Map::new();
    for (layer, data) in &outcome.results {
        results.insert(layer.clone(), json_compat::yaml_to_value(data));
    }
    let mut object = Map::new();
    object.insert("intent".into(), Value::from(outcome.intent));
    object.insert("layer".into(), Value::from(outcome.layer));
    object.insert(
        "layers_queried".into(),
        Value::Array(
            outcome
                .layers_queried
                .iter()
                .cloned()
                .map(Value::from)
                .collect(),
        ),
    );
    object.insert("results".into(), Value::Object(results));
    if outcome.degraded {
        object.insert("degraded".into(), Value::from(true));
    }
    object.insert("empty".into(), Value::from(outcome.empty));
    Value::Object(object)
}

/// `_echo_route_result`: intent header, layers, one section per layer.
fn print_route(outcome: &cairn_query::tools_km::RouteOutcome) {
    use yaml_rust2::Yaml;
    println!("Intent: {} (routed to {})", outcome.intent, outcome.layer);
    println!("Layers queried: {}", outcome.layers_queried.join(", "));
    for (layer, data) in &outcome.results {
        println!("\n--- {layer} ---");
        match data {
            Yaml::Hash(hash) => {
                for (key, value) in hash {
                    println!(
                        "  {}: {}",
                        cairn_query::tools_km::router_display(key),
                        cairn_query::tools_km::router_display(value)
                    );
                }
            }
            Yaml::Array(items) => {
                for item in items {
                    println!("  {}", cairn_query::tools_km::router_display(item));
                }
            }
            _ => {}
        }
    }
}

/// ask handler: natural-language routing across the knowledge layers.
fn ask(args: crate::cmds_core::AskArgs) -> ExitCode {
    if args.question.trim().is_empty() {
        return click_usage_error(
            "cairn ask [OPTIONS] QUESTION",
            "ask",
            "Question must not be empty.",
        );
    }
    if args.all_repos {
        return ask_all_repos(&args);
    }
    let db = db_arg(&args.db);
    let knowledge = args.knowledge.clone().unwrap_or_else(knowledge_default);
    let Ok(conn) = cairn_store::schema::open_initialized(Path::new(&db)) else {
        return command_error(&format!("cannot open {db}"));
    };
    let bundle = cairn_query::tools_km::Bundle::new(knowledge);
    let outcome = cairn_query::tools_km::route_query(&conn, &bundle, &args.question);
    if args.as_json {
        println!("{}", json_compat::dumps(&route_json(&outcome)));
        return ExitCode::SUCCESS;
    }
    print_route(&outcome);
    ExitCode::SUCCESS
}

/// `--all-repos`: one attributed answer per reachable registered store; an
/// unavailable store never aborts the query.
fn ask_all_repos(args: &crate::cmds_core::AskArgs) -> ExitCode {
    let mut answers: Vec<(String, cairn_query::tools_km::RouteOutcome)> = Vec::new();
    let mut states: Vec<(String, String)> = Vec::new();
    // The routing core is infallible, so per-store errors stay empty; the
    // payload keeps the key because the CLI contract renders it.
    let errors: Vec<(String, String)> = Vec::new();
    let mut dropped: Vec<String> = Vec::new();
    if let Some(home) = store_paths::cairn_home() {
        for (ws_path, key) in store_paths::load_registry() {
            let store_home = home.join(&key);
            let db = store_home.join(".kg");
            let mut state = classify_store(&store_home, &db).to_string();
            if state == "ok" {
                match cairn_store::conn::open_read_only(&db) {
                    Err(_) => state = "locked".into(),
                    Ok(conn) => {
                        let bundle =
                            cairn_query::tools_km::Bundle::new(store_home.join(".knowledge"));
                        answers.push((
                            ws_path.clone(),
                            cairn_query::tools_km::route_query(&conn, &bundle, &args.question),
                        ));
                    }
                }
            }
            if state != "ok" {
                dropped.push(ws_path.clone());
            }
            states.push((ws_path, state));
        }
    }
    if args.as_json {
        let answers_map: Map<String, Value> = answers
            .iter()
            .map(|(ws, outcome)| (ws.clone(), route_json(outcome)))
            .collect();
        let to_map = |pairs: &[(String, String)]| -> Value {
            Value::Object(
                pairs
                    .iter()
                    .map(|(k, v)| (k.clone(), Value::from(v.clone())))
                    .collect::<Map<String, Value>>(),
            )
        };
        let mut payload = Map::new();
        payload.insert("answers".into(), Value::Object(answers_map));
        payload.insert("states".into(), to_map(&states));
        payload.insert("errors".into(), to_map(&errors));
        payload.insert(
            "dropped".into(),
            Value::Array(dropped.iter().cloned().map(Value::from).collect()),
        );
        println!("{}", json_compat::dumps(&Value::Object(payload)));
        return ExitCode::SUCCESS;
    }
    if states.is_empty() {
        println!("No stores are registered.");
        return ExitCode::SUCCESS;
    }
    for (ws_path, outcome) in &answers {
        println!("=== {ws_path} ===");
        print_route(outcome);
    }
    if !dropped.is_empty() {
        println!("\nDropped stores:");
        for ws_path in &dropped {
            let detail = errors
                .iter()
                .find(|(k, _)| k == ws_path)
                .map(|(_, v)| format!(" ({v})"))
                .unwrap_or_default();
            let state = states
                .iter()
                .find(|(k, _)| k == ws_path)
                .map(|(_, v)| v.clone())
                .unwrap_or_default();
            println!("  {state}: {ws_path}{detail}");
        }
    }
    ExitCode::SUCCESS
}

/// The default knowledge bundle path for the current context.
fn knowledge_default() -> String {
    store_paths::resolve_store(None)
        .knowledge
        .to_string_lossy()
        .into_owned()
}

/// context handler: compass + wiki + memory context for a file.
fn context(args: crate::cmds_core::ContextArgs) -> ExitCode {
    let store = store_paths::resolve_store(None);
    let db = store.db.to_string_lossy().into_owned();
    let refresh = refresh_flag(args.refresh, args.no_refresh);
    if fresh_conn(&db, refresh).is_err() {
        return command_error(&format!("cannot open {db}"));
    }
    let knowledge = args
        .knowledge
        .clone()
        .unwrap_or_else(|| store.knowledge.to_string_lossy().into_owned());
    let bundle = cairn_query::tools_km::Bundle::new(knowledge);
    let file_path = &args.file_path;
    let parts: Vec<&str> = file_path.split('/').filter(|p| !p.is_empty()).collect();
    let module_guess = if parts.len() >= 4 {
        parts[..4].join("/")
    } else {
        file_path.clone()
    };
    let mut out = vec![
        format!("Context for {file_path}:"),
        format!("  inferred module: {module_guess}"),
    ];
    for cid in bundle.list_concepts(Some("compass/")) {
        let Ok(concept) = bundle.read_concept(&cid) else {
            continue;
        };
        if let Some(resource) = concept.resource.as_deref().filter(|r| !r.is_empty())
            && (file_path.contains(resource) || resource.contains(file_path))
        {
            out.push(format!(
                "\n# Compass: {}\n{}",
                concept.title.unwrap_or_default(),
                concept.body
            ));
            break;
        }
    }
    let seg = Path::new(file_path)
        .file_stem()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_default();
    if !seg.is_empty() {
        for concept in bundle.search(&seg, Some(3)).unwrap_or_default() {
            if concept.type_ == "Wiki-Article" || concept.type_ == "Wiki-Feature" {
                out.push(format!(
                    "\n# Wiki: {}\n{}...",
                    concept.title.unwrap_or_default(),
                    concept.body.chars().take(500).collect::<String>()
                ));
                break;
            }
        }
        for concept in bundle.search(&seg, Some(3)).unwrap_or_default() {
            if bundle
                .relativize_id(&concept.concept_id)
                .starts_with("memory/")
            {
                out.push(format!("\n# Memory: {}", concept.title.unwrap_or_default()));
                if let Some(description) = concept.description.as_deref().filter(|d| !d.is_empty())
                {
                    out.push(format!("  {description}"));
                }
                break;
            }
        }
    }
    println!("{}", out.join("\n"));
    ExitCode::SUCCESS
}

/// review handler: diff pack, pre-submit memory guard, or event capture.
fn review(args: crate::cmds_core::ReviewArgs) -> ExitCode {
    if let Some(event_path) = &args.capture_event {
        let path = Path::new(event_path);
        if path.is_dir() {
            return click_usage_error(
                "cairn review [OPTIONS]",
                "review",
                &format!(
                    "Invalid value for '--capture-event': Path '{event_path}' is a directory."
                ),
            );
        }
        if !path.exists() {
            return click_usage_error(
                "cairn review [OPTIONS]",
                "review",
                &format!(
                    "Invalid value for '--capture-event': File '{event_path}' does not exist."
                ),
            );
        }
    }
    let given: Vec<&str> = [
        ("--base", args.base.is_some()),
        ("--pre-submit", args.pre_submit),
        ("--capture-event", args.capture_event.is_some()),
    ]
    .into_iter()
    .filter(|(_, present)| *present)
    .map(|(flag, _)| flag)
    .collect();
    if given.len() > 1 {
        return click_usage_error(
            "cairn review [OPTIONS]",
            "review",
            &format!(
                "{} are mutually exclusive; pass exactly one.",
                given.join(", ")
            ),
        );
    }
    if given.is_empty() {
        return click_usage_error(
            "cairn review [OPTIONS]",
            "review",
            "Pass exactly one mode: --base <ref>, --pre-submit, or --capture-event <file>.",
        );
    }
    if args.gate && !args.pre_submit {
        return click_usage_error(
            "cairn review [OPTIONS]",
            "review",
            "--gate requires --pre-submit.",
        );
    }
    if args.capture_event.is_some() {
        return review_capture_event(&args);
    }
    let ws = store_paths::resolve_workspace(None);
    let workspace_str = ws.to_string_lossy().into_owned();
    let db = db_arg(&args.db);
    let Ok(mut conn) = cairn_store::schema::open_initialized(Path::new(&db)) else {
        return command_error(&format!("cannot read store {db}"));
    };
    let bundle = cairn_query::tools_km::Bundle::new(knowledge_default());
    let config = load_config(ws);
    let registry = cairn_query::graph::merged_registry(&config.taint_sources, &config.taint_sinks);
    let refresh = refresh_flag(args.refresh, args.no_refresh);
    let mut refresh_fn = |conn: &mut Connection| refresh_with_banner(conn, &db, refresh);
    if args.pre_submit {
        let result = match cairn_query::review::build_pre_submit(
            &mut conn,
            &workspace_str,
            &bundle,
            args.fuzzy,
            &registry,
            &mut refresh_fn,
        ) {
            Ok(result) => result,
            Err(message) => return command_error(&message),
        };
        match cairn_query::review::render_pre_submit(&result, args.output_format.as_str()) {
            Ok(rendered) => {
                if !rendered.is_empty() {
                    println!("{rendered}");
                }
            }
            Err(message) => return command_error(&message),
        }
        if args.gate {
            if let Some(error) = &result.memories.error {
                return command_error(&format!("pre-submit gate could not read memories: {error}"));
            }
            if cairn_query::review::has_blocking_matches(&result) {
                return ExitCode::FAILURE;
            }
        }
        return ExitCode::SUCCESS;
    }
    let Some(base) = args.base.clone() else {
        return ExitCode::SUCCESS;
    };
    let pack = match cairn_query::review::build_pack(
        &mut conn,
        &workspace_str,
        &bundle,
        &base,
        args.fuzzy,
        &registry,
        &mut refresh_fn,
    ) {
        Ok(pack) => pack,
        Err(message) => return command_error(&message),
    };
    match cairn_query::review::render_pack(&pack, args.output_format.as_str()) {
        Ok(text) => {
            println!("{text}");
            ExitCode::SUCCESS
        }
        Err(message) => command_error(&message),
    }
}

/// The `--capture-event` arm: record one resolved-comment event as a memory.
fn review_capture_event(args: &crate::cmds_core::ReviewArgs) -> ExitCode {
    let event_path = args.capture_event.clone().unwrap_or_default();
    let payload_text = match std::fs::read_to_string(Path::new(&event_path)) {
        Ok(text) => text,
        Err(e) => return command_error(&format!("cannot read event file {event_path}: {e}")),
    };
    let payload: Value = match serde_json::from_str(&payload_text) {
        Ok(value) => value,
        Err(e) => return command_error(&format!("cannot read event file {event_path}: {e}")),
    };
    let bundle = cairn_query::tools_km::Bundle::new(knowledge_default());
    let db = db_arg(&args.db);
    let Ok(conn) = cairn_store::schema::open_initialized(Path::new(&db)) else {
        return command_error(&format!("cannot read store {db}"));
    };
    match cairn_query::review::capture_review_event(&conn, &bundle, &payload) {
        Err(message) => command_error(&message),
        Ok(outcome) => {
            let path = outcome.path.clone().unwrap_or_default();
            if outcome.recorded
                && cairn_build::embeddings::embed_memory_concepts(
                    &conn,
                    &bundle,
                    std::slice::from_ref(&path),
                )
                .is_err()
            {
                eprintln!(
                    "Embedding failed; memories saved without vectors \
                     (run `cairn memory embed` to backfill)."
                );
            }
            if outcome.recorded {
                println!(
                    "Recorded {} '{}' -> {} (tier={})",
                    outcome.type_.unwrap_or_default(),
                    outcome.title.unwrap_or_default(),
                    path,
                    outcome.tier.unwrap_or_default()
                );
            } else {
                println!("Comment is not resolved; nothing recorded.");
            }
            ExitCode::SUCCESS
        }
    }
}

// --- cairn dataflow -----------------------------------------------------------------

/// dataflow build handler.
fn dataflow_build(args: crate::cmds_core::DataflowBuildArgs) -> ExitCode {
    let db = db_arg(&args.db);
    let workspace = store_paths::resolve_workspace(None);
    let Ok(mut conn) = cairn_store::schema::open_initialized(Path::new(&db)) else {
        return command_error(&format!("cannot open {db}"));
    };
    let count = match cairn_build::dataflow::build_dataflow_index(
        &mut conn,
        &workspace.to_string_lossy(),
        args.max_symbols,
    ) {
        Ok(count) => count,
        Err(message) => return command_error(&message),
    };
    println!("Dataflow index built: {count} public symbols indexed.");
    ExitCode::SUCCESS
}

/// dataflow lookup handler.
fn dataflow_lookup(args: crate::cmds_core::DataflowLookupArgs) -> ExitCode {
    let db = db_arg(&args.db);
    let Ok(conn) = cairn_store::schema::open_initialized(Path::new(&db)) else {
        return command_error(&format!("cannot open {db}"));
    };
    let row = match cairn_build::dataflow::get_dataflow(&conn, &args.symbol) {
        Ok(row) => row,
        Err(message) => return command_error(&message),
    };
    let Some(row) = row else {
        eprintln!("{}", missing_dataflow_message(&conn, &args.symbol));
        return ExitCode::FAILURE;
    };
    if args.as_json {
        println!("{}", json_compat::dumps(&dataflow_json(&row)));
        return ExitCode::SUCCESS;
    }
    print_dataflow(&row);
    ExitCode::SUCCESS
}

fn missing_dataflow_message(conn: &Connection, symbol: &str) -> String {
    if cairn_build::dataflow::symbol_exists(conn, symbol) {
        format!("No dataflow entry for '{symbol}': the index covers public symbols only.")
    } else {
        format!("No dataflow entry for '{symbol}'. Run `cairn dataflow build` first.")
    }
}

fn dataflow_json(row: &cairn_build::dataflow::DataflowRow) -> Value {
    let strings =
        |items: &[String]| Value::Array(items.iter().map(|s| Value::from(s.clone())).collect());
    let mut map = Map::new();
    map.insert("symbol".into(), Value::from(row.symbol.clone()));
    map.insert("repo".into(), Value::from(row.repo.clone()));
    map.insert("within_repo".into(), strings(&row.within_repo));
    map.insert("cross_repo".into(), strings(&row.cross_repo));
    map.insert("updated".into(), number_value(row.updated));
    if !row.analysis_errors.is_empty() {
        map.insert("analysis_errors".into(), strings(&row.analysis_errors));
    }
    if row.ambiguous {
        map.insert("ambiguous".into(), Value::from(true));
    }
    Value::Object(map)
}

fn print_dataflow(row: &cairn_build::dataflow::DataflowRow) {
    println!("Symbol: {}  (repo: {})", row.symbol, row.repo);
    if row.ambiguous {
        println!(
            "Note: '{}' is public in multiple repos; showing the {} row.",
            row.symbol, row.repo
        );
    }
    if !row.analysis_errors.is_empty() {
        eprintln!("Analysis errors: {}", row.analysis_errors.join("; "));
    }
    if !row.within_repo.is_empty() {
        println!("Within-repo impact ({} symbols):", row.within_repo.len());
        for symbol in row.within_repo.iter().take(20) {
            println!("  {symbol}");
        }
        if row.within_repo.len() > 20 {
            println!("  ... and {} more", row.within_repo.len() - 20);
        }
    } else {
        println!("Within-repo impact: (none)");
    }
    if !row.cross_repo.is_empty() {
        println!("Cross-repo consumers: {}", row.cross_repo.join(", "));
    } else {
        println!("Cross-repo consumers: (none)");
    }
}

// --- cairn communities -------------------------------------------------------------

/// communities handler: seeded Louvain partition persisted, then the hub summary.
fn communities(args: crate::cmds_core::CommunitiesArgs) -> ExitCode {
    let db = db_arg(&args.db);
    let path = PathBuf::from(&db);
    if !path.is_file() {
        return command_error(&format!("cannot open {db}"));
    }
    let Ok(mut conn) = cairn_store::conn::open_writable(&path) else {
        return command_error(&format!("cannot open {db}"));
    };
    let count = match cairn_build::communities::refresh_communities(&mut conn) {
        Ok(count) => count,
        Err(e) => return command_error(&format!("cairn communities: {e} ({db})")),
    };
    if count == 0 {
        println!("no communities found");
        return ExitCode::SUCCESS;
    }
    println!("{count} communities");
    println!("Global hubs:");
    if let Err(e) = print_hub_rows(&conn, None, args.top_k, "  ") {
        return command_error(&format!("cairn communities: {e} ({db})"));
    }
    println!("Communities:");
    let rows = match cairn_build::communities::community_rows(&conn) {
        Ok(rows) => rows,
        Err(e) => return command_error(&format!("cairn communities: {e} ({db})")),
    };
    for (id, label, size) in rows {
        println!("  {id}: {label} ({size} symbols)");
        if let Err(e) = print_hub_rows(&conn, Some(id), args.top_k, "    ") {
            return command_error(&format!("cairn communities: {e} ({db})"));
        }
    }
    ExitCode::SUCCESS
}

/// One hub block: qualified name and degree per row, indented.
fn print_hub_rows(
    conn: &Connection,
    community_id: Option<i64>,
    top_k: usize,
    indent: &str,
) -> Result<(), String> {
    for row in cairn_build::communities::hub_rows(conn, community_id, top_k)? {
        println!(
            "{indent}{} (degree {})",
            row.qualified_name, row.structural_degree
        );
    }
    Ok(())
}

// --- cairn viz ---------------------------------------------------------------------

/// viz handler.
fn viz(args: crate::cmds_core::VizArgs) -> ExitCode {
    if args.export_path.is_some() && args.output.is_some() {
        return usage_error("--export and --output are mutually exclusive.");
    }
    let db = db_arg(&args.db);
    let conn = open_read_aware(&db);
    let Ok(conn) = conn else {
        return command_error(&format!("cannot open {db}"));
    };
    let graph_result: Result<cairn_query::graph::Graph, rusqlite::Error> = match args.scope {
        VizScope::Symbol => {
            cairn_query::graph::get_symbol_graph(&conn, args.symbol.as_deref().unwrap_or(""))
        }
        VizScope::Impact => cairn_query::graph::get_impact_graph(
            &conn,
            args.symbol.as_deref().unwrap_or(""),
            args.depth,
        ),
        VizScope::Module => {
            cairn_query::graph::get_module_graph(&conn, args.module.as_deref().unwrap_or(""), false)
        }
        VizScope::Repo => {
            cairn_query::graph::get_repo_graph(&conn, args.repo.as_deref().unwrap_or(""))
        }
        VizScope::Deps => {
            let ws = store_paths::resolve_workspace(None);
            let env_raw = std::env::var("CAIRN_REPO_NAMESPACES").ok();
            let namespaces = cairn_query::graph::resolve_namespaces(&ws, env_raw.as_deref());
            cairn_query::graph::get_deps_graph(&conn, &namespaces)
        }
    };
    let graph = match graph_result {
        Ok(graph) => graph,
        Err(e) => return command_error(&e.to_string()),
    };
    if let Some(export_path) = &args.export_path {
        let viewer = viewer_js();
        let graph_json = json_compat::dumps_compact(&graph).replace('<', "\\u003c");
        let html = cairn_query::graph::to_html(&graph, &viewer, &graph_json);
        if let Err(e) = std::fs::write(export_path, html.as_bytes()) {
            return command_error(&format!("cannot write {export_path}: {e}"));
        }
        println!("Wrote {export_path}");
        return ExitCode::SUCCESS;
    }
    let out = match args.format {
        VizFormat::Mermaid => {
            if args.do_embed {
                cairn_query::graph::viz_embed(&graph)
            } else {
                cairn_query::graph::to_mermaid(&graph)
            }
        }
        VizFormat::Dot => cairn_query::graph::to_dot(&graph),
        VizFormat::Json => json_compat::dumps(&graph),
    };
    match &args.output {
        None => println!("{out}"),
        Some(path) => {
            if let Err(e) = std::fs::write(path, out.as_bytes()) {
                return command_error(&format!("cannot write {path}: {e}"));
            }
            println!("Wrote {path}");
        }
    }
    ExitCode::SUCCESS
}

/// The dashboard's bundled vis-network build without its source-map pointer.
fn viewer_js() -> String {
    crate::dashboard::assets::static_file("/vis-network.min.js")
        .map(|bytes| {
            String::from_utf8_lossy(bytes)
                .lines()
                .filter(|line| !line.starts_with("//# sourceMappingURL="))
                .collect::<Vec<_>>()
                .join("\n")
        })
        .unwrap_or_default()
}

// --- per-command argument structs --------------------------------------------

#[derive(clap::Args)]
pub struct InitArgs {
    /// Workspace root (default: cwd).
    #[arg(long = "workspace")]
    pub ws_arg: Option<String>,
    /// Register without building the graph.
    #[arg(long = "no-build", default_value_t = false)]
    pub no_build: bool,
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
    /// Flag cycle-carrying results above --threshold as name collisions and fall back to the dataflow index.
    #[arg(long = "guard")]
    pub guard: bool,
    /// Impacted-symbol count above which --guard treats a cycle-carrying result as a collision.
    #[arg(long = "threshold", default_value_t = 100, requires = "guard")]
    pub threshold: usize,
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

#[derive(clap::Args)]
pub struct SemanticArgs {
    /// Query text.
    pub query: String,
    /// SQLite DB path.
    #[arg(long = "db")]
    pub db: Option<String>,
    /// Max results.
    #[arg(long = "limit", default_value_t = 20)]
    pub limit: usize,
    /// Min cosine similarity (0..1). Lower = more recall, more noise.
    #[arg(long = "threshold", default_value_t = 0.3)]
    pub threshold: f64,
    /// Emit JSON.
    #[arg(long = "json")]
    pub as_json: bool,
    /// Attach each hit's 1-hop callers/callees (precise-only, capped at 5 each).
    #[arg(long = "include-callers")]
    pub include_callers: bool,
}

#[derive(clap::Args)]
pub struct EmbedArgs {
    /// SQLite DB path.
    #[arg(long = "db")]
    pub db: Option<String>,
    /// Embedding batch size.
    #[arg(long = "batch-size", default_value_t = 64)]
    pub batch_size: usize,
    /// Max symbols to embed (debug).
    #[arg(long = "limit")]
    pub limit: Option<usize>,
    /// Skip deleting embedding rows for symbols that no longer exist.
    #[arg(long = "no-reap")]
    pub no_reap: bool,
    /// Download the native bge-m3 model weights and exit (local-embed builds).
    #[arg(long = "download-model")]
    pub download_model: bool,
    /// Rebuild native ANN index after embedding.
    #[arg(long = "build-index")]
    pub build_index: bool,
    /// Store one vector per symbol (the single-vector build).
    #[arg(long = "no-multivector", default_value_t = false)]
    pub no_multivector: bool,
}

#[derive(clap::Args)]
pub struct GrepArgs {
    /// Pattern to search for.
    pub pattern: String,
    /// SQLite DB path.
    #[arg(long = "db")]
    pub db: Option<String>,
    /// Emit JSON.
    #[arg(long = "json")]
    pub as_json: bool,
    /// Ignore case.
    #[arg(long = "ignore-case", short = 'i')]
    pub ignore_case: bool,
    /// Treat PATTERN as a literal.
    #[arg(long = "fixed")]
    pub fixed: bool,
    /// Only search repository-relative paths under this prefix.
    #[arg(long = "in", default_value = "")]
    pub path_prefix: String,
    /// Maximum hits returned.
    #[arg(long = "max-hits", default_value_t = cairn_query::grep::DEFAULT_MAX_HITS, value_parser = clap::builder::RangedU64ValueParser::<usize>::new().range(1..))]
    pub max_hits: usize,
    /// Reindex drifted files before answering.
    #[arg(long = "refresh")]
    pub refresh: bool,
    /// Answer from the current graph without reindexing.
    #[arg(long = "no-refresh")]
    pub no_refresh: bool,
}

#[derive(clap::Args)]
pub struct MapArgs {
    /// SQLite DB path.
    #[arg(long = "db")]
    pub db: Option<String>,
    /// Emit JSON.
    #[arg(long = "json")]
    pub as_json: bool,
    /// Maximum directory clusters per repository.
    #[arg(long = "max-clusters", default_value_t = CLI_DEFAULT_CLUSTER_CAP, value_parser = clap::builder::RangedU64ValueParser::<usize>::new().range(1..))]
    pub max_clusters: usize,
    /// Maximum in-degree hubs per cluster.
    #[arg(long = "max-hubs", default_value_t = CLI_DEFAULT_HUB_CAP, value_parser = clap::builder::RangedU64ValueParser::<usize>::new().range(1..))]
    pub max_hubs: usize,
    /// Maximum workspace-wide hotspots.
    #[arg(long = "max-hotspots", default_value_t = CLI_DEFAULT_HOTSPOT_CAP, value_parser = clap::builder::RangedU64ValueParser::<usize>::new().range(1..))]
    pub max_hotspots: usize,
    /// Reindex drifted files before answering.
    #[arg(long = "refresh")]
    pub refresh: bool,
    /// Answer from the current graph without reindexing.
    #[arg(long = "no-refresh")]
    pub no_refresh: bool,
}

/// The blast `--format` choices (click.Choice parity).
#[derive(Clone, Copy, Debug, clap::ValueEnum)]
pub enum BlastFormat {
    Text,
    Markdown,
    Mermaid,
    Json,
}

impl BlastFormat {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Text => "text",
            Self::Markdown => "markdown",
            Self::Mermaid => "mermaid",
            Self::Json => "json",
        }
    }
}

#[derive(clap::Args)]
pub struct BlastArgs {
    /// SQLite DB path.
    #[arg(long = "db")]
    pub db: Option<String>,
    /// Compare against this ref's merge base.
    #[arg(long = "base")]
    pub base: Option<String>,
    /// Output format.
    #[arg(long = "format", default_value = "text")]
    pub output_format: BlastFormat,
    /// Also traverse unresolved name-only edges.
    #[arg(long = "fuzzy", default_value_t = false)]
    pub fuzzy: bool,
    /// Reindex drifted files before diffing.
    #[arg(long = "refresh")]
    pub refresh: bool,
    /// Answer from the current graph without reindexing.
    #[arg(long = "no-refresh")]
    pub no_refresh: bool,
    /// Write to file instead of stdout.
    #[arg(long = "output")]
    pub output: Option<String>,
}

#[derive(clap::Args)]
pub struct AskArgs {
    /// Question text.
    pub question: String,
    /// SQLite DB path.
    #[arg(long = "db")]
    pub db: Option<String>,
    /// Knowledge bundle path.
    #[arg(long = "knowledge")]
    pub knowledge: Option<String>,
    /// Emit JSON.
    #[arg(long = "json")]
    pub as_json: bool,
    /// Route across every registered workspace store.
    #[arg(long = "all-repos")]
    pub all_repos: bool,
}

#[derive(clap::Args)]
pub struct ContextArgs {
    /// File to load context for.
    pub file_path: String,
    /// Knowledge bundle path.
    #[arg(long = "knowledge")]
    pub knowledge: Option<String>,
    /// Reindex drifted files before answering.
    #[arg(long = "refresh")]
    pub refresh: bool,
    /// Answer from the current graph without reindexing.
    #[arg(long = "no-refresh")]
    pub no_refresh: bool,
}

/// The review `--format` choices (click.Choice parity).
#[derive(Clone, Copy, Debug, clap::ValueEnum)]
pub enum ReviewFormat {
    Text,
    Markdown,
}

impl ReviewFormat {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Text => "text",
            Self::Markdown => "markdown",
        }
    }
}

#[derive(clap::Args)]
pub struct ReviewArgs {
    /// SQLite DB path.
    #[arg(long = "db")]
    pub db: Option<String>,
    /// Build the review pack for the diff against this ref's merge base.
    #[arg(long = "base")]
    pub base: Option<String>,
    /// Check the diff against recorded mistake and pattern memories.
    #[arg(long = "pre-submit")]
    pub pre_submit: bool,
    /// Record a resolved review-comment event JSON as a memory.
    #[arg(long = "capture-event")]
    pub capture_event: Option<String>,
    /// With --pre-submit, exit non-zero when a mistake or pattern matches the diff.
    #[arg(long = "gate")]
    pub gate: bool,
    /// Pack rendering format.
    #[arg(long = "format", default_value = "text")]
    pub output_format: ReviewFormat,
    /// Also traverse unresolved name-only edges.
    #[arg(long = "fuzzy", default_value_t = false)]
    pub fuzzy: bool,
    /// Reindex drifted files before diffing.
    #[arg(long = "refresh")]
    pub refresh: bool,
    /// Diff against the current graph without reindexing.
    #[arg(long = "no-refresh")]
    pub no_refresh: bool,
}

#[derive(clap::Args)]
pub struct CommunitiesArgs {
    /// SQLite DB path.
    #[arg(long = "db")]
    pub db: Option<String>,
    /// Top-K hubs listed globally and per community.
    #[arg(long = "top-k", default_value_t = 10, value_parser = clap::builder::RangedU64ValueParser::<usize>::new().range(1..))]
    pub top_k: usize,
}

#[derive(clap::Args)]
pub struct DataflowBuildArgs {
    /// SQLite DB path.
    #[arg(long = "db")]
    pub db: Option<String>,
    /// Cap on public symbols indexed per run (default: CAIRN_DATAFLOW_MAX_SYMBOLS, else 2000).
    #[arg(long = "max-symbols", allow_negative_numbers = true)]
    pub max_symbols: Option<i64>,
}

#[derive(clap::Args)]
pub struct DataflowLookupArgs {
    /// Symbol to look up.
    pub symbol: String,
    /// SQLite DB path.
    #[arg(long = "db")]
    pub db: Option<String>,
    /// Emit JSON.
    #[arg(long = "json")]
    pub as_json: bool,
}

#[derive(clap::Args)]
pub struct RationaleArgs {
    /// Symbol name or qualified-name substring (case-insensitive).
    #[arg(long = "symbol")]
    pub symbol: Option<String>,
    /// File path substring (case-insensitive).
    #[arg(long = "file")]
    pub file_pattern: Option<String>,
    /// SQLite DB path.
    #[arg(long = "db")]
    pub db: Option<String>,
    /// Emit JSON rows.
    #[arg(long = "json")]
    pub as_json: bool,
}

/// The viz `--format` choices (click.Choice parity).
#[derive(Clone, Copy, Debug, clap::ValueEnum)]
pub enum VizFormat {
    Mermaid,
    Dot,
    Json,
}

impl VizFormat {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Mermaid => "mermaid",
            Self::Dot => "dot",
            Self::Json => "json",
        }
    }
}

/// The viz `--scope` choices (click.Choice parity).
#[derive(Clone, Copy, Debug, clap::ValueEnum)]
pub enum VizScope {
    Symbol,
    Module,
    Impact,
    Repo,
    Deps,
}

impl VizScope {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Symbol => "symbol",
            Self::Module => "module",
            Self::Impact => "impact",
            Self::Repo => "repo",
            Self::Deps => "deps",
        }
    }
}

#[derive(clap::Args)]
pub struct VizArgs {
    /// Output format.
    #[arg(long = "format", default_value = "mermaid")]
    pub format: VizFormat,
    /// Graph scope.
    #[arg(long = "scope", default_value = "symbol")]
    pub scope: VizScope,
    /// Symbol name (scope=symbol/impact).
    #[arg(long = "symbol")]
    pub symbol: Option<String>,
    /// Module path (scope=module).
    #[arg(long = "module")]
    pub module: Option<String>,
    /// Repo name (scope=repo).
    #[arg(long = "repo")]
    pub repo: Option<String>,
    /// Traversal depth (scope=impact).
    #[arg(long = "depth", default_value_t = 3)]
    pub depth: i64,
    /// Write to file instead of stdout.
    #[arg(long = "output")]
    pub output: Option<String>,
    /// Wrap in OKF markdown block.
    #[arg(long = "embed")]
    pub do_embed: bool,
    /// Write one self-contained HTML file (overrides --format; not valid with --output).
    #[arg(long = "export")]
    pub export_path: Option<String>,
    /// SQLite DB path.
    #[arg(long = "db")]
    pub db: Option<String>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn staging_build_swaps_the_temp_db_over_the_store() {
        let dir = std::env::temp_dir().join(format!(
            "cairn-staging-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("clock")
                .as_nanos()
        ));
        std::fs::create_dir_all(dir.join(".git")).expect("repo marker");
        std::fs::write(dir.join("mod.py"), "def f():\n    return 1\n").expect("source");
        let db = dir.join("store.kg");
        let db_str = db.to_string_lossy().to_string();
        {
            let conn = cairn_store::schema::open_initialized(&db).expect("seed store");
            conn.execute(
                "INSERT INTO build_runs (kind, started_at) VALUES ('build', 1.0)",
                [],
            )
            .expect("seed telemetry");
        }

        let code = build(BuildArgs {
            repo: None,
            workspace: Some(dir.to_string_lossy().to_string()),
            db: Some(db_str.clone()),
            verbose: false,
            staging: true,
            with_closure: false,
        });

        assert_eq!(code, ExitCode::SUCCESS);
        assert!(!Path::new(&format!("{db_str}.tmp")).exists());
        assert!(!Path::new(&format!("{db_str}.tmp.build.lock")).exists());
        let conn = Connection::open(&db).expect("open store");
        let count = |sql: &str| -> i64 { conn.query_row(sql, [], |r| r.get(0)).expect("count") };
        assert_eq!(count("SELECT COUNT(*) FROM files"), 1);
        assert_eq!(count("SELECT COUNT(*) FROM build_runs"), 1);
        drop(conn);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn refresh_repair_defers_while_a_build_holds_the_lock() {
        let env = crate::test_env::EnvScope::new("refresh-lock");
        let ws = env.dir().join("ws");
        let repo = ws.join("repo");
        std::fs::create_dir_all(repo.join(".git")).expect("repo marker");
        std::fs::write(repo.join("mod.py"), "def one():\n    return 1\n").expect("source");
        let db = ws.join("store.kg");
        let db_str = db.to_string_lossy().to_string();
        let ws_str = ws.to_string_lossy().to_string();
        cairn_build::builder::build_graph(&ws_str, &db_str, None, Some(1)).expect("full build");

        let drifted = "def one():\n    return 1\n\n\ndef two():\n    return 2\n";
        std::fs::write(repo.join("mod.py"), drifted).expect("drift fixture");

        let saved = std::env::var_os("CAIRN_WORKSPACE");
        // SAFETY: EnvScope holds the env lock for the rest of this test.
        unsafe { std::env::set_var("CAIRN_WORKSPACE", &ws) };
        let mut conn = cairn_store::schema::open_initialized(&db).expect("open store");
        let stored_size = |conn: &Connection| -> i64 {
            conn.query_row("SELECT size FROM files", [], |r| r.get(0))
                .expect("files row")
        };
        let built_size = stored_size(&conn);

        let held = cairn_store::swap::build_lock(&db).expect("hold build lock");
        let deferred = refresh_conn(&mut conn, &db_str, Some(true));
        let size_under_lock = stored_size(&conn);
        drop(held);
        let released = refresh_conn(&mut conn, &db_str, Some(true));
        let size_after = stored_size(&conn);

        // SAFETY: still holding the env lock.
        unsafe {
            match &saved {
                Some(v) => std::env::set_var("CAIRN_WORKSPACE", v),
                None => std::env::remove_var("CAIRN_WORKSPACE"),
            }
        }

        assert_eq!(built_size, "def one():\n    return 1\n".len() as i64);
        assert!(
            deferred.is_ok(),
            "read-path refresh must not fail on lock contention: {deferred:?}"
        );
        assert_eq!(
            size_under_lock, built_size,
            "repair wrote to the store while another build held the lock"
        );
        assert!(released.is_ok(), "{released:?}");
        assert_eq!(
            size_after,
            drifted.len() as i64,
            "repair must run once the lock frees"
        );
        let _ = std::fs::remove_dir_all(env.dir());
    }

    #[test]
    fn refresh_probe_reports_fresh_through_the_locked_path() {
        let env = crate::test_env::EnvScope::new("refresh-fresh");
        let ws = env.dir().join("ws");
        std::fs::create_dir_all(ws.join(".git")).expect("repo marker");
        std::fs::write(ws.join("mod.py"), "def one():\n    return 1\n").expect("source");
        let db = ws.join("store.kg");
        let db_str = db.to_string_lossy().to_string();
        let ws_str = ws.to_string_lossy().to_string();
        cairn_build::builder::build_graph(&ws_str, &db_str, None, Some(1)).expect("full build");
        let _ = std::fs::remove_file(format!("{db_str}.build.lock"));

        let saved = std::env::var_os("CAIRN_WORKSPACE");
        // SAFETY: EnvScope holds the env lock for the rest of this test.
        unsafe { std::env::set_var("CAIRN_WORKSPACE", &ws) };
        let mut conn = cairn_store::schema::open_initialized(&db).expect("open store");
        let files_before: i64 = conn
            .query_row("SELECT COUNT(*) FROM files", [], |r| r.get(0))
            .expect("files count");
        let probe = refresh_conn(&mut conn, &db_str, Some(true));
        // SAFETY: still holding the env lock.
        unsafe {
            match &saved {
                Some(v) => std::env::set_var("CAIRN_WORKSPACE", v),
                None => std::env::remove_var("CAIRN_WORKSPACE"),
            }
        }

        assert!(probe.is_ok(), "fresh probe must not error: {probe:?}");
        let files_after: i64 = conn
            .query_row("SELECT COUNT(*) FROM files", [], |r| r.get(0))
            .expect("files count");
        assert_eq!(files_before, files_after);
        drop(conn);
        assert!(
            Path::new(&format!("{db_str}.build.lock")).exists(),
            "the repair path must acquire the build lock before detecting"
        );
        let _ = std::fs::remove_dir_all(env.dir());
    }
}
