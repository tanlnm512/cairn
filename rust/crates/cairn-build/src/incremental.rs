//! Incremental graft: per-file delete+reinsert with resolver repair, plus
//! change detection (git listings unioned with stat drift) and boot catch-up.

use std::collections::{BTreeSet, HashMap, HashSet};
use std::path::{Component, Path, PathBuf};

use rusqlite::{Connection, params};

use crate::builder::{IdGen, RepoEdges, insert_parse_error, insert_parsed_file};
use crate::parse_dispatch;
use crate::scanner;
use crate::timefmt::mtime_f64;

/// Counts and failures of one reindex pass.
#[derive(Debug, Clone, Default)]
pub struct ReindexResult {
    pub reindexed: usize,
    pub deleted: usize,
    pub errors: Vec<String>,
    /// Ids of the symbols inserted by the reindexed files (post-commit).
    pub reindexed_symbol_ids: Vec<String>,
    /// Model stamps of the embedding rows the delete legs removed.
    pub dropped_embedding_models: Vec<String>,
}

/// Summary of one `incremental_update` pass.
#[derive(Debug, Clone, Default)]
pub struct UpdateSummary {
    pub repos_scanned: usize,
    pub files_reindexed: usize,
    pub files_deleted: usize,
    pub errors: Vec<String>,
    /// Symbols whose embeddings the reindex dropped and that still lack a
    /// current-model row (0 when a real backend re-embedded them).
    pub symbols_awaiting_embed: usize,
}

