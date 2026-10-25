//! Precomputed dataflow index for public/exported symbols (`graph/dataflow.py`).

use std::collections::BTreeSet;
use std::path::Path;
use std::time::UNIX_EPOCH;

use rusqlite::{Connection, params};

pub const CLOSURE_MAX_DEPTH: i64 = 4;
pub const DEFAULT_MAX_SYMBOLS: usize = 2000;
pub const MAX_SYMBOLS_ENV: &str = "CAIRN_DATAFLOW_MAX_SYMBOLS";

pub struct DataflowRow {
    pub symbol: String,
    pub repo: String,
    pub within_repo: Vec<String>,
    pub cross_repo: Vec<String>,
    pub updated: f64,
    pub analysis_errors: Vec<String>,
    pub ambiguous: bool,
}

/// Resolve the explicit, environment, or default symbol cap; non-positive or
/// unparseable values warn and fall back (python `resolve_max_symbols`).
pub fn resolve_max_symbols(explicit: Option<i64>) -> usize {
    if let Some(value) = explicit {
        if value <= 0 {
            eprintln!("warning: max_symbols={value} must be positive; using {DEFAULT_MAX_SYMBOLS}");
            return DEFAULT_MAX_SYMBOLS;
        }
        return value as usize;
    }
    let Ok(raw) = std::env::var(MAX_SYMBOLS_ENV) else {
        return DEFAULT_MAX_SYMBOLS;
    };
    let raw = raw.trim();
    if raw.is_empty() {
        return DEFAULT_MAX_SYMBOLS;
    }
    match raw.parse::<i64>() {
        Ok(value) if value > 0 => value as usize,
        Ok(_) => {
            eprintln!(
                "warning: {MAX_SYMBOLS_ENV}='{raw}' must be positive; using {DEFAULT_MAX_SYMBOLS}"
            );
            DEFAULT_MAX_SYMBOLS
        }
        Err(_) => {
            eprintln!(
                "warning: {MAX_SYMBOLS_ENV}='{raw}' is not an integer; using {DEFAULT_MAX_SYMBOLS}"
            );
            DEFAULT_MAX_SYMBOLS
        }
    }
}

fn row_is_public(name: &str, modifiers: &str, language: &str) -> bool {
    if matches!(language, "java" | "kotlin") && !modifiers.contains("public") {
        return false;
    }
    if language == "python" && name.starts_with('_') {
        return false;
    }
    true
}

/// Every public callable/type with its repository, deduped per (name, repo).
fn public_symbols(conn: &Connection) -> Result<Vec<(String, String)>, String> {
    let mut stmt = conn
        .prepare(
            "SELECT s.name, s.modifiers, r.name AS repo, r.language \
             FROM symbols s \
             JOIN files f ON s.file_id = f.id \
             JOIN repos r ON f.repo_id = r.id \
             WHERE s.kind IN ('function', 'method', 'class', 'interface', 'enum') \
             ORDER BY s.name, r.name",
        )
        .map_err(|e| e.to_string())?;
    let mapped = stmt
        .query_map([], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, Option<String>>(1)?.unwrap_or_default(),
                r.get::<_, Option<String>>(2)?.unwrap_or_default(),
                r.get::<_, Option<String>>(3)?.unwrap_or_default(),
            ))
        })
        .map_err(|e| e.to_string())?;
    let mut distinct: Vec<(String, String)> = Vec::new();
    let mut seen: BTreeSet<(String, String)> = BTreeSet::new();
    for row in mapped {
        let (name, modifiers, repo, language) = row.map_err(|e| e.to_string())?;
        if !row_is_public(&name, &modifiers, &language) {
            continue;
        }
        if seen.insert((name.clone(), repo.clone())) {
            distinct.push((name, repo));
        }
    }
    Ok(distinct)
}

struct DataflowPayload {
    within: Vec<String>,
    cross: Vec<String>,
    errors: Vec<String>,
}

