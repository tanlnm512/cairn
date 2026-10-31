//! Graph builder: scan -> parse (rayon) -> store -> resolve edges.

use std::collections::HashMap;
use std::path::Path;
use std::time::Instant;

use cairn_parse::model::ParsedFile;
use cairn_query::json_compat::dumps_compact;
use cairn_store::swap;
use rayon::prelude::*;
use rusqlite::Connection;
use serde_json::Value;

use crate::config::load_config;
use crate::parse_dispatch::parse_language_file;
use crate::resolver::EdgeSignal;
use crate::routes;
use crate::scanner;
use crate::scip::importer::{import_scip_file_opts, replace_skip_row};
use crate::scip::indexers::generate_index_result;
use crate::service_calls;
use crate::timefmt::now_iso;

/// Kinds a rationale record can be attributed to (innermost containing wins).
const CALLABLE_OR_TYPE_KINDS: &[&str] = &[
    "function",
    "method",
    "constructor",
    "class",
    "interface",
    "enum",
    "protocol",
    "mixin",
    "implementation",
];

/// Build-result counts.
#[derive(Debug, Clone, Default, serde::Serialize)]
pub struct BuildSummary {
    pub repos: usize,
    pub files: usize,
    pub symbols: usize,
    pub edges: usize,
    pub imports: usize,
    pub skipped: usize,
    pub parse_errors: usize,
    pub resolution: ResolutionCounts,
    /// Present only when a configured SCIP overlay ran.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub scip: Option<ScipReport>,
}

/// Aggregated counts of the applied SCIP indexes.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, serde::Serialize)]
pub struct ScipReport {
    pub edges: usize,
    pub disagreements: usize,
    pub upgrades: usize,
    pub join_anomalies: usize,
    pub stale_files: usize,
    pub external: usize,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, serde::Serialize)]
pub struct ResolutionCounts {
    pub exact: usize,
    pub ambiguous: usize,
    pub unresolved: usize,
}

/// Per-stage wall times in milliseconds.
#[derive(Debug, Clone, Copy, Default)]
pub struct StageTimings {
    pub scan_ms: f64,
    pub parse_ms: f64,
    pub insert_ms: f64,
    pub resolve_ms: f64,
    pub imports_ms: f64,
    pub total_ms: f64,
}

struct ParseOutcome {
    idx: usize,
    pf: Option<ParsedFile>,
    err: Option<String>,
}

/// Deterministic 32-hex row ids, assigned at insert time in stable scan order.
pub(crate) struct IdGen(u64);

impl IdGen {
    pub(crate) fn next_id(&mut self) -> String {
        self.0 += 1;
        format!("{:032x}", self.0)
    }

    /// Continue the sequence after every sequence-shaped id already stored,
    /// so a graft never collides with rows from the original build.
    pub(crate) fn continuing(conn: &Connection) -> Result<IdGen, String> {
        let mut max = 0u64;
        for table in [
            "files",
            "symbols",
            "edges",
            "imports",
            "rationale",
            "parse_errors",
        ] {
            let mut stmt = conn
                .prepare(&format!("SELECT id FROM {table}"))
                .map_err(rusqlite_err)?;
            let rows = stmt
                .query_map([], |r| r.get::<_, String>(0))
                .map_err(rusqlite_err)?;
            for id in rows {
                let id = id.map_err(rusqlite_err)?;
                if let Some(v) = sequence_id_value(&id)
                    && v > max
                {
                    max = v;
                }
            }
        }
        Ok(IdGen(max))
    }
}

/// The counter value behind a builder-shaped id; None for hash/uuid-shaped ids.
fn sequence_id_value(id: &str) -> Option<u64> {
    let stripped = id.trim_start_matches('0');
    if stripped.is_empty() {
        return Some(0);
    }
    if stripped.len() > 16 || !stripped.bytes().all(|b| b.is_ascii_hexdigit()) {
        return None;
    }
    u64::from_str_radix(stripped, 16).ok()
}

/// repo -> (file_id -> queued signals), in insertion order.
pub(crate) type RepoEdges = Vec<(String, Vec<(String, Vec<EdgeSignal>)>)>;

/// Build or rebuild the graph for `workspace` into the store at `db_path`.
/// A full build atomically replaces the store; `repo_filter` replaces only that
/// repo in place. `workers` pins the parse pool size; None honors CAIRN_WORKERS
/// then cpu count.
pub fn build_graph(
    workspace: &str,
    db_path: &str,
    repo_filter: Option<&str>,
    workers: Option<usize>,
) -> Result<(BuildSummary, StageTimings), String> {
    build_graph_with(workspace, db_path, repo_filter, workers, |_, _| Ok(()))
}

/// `build_graph` that runs `before_swap` on a full build's staged store, under
/// the build lock and ahead of the swap; an Err aborts the swap.
pub fn build_graph_with(
    workspace: &str,
    db_path: &str,
    repo_filter: Option<&str>,
    workers: Option<usize>,
    before_swap: impl FnOnce(&Path, &BuildSummary) -> Result<(), String>,
) -> Result<(BuildSummary, StageTimings), String> {
    let started = Instant::now();
    let mut timings = StageTimings::default();

    let db = Path::new(db_path);
    let _lock = swap::build_lock(db)?;
    // A full scan that finds no repository would leave the existing store
    // serving the wrong workspace's graph.
    if repo_filter.is_none()
        && scanner::discover_repo_records(workspace)?.is_empty()
        && store_file_holds_graph(db)
    {
        return Err(format!(
            "no repositories discovered under {workspace} but {} already \
             holds a graph — refusing to keep serving it; check the workspace path",
            db.display()
        ));
    }

    let t = Instant::now();
    let (files, skips) = scanner::scan_workspace(workspace, repo_filter)?;
    timings.scan_ms = elapsed_ms(t);

    let summary = match repo_filter {
        Some(_) => {
            let mut conn = open_store(db)?;
            populate(
                &mut conn,
                workspace,
                &files,
                &skips,
                repo_filter,
                workers,
                &mut timings,
            )?
        }
        None => {
            let staged = swap::staging_path(db);
            swap::discard(&staged);
            let result = build_staged(
                &staged,
                db,
                files.is_empty(),
                |conn| populate(conn, workspace, &files, &skips, None, workers, &mut timings),
                before_swap,
            );
            swap::discard(&staged);
            result?
        }
    };
    timings.total_ms = elapsed_ms(started);
    Ok((summary, timings))
}