/// Reindex absolute paths (edit, create, or delete) and repair the graph.
pub fn reindex_paths(conn: &mut Connection, workspace: &str, paths: &[String]) -> ReindexResult {
    let mut result = ReindexResult::default();
    let mut ids = match IdGen::continuing(conn) {
        Ok(ids) => ids,
        Err(e) => {
            result.errors.push(e);
            return result;
        }
    };
    // Queued signals grouped by repo (insertion-ordered)
    // for the batched resolver re-run, plus the names whose global candidate
    // count changed for incoming-edge repair.
    let mut repo_edges: RepoEdges = Vec::new();
    let mut changed_target_names: BTreeSet<String> = BTreeSet::new();
    // Consumer repos whose imports edges the delete-leg backfill nulls; they
    // need a materialize pass even with no file of their own reindexed.
    let mut imports_refresh_repos: Vec<String> = Vec::new();
    // (model, rowid) of embedding rows deleted by committed legs; their vec0
    // mirrors must go with them.
    let mut doomed_vec_rows: Vec<(String, i64)> = Vec::new();

    for abs_path in paths {
        let Some((repo, rel_to_repo)) = repo_relative_path(workspace, abs_path) else {
            continue;
        };
        let tracked = find_tracked_file_row(conn, abs_path, &repo, &rel_to_repo);
        let (stored_repo, stored_path, file_id) = match &tracked {
            Some(row) => (row.1.clone(), row.2.clone(), Some(row.0.clone())),
            None => (repo.clone(), rel_to_repo.clone(), None),
        };

        let mut deleted_names: HashSet<String> = HashSet::new();
        if let Some(fid) = &file_id
            && let Ok(mut stmt) =
                conn.prepare("SELECT name FROM symbols WHERE file_id = ? AND name IS NOT NULL")
        {
            let rows = stmt.query_map([fid], |r| r.get::<_, String>(0));
            if let Ok(rows) = rows {
                for name in rows.flatten() {
                    deleted_names.insert(name);
                }
            }
        }

        let tx = match conn.transaction() {
            Ok(tx) => tx,
            Err(e) => {
                result.errors.push(format!("{abs_path}: {e}"));
                continue;
            }
        };

        // Keep each file replacement atomic: the whole delete leg plus the
        // re-parse insert commit together or roll back together.
        if let Some(fid) = &file_id {
            // Must run before the backfill nulls target_id: it decides which
            // consumer repos join the imports refresh below.
            if let Ok(mut stmt) = tx.prepare(
                "SELECT DISTINCT f.repo_id FROM edges e \
                 JOIN symbols s ON s.id = e.source_id \
                 JOIN files f ON f.id = s.file_id \
                 WHERE e.kind = 'imports' \
                   AND e.target_id IN (SELECT id FROM symbols WHERE file_id = ?)",
            ) && let Ok(rows) = stmt.query_map([fid], |r| r.get::<_, String>(0))
            {
                for repo in rows.flatten() {
                    if !imports_refresh_repos.contains(&repo) {
                        imports_refresh_repos.push(repo);
                    }
                }
            }
            let legs: [(&str, &str); 4] = [
                (
                    "DELETE FROM edges WHERE source_id IN (SELECT id FROM symbols WHERE file_id = ?)",
                    fid,
                ),
                (
                    "UPDATE imports SET resolved_symbol_id = NULL WHERE resolved_symbol_id IN (SELECT id FROM symbols WHERE file_id = ?)",
                    fid,
                ),
                ("DELETE FROM imports WHERE file_id = ?", fid),
                ("DELETE FROM rationale WHERE file_id = ?", fid),
            ];
            let mut failed: Option<String> = None;
            let mut doomed: Vec<(String, i64)> = Vec::new();
            for (sql, param) in legs {
                if let Err(e) = tx.execute(sql, [param]) {
                    failed = Some(e.to_string());
                    break;
                }
            }
            // Backfill names before nulling targets so repair can find them.
            if failed.is_none() {
                let backfill = "UPDATE edges SET \
                  target_name = COALESCE(target_name, \
                    (SELECT name FROM symbols WHERE id = edges.target_id)), \
                  target_id = NULL, resolution = 'unresolved' \
                  WHERE target_id IN (SELECT id FROM symbols WHERE file_id = ?)";
                if let Err(e) = tx.execute(backfill, [fid]) {
                    failed = Some(e.to_string());
                }
            }
            // Embedding rows FK-reference symbols(id); clear them BEFORE the
            // symbol delete or the FK blocks it. Their rowids are collected
            // first so the vec0 mirrors can follow them out.
            if failed.is_none() {
                match embedding_rowids(&tx, fid) {
                    Ok(rowids) => doomed = rowids,
                    Err(e) => failed = Some(e),
                }
            }
            if failed.is_none() {
                for table in ["embeddings", "embeddings_mv"] {
                    let sql = format!(
                        "DELETE FROM {table} WHERE symbol_id IN \
                         (SELECT id FROM symbols WHERE file_id = ?)"
                    );
                    if let Err(e) = tx.execute(&sql, [fid]) {
                        failed = Some(e.to_string());
                        break;
                    }
                }
            }
            for (sql, param) in [
                ("DELETE FROM symbols WHERE file_id = ?", fid),
                ("DELETE FROM parse_errors WHERE file_path = ?", &stored_path),
                ("DELETE FROM files WHERE id = ?", fid),
            ] {
                if failed.is_none()
                    && let Err(e) = tx.execute(sql, [param])
                {
                    failed = Some(e.to_string());
                }
            }
            if let Some(e) = failed {
                drop(tx);
                record_failure(
                    conn,
                    &mut ids,
                    &stored_repo,
                    &rel_to_repo,
                    abs_path,
                    &e,
                    &mut result,
                );
                continue;
            }
            doomed_vec_rows.append(&mut doomed);
        }

        // Check if file still exists on disk.
        if !Path::new(abs_path).exists() {
            // Only a tracked file counts as deleted; a ghost path never in the
            // DB is a no-op.
            if file_id.is_some() {
                result.deleted += 1;
                changed_target_names.extend(deleted_names);
            }
            let _ = tx.execute(
                "DELETE FROM pending_sync WHERE path IN (?, ?)",
                params![abs_path, stored_path],
            );
            if let Err(e) = tx.commit() {
                result.errors.push(format!("{abs_path}: {e}"));
            }
            continue;
        }

        let Some(suffix) = Path::new(abs_path)
            .extension()
            .and_then(|e| e.to_str())
            .map(|e| format!(".{e}"))
        else {
            let _ = tx.commit();
            continue;
        };
        if scanner::extension_language(&suffix).is_none() {
            let _ = tx.commit();
            continue;
        }
        let language = scanner::resolve_file_language(&suffix, Path::new(abs_path));
        if !parse_dispatch::is_language_available(&language) {
            // Fresh builds skip unavailable parsers; strict refresh converges.
            let _ = tx.execute(
                "DELETE FROM pending_sync WHERE path IN (?, ?)",
                params![rel_to_repo, abs_path],
            );
            let committed = tx.commit();
            result.deleted += 1;
            if file_id.is_some() {
                changed_target_names.extend(deleted_names);
            }
            if let Err(e) = committed {
                result.errors.push(format!("{abs_path}: {e}"));
            }
            continue;
        }

        let outcome = run_graft_leg(
            &tx,
            &mut ids,
            &stored_repo,
            &rel_to_repo,
            abs_path,
            &language,
            &mut repo_edges,
        );
        match outcome {
            Ok(new_file_id) => {
                result.reindexed += 1;
                let _ = tx.execute(
                    "DELETE FROM pending_sync WHERE path IN (?, ?)",
                    params![rel_to_repo, abs_path],
                );
                let mut changed_names = deleted_names;
                if let Some(new_fid) = &new_file_id
                    && let Ok(mut stmt) =
                        tx.prepare("SELECT DISTINCT name FROM symbols WHERE file_id = ?")
                {
                    let rows = stmt.query_map([new_fid], |r| r.get::<_, String>(0));
                    if let Ok(rows) = rows {
                        for name in rows.flatten() {
                            changed_names.insert(name);
                        }
                    }
                }
                let committed = tx.commit();
                changed_target_names.extend(changed_names);
                match committed {
                    Ok(()) => {
                        if let Some(new_fid) = &new_file_id
                            && let Ok(mut stmt) =
                                conn.prepare("SELECT id FROM symbols WHERE file_id = ?")
                        {
                            let rows = stmt.query_map([new_fid], |r| r.get::<_, String>(0));
                            if let Ok(rows) = rows {
                                result.reindexed_symbol_ids.extend(rows.flatten());
                            }
                        }
                    }
                    Err(e) => result.errors.push(format!("{abs_path}: {e}")),
                }
            }
            Err(e) => {
                drop(tx);
                record_failure(
                    conn,
                    &mut ids,
                    &stored_repo,
                    &rel_to_repo,
                    abs_path,
                    &e,
                    &mut result,
                );
            }
        }
    }

    // Batched resolver re-run for every re-parsed file.
    for (repo, edges_by_file) in &repo_edges {
        if let Err(e) = crate::resolver::resolve_repo_edges(conn, repo, edges_by_file) {
            result.errors.push(format!("resolver/{repo}: {e}"));
        }
    }

    // Repair pass: incoming edges whose target symbol was deleted+recreated
    // with a new id would otherwise silently drop after an update.
    if !changed_target_names.is_empty() {
        let sorted: Vec<&str> = changed_target_names.iter().map(|s| s.as_str()).collect();
        if let Err(e) = crate::resolver::repair_incoming_edges(conn, &sorted) {
            result.errors.push(format!("repair: {e}"));
        }
    }

    // Imports-edge refresh: recompute kind='imports' edges for every repo
    // that had a file reindexed or an imports edge nulled by the backfill
    // (delete+reinsert is materialize's idempotence contract).
    let mut repos: Vec<String> = repo_edges.iter().map(|(r, _)| r.clone()).collect();
    for repo in imports_refresh_repos {
        if !repos.contains(&repo) {
            repos.push(repo);
        }
    }
    for repo in repos {
        if let Err(e) = crate::builder::materialize_import_edges(conn, &mut ids, Some(&repo)) {
            result.errors.push(format!("imports-edges/{repo}: {e}"));
        }
    }

    // Stale vec0 rows would pair a reused embeddings rowid with an
    // unrelated vector.
    let mut by_model: HashMap<&str, Vec<i64>> = HashMap::new();
    for (model, rowid) in &doomed_vec_rows {
        by_model.entry(model.as_str()).or_default().push(*rowid);
    }
    for (model, rowids) in by_model {
        if let Err(e) = cairn_store::vec0::delete_index_rows(conn, model, &rowids) {
            result.errors.push(format!("vec0/{model}: {e}"));
        }
    }
    result.dropped_embedding_models = doomed_vec_rows
        .iter()
        .map(|(model, _)| model.clone())
        .collect();
    if result.reindexed > 0 || result.deleted > 0 {
        let _ = conn.execute(
            "INSERT INTO closure_state (key, value) VALUES ('stale', '1') \
             ON CONFLICT(key) DO UPDATE SET value = '1'",
            [],
        );
    }

    result
}