fn compute_dataflow_row(
    conn: &Connection,
    workspace: &Path,
    name: &str,
    repo: &str,
    cross_cache: &mut std::collections::HashMap<String, Vec<String>>,
) -> DataflowPayload {
    let mut errors: Vec<String> = Vec::new();
    let mut within: Vec<String> = Vec::new();
    match cairn_query::graph::impact_analysis(conn, name, 5, false, 500) {
        Ok(impact) => {
            let mut names: BTreeSet<String> = BTreeSet::new();
            for item in &impact.impacted {
                if item.symbol != name && item.repo == repo {
                    names.insert(item.symbol.clone());
                }
            }
            within = names.into_iter().collect();
        }
        Err(e) => {
            errors.push(format!("impact_analysis: {e}"));
        }
    }
    let cross = if let Some(cached) = cross_cache.get(repo) {
        cached.clone()
    } else {
        let mut cross = Vec::new();
        let env_raw = std::env::var("CAIRN_REPO_NAMESPACES").ok();
        let namespaces = cairn_query::graph::resolve_namespaces(workspace, env_raw.as_deref());
        match cairn_query::graph::cross_repo_deps(conn, repo, &namespaces) {
            Ok(xref) => cross = xref.dependents.iter().map(|d| d.repo.clone()).collect(),
            Err(e) => errors.push(format!("cross_repo_deps: {e}")),
        }
        cross_cache.insert(repo.to_string(), cross.clone());
        cross
    };
    DataflowPayload {
        within,
        cross,
        errors,
    }
}

fn purge_non_public_rows(conn: &Connection, live_names: &BTreeSet<String>) -> Result<(), String> {
    conn.execute_batch(
        "CREATE TEMP TABLE IF NOT EXISTS dataflow_live_names (symbol TEXT PRIMARY KEY); \
         DELETE FROM dataflow_live_names;",
    )
    .map_err(|e| e.to_string())?;
    let mut stmt = conn
        .prepare("INSERT OR IGNORE INTO dataflow_live_names (symbol) VALUES (?1)")
        .map_err(|e| e.to_string())?;
    for name in live_names {
        stmt.execute(params![name]).map_err(|e| e.to_string())?;
    }
    conn.execute(
        "DELETE FROM dataflow WHERE symbol NOT IN (SELECT symbol FROM dataflow_live_names)",
        [],
    )
    .map_err(|e| e.to_string())?;
    Ok(())
}

fn now_epoch() -> f64 {
    std::time::SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

fn insert_row(
    conn: &Connection,
    name: &str,
    repo: &str,
    payload: &DataflowPayload,
    now: f64,
) -> Result<(), String> {
    conn.execute(
        "INSERT OR REPLACE INTO dataflow (symbol, repo, within_repo, cross_repo, analysis_errors, updated) \
         VALUES (?, ?, ?, ?, ?, ?)",
        params![
            name,
            repo,
            serde_json::to_string(&payload.within).unwrap_or_else(|_| "[]".into()),
            serde_json::to_string(&payload.cross).unwrap_or_else(|_| "[]".into()),
            serde_json::to_string(&payload.errors).unwrap_or_else(|_| "[]".into()),
            now,
        ],
    )
    .map_err(|e| e.to_string())?;
    Ok(())
}

/// Build bounded dataflow rows for public symbols and return the count.
pub fn build_dataflow_index(
    conn: &mut Connection,
    workspace: &str,
    max_symbols: Option<i64>,
) -> Result<usize, String> {
    let max_symbols = resolve_max_symbols(max_symbols);
    let workspace = Path::new(workspace)
        .canonicalize()
        .unwrap_or_else(|_| std::path::PathBuf::from(workspace));
    let mut symbols = public_symbols(conn)?;
    if symbols.len() > max_symbols {
        eprintln!(
            "warning: dataflow index is partial -- {} public symbols found, capping at {}. \
             Set {MAX_SYMBOLS_ENV} (or --max-symbols on `cairn dataflow build`) above {} \
             for full coverage.",
            symbols.len(),
            max_symbols,
            symbols.len(),
        );
        // A capped build keeps rows of still-public symbols beyond the cap,
        // so only names the graph no longer declares public are stale.
        let live: BTreeSet<String> = symbols.iter().map(|(name, _)| name.clone()).collect();
        purge_non_public_rows(conn, &live)?;
        symbols.truncate(max_symbols);
    } else {
        conn.execute("DELETE FROM dataflow", [])
            .map_err(|e| e.to_string())?;
    }
    let mut count = 0usize;
    let now = now_epoch();
    let mut cross_cache: std::collections::HashMap<String, Vec<String>> =
        std::collections::HashMap::new();
    let tx = conn.transaction().map_err(|e| e.to_string())?;
    {
        for (name, repo) in &symbols {
            let payload = compute_dataflow_row(&tx, &workspace, name, repo, &mut cross_cache);
            insert_row(&tx, name, repo, &payload, now)?;
            count += 1;
        }
    }
    tx.commit().map_err(|e| e.to_string())?;
    Ok(count)
}

/// One symbol's precomputed dataflow row or None.
pub fn get_dataflow(conn: &Connection, symbol: &str) -> Result<Option<DataflowRow>, String> {
    let mut stmt = conn
        .prepare("SELECT repo, within_repo, cross_repo, analysis_errors, updated FROM dataflow WHERE symbol = ?1 ORDER BY repo")
        .map_err(|e| e.to_string())?;
    let mapped = stmt
        .query_map(params![symbol], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, Option<String>>(1)?,
                r.get::<_, Option<String>>(2)?,
                r.get::<_, Option<String>>(3)?,
                r.get::<_, Option<f64>>(4)?.unwrap_or(0.0),
            ))
        })
        .map_err(|e| e.to_string())?;
    let rows: Vec<_> = mapped
        .collect::<Result<_, _>>()
        .map_err(|e| e.to_string())?;
    let Some(first) = rows.first() else {
        return Ok(None);
    };
    let parse = |raw: &Option<String>| -> Vec<String> {
        raw.as_deref()
            .and_then(|body| serde_json::from_str(body).ok())
            .unwrap_or_default()
    };
    Ok(Some(DataflowRow {
        symbol: symbol.to_string(),
        repo: first.0.clone(),
        within_repo: parse(&first.1),
        cross_repo: parse(&first.2),
        updated: first.4,
        analysis_errors: parse(&first.3),
        ambiguous: rows.len() > 1,
    }))
}