/// Full build into a fresh `staged` store, swapped over `db` unless the scan found nothing.
fn build_staged(
    staged: &Path,
    db: &Path,
    scanned_nothing: bool,
    fill: impl FnOnce(&mut Connection) -> Result<BuildSummary, String>,
    before_swap: impl FnOnce(&Path, &BuildSummary) -> Result<(), String>,
) -> Result<BuildSummary, String> {
    let summary = {
        let mut conn = open_store(staged)?;
        fill(&mut conn)?
    };
    if scanned_nothing {
        if summary.skipped == 0 {
            return Ok(summary);
        }
        if store_file_holds_graph(db) {
            return Err(format!(
                "scan found no indexable files ({} skipped) but {} already holds a \
                 graph — refusing to replace it; check the workspace path and skip \
                 configuration",
                summary.skipped,
                db.display()
            ));
        }
    }
    before_swap(staged, &summary)?;
    swap::install_staged(staged, db)?;
    Ok(summary)
}

fn open_store(db: &Path) -> Result<Connection, String> {
    cairn_store::schema::open_initialized(db)
        .map_err(|e| format!("open store {}: {e}", db.display()))
}

fn populate(
    conn: &mut Connection,
    workspace: &str,
    files: &[scanner::FileInfo],
    skips: &[scanner::SkipInfo],
    repo_filter: Option<&str>,
    workers: Option<usize>,
    timings: &mut StageTimings,
) -> Result<BuildSummary, String> {
    let tx = conn.transaction().map_err(rusqlite_err)?;

    if files.is_empty() {
        let skip_count = record_skips(&tx, skips)?;
        tx.commit().map_err(rusqlite_err)?;
        return Ok(BuildSummary {
            skipped: skip_count,
            ..BuildSummary::default()
        });
    }

    // Repos upsert: workspace-relative paths keep the store portable.
    let ws_root = std::fs::canonicalize(workspace).map_err(|e| e.to_string())?;
    let mut repos_seen: Vec<(String, String)> = Vec::new(); // (repo, repo_path)
    for f in files {
        if !repos_seen.iter().any(|(repo, _)| repo == &f.repo) {
            repos_seen.push((f.repo.clone(), f.repo_path.clone()));
        }
    }
    for (repo_name, repo_path) in &repos_seen {
        let rel_repo_path = std::fs::canonicalize(repo_path)
            .ok()
            .and_then(|p| p.strip_prefix(&ws_root).ok().map(Path::to_path_buf))
            .map(|p| p.to_string_lossy().replace(std::path::MAIN_SEPARATOR, "/"))
            .filter(|p| !p.is_empty())
            .unwrap_or_else(|| ".".to_string());
        let language: Option<String> = {
            let repo_files: Vec<scanner::FileInfo> = files
                .iter()
                .filter(|f| &f.repo == repo_name)
                .cloned()
                .collect();
            scanner::infer_repo_language(&repo_files)
        };
        tx.execute(
            "INSERT INTO repos (id, name, path, language, git_remote, indexed_at)
             VALUES (?, ?, ?, ?, ?, ?)
             ON CONFLICT(id) DO UPDATE SET
               name=excluded.name, path=excluded.path,
               language=excluded.language, git_remote=excluded.git_remote,
               indexed_at=excluded.indexed_at",
            rusqlite::params![
                repo_name,
                repo_name,
                rel_repo_path,
                language,
                git_remote_url(repo_path),
                now_iso(),
            ],
        )
        .map_err(rusqlite_err)?;
    }

    // Continue past the rows about to be cleared: unkeyed symbol-id tables
    // (closure, communities) must never resolve to a rebuilt row.
    let mut ids = IdGen::continuing(&tx)?;
    if let Some(repo) = repo_filter {
        // The marker flags an interrupted rebuild until the last write lands.
        tx.execute(
            "INSERT INTO repo_build_state (repo_id, state, started_at)
             VALUES (?, 'building', ?)
             ON CONFLICT(repo_id) DO UPDATE SET
               state='building', started_at=excluded.started_at",
            rusqlite::params![repo, now_iso()],
        )
        .map_err(rusqlite_err)?;
        clear_repo(&tx, repo)?;
    }

    let skip_count = record_skips(&tx, skips)?;

    // First pass: parse all files on the rayon pool.
    let t = Instant::now();
    let workers = workers.unwrap_or_else(configured_workers);
    let mut outcomes: Vec<ParseOutcome> = {
        let pool = rayon::ThreadPoolBuilder::new()
            .num_threads(workers)
            .build()
            .map_err(|e| e.to_string())?;
        pool.install(|| {
            files
                .par_iter()
                .enumerate()
                .map(
                    |(idx, fi)| match parse_language_file(&fi.language, Path::new(&fi.path)) {
                        Ok(pf) => ParseOutcome {
                            idx,
                            pf: Some(pf),
                            err: None,
                        },
                        Err(err) => ParseOutcome {
                            idx,
                            pf: None,
                            err: Some(err),
                        },
                    },
                )
                .collect::<Vec<_>>()
        })
    };
    // Output must be independent of parse completion order: restore scan order
    // before any insert touches the store.
    outcomes.sort_by_key(|o| o.idx);
    timings.parse_ms = elapsed_ms(t);

    // Second pass: insert into SQLite.
    let t = Instant::now();
    let mut summary = BuildSummary {
        skipped: skip_count,
        ..BuildSummary::default()
    };
    summary.repos = repos_seen.len();
    // repo -> (file_id -> queued signals), in insertion order.
    let mut repo_edges: RepoEdges = Vec::new();
    for outcome in &mut outcomes {
        let fi = &files[outcome.idx];
        let Some(pf) = outcome.pf.as_mut() else {
            let err = outcome
                .err
                .clone()
                .unwrap_or_else(|| "Unknown parse error".to_string());
            insert_parse_error(&tx, &mut ids, &fi.repo, &fi.rel_path, &err, None)?;
            summary.parse_errors += 1;
            continue;
        };
        match insert_parsed_file(
            &tx,
            &mut ids,
            &fi.repo,
            &fi.rel_path,
            &fi.path,
            &fi.language,
            &fi.hash,
            pf,
            &mut repo_edges,
        ) {
            Ok((sc, ec, ic)) => {
                summary.symbols += sc;
                summary.edges += ec;
                summary.imports += ic;
                summary.files += 1;
            }
            Err(e) => {
                insert_parse_error(
                    &tx,
                    &mut ids,
                    &fi.repo,
                    &fi.rel_path,
                    &format!("Insertion error: {e}"),
                    None,
                )?;
                summary.parse_errors += 1;
            }
        }
    }
    tx.commit().map_err(rusqlite_err)?;
    timings.insert_ms = elapsed_ms(t);

    // Third pass: resolve queued edge targets per repository.
    let t = Instant::now();
    for (repo, edges_by_file) in &repo_edges {
        let stats =
            crate::resolver::resolve_repo_edges(conn, repo, edges_by_file).map_err(rusqlite_err)?;
        summary.resolution.exact += stats.exact;
        summary.resolution.ambiguous += stats.ambiguous;
        summary.resolution.unresolved += stats.unresolved;
    }
    // clear_repo demoted other repos' incoming edges to bare names; the
    // rebuilt symbols must re-resolve them.
    if let Some(repo) = repo_filter {
        let names: Vec<String> = {
            let mut stmt = conn
                .prepare(
                    "SELECT DISTINCT s.name FROM symbols s
                     JOIN files f ON s.file_id = f.id
                     WHERE f.repo_id = ?1 AND s.name IS NOT NULL",
                )
                .map_err(rusqlite_err)?;
            stmt.query_map([repo], |r| r.get::<_, String>(0))
                .map_err(rusqlite_err)?
                .collect::<Result<_, _>>()
                .map_err(rusqlite_err)?
        };
        let refs: Vec<&str> = names.iter().map(String::as_str).collect();
        crate::resolver::repair_incoming_edges(conn, &refs).map_err(rusqlite_err)?;
    }
    timings.resolve_ms = elapsed_ms(t);

    // Fourth pass: materialized module-import edges (not counted in the summary).
    let t = Instant::now();
    {
        let tx = conn.transaction().map_err(rusqlite_err)?;
        let import_edges = materialize_import_edges(&tx, &mut ids, None)?;
        tx.commit().map_err(rusqlite_err)?;
        timings.imports_ms = elapsed_ms(t);
        let _ = import_edges;
    }

    // Fifth pass: configured SCIP indexes as an edges-only overlay over the
    // resolved graph. Best-effort by contract: an unappliable index degrades
    // to tree-sitter edges, never a failed build.
    summary.scip = apply_scip_overlay(
        conn,
        workspace,
        &repos_seen
            .iter()
            .map(|(repo, _)| repo.clone())
            .collect::<Vec<_>>(),
        true,
        &mut |line| eprintln!("{line}"),
    );

    if let Some(repo) = repo_filter {
        conn.execute("DELETE FROM repo_build_state WHERE repo_id = ?", [repo])
            .map_err(rusqlite_err)?;
    }
    Ok(summary)
}