/// Best-effort embed-debt event so doctor can report a fully dropped index.
pub fn record_embed_debt(conn: &Connection, count: usize) {
    let _ = conn.execute(
        "INSERT INTO events (ts, name, session_id, attrs) VALUES (?, 'embed_debt', '', ?)",
        params![crate::timefmt::now_iso(), count.to_string()],
    );
}

/// The latest recorded embed-debt count; 0 when none or unreadable.
pub fn recorded_embed_debt(conn: &Connection) -> usize {
    conn.query_row(
        "SELECT attrs FROM events WHERE name = 'embed_debt' ORDER BY id DESC LIMIT 1",
        [],
        |r| r.get::<_, String>(0),
    )
    .ok()
    .and_then(|s| s.trim().parse().ok())
    .unwrap_or(0)
}

/// (model, rowid) of a file's embedding rows ahead of their delete.
fn embedding_rowids(conn: &Connection, file_id: &str) -> Result<Vec<(String, i64)>, String> {
    let mut stmt = conn
        .prepare(
            "SELECT model, rowid FROM embeddings WHERE symbol_id IN \
             (SELECT id FROM symbols WHERE file_id = ?)",
        )
        .map_err(|e| e.to_string())?;
    let rows = stmt
        .query_map([file_id], |r| {
            Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?))
        })
        .map_err(|e| e.to_string())?;
    rows.collect::<Result<_, _>>().map_err(|e| e.to_string())
}