/// Whether a bare symbol name exists in the graph (the lookup miss hint).
pub fn symbol_exists(conn: &Connection, symbol: &str) -> bool {
    conn.query_row(
        "SELECT 1 FROM symbols WHERE name = ?1 LIMIT 1",
        params![symbol],
        |_| Ok(()),
    )
    .is_ok()
}

/// Refresh or delete dataflow rows for affected names.
pub fn maintain_dataflow_index(
    conn: &Connection,
    workspace: &str,
    affected_names: &BTreeSet<String>,
) -> Result<usize, String> {
    let workspace = Path::new(workspace)
        .canonicalize()
        .unwrap_or_else(|_| std::path::PathBuf::from(workspace));
    let mut count = 0usize;
    let now = now_epoch();
    let mut cross_cache: std::collections::HashMap<String, Vec<String>> =
        std::collections::HashMap::new();
    for name in affected_names {
        if name.is_empty() {
            continue;
        }
        let mut stmt = conn
            .prepare(
                "SELECT s.name, s.modifiers, r.name AS repo, r.language \
                 FROM symbols s \
                 JOIN files f ON s.file_id = f.id \
                 JOIN repos r ON f.repo_id = r.id \
                 WHERE s.kind IN ('function', 'method', 'class', 'interface', 'enum') \
                   AND s.name = ?1",
            )
            .map_err(|e| e.to_string())?;
        let mapped = stmt
            .query_map(params![name], |r| {
                Ok((
                    r.get::<_, String>(0)?,
                    r.get::<_, Option<String>>(1)?.unwrap_or_default(),
                    r.get::<_, Option<String>>(2)?.unwrap_or_default(),
                    r.get::<_, Option<String>>(3)?.unwrap_or_default(),
                ))
            })
            .map_err(|e| e.to_string())?;
        let rows: Vec<_> = mapped
            .collect::<Result<_, _>>()
            .map_err(|e| e.to_string())?;
        let mut repos_done: BTreeSet<String> = BTreeSet::new();
        for (sym_name, modifiers, repo, language) in &rows {
            if !row_is_public(sym_name, modifiers, language) {
                continue;
            }
            if !repos_done.insert(repo.clone()) {
                continue;
            }
            let payload = compute_dataflow_row(conn, &workspace, name, repo, &mut cross_cache);
            insert_row(conn, name, repo, &payload, now)?;
            count += 1;
        }
        if repos_done.is_empty() {
            let deleted = conn
                .execute("DELETE FROM dataflow WHERE symbol = ?1", params![name])
                .map_err(|e| e.to_string())?;
            count += deleted;
        }
    }
    Ok(count)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resolve_max_symbols_rejects_non_positive_explicit_values() {
        assert_eq!(resolve_max_symbols(None), DEFAULT_MAX_SYMBOLS);
        assert_eq!(resolve_max_symbols(Some(7)), 7);
        assert_eq!(resolve_max_symbols(Some(0)), DEFAULT_MAX_SYMBOLS);
        assert_eq!(resolve_max_symbols(Some(-1)), DEFAULT_MAX_SYMBOLS);
    }
}