const SCIP_SKIP_IMPORT_FAILED: &str = "scip_import_failed";
const SCIP_SKIP_INDEX_MISSING: &str = "scip_index_missing";
const SCIP_SKIP_COMMANDS_NOT_ALLOWED: &str = "scip_commands_not_allowed";
/// Defensive catch-all for generation failures the indexers classify without a reason.
const SCIP_SKIP_GEN_FAILED: &str = "scip_gen_failed";

/// `scip.commands` runs repo-committed argv verbatim; it executes only with
/// explicit operator opt-in via a non-empty CAIRN_SCIP_COMMANDS.
fn scip_commands_allowed() -> bool {
    std::env::var("CAIRN_SCIP_COMMANDS").is_ok_and(|v| !v.is_empty())
}

/// Whether any indexed file's mtime is newer than the index file's (unix seconds).
fn index_is_stale(conn: &Connection, idx_path: &Path) -> bool {
    let max_mtime: Option<f64> = conn
        .query_row("SELECT MAX(mtime) FROM files", [], |r| r.get(0))
        .ok()
        .flatten();
    let index_mtime = std::fs::metadata(idx_path)
        .and_then(|m| m.modified())
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_secs_f64());
    max_mtime.is_some_and(|t| index_mtime.is_some_and(|i| t > i))
}