/// Parse and insert one file inside the graft transaction; returns its new file id.
#[allow(clippy::too_many_arguments)]
fn run_graft_leg(
    tx: &Connection,
    ids: &mut IdGen,
    stored_repo: &str,
    rel_to_repo: &str,
    abs_path: &str,
    language: &str,
    repo_edges: &mut RepoEdges,
) -> Result<Option<String>, String> {
    let file_hash = scanner::file_sha256(Path::new(abs_path))?;
    let mut pf = parse_dispatch::parse_language_file(language, Path::new(abs_path))?;
    insert_parsed_file(
        tx,
        ids,
        stored_repo,
        rel_to_repo,
        abs_path,
        language,
        &file_hash,
        &mut pf,
        repo_edges,
    )?;
    let file_id: Option<String> = tx
        .query_row(
            "SELECT id FROM files WHERE repo_id = ? AND path = ?",
            params![stored_repo, rel_to_repo],
            |r| r.get(0),
        )
        .ok();
    Ok(file_id)
}

/// Roll the whole file leg back (caller dropped the tx) and record why.
fn record_failure(
    conn: &Connection,
    ids: &mut IdGen,
    stored_repo: &str,
    rel_to_repo: &str,
    abs_path: &str,
    err: &str,
    result: &mut ReindexResult,
) {
    result.errors.push(format!("{abs_path}: {err}"));
    let _ = insert_parse_error(conn, ids, stored_repo, rel_to_repo, err, None);
}

/// An absolute path's repository and repo-relative path (lexical, /-separated).
pub(crate) fn repo_relative_path(workspace: &str, abs_path: &str) -> Option<(String, String)> {
    let repo = scanner::infer_repo_for_path(workspace, Path::new(abs_path))?;
    let repo_path = scanner::resolve_repo_path(workspace, &repo).ok()?;
    let repo_norm = normalize_lexical(&repo_path);
    let abs_norm = normalize_lexical(Path::new(abs_path));
    let rel = abs_norm.strip_prefix(&repo_norm).ok()?;
    Some((
        repo,
        rel.to_string_lossy()
            .replace(std::path::MAIN_SEPARATOR, "/"),
    ))
}

/// `os.path.normpath` shape: collapse `.`, `..`, and duplicate separators.
pub(crate) fn normalize_lexical(path: &Path) -> PathBuf {
    let mut out: Vec<Component> = Vec::new();
    for c in path.components() {
        match c {
            Component::CurDir => {}
            Component::ParentDir => {
                let popped = match out.last() {
                    Some(Component::Normal(_)) | Some(Component::CurDir) => {
                        out.pop();
                        true
                    }
                    _ => false,
                };
                if !popped {
                    out.push(c);
                }
            }
            other => out.push(other),
        }
    }
    out.iter().collect()
}