/// Apply the configured SCIP indexes as an edges-only overlay; None when none
/// is configured. Every failure degrades to tree-sitter edges with a skip row.
pub fn apply_scip_overlay(
    conn: &Connection,
    workspace: &str,
    repo_ids: &[String],
    generate_missing: bool,
    log: &mut dyn FnMut(&str),
) -> Option<ScipReport> {
    let (indexes, generated, commands) = match load_config(Path::new(workspace)) {
        Ok(cfg) => (cfg.scip_indexes, cfg.scip_generated, cfg.scip_commands),
        Err(e) => {
            log(&format!(
                "  [scip] config load failed ({e}); overlay skipped"
            ));
            return None;
        }
    };
    for (lang, _) in &commands {
        if !indexes.iter().any(|(l, _)| l == lang) && !generated.iter().any(|l| l == lang) {
            log(&format!(
                "  [scip] scip.commands.{lang} ignored: no scip.indexes or \
                 scip.generated entry for {lang}"
            ));
        }
    }
    if indexes.is_empty() && generated.is_empty() {
        return None;
    }

    // Skip attribution is workspace-scoped, not per-document, so a repo id is
    // only safe to guess in a single-repo workspace.
    let repo_id = match repo_ids {
        [only] => only.as_str(),
        _ => "",
    };
    let mut report = ScipReport::default();
    let ws_root = match std::fs::canonicalize(workspace) {
        Ok(root) => root,
        Err(e) => {
            log(&format!("  [scip] overlay skipped: {e}"));
            return Some(report);
        }
    };
    // Committed indexes resolve against the workspace root; `scip.generated`
    // languages resolve into the cairn store. The stale guard covers store
    // entries only: committed mtimes reflect checkout, not generation.
    let mut entries: Vec<(String, std::path::PathBuf, String, bool)> = indexes
        .iter()
        .map(|(lang, rel)| (lang.clone(), ws_root.join(rel), rel.clone(), false))
        .collect();
    let store_dir = (!generated.is_empty())
        .then(|| crate::scip::store_scip_dir(workspace))
        .flatten();
    for lang in &generated {
        match &store_dir {
            Some(dir) => {
                let path = dir.join(format!("{lang}.scip"));
                entries.push((
                    lang.clone(),
                    path.clone(),
                    path.to_string_lossy().into_owned(),
                    true,
                ));
            }
            None => {
                log(&format!(
                    "  [scip] {lang}: no cairn store for this workspace \
                     (run `cairn init`); generated index unavailable"
                ));
                record_scip_skip(
                    conn,
                    repo_id,
                    &format!("scip/generated/{lang}.scip"),
                    SCIP_SKIP_INDEX_MISSING,
                );
            }
        }
    }
    entries.sort();
    for (lang, idx_path, display_path, stale_guard) in &entries {
        // cairn owns store indexes: a stale one is rebuilt by the generation
        // path below rather than nagged about forever.
        if *stale_guard
            && generate_missing
            && idx_path.exists()
            && index_is_stale(conn, idx_path)
            && let Err(e) = std::fs::remove_file(idx_path)
        {
            log(&format!(
                "  [scip] {lang}: cannot remove stale store index ({e}); \
                 keeping tree-sitter edges"
            ));
        }
        if !idx_path.exists() {
            if !generate_missing {
                log(&format!(
                    "  [scip] {lang}: index {display_path} missing; keeping tree-sitter edges"
                ));
                record_scip_skip(conn, repo_id, display_path, SCIP_SKIP_INDEX_MISSING);
                continue;
            }
            let configured = commands.iter().find(|(cmd_lang, _)| cmd_lang == lang);
            let result = match configured {
                Some(_) if !scip_commands_allowed() => {
                    log(&format!(
                        "  [scip] {lang}: scip.commands entry ignored; running a \
                         repo-committed command line is unsafe -- set \
                         CAIRN_SCIP_COMMANDS=1 to allow it; keeping tree-sitter edges"
                    ));
                    record_scip_skip(conn, repo_id, display_path, SCIP_SKIP_COMMANDS_NOT_ALLOWED);
                    continue;
                }
                Some((_, argv)) => crate::scip::indexers::run_configured_command(
                    lang,
                    argv,
                    idx_path,
                    &ws_root.to_string_lossy(),
                    log,
                ),
                None => generate_index_result(lang, idx_path, &ws_root.to_string_lossy(), log),
            };
            if !result.ok {
                let reason = result.reason.unwrap_or(SCIP_SKIP_GEN_FAILED);
                record_scip_skip(conn, repo_id, display_path, reason);
                log(&format!(
                    "  [scip] {lang}: index generation failed ({reason}); \
                     keeping tree-sitter edges. {}",
                    result.detail
                ));
                continue;
            }
        }
        match import_scip_file_opts(conn, &idx_path.to_string_lossy(), workspace, *stale_guard) {
            Ok(record) => {
                report.edges += record.edges;
                report.disagreements += record.disagreements;
                report.upgrades += record.upgrades;
                if record.join_anomalies > 0 {
                    report.join_anomalies += record.join_anomalies;
                    log(&format!(
                        "  [scip] {lang}: {} file(s) kept tree-sitter edges (join anomaly)",
                        record.join_anomalies
                    ));
                }
                if record.stale_documents > 0 {
                    report.stale_files += record.stale_documents;
                    log(&format!(
                        "  [scip] {lang}: {} file(s) kept tree-sitter edges \
                         (file newer than index; regenerate the index)",
                        record.stale_documents
                    ));
                }
                report.external += record.external;
            }
            Err(e) => {
                log(&format!(
                    "  [scip] index import failed ({display_path}): {e}; \
                     keeping tree-sitter edges"
                ));
                record_scip_skip(conn, repo_id, display_path, SCIP_SKIP_IMPORT_FAILED);
            }
        }
    }
    Some(report)
}

/// Best-effort skip row: a lost row never fails the overlay.
fn record_scip_skip(conn: &Connection, repo_id: &str, path: &str, reason: &str) {
    let _ = replace_skip_row(conn, repo_id, path, reason);
}

fn elapsed_ms(t: Instant) -> f64 {
    t.elapsed().as_secs_f64() * 1000.0
}

fn rusqlite_err(e: rusqlite::Error) -> String {
    e.to_string()
}

fn configured_workers() -> usize {
    std::env::var("CAIRN_WORKERS")
        .ok()
        .and_then(|raw| raw.parse::<usize>().ok())
        .map(|n| n.clamp(1, 256))
        .unwrap_or_else(|| {
            std::thread::available_parallelism()
                .map(|n| n.get())
                .unwrap_or(4)
        })
}

fn store_file_holds_graph(db: &Path) -> bool {
    db.exists()
        && cairn_store::conn::open_read_only(db)
            .and_then(|conn| {
                conn.query_row(
                    "SELECT (SELECT COUNT(*) FROM files) + (SELECT COUNT(*) FROM symbols)",
                    [],
                    |r| r.get::<_, i64>(0),
                )
            })
            .is_ok_and(|count| count > 0)
}

/// Delete one repo's graph rows, demoting incoming edges to unresolved names.
fn clear_repo(conn: &Connection, repo: &str) -> Result<(), String> {
    const REPO_SYMBOLS: &str =
        "SELECT s.id FROM symbols s JOIN files f ON s.file_id = f.id WHERE f.repo_id = ?1";
    const REPO_FILES: &str = "SELECT id FROM files WHERE repo_id = ?1";

    let doomed_vec_rows: Vec<(String, i64)> = {
        let mut stmt = conn
            .prepare(&format!(
                "SELECT model, rowid FROM embeddings WHERE symbol_id IN ({REPO_SYMBOLS})"
            ))
            .map_err(rusqlite_err)?;
        stmt.query_map([repo], |r| Ok((r.get(0)?, r.get(1)?)))
            .map_err(rusqlite_err)?
            .collect::<Result<_, _>>()
            .map_err(rusqlite_err)?
    };

    let statements = [
        "DELETE FROM parse_errors WHERE repo_id = ?1".to_string(),
        "DELETE FROM skipped_files WHERE repo_id = ?1".to_string(),
        format!(
            "UPDATE edges SET target_name = COALESCE(target_name, \
               (SELECT name FROM symbols WHERE id = edges.target_id)), \
             target_id = NULL, resolution = 'unresolved' \
             WHERE target_id IN ({REPO_SYMBOLS})"
        ),
        format!("DELETE FROM edges WHERE source_id IN ({REPO_SYMBOLS})"),
        format!(
            "UPDATE imports SET resolved_symbol_id = NULL \
             WHERE resolved_symbol_id IN ({REPO_SYMBOLS})"
        ),
        format!("DELETE FROM imports WHERE file_id IN ({REPO_FILES})"),
        format!("DELETE FROM rationale WHERE file_id IN ({REPO_FILES})"),
        format!("DELETE FROM embeddings WHERE symbol_id IN ({REPO_SYMBOLS})"),
        format!("DELETE FROM embeddings_mv WHERE symbol_id IN ({REPO_SYMBOLS})"),
        format!("DELETE FROM symbols WHERE file_id IN ({REPO_FILES})"),
        "DELETE FROM files WHERE repo_id = ?1".to_string(),
    ];
    for sql in &statements {
        conn.execute(sql, [repo]).map_err(rusqlite_err)?;
    }

    // Stale vec0 rows would pair a reused embeddings rowid with an unrelated vector.
    let mut by_model: HashMap<&str, Vec<i64>> = HashMap::new();
    for (model, rowid) in &doomed_vec_rows {
        by_model.entry(model).or_default().push(*rowid);
    }
    for (model, rowids) in by_model {
        cairn_store::vec0::delete_index_rows(conn, model, &rowids).map_err(rusqlite_err)?;
    }
    Ok(())
}

/// Skips are content-keyed so the same scan yields the same row ids.
fn skip_id(s: &scanner::SkipInfo) -> String {
    use sha2::{Digest, Sha256};
    let mut hasher = Sha256::new();
    hasher.update(b"skipped_files\0");
    hasher.update(s.repo.as_bytes());
    hasher.update([0]);
    hasher.update(s.rel_path.as_bytes());
    hasher.update([0]);
    hasher.update(s.reason.as_bytes());
    let digest = hasher.finalize();
    digest.iter().map(|b| format!("{b:02x}")).collect()
}

fn record_skips(conn: &Connection, skips: &[scanner::SkipInfo]) -> Result<usize, String> {
    let mut recorded = 0;
    for s in skips {
        let result = conn.execute(
            "INSERT INTO skipped_files
               (id, repo_id, path, reason, size_bytes, recorded_at)
             VALUES (?, ?, ?, ?, ?, ?)",
            rusqlite::params![
                skip_id(s),
                s.repo,
                s.rel_path,
                s.reason,
                s.size_bytes,
                now_iso()
            ],
        );
        // A bad skip row is not worth failing the build over.
        if result.is_ok() {
            recorded += 1;
        }
    }
    Ok(recorded)
}

pub(crate) fn insert_parse_error(
    conn: &Connection,
    ids: &mut IdGen,
    repo: &str,
    path: &str,
    error_message: &str,
    stack_trace: Option<&str>,
) -> Result<(), String> {
    ensure_repo_row(conn, repo)?;
    conn.execute(
        "INSERT INTO parse_errors (id, file_path, repo_id, error_message, stack_trace, timestamp)
         VALUES (?, ?, ?, ?, ?, ?)",
        rusqlite::params![
            ids.next_id(),
            path,
            repo,
            error_message,
            stack_trace,
            now_iso()
        ],
    )
    .map_err(rusqlite_err)?;
    Ok(())
}

fn ensure_repo_row(conn: &Connection, repo: &str) -> Result<(), String> {
    conn.execute(
        "INSERT INTO repos (id, name, path, language, git_remote, indexed_at)
         VALUES (?, ?, '.', '', NULL, ?)
         ON CONFLICT(id) DO NOTHING",
        rusqlite::params![repo, repo, now_iso()],
    )
    .map_err(rusqlite_err)?;
    Ok(())
}

fn git_remote_url(repo_path: &str) -> Option<String> {
    let output = std::process::Command::new("git")
        .args(["config", "--get", "remote.origin.url"])
        .current_dir(repo_path)
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if text.is_empty() { None } else { Some(text) }
}

/// Same-named owner id and its declared line span; the module symbol has none.
type OwnerCandidate = (String, Option<(usize, usize)>);

fn enclosing_owner(
    owners: &[OwnerCandidate],
    line: usize,
    exclude: Option<&str>,
    decorator: bool,
) -> Option<String> {
    let candidates = || {
        owners
            .iter()
            .filter(move |(id, _)| Some(id.as_str()) != exclude)
            .filter_map(|(id, span)| span.map(|span| (id, span)))
    };
    // A decorator line sits above its definition's span, so the nearest
    // following definition owns it — never an enclosing same-named one.
    decorator
        .then(|| {
            candidates()
                .filter(|(_, (start, _))| *start >= line)
                .min_by_key(|(_, (start, _))| *start)
                .map(|(id, _)| id)
        })
        .flatten()
        .or_else(|| {
            candidates()
                .filter(|(_, (start, end))| (*start..=*end).contains(&line))
                .min_by_key(|(_, (start, end))| end - start)
                .map(|(id, _)| id)
        })
        // No span holds the line: keep declaration-order first-wins.
        .or_else(|| owners.first().map(|(id, _)| id))
        .cloned()
}