/// The tracked row for an absolute path: `(id, repo_id, path)`, or None.
fn find_tracked_file_row(
    conn: &Connection,
    abs_path: &str,
    repo: &str,
    rel_to_repo: &str,
) -> Option<(String, String, String)> {
    let row = |path: &str, repo_id: &str| -> Option<(String, String, String)> {
        conn.query_row(
            "SELECT id, repo_id, path FROM files WHERE path = ? AND repo_id = ?",
            params![path, repo_id],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
        )
        .ok()
    };
    row(rel_to_repo, repo).or_else(|| row(abs_path, repo))
}

/// Reindex changed files; the entry the CLI update command drives.
pub fn incremental_update(
    workspace: &str,
    db_path: &str,
    repo: Option<&str>,
    diff_ref: Option<&str>,
) -> Result<UpdateSummary, String> {
    let repos: Vec<String> = match repo {
        Some(repo) => vec![repo.to_string()],
        None => scanner::discover_repo_records(workspace)?
            .into_iter()
            .map(|r| r.repo_id)
            .collect(),
    };
    let (_lock, mut conn) = cairn_store::swap::open_locked(Path::new(db_path))?;
    let mut all_paths: Vec<String> = Vec::new();
    let mut detect_errors: Vec<String> = Vec::new();
    for r in &repos {
        let repo_path = scanner::resolve_repo_path(workspace, r)?;
        match changed_source_files(workspace, &repo_path, Some(&conn), diff_ref) {
            Ok(changed) => {
                for warning in &changed.warnings {
                    detect_errors.push(format!("detect/{r}: {warning}"));
                }
                all_paths.extend(
                    changed
                        .paths
                        .into_iter()
                        .map(|rel| repo_path.join(rel).to_string_lossy().into_owned()),
                );
            }
            Err(e) => detect_errors.push(format!("detect/{r}: {e}")),
        }
    }

    let result = reindex_paths(&mut conn, workspace, &all_paths);
    detect_errors.extend(result.errors);
    let symbols_awaiting_embed = crate::embeddings::reembed_updated_symbols(
        &conn,
        workspace,
        &result.reindexed_symbol_ids,
        &result.dropped_embedding_models,
    );
    if symbols_awaiting_embed > 0 {
        record_embed_debt(&conn, symbols_awaiting_embed);
    }
    Ok(UpdateSummary {
        repos_scanned: repos.len(),
        files_reindexed: result.reindexed,
        files_deleted: result.deleted,
        errors: detect_errors,
        symbols_awaiting_embed,
    })
}

/// The `diff_ref` range's paths (Err on git failure), else worktree diff,
/// untracked files, and files-table stat drift.
pub(crate) fn changed_source_files(
    workspace: &str,
    repo_path: &Path,
    conn: Option<&Connection>,
    diff_ref: Option<&str>,
) -> Result<ChangedFiles, String> {
    if let Some(diff_ref) = diff_ref {
        let paths = git_paths(&["diff", "-z", "--name-only", diff_ref], repo_path)?;
        return Ok(with_warnings(filter_source_paths(paths), Vec::new()));
    }

    let mut paths: Vec<String> = Vec::new();
    let mut warnings: Vec<String> = Vec::new();
    // Each git leg degrades independently: a repo with no commits fails
    // `diff HEAD` but its untracked files still scan.
    match git_paths(&["diff", "-z", "--name-only", "HEAD"], repo_path) {
        Ok(diff) => paths.extend(diff),
        Err(e) => warnings.push(format!("git diff HEAD: {e}")),
    }
    match git_paths(
        &["ls-files", "-z", "--others", "--exclude-standard"],
        repo_path,
    ) {
        Ok(untracked) => paths.extend(untracked),
        Err(e) => warnings.push(format!("git ls-files --others: {e}")),
    }
    if let Some(conn) = conn {
        paths.extend(changed_via_stat(workspace, repo_path, conn));
    }
    let filtered = filter_source_paths(paths);
    warnings.retain(|w| !w.is_empty());
    Ok(with_warnings(filtered, warnings))
}

/// Paths plus detection warnings (empty when every leg ran clean).
#[derive(Debug, Clone, Default)]
pub struct ChangedFiles {
    pub paths: Vec<String>,
    pub warnings: Vec<String>,
}