/// Insert one parsed file and return symbol, edge, and import counts.
#[allow(clippy::too_many_arguments)]
pub(crate) fn insert_parsed_file(
    conn: &Connection,
    ids: &mut IdGen,
    repo: &str,
    rel_path: &str,
    abs_path: &str,
    language: &str,
    file_hash: &str,
    pf: &mut ParsedFile,
    repo_edges: &mut RepoEdges,
) -> Result<(usize, usize, usize), String> {
    ensure_repo_row(conn, repo)?;
    let file_id = ids.next_id();
    let (file_size, file_mtime) = match std::fs::metadata(abs_path) {
        Ok(st) => (
            st.len() as i64,
            crate::timefmt::mtime_f64(&st).unwrap_or(0.0),
        ),
        Err(_) => (0, 0.0),
    };
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language, hash, line_count,
           indexed_at, size, mtime)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rusqlite::params![
            file_id,
            repo,
            rel_path,
            language,
            file_hash,
            pf.line_count as i64,
            now_iso(),
            file_size,
            file_mtime
        ],
    )
    .map_err(rusqlite_err)?;

    // Framework route + service-call detection merges before insertion; both
    // are best-effort sugar that never fails the file's indexing.
    if let Some(route_extraction) = routes::detect_routes(pf, language) {
        pf.symbols.extend(route_extraction.routes);
        pf.edges.extend(route_extraction.references);
    }
    if let Some(sc_extraction) = service_calls::detect_service_calls(pf, language) {
        pf.edges.extend(sc_extraction.edges);
    }

    let file_imports_summary: Option<String> = {
        let joined = pf
            .imports
            .iter()
            .take(20)
            .map(|imp| imp.imported_path.clone())
            .collect::<Vec<_>>()
            .join(", ");
        if joined.is_empty() {
            None
        } else {
            Some(joined)
        }
    };

    // Symbol ids in declaration order drive contains edges and rationale spans.
    let mut sym_ids: Vec<String> = Vec::with_capacity(pf.symbols.len());
    let mut qname_ids: HashMap<String, Vec<OwnerCandidate>> = HashMap::new();
    let mut in_file: HashMap<String, Vec<OwnerCandidate>> = HashMap::new();
    for sym in &mut pf.symbols {
        let sym_id = ids.next_id();
        if sym.parent_scope.is_none()
            && let Some(qname) = &sym.qualified_name
            && qname.contains('.')
        {
            sym.parent_scope = qname.rsplit_once('.').map(|(scope, _)| scope.to_string());
        }
        if sym.imports_summary.is_none() {
            sym.imports_summary = file_imports_summary.clone();
        }
        if let Some(qname) = &sym.qualified_name {
            qname_ids
                .entry(qname.clone())
                .or_default()
                .push((sym_id.clone(), Some((sym.line_start, sym.line_end))));
        }
        in_file
            .entry(sym.name.clone())
            .or_default()
            .push((sym_id.clone(), Some((sym.line_start, sym.line_end))));
        sym_ids.push(sym_id);
    }

    let module_id = ids.next_id();
    let module_name = Path::new(rel_path)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or_default()
        .to_string();
    let module_qname = module_dotted(rel_path);
    in_file
        .entry(module_name.clone())
        .or_default()
        .push((module_id.clone(), None));

    // The module symbol lands after declared symbols (insert order).
    {
        let mut stmt = conn
            .prepare(
                "INSERT INTO symbols (id, file_id, name, qualified_name, kind, line_start,
                   line_end, column_start, column_end, docstring, modifiers, metadata,
                   parameters, return_type, parent_scope, imports_summary, body, arity, source)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'tree_sitter')",
            )
            .map_err(rusqlite_err)?;
        for (sym_id, sym) in sym_ids.iter().zip(&pf.symbols) {
            stmt.execute(rusqlite::params![
                sym_id,
                file_id,
                sym.name,
                sym.qualified_name,
                sym.kind,
                sym.line_start as i64,
                sym.line_end as i64,
                sym.column_start as i64,
                sym.column_end as i64,
                sym.docstring,
                json_modifiers(&sym.modifiers),
                sym.metadata
                    .as_ref()
                    .map(|m| dumps_compact(&Value::Object(m.clone()))),
                sym.parameters,
                sym.return_type,
                sym.parent_scope,
                sym.imports_summary,
                sym.body,
                sym.arity.map(|a| a as i64),
            ])
            .map_err(rusqlite_err)?;
        }
        stmt.execute(rusqlite::params![
            module_id,
            file_id,
            module_name,
            module_qname,
            "module",
            1,
            1,
            0,
            0,
            Option::<String>::None,
            "[]",
            Option::<String>::None,
            Option::<String>::None,
            Option::<String>::None,
            Option::<String>::None,
            file_imports_summary,
            Option::<String>::None,
            Option::<i64>::None,
        ])
        .map_err(rusqlite_err)?;
    }

    {
        let mut stmt = conn
            .prepare(
                "INSERT INTO imports (id, file_id, imported_path, resolved_symbol_id, line, local_alias)
                 VALUES (?, ?, ?, ?, ?, ?)",
            )
            .map_err(rusqlite_err)?;
        for imp in &pf.imports {
            stmt.execute(rusqlite::params![
                ids.next_id(),
                file_id,
                imp.imported_path,
                Option::<String>::None,
                imp.line as i64,
                imp.local_alias,
            ])
            .map_err(rusqlite_err)?;
        }
    }

    let mut edge_rows: usize = 0;
    {
        let mut stmt = conn
            .prepare(
                "INSERT INTO edges (id, source_id, target_id, target_name, kind, line, column,
                   resolution, source)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'tree_sitter')",
            )
            .map_err(rusqlite_err)?;

        // Contains edges: parent is the qualified scope, else the file module.
        for (sym_id, sym) in sym_ids.iter().zip(&pf.symbols) {
            let parent_id = match &sym.parent_scope {
                Some(scope) => qname_ids
                    .get(scope)
                    .and_then(|owners| enclosing_owner(owners, sym.line_start, Some(sym_id), false))
                    .or_else(|| {
                        let last = scope
                            .rsplit_once('.')
                            .map(|(_, last)| last)
                            .unwrap_or(scope);
                        in_file.get(last).and_then(|owners| {
                            enclosing_owner(owners, sym.line_start, Some(sym_id), false)
                        })
                    }),
                None => Some(module_id.clone()),
            };
            let Some(parent_id) = parent_id else { continue };
            stmt.execute(rusqlite::params![
                ids.next_id(),
                parent_id,
                sym_id,
                sym.name,
                "contains",
                sym.line_start as i64,
                sym.column_start as i64,
                "exact",
            ])
            .map_err(rusqlite_err)?;
            edge_rows += 1;
        }

        // Reference edges: source resolved now; target left for the resolver.
        let repo_entry = match repo_edges.iter_mut().find(|(r, _)| r == repo) {
            Some((_, files)) => files,
            None => {
                repo_edges.push((repo.to_string(), Vec::new()));
                &mut repo_edges.last_mut().expect("pushed").1
            }
        };
        let signals = match repo_entry.iter_mut().find(|(fid, _)| fid == &file_id) {
            Some((_, signals)) => signals,
            None => {
                repo_entry.push((file_id.clone(), Vec::new()));
                &mut repo_entry.last_mut().expect("pushed").1
            }
        };

        for edge in &pf.edges {
            let source_id = match in_file.get(&edge.source_name) {
                Some(owners) => enclosing_owner(owners, edge.line, None, edge.kind == "decorates"),
                None => {
                    if edge.source_name.is_empty() {
                        Some(module_id.clone())
                    } else {
                        continue; // owner name not declared in this file; cannot attach
                    }
                }
            };
            let Some(source_id) = source_id else { continue };
            let edge_id = ids.next_id();
            stmt.execute(rusqlite::params![
                edge_id,
                source_id,
                Option::<String>::None,
                edge.target_name,
                edge.kind,
                edge.line as i64,
                edge.column as i64,
                Option::<String>::None,
            ])
            .map_err(rusqlite_err)?;
            edge_rows += 1;
            signals.push(EdgeSignal {
                edge_id,
                target_name: edge.target_name.clone(),
                receiver_type: edge.receiver_type.clone(),
                call_arity: edge.call_arity.map(|a| a as i64),
                generic_tier: edge.generic_tier,
            });
        }
    }

    if !pf.rationale.is_empty() {
        let spans: Vec<(String, usize, usize)> = sym_ids
            .iter()
            .zip(&pf.symbols)
            .filter(|(_, sym)| CALLABLE_OR_TYPE_KINDS.contains(&sym.kind.as_str()))
            .map(|(sym_id, sym)| (sym_id.clone(), sym.line_start, sym.line_end))
            .collect();
        let mut stmt = conn
            .prepare(
                "INSERT INTO rationale (id, file_id, symbol_id, line, kind, text)
                 VALUES (?, ?, ?, ?, ?, ?)",
            )
            .map_err(rusqlite_err)?;
        for record in &pf.rationale {
            let mut symbol_id: Option<String> = None;
            let mut best: Option<usize> = None;
            for (sym_id, line_start, line_end) in &spans {
                let width = line_end.saturating_sub(*line_start);
                if *line_start <= record.line
                    && record.line <= *line_end
                    && best.is_none_or(|b| width < b)
                {
                    symbol_id = Some(sym_id.clone());
                    best = Some(width);
                }
            }
            stmt.execute(rusqlite::params![
                ids.next_id(),
                file_id,
                symbol_id,
                record.line as i64,
                record.kind,
                record.text
            ])
            .map_err(rusqlite_err)?;
        }
    }

    Ok((sym_ids.len() + 1, edge_rows, pf.imports.len()))
}