fn with_warnings(paths: Vec<String>, warnings: Vec<String>) -> ChangedFiles {
    ChangedFiles { paths, warnings }
}

fn filter_source_paths(paths: Vec<String>) -> Vec<String> {
    let mut seen: HashSet<String> = HashSet::new();
    paths
        .into_iter()
        .filter(|path| {
            let mapped = Path::new(path)
                .extension()
                .and_then(|e| e.to_str())
                .map(|e| format!(".{e}"))
                .and_then(|s| scanner::extension_language(&s));
            !path.is_empty() && mapped.is_some() && seen.insert(path.clone())
        })
        .collect()
}

/// A NUL-separated git path listing; Err carries git's stderr.
fn git_paths(args: &[&str], cwd: &Path) -> Result<Vec<String>, String> {
    let output = std::process::Command::new("git")
        .args(args)
        .current_dir(cwd)
        .output()
        .map_err(|e| format!("git {}: {e}", args.join(" ")))?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
        let detail = if stderr.is_empty() {
            format!("exit {}", output.status)
        } else {
            stderr
        };
        return Err(format!("git {}: {detail}", args.join(" ")));
    }
    Ok(output
        .stdout
        .split(|b| *b == 0)
        .filter(|p| !p.is_empty())
        .map(|p| String::from_utf8_lossy(p).into_owned())
        .collect())
}

/// Size-equal mtime drift only: content-unchanged (and the stored stat
/// refreshed so the drift does not re-flag every pass) when the hash matches.
fn unchanged_by_hash(
    conn: &Connection,
    file_id: &str,
    path: &Path,
    stored_hash: Option<&str>,
    st: &std::fs::Metadata,
) -> bool {
    let Some(stored) = stored_hash.filter(|h| !h.is_empty()) else {
        return false;
    };
    match scanner::file_sha256(path) {
        Ok(current) if current == stored => {
            let _ = conn.execute(
                "UPDATE files SET size = ?, mtime = ? WHERE id = ?",
                params![st.len() as i64, mtime_f64(st).unwrap_or(0.0), file_id],
            );
            true
        }
        _ => false,
    }
}

/// One files-table stat row for drift detection.
struct FileStatRow {
    id: String,
    path: String,
    size: Option<i64>,
    mtime: Option<f64>,
    hash: Option<String>,
}

fn file_stat_rows(conn: &Connection, repo_id: &str) -> Option<Vec<FileStatRow>> {
    let mut stmt = conn
        .prepare("SELECT id, path, size, mtime, hash FROM files WHERE repo_id = ?")
        .ok()?;
    let rows = stmt
        .query_map([repo_id], |r| {
            Ok(FileStatRow {
                id: r.get(0)?,
                path: r.get(1)?,
                size: r.get(2)?,
                mtime: r.get(3)?,
                hash: r.get(4)?,
            })
        })
        .ok()?;
    Some(rows.collect::<Result<Vec<_>, _>>().unwrap_or_default())
}

/// Size/mtime comparison against the files table (the `cairn sync` signal).
fn changed_via_stat(workspace: &str, repo_path: &Path, conn: &Connection) -> Vec<String> {
    let repo_name = scanner::repository_id_for_path(workspace, repo_path);
    let file_rows = match file_stat_rows(conn, &repo_name) {
        Some(rows) => rows,
        None => return Vec::new(),
    };

    let mut changed: Vec<String> = Vec::new();
    let mut existing_rel: HashSet<String> = HashSet::new();
    for row in file_rows {
        existing_rel.insert(row.path.clone());
        let p = if Path::new(&row.path).is_absolute() {
            PathBuf::from(&row.path)
        } else {
            repo_path.join(&row.path)
        };
        let Ok(st) = std::fs::metadata(&p) else {
            changed.push(row.path); // deleted since last index
            continue;
        };
        let size_changed = st.len() as i64 != row.size.unwrap_or(0);
        let mtime_drifted = (mtime_f64(&st).unwrap_or(0.0) - row.mtime.unwrap_or(0.0)).abs() > 0.5;
        if size_changed
            || (mtime_drifted && !unchanged_by_hash(conn, &row.id, &p, row.hash.as_deref(), &st))
        {
            changed.push(row.path);
        }
    }

    // New source files not yet in the table.
    if let Ok((files, _)) = scanner::iter_files_walk(&repo_name, repo_path) {
        for f in files {
            if !existing_rel.contains(&f.rel_path) {
                changed.push(f.rel_path);
            }
        }
    }
    changed
}