/// `json.dumps(list)` shape: `["a", "b"]`.
fn json_modifiers(modifiers: &[String]) -> String {
    let items: Vec<Value> = modifiers.iter().cloned().map(Value::String).collect();
    dumps_compact(&Value::Array(items))
}

/// Package-aware dotted module path (__init__ collapses to its package).
fn module_dotted(rel_path: &str) -> String {
    let path = Path::new(rel_path);
    let mut parts: Vec<String> = path
        .parent()
        .unwrap_or_else(|| Path::new(""))
        .components()
        .filter_map(|c| match c {
            std::path::Component::Normal(p) => Some(p.to_string_lossy().into_owned()),
            _ => None,
        })
        .collect();
    let stem = path
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or_default()
        .to_string();
    parts.push(stem);
    if parts.len() > 1 && parts.last().is_some_and(|last| last == "__init__") {
        parts.pop();
    }
    parts.join(".")
}

/// Dotted suffixes, longest first ("a.b.c" -> ["a.b.c", "b.c", "c"]).
fn dotted_suffixes(dotted: &str) -> Vec<String> {
    let segs: Vec<&str> = dotted.split('.').filter(|s| !s.is_empty()).collect();
    (0..segs.len()).map(|i| segs[i..].join(".")).collect()
}

/// Normalize an import token to a dotted module path.
fn norm_module_token(token: &str) -> String {
    let t: String = token
        .trim()
        .trim_matches(|c| c == '\'' || c == '"')
        .replace('\\', ".");
    t.replace('/', ".")
        .split('.')
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join(".")
}