/// Freshness-probe debounce window: back-to-back reads within it skip the walk.
pub const PROBE_DEBOUNCE_SECS: f64 = 2.0;

/// True when the last probe stamp `<db>.probe` is fresher than the debounce
/// window AND newer than the store file — a replaced store never debounces.
pub fn probe_recent(db: &Path) -> bool {
    let stamp = probe_stamp_path(db);
    let Ok(meta) = std::fs::metadata(&stamp) else {
        return false;
    };
    let Ok(modified) = meta.modified() else {
        return false;
    };
    if let Ok(db_modified) = std::fs::metadata(db).and_then(|m| m.modified())
        && db_modified > modified
    {
        return false;
    }
    modified
        .elapsed()
        .map(|e| e.as_secs_f64() < PROBE_DEBOUNCE_SECS)
        .unwrap_or(true)
}

/// Best-effort refresh of the probe stamp; a failed write only disables debounce.
pub fn mark_probe(db: &Path) {
    let _ = std::fs::write(probe_stamp_path(db), b"");
}

fn probe_stamp_path(db: &Path) -> PathBuf {
    let mut raw = db.as_os_str().to_os_string();
    raw.push(".probe");
    PathBuf::from(raw)
}

/// Files-table-vs-disk drift across every repo (the freshness probe).
pub fn detect_changed(conn: &Connection, workspace: &str) -> Vec<String> {
    let mut changed: Vec<String> = Vec::new();
    let Ok(records) = scanner::discover_repo_records(workspace) else {
        return changed;
    };
    for record in records {
        let repo_name = record.repo_id.as_str();
        // An unknown repo scope never falls back to every repo's rows.
        let Some(file_rows) = file_stat_rows(conn, repo_name) else {
            continue;
        };

        for row in &file_rows {
            let p = scanner::resolve_file_path(workspace, repo_name, &row.path);
            let Ok(st) = std::fs::metadata(&p) else {
                changed.push(p.to_string_lossy().into_owned()); // deleted/moved
                continue;
            };
            let size_changed = st.len() as i64 != row.size.unwrap_or(0);
            let mtime_drifted =
                (mtime_f64(&st).unwrap_or(0.0) - row.mtime.unwrap_or(0.0)).abs() > 0.5;
            if size_changed
                || (mtime_drifted
                    && !unchanged_by_hash(conn, &row.id, &p, row.hash.as_deref(), &st))
            {
                changed.push(p.to_string_lossy().into_owned());
            }
        }

        // New source files not yet in the DB; compare on both stored forms.
        let existing: HashSet<&str> = file_rows.iter().map(|r| r.path.as_str()).collect();
        if let Ok((files, _)) = scanner::iter_files_walk(repo_name, &record.path) {
            for f in files {
                if !existing.contains(f.rel_path.as_str()) && !existing.contains(f.path.as_str()) {
                    changed.push(f.path);
                }
            }
        }
    }
    changed
}

/// Detect and re-index disk changes; called once at daemon boot.
pub fn ensure_fresh_force(conn: &mut Connection, workspace: &str) -> Result<usize, String> {
    let changed = detect_changed(conn, workspace);
    if changed.is_empty() {
        return Ok(0);
    }
    let drifted: BTreeSet<String> = changed.into_iter().collect();
    let drifted: Vec<String> = drifted.into_iter().collect();
    repair_changed(conn, workspace, &drifted, false)
}

/// Reindex drifted paths; strict mode demands every path repaired or removed.
pub fn repair_changed(
    conn: &mut Connection,
    workspace: &str,
    drifted: &[String],
    strict: bool,
) -> Result<usize, String> {
    let result = reindex_paths(conn, workspace, drifted);
    if !result.errors.is_empty() {
        return Err(format!(
            "graph refresh failed: {}",
            result.errors.join("; ")
        ));
    }
    let repaired = result.reindexed + result.deleted;
    if strict && repaired != drifted.len() {
        return Err(format!(
            "graph refresh incomplete: {repaired}/{} file(s)",
            drifted.len()
        ));
    }
    Ok(repaired)
}