/// Normalized import bases across supported parser dialects.
fn import_module_bases(raw: &str) -> Vec<String> {
    let text = raw.trim().trim_end_matches(';').trim();
    let mut bases: Vec<String> = Vec::new();

    let mut add = |module_token: &str, name_tokens: &[String]| {
        let module = norm_module_token(module_token);
        if module.is_empty() {
            return;
        }
        for name in name_tokens {
            let n = name.trim().trim_matches(['{', '}']);
            let n = n.split(" as ").next().unwrap_or("").trim();
            if !n.is_empty() {
                bases.push(format!("{module}.{}", norm_module_token(n)));
            }
        }
        bases.push(module);
    };

    if let Some(rest) = text.strip_prefix("from ") {
        let rest = rest.trim();
        if let Some((module_tok, names_part)) = rest.split_once(" import ") {
            let names: Vec<String> = names_part.split(',').map(str::to_string).collect();
            add(module_tok, &names);
        } else {
            add(rest, &[]);
        }
    } else if let Some(rest) = text.strip_prefix("import ") {
        let rest = rest.trim();
        if let Some((names_part, module_tok)) = rest.split_once(" from ") {
            let names: Vec<String> = names_part
                .trim_matches(|c: char| c == '{' || c == '}' || c.is_whitespace())
                .split(',')
                .map(str::to_string)
                .collect();
            add(module_tok, &names);
        } else {
            let mut rest = rest.split(" as ").next().unwrap_or(rest).trim().to_string();
            if let Some(static_rest) = rest.strip_prefix("static ") {
                rest = static_rest.trim().to_string();
            }
            let norm = norm_module_token(&rest);
            if !norm.is_empty() {
                bases.push(norm);
            }
        }
    } else {
        let norm = norm_module_token(text);
        if !norm.is_empty() {
            bases.push(norm.clone());
            if let Some((parent, last)) = norm.rsplit_once('.')
                && !parent.is_empty()
                && last != norm
            {
                bases.push(parent.to_string());
            }
        }
    }
    bases.retain(|b| !b.is_empty());
    bases
}

/// Rebuild import edges for `repo` (all repos when None) and return the count.
pub(crate) fn materialize_import_edges(
    conn: &Connection,
    ids: &mut IdGen,
    repo: Option<&str>,
) -> Result<usize, String> {
    let module_rows: Vec<(String, String, String, String)> = {
        let mut stmt = conn
            .prepare(
                "SELECT s.id AS mid, f.id AS fid, f.path, f.repo_id
                 FROM symbols s JOIN files f ON s.file_id = f.id
                 WHERE s.kind = 'module'",
            )
            .map_err(rusqlite_err)?;

        stmt.query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)))
            .map_err(rusqlite_err)?
            .collect::<Result<Vec<_>, _>>()
            .map_err(rusqlite_err)?
    };
    if module_rows.is_empty() {
        return Ok(0);
    }

    let mut module_by_fid: HashMap<String, String> = HashMap::new();
    let mut module_qname_by_fid: HashMap<String, String> = HashMap::new();
    let mut files_by_candidate: HashMap<String, Vec<String>> = HashMap::new();
    for (mid, fid, path, _) in &module_rows {
        if module_by_fid.contains_key(fid) {
            continue;
        }
        let dotted = module_dotted(path);
        module_by_fid.insert(fid.clone(), mid.clone());
        module_qname_by_fid.insert(fid.clone(), dotted.clone());
        for cand in dotted_suffixes(&dotted) {
            files_by_candidate
                .entry(cand)
                .or_default()
                .push(fid.clone());
        }
    }

    // Only the deleted sources and the re-read imports scope to `repo`;
    // candidate maps above stay global so cross-repo modules still match.
    let source_ids: Vec<String> = match repo {
        Some(repo) => module_rows
            .iter()
            .filter(|(.., r)| r == repo)
            .map(|(mid, ..)| mid.clone())
            .collect(),
        None => module_rows.iter().map(|(mid, ..)| mid.clone()).collect(),
    };
    if source_ids.is_empty() {
        return Ok(0);
    }

    let import_rows: Vec<(String, String, Option<i64>)> = match repo {
        Some(repo) => {
            let mut stmt = conn
                .prepare(
                    "SELECT i.file_id, i.imported_path, i.line FROM imports i
                     WHERE i.file_id IN (SELECT id FROM files WHERE repo_id = ?)",
                )
                .map_err(rusqlite_err)?;
            stmt.query_map([repo], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))
                .map_err(rusqlite_err)?
                .collect::<Result<Vec<_>, _>>()
                .map_err(rusqlite_err)?
        }
        None => {
            let mut stmt = conn
                .prepare("SELECT file_id, imported_path, line FROM imports")
                .map_err(rusqlite_err)?;
            stmt.query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))
                .map_err(rusqlite_err)?
                .collect::<Result<Vec<_>, _>>()
                .map_err(rusqlite_err)?
        }
    };
    {
        let placeholders = source_ids.iter().map(|_| "?").collect::<Vec<_>>().join(",");
        let sql =
            format!("DELETE FROM edges WHERE kind = 'imports' AND source_id IN ({placeholders})");
        let params: Vec<&dyn rusqlite::ToSql> = source_ids
            .iter()
            .map(|s| s as &dyn rusqlite::ToSql)
            .collect();
        conn.execute(&sql, params.as_slice())
            .map_err(rusqlite_err)?;
    }

    let mut inserted = 0usize;
    for (file_id, imported_path, line) in &import_rows {
        let Some(source_mid) = module_by_fid.get(file_id) else {
            continue;
        };
        for base in import_module_bases(imported_path) {
            let mut matched: Option<String> = None;
            for cand in dotted_suffixes(&base) {
                let Some(fids) = files_by_candidate.get(&cand) else {
                    continue; // try a shorter suffix
                };
                if fids.len() == 1 {
                    matched = Some(fids[0].clone());
                    break;
                }
                break; // ambiguous at this length; shorter is never safer
            }
            let Some(matched) = matched else { continue };
            if matched == *file_id {
                continue;
            }
            conn.execute(
                "INSERT INTO edges (id, source_id, target_id, target_name, kind, line, column,
                   resolution, source)
                 VALUES (?, ?, ?, ?, 'imports', ?, 0, 'exact', 'tree_sitter')",
                rusqlite::params![
                    ids.next_id(),
                    source_mid,
                    module_by_fid.get(&matched),
                    module_qname_by_fid.get(&matched),
                    line.unwrap_or(0),
                ],
            )
            .map_err(rusqlite_err)?;
            inserted += 1;
            break; // first base that resolves wins
        }
    }
    Ok(inserted)
}
