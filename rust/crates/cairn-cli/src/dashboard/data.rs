//! Read-only data access for the dashboard.

use std::path::PathBuf;

use rusqlite::Connection;
use serde_json::{Value, json};

use cairn_query::graph as query_graph;
use cairn_query::tools_nav::is_test_symbol;
use cairn_query::tools_search;

/// `bench.agent_suite` chars-per-token divisor (heuristic mode).
pub const CHARS_PER_TOKEN: i64 = 4;

/// Tokenizer mode that estimates from the CHARS_PER_TOKEN divisor.
pub const HEURISTIC_MODE: &str = "heuristic (chars/4)";

pub const GRAPH_SCOPES: [&str; 5] = ["symbol", "module", "impact", "deps", "repo"];

/// llm.tasks Task.status vocabulary — the /tasks filter's allowed values.
pub const TASK_STATUSES: [&str; 5] = ["pending", "in-progress", "done", "failed", "dropped"];

/// Agent-memory type vocabulary — the /memory filter's allowed values.
pub const MEMORY_TYPES: [&str; 4] = ["decision", "pattern", "mistake", "workaround"];

/// Seed knowledge families; route options add families found in the corpus.
pub const KNOWLEDGE_FAMILIES: [&str; 4] = ["business-rule", "decision", "spec", "workflow"];

/// Knowledge doc lifecycle statuses (knowledge.store DOC_STATUSES).
pub const DOC_STATUSES: [&str; 3] = ["active", "superseded", "archived"];

/// Valid window presets; "all" is the unbounded default.
pub const WINDOW_PRESETS: [&str; 4] = ["24h", "7d", "30d", "all"];

/// Preset -> seconds back from now; "all" is absent (unbounded).
const WINDOW_SECONDS: [(&str, f64); 3] = [
    ("24h", 86_400.0),
    ("7d", 7.0 * 86_400.0),
    ("30d", 30.0 * 86_400.0),
];

/// Exports fetch the filtered set in one unpaginated call.
pub const EXPORT_ROW_LIMIT: i64 = 1_000_000;

pub const HISTORY_PAGE_SIZE: i64 = 50;

/// Seconds of inactivity that split a session into separate chains.
pub const SESSION_GAP_S: i64 = 1800;

pub const CHAINS_MAX_CHAINS: usize = 20;
pub const CHAINS_CALLS_PER_CHAIN: usize = 25;

const INSPECT_NEIGHBOR_CAP: usize = 10;
const INSPECT_IMPACT_CAP: usize = 15;
const CANDIDATES_LIMIT: i64 = 10;
const SUGGEST_LIMIT: i64 = 10;

/// Validated window preset plus its epoch cutoff (None means unbounded).
pub fn resolve_window(window: Option<&str>, now: f64) -> (String, Option<f64>) {
    let preset = window.unwrap_or("");
    let matched = WINDOW_PRESETS.iter().find(|p| **p == preset).copied();
    match matched {
        Some(preset) => {
            let seconds = WINDOW_SECONDS
                .iter()
                .find(|(p, _)| *p == preset)
                .map(|(_, s)| *s);
            (preset.to_string(), seconds.map(|s| now - s))
        }
        None => ("all".to_string(), None),
    }
}

/// Run a query and collect rows as JSON objects keyed by column name
/// (BLOBs render as hex text; the only consumer that decodes them is the
/// settings parity check).
pub(crate) fn query_rows(
    conn: &Connection,
    sql: &str,
    params: &[&dyn rusqlite::ToSql],
) -> Vec<Value> {
    let mut stmt = match conn.prepare(sql) {
        Ok(stmt) => stmt,
        Err(_) => return Vec::new(),
    };
    let names: Vec<String> = stmt.column_names().into_iter().map(String::from).collect();
    let mut rows = match stmt.query(params) {
        Ok(rows) => rows,
        Err(_) => return Vec::new(),
    };
    let mut out = Vec::new();
    while let Ok(Some(row)) = rows.next() {
        let mut map = serde_json::Map::new();
        for (index, name) in names.iter().enumerate() {
            let value: Value = row
                .get_ref(index)
                .map(|ref_val| {
                    crate::cli_rows::json_value(ref_val, crate::cli_rows::BlobEncoding::Hex)
                })
                .unwrap_or(Value::Null);
            map.insert(name.clone(), value);
        }
        out.push(Value::Object(map));
    }
    out
}

fn scalar_i64(conn: &Connection, sql: &str, params: &[&dyn rusqlite::ToSql]) -> Option<i64> {
    conn.query_row(sql, params, |row| row.get::<_, i64>(0)).ok()
}

/// Mixed-type bind parameter (SQLite stores invoked_at as REAL, ids as INTEGER).
#[derive(Debug, Clone)]
pub enum P {
    S(String),
    I(i64),
    F(f64),
}

impl rusqlite::ToSql for P {
    fn to_sql(&self) -> rusqlite::Result<rusqlite::types::ToSqlOutput<'_>> {
        match self {
            P::S(text) => text.to_sql(),
            P::I(int) => int.to_sql(),
            P::F(float) => float.to_sql(),
        }
    }
}

fn bind(params: &[P]) -> Vec<&dyn rusqlite::ToSql> {
    params.iter().map(|p| p as &dyn rusqlite::ToSql).collect()
}

// --- projects ---

/// Project counts, freshness, and embedding status (list_projects port).
pub fn list_projects(conn: &Connection) -> Vec<Value> {
    let rows = query_rows(
        conn,
        "SELECT r.id, r.name, r.path, r.language,
                r.indexed_at AS repo_indexed_at,
                (SELECT COUNT(*) FROM files f WHERE f.repo_id = r.id) AS file_count,
                (SELECT COUNT(*) FROM symbols s JOIN files f ON s.file_id = f.id
                 WHERE f.repo_id = r.id) AS symbol_count,
                (SELECT COUNT(*) FROM edges e JOIN symbols s ON e.source_id = s.id
                 JOIN files f ON s.file_id = f.id
                 WHERE f.repo_id = r.id) AS edge_count,
                (SELECT MAX(f.indexed_at) FROM files f WHERE f.repo_id = r.id)
                    AS last_file_indexed
         FROM repos r
         ORDER BY r.name",
        &[],
    );
    // Repo ids are TEXT; string keys throughout (never Value::to_string,
    // which quotes strings).
    let mut embedded_counts: std::collections::HashMap<String, i64> =
        std::collections::HashMap::new();
    for row in query_rows(
        conn,
        "SELECT f.repo_id AS repo_id, COUNT(DISTINCT e.symbol_id) AS embedded
         FROM embeddings e
         JOIN symbols s ON e.symbol_id = s.id
         JOIN files f ON s.file_id = f.id
         GROUP BY f.repo_id",
        &[],
    ) {
        if let (Some(id), Some(count)) = (row["repo_id"].as_str(), row["embedded"].as_i64()) {
            embedded_counts.insert(id.to_string(), count);
        }
    }
    let mut models: std::collections::HashMap<String, Vec<Value>> =
        std::collections::HashMap::new();
    for row in query_rows(
        conn,
        "SELECT DISTINCT f.repo_id AS repo_id, e.model
         FROM embeddings e
         JOIN symbols s ON e.symbol_id = s.id
         JOIN files f ON s.file_id = f.id
         ORDER BY e.model",
        &[],
    ) {
        if let Some(key) = row["repo_id"].as_str() {
            models
                .entry(key.to_string())
                .or_default()
                .push(row["model"].clone());
        }
    }
    let mut projects = Vec::new();
    for row in &rows {
        let repo_key = row["id"].as_str().unwrap_or("").to_string();
        let embedded = embedded_counts.get(&repo_key).copied().unwrap_or(0);
        let symbol_count = row["symbol_count"].as_i64().unwrap_or(0);
        let status = if embedded == 0 || symbol_count == 0 {
            "not"
        } else if embedded < symbol_count {
            "partial"
        } else {
            "embedded"
        };
        let last = if row["last_file_indexed"].is_null() {
            row["repo_indexed_at"].clone()
        } else {
            row["last_file_indexed"].clone()
        };
        projects.push(json!({
            "id": row["id"],
            "name": row["name"],
            "path": row["path"],
            "language": row["language"],
            "file_count": row["file_count"],
            "symbol_count": row["symbol_count"],
            "edge_count": row["edge_count"],
            "last_indexed": last,
            "embedding_status": status,
            "embedding_models": models.get(&repo_key).cloned().unwrap_or_default(),
        }));
    }
    projects
}

// --- symbol candidates / suggest ---

fn match_rows(mut fetched: Vec<Value>, limit: usize) -> Value {
    let truncated = fetched.len() > limit;
    fetched.truncate(limit);
    json!({"matches": fetched, "truncated": truncated})
}

/// Exact-name matches with disambiguating context and a truncation flag.
pub fn symbol_candidates(conn: &Connection, name: &str) -> Value {
    if name.trim().is_empty() {
        return json!({"matches": [], "truncated": false});
    }
    let fetched = query_rows(
        conn,
        "SELECT s.name AS name, s.kind AS kind, f.path AS file, f.repo_id AS repo_id
         FROM symbols s
         LEFT JOIN files f ON s.file_id = f.id
         WHERE s.name = ?
         ORDER BY f.path ASC, f.repo_id ASC, s.id ASC
         LIMIT ?",
        &[&name, &(CANDIDATES_LIMIT + 1)],
    );
    match_rows(fetched, CANDIDATES_LIMIT as usize)
}

/// Escaped prefix matches, shortest names first, for search typeahead.
pub fn symbol_suggest(conn: &Connection, prefix: &str) -> Value {
    if prefix.trim().is_empty() {
        return json!({"matches": [], "truncated": false});
    }
    let mut escaped = prefix.trim().replace('\\', "\\\\");
    escaped = escaped.replace('%', "\\%").replace('_', "\\_");
    let pattern = format!("{escaped}%");
    let fetched = query_rows(
        conn,
        "SELECT s.name AS name, s.kind AS kind, f.path AS file, f.repo_id AS repo_id
         FROM symbols s
         LEFT JOIN files f ON s.file_id = f.id
         WHERE s.name LIKE ? ESCAPE '\\'
         ORDER BY LENGTH(s.name) ASC, s.name ASC, f.path ASC, f.repo_id ASC, s.id ASC
         LIMIT ?",
        &[&pattern, &(SUGGEST_LIMIT + 1)],
    );
    match_rows(fetched, SUGGEST_LIMIT as usize)
}

// --- symbol inspect panel ---

/// One deterministic symbol panel payload with resolved neighbors and honest truncation.
pub fn inspect_symbol(conn: &Connection, name: &str) -> Value {
    if name.trim().is_empty() {
        return json!({"found": false, "name": name});
    }
    let name = name.trim();
    let rows = query_rows(
        conn,
        "SELECT s.id, s.name, s.kind, s.qualified_name, s.line_start, s.docstring,
                f.path AS file, f.repo_id
         FROM symbols s JOIN files f ON s.file_id = f.id
         WHERE s.name = ? ORDER BY f.path ASC, s.id ASC LIMIT 1",
        &[&name],
    );
    let Some(row) = rows.into_iter().next() else {
        return json!({"found": false, "name": name});
    };
    // .kg symbol ids are TEXT uuids; the edge binds must stay strings.
    let symbol_id = row["id"].as_str().unwrap_or("").to_string();
    let same_name_count = scalar_i64(
        conn,
        "SELECT COUNT(*) FROM symbols WHERE name = ?",
        &[&name],
    )
    .unwrap_or(0);

    let cap = (INSPECT_NEIGHBOR_CAP + 1) as i64;
    let callers_raw = query_rows(
        conn,
        "SELECT s.name, s.kind, f.path AS file, e.kind AS ekind
         FROM edges e JOIN symbols s ON e.source_id = s.id
         JOIN files f ON s.file_id = f.id
         WHERE e.target_id = ? LIMIT ?",
        &[&symbol_id, &cap],
    );
    let callees_raw = query_rows(
        conn,
        "SELECT t.name, t.kind, f.path AS file, e.kind AS ekind
         FROM edges e JOIN symbols t ON e.target_id = t.id
         JOIN files f ON t.file_id = f.id
         WHERE e.source_id = ? LIMIT ?",
        &[&symbol_id, &cap],
    );
    let neighbors = |fetched: Vec<Value>| -> (Vec<Value>, bool) {
        let truncated = fetched.len() > INSPECT_NEIGHBOR_CAP;
        let mut rows = fetched;
        rows.truncate(INSPECT_NEIGHBOR_CAP);
        (
            rows.iter()
                .map(|r| {
                    json!({
                        "name": r["name"], "kind": r["kind"],
                        "file": r["file"], "edge_kind": r["ekind"],
                    })
                })
                .collect(),
            truncated,
        )
    };
    let (callers, callers_truncated) = neighbors(callers_raw);
    let (callees, callees_truncated) = neighbors(callees_raw);

    let impact = query_graph::impact_analysis(conn, name, 3, false, 500).unwrap_or_default();
    let affected: Vec<&query_graph::ImpactEntry> = impact
        .impacted
        .iter()
        .filter(|entry| is_test_symbol(&entry.file, &entry.symbol, "").is_some())
        .collect();
    let impact_view = json!({
        "total": impact.total,
        "truncated": impact.truncated,
        "top": impact.impacted.iter().take(INSPECT_IMPACT_CAP).map(|r| {
            json!({"symbol": r.symbol, "file": r.file, "depth": r.depth})
        }).collect::<Vec<_>>(),
        "affected_tests": affected.iter().take(INSPECT_IMPACT_CAP).map(|t| {
            json!({"symbol": t.symbol, "file": t.file})
        }).collect::<Vec<_>>(),
        "affected_tests_total": affected.len(),
    });
    json!({
        "found": true,
        "symbol": {
            "name": row["name"],
            "kind": row["kind"],
            "qualified_name": row["qualified_name"],
            "file": row["file"],
            "repo": row["repo_id"],
            "line_start": row["line_start"],
            "docstring": row["docstring"],
        },
        "same_name_count": same_name_count,
        "callers": callers,
        "callers_truncated": callers_truncated,
        "callees": callees,
        "callees_truncated": callees_truncated,
        "impact": impact_view,
    })
}

// --- health ---

/// Build age formatted exactly as doctor's freshness check.
pub fn age_str(started_at: &str) -> Option<String> {
    let secs = super::env::parse_iso_utc(started_at)?;
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .ok()?
        .as_secs() as i64;
    let age = now - secs;
    if age < 0 {
        return Some("just now".into());
    }
    if age >= 86_400 {
        return Some(format!("{}d old", age / 86_400));
    }
    if age >= 3600 {
        return Some(format!("{}h old", age / 3600));
    }
    if age >= 60 {
        return Some(format!("{}m old", age / 60));
    }
    Some(format!("{age}s old"))
}

/// Effective telemetry retention policy (telemetry.sink.retention_policy port).
pub fn retention_policy() -> Value {
    let max_rows = std::env::var("CAIRN_TOOL_METRICS_MAX_ROWS")
        .ok()
        .and_then(|raw| raw.trim().parse::<i64>().ok().filter(|v| *v >= 0))
        .unwrap_or(50_000);
    let max_age = std::env::var("CAIRN_TOOL_METRICS_MAX_AGE_SECONDS")
        .ok()
        .and_then(|raw| raw.trim().parse::<f64>().ok().filter(|v| *v >= 0.0));
    json!({
        "events_max_rows": 5000,
        "build_runs_max_rows": 500,
        "tool_metrics_max_rows": max_rows,
        "tool_metrics_max_age_seconds": max_age.map(Value::from).unwrap_or(Value::Null),
    })
}

/// The backend stamp that scopes embedding staleness (current_model port);
/// None when the configured backend is unresolvable.
pub fn current_model_stamp() -> Option<String> {
    let name = tools_search::backend_name();
    match name.as_str() {
        "server" | "omlx" | "ollama" => tools_search::server_model_stamp(&name).ok(),
        // Local inference is unavailable: local resolves to hash.
        "local" | "hash" => Some(tools_search::HASH_MODEL.to_string()),
        _ => None,
    }
}

fn vec0_table_name(model: &str) -> String {
    let safe: String = model
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '_' {
                c
            } else {
                '_'
            }
        })
        .collect();
    format!("vec_{safe}")
}

/// Health display data while degrading unavailable reads to null/zero.
pub fn get_health(conn: &Connection, db_path: &str) -> Value {
    let db_size_bytes = std::fs::metadata(db_path).map(|m| m.len()).unwrap_or(0);

    let brow = query_rows(
        conn,
        "SELECT started_at FROM build_runs ORDER BY started_at DESC LIMIT 1",
        &[],
    )
    .into_iter()
    .next();
    let last_build_at = brow.as_ref().map(|r| r["started_at"].clone());
    let last_build_age = brow
        .as_ref()
        .and_then(|r| r["started_at"].as_str())
        .and_then(age_str);

    let name = tools_search::backend_name();
    let hash_fallback = name == "local";
    let ann_model = current_model_stamp();
    let embedding_rows = ann_model
        .as_deref()
        .and_then(|model| {
            scalar_i64(
                conn,
                "SELECT COUNT(*) FROM embeddings WHERE model = ?",
                &[&model],
            )
        })
        .unwrap_or(0);
    let mut index_present: Option<bool> = None;
    let mut index_rows: Option<i64> = None;
    if embedding_rows > 0
        && let Some(model) = &ann_model
    {
        let table = vec0_table_name(model);
        let present = scalar_i64(
            conn,
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
            &[&table],
        )
        .unwrap_or(0);
        index_present = Some(present > 0);
        if present > 0 {
            index_rows = scalar_i64(conn, &format!("SELECT COUNT(*) FROM \"{table}\""), &[]);
        }
    }
    let tool_metrics_rows = scalar_i64(conn, "SELECT COUNT(*) AS n FROM tool_metrics", &[]);
    json!({
        "db_size_bytes": db_size_bytes,
        "last_build_at": last_build_at.unwrap_or(Value::Null),
        "last_build_age": last_build_age.map(Value::from).unwrap_or(Value::Null),
        "embed_backend": name,
        "hash_fallback": hash_fallback,
        "ann_configured": std::env::var("CAIRN_ANN_BACKEND")
            .map(|v| v.trim().to_lowercase())
            .unwrap_or_else(|_| "sqlite-vec".into()),
        "ann_backend_enabled": tools_search::ann_backend_enabled(),
        "ann_model": ann_model.map(Value::from).unwrap_or(Value::Null),
        "ann_embedding_rows": embedding_rows,
        "ann_index_exists": index_present.map(Value::from).unwrap_or(Value::Null),
        "ann_index_rows": index_rows.map(Value::from).unwrap_or(Value::Null),
        "reranker_available": tools_search::reranker_available(),
        "events_max_rows": retention_policy()["events_max_rows"].clone(),
        "build_runs_max_rows": retention_policy()["build_runs_max_rows"].clone(),
        "tool_metrics_max_rows": retention_policy()["tool_metrics_max_rows"].clone(),
        "tool_metrics_max_age_seconds": retention_policy()["tool_metrics_max_age_seconds"].clone(),
        "tool_metrics_rows": tool_metrics_rows.map(Value::from).unwrap_or(Value::Null),
    })
}

// --- database schema ---

const ANN_TABLE_PREFIXES: [&str; 2] = ["vec_", "vecmv_"];

/// The user schema as table nodes plus declared and inferred FK edges.
pub fn get_database_schema(conn: &Connection) -> Value {
    let mut tables: Vec<String> = query_rows(
        conn,
        "SELECT name FROM sqlite_master WHERE type='table'",
        &[],
    )
    .into_iter()
    .filter_map(|row| row["name"].as_str().map(String::from))
    .filter(|name| {
        !name.starts_with("sqlite_")
            && !name.contains("_fts")
            && !ANN_TABLE_PREFIXES.iter().any(|p| name.starts_with(p))
    })
    .collect();
    tables.sort();

    let q = |identifier: &str| format!("\"{}\"", identifier.replace('"', "\"\""));
    let mut meta: std::collections::BTreeMap<String, (Vec<Value>, i64)> = Default::default();
    let mut skipped: Vec<Value> = Vec::new();
    for name in &tables {
        let columns: Vec<Value> = match conn.prepare(&format!("PRAGMA table_info({})", q(name))) {
            Ok(mut stmt) => {
                let mut rows = match stmt.query([]) {
                    Ok(rows) => rows,
                    Err(err) => {
                        skipped.push(json!({"name": name, "reason": format!("Error: {err}")}));
                        continue;
                    }
                };
                let mut cols = Vec::new();
                while let Ok(Some(row)) = rows.next() {
                    let column_type: String = row.get(2).unwrap_or_default();
                    let pk: i64 = row.get(5).unwrap_or(0);
                    cols.push(json!({
                        "name": row.get::<_, String>(1).unwrap_or_default(),
                        "type": column_type,
                        "pk": pk != 0,
                    }));
                }
                cols
            }
            Err(err) => {
                skipped.push(json!({"name": name, "reason": format!("Error: {err}")}));
                continue;
            }
        };
        let count = scalar_i64(conn, &format!("SELECT COUNT(*) FROM {}", q(name)), &[]);
        match count {
            Some(rows) => {
                meta.insert(name.clone(), (columns, rows));
            }
            None => {
                skipped.push(json!({"name": name, "reason": "Error: count failed"}));
            }
        }
    }
    let tables: Vec<&String> = tables
        .iter()
        .filter(|name| meta.contains_key(*name))
        .collect();

    let mut declared: std::collections::HashSet<(String, String)> = Default::default();
    let mut edges: std::collections::BTreeMap<(String, String), Value> = Default::default();
    for name in &tables {
        let Ok(mut stmt) = conn.prepare(&format!("PRAGMA foreign_key_list({})", q(name))) else {
            continue;
        };
        let Ok(mut rows) = stmt.query([]) else {
            continue;
        };
        while let Ok(Some(row)) = rows.next() {
            let target: String = row.get(2).unwrap_or_default();
            let column: String = row.get(3).unwrap_or_default();
            if !tables.iter().any(|t| **t == target) {
                continue;
            }
            declared.insert((name.to_string(), column.clone()));
            let edge = edges
                .entry((name.to_string(), target.clone()))
                .or_insert_with(
                    || json!({"from": name, "to": target, "kind": "fk", "columns": []}),
                );
            if let Some(columns) = edge["columns"].as_array_mut() {
                columns.push(Value::from(column));
            }
        }
    }

    for name in &tables {
        let Some((columns, _)) = meta.get(*name) else {
            continue;
        };
        for column in columns {
            let col = column["name"].as_str().unwrap_or("");
            if !col.ends_with("_id")
                || col == "id"
                || declared.contains(&(name.to_string(), col.to_string()))
            {
                continue;
            }
            let base = &col[..col.len() - 3];
            let target = if tables.iter().any(|t| **t == format!("{base}s")) {
                format!("{base}s")
            } else if tables.iter().any(|t| **t == base) {
                base.to_string()
            } else {
                continue;
            };
            if &target == *name {
                continue;
            }
            let edge = edges
                .entry((name.to_string(), target.clone()))
                .or_insert_with(
                    || json!({"from": name, "to": target, "kind": "inferred", "columns": []}),
                );
            if let Some(columns) = edge["columns"].as_array_mut() {
                columns.push(Value::from(col));
            }
        }
    }

    json!({
        "tables": tables.iter().map(|name| {
            let (columns, rows) = &meta[*name];
            json!({"name": name, "rows": rows, "columns": columns})
        }).collect::<Vec<_>>(),
        "edges": edges.values().cloned().collect::<Vec<_>>(),
        "skipped": skipped,
    })
}

// --- history / tokens / chains ---

fn parse_history_cursor(cursor: Option<&str>) -> Option<(f64, i64)> {
    let cursor = cursor?;
    let (ts, row_id) = cursor.split_once(',')?;
    Some((ts.parse().ok()?, row_id.parse().ok()?))
}

struct HistoryFilters {
    clauses: Vec<String>,
    params: Vec<P>,
}

fn history_filters(
    tool_name: Option<&str>,
    session_id: Option<&str>,
    source: Option<&str>,
    since: Option<f64>,
) -> HistoryFilters {
    let mut filters = HistoryFilters {
        clauses: Vec::new(),
        params: Vec::new(),
    };
    if let Some(tool) = tool_name {
        // Prefix match treats typed text literally; stored names are namespaced.
        filters
            .clauses
            .push("substr(tool_name, 1, length(?)) = ?".into());
        filters.params.push(P::S(tool.to_string()));
        filters.params.push(P::S(tool.to_string()));
    }
    if let Some(session) = session_id {
        filters.clauses.push("session_id = ?".into());
        filters.params.push(P::S(session.to_string()));
    }
    if let Some(source) = source {
        filters.clauses.push("source = ?".into());
        filters.params.push(P::S(source.to_string()));
    }
    if let Some(since) = since {
        // NULL invoked_at never satisfies the comparison.
        filters.clauses.push("invoked_at >= ?".into());
        filters.params.push(P::F(since));
    }
    filters
}

/// Filters and paging bounds for one history query.
pub struct HistoryQuery<'a> {
    pub tool_name: Option<&'a str>,
    pub session_id: Option<&'a str>,
    pub source: Option<&'a str>,
    pub since: Option<f64>,
    pub before: Option<&'a str>,
    pub after: Option<&'a str>,
    pub limit: i64,
}

/// One newest-first history page using filters, windows, and keyset cursors.
pub fn list_history(conn: &Connection, query: HistoryQuery<'_>) -> Value {
    let HistoryQuery {
        tool_name,
        session_id,
        source,
        since,
        before,
        after,
        limit,
    } = query;
    let limit = limit.max(1);
    let before_key = parse_history_cursor(before);
    let after_key = parse_history_cursor(after);
    let backward = after_key.is_some();
    let filters = history_filters(tool_name, session_id, source, since);

    let mut clauses = filters.clauses.clone();
    let mut params: Vec<P> = filters.params.clone();
    if let Some((ts, id)) = before_key {
        clauses.push("(invoked_at, id) < (?, ?)".into());
        params.push(P::F(ts));
        params.push(P::I(id));
    }
    if let Some((ts, id)) = after_key {
        clauses.push("(invoked_at, id) > (?, ?)".into());
        params.push(P::F(ts));
        params.push(P::I(id));
    }
    let where_clause = if clauses.is_empty() {
        String::new()
    } else {
        format!(" WHERE {}", clauses.join(" AND "))
    };
    let direction = if backward {
        "ASC, id ASC"
    } else {
        "DESC, id DESC"
    };
    let sql = format!(
        "SELECT id, tool_name, session_id, source, invoked_at, duration_ms,
                status, error_message, req_chars, resp_chars, args_summary
         FROM tool_metrics{where_clause}
         ORDER BY invoked_at {direction}
         LIMIT ?"
    );
    let mut sql_params = params.clone();
    sql_params.push(P::I(limit + 1));
    let fetched = query_rows(conn, &sql, &bind(&sql_params));

    let page: Vec<Value> = if backward {
        fetched
            .iter()
            .take(limit as usize)
            .cloned()
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
            .collect()
    } else {
        fetched.iter().take(limit as usize).cloned().collect()
    };

    let divisor = estimate_divisor(conn, &filters.clauses, &filters.params);
    let rows: Vec<Value> = page
        .iter()
        .map(|row| {
            let est = |chars: &Value| {
                if chars.is_null() {
                    Value::Null
                } else {
                    Value::from(chars.as_i64().unwrap_or(0) / divisor)
                }
            };
            json!({
                "id": row["id"],
                "tool_name": row["tool_name"],
                "session_id": row["session_id"],
                "source": row["source"],
                "invoked_at": row["invoked_at"],
                "duration_ms": row["duration_ms"],
                "status": row["status"],
                "error_message": row["error_message"],
                "req_chars": row["req_chars"],
                "resp_chars": row["resp_chars"],
                "est_req_tokens": est(&row["req_chars"]),
                "est_resp_tokens": est(&row["resp_chars"]),
                "args_summary": row["args_summary"],
            })
        })
        .collect();

    let has_neighbor = |edge: &Value, comparison: &str| -> bool {
        let mut probe_clauses = filters.clauses.clone();
        probe_clauses.push(format!("(invoked_at, id) {comparison} (?, ?)"));
        let probe_where = format!(" WHERE {}", probe_clauses.join(" AND "));
        let mut probe_params = filters.params.clone();
        if let Some(ts) = edge["invoked_at"].as_f64() {
            probe_params.push(P::F(ts));
        } else if let Some(ts) = edge["invoked_at"].as_str() {
            probe_params.push(P::S(ts.to_string()));
        }
        probe_params.push(P::I(edge["id"].as_i64().unwrap_or(0)));
        conn.query_row(
            &format!("SELECT 1 FROM tool_metrics{probe_where} LIMIT 1"),
            bind(&probe_params).as_slice(),
            |_| Ok(()),
        )
        .is_ok()
    };

    let mut next_cursor: Option<String> = None;
    let mut prev_cursor: Option<String> = None;
    if let (Some(newest), Some(oldest)) = (rows.first(), rows.last()) {
        let more_older = if !backward {
            fetched.len() > limit as usize
        } else {
            has_neighbor(oldest, "<")
        };
        if more_older && !oldest["invoked_at"].is_null() {
            next_cursor = Some(format!("{},{}", oldest["invoked_at"], oldest["id"]));
        }
        if has_neighbor(newest, ">") {
            prev_cursor = Some(format!("{},{}", newest["invoked_at"], newest["id"]));
        }
    }

    json!({"rows": rows, "next": next_cursor, "prev": prev_cursor})
}

/// Heuristic-mode chars-per-token divisor; exact calibration is not supported.
fn estimate_divisor(_conn: &Connection, _clauses: &[String], _params: &[P]) -> i64 {
    CHARS_PER_TOKEN
}

/// Per-tool token aggregates with mode context and unknown-safe truncation counts.
pub fn get_tool_tokens(conn: &Connection, since: Option<f64>) -> Value {
    let mut clauses: Vec<String> = Vec::new();
    let mut params: Vec<P> = Vec::new();
    if let Some(since) = since {
        clauses.push("invoked_at >= ?".into());
        params.push(P::F(since));
    }
    let where_clause = if clauses.is_empty() {
        String::new()
    } else {
        format!(" WHERE {}", clauses.join(" AND "))
    };
    let divisor = estimate_divisor(conn, &clauses, &params);
    let rows = query_rows(
        conn,
        &format!(
            "SELECT tool_name,
                    COUNT(*) AS calls,
                    SUM(req_chars) AS total_req_chars,
                    SUM(resp_chars) AS total_resp_chars,
                    COUNT(truncated_from_chars) AS truncated_calls,
                    SUM(CASE WHEN truncated_from_chars IS NOT NULL
                             THEN truncated_from_chars
                                  - COALESCE(truncated_to_chars, truncated_from_chars)
                        END) AS truncated_chars
             FROM tool_metrics{where_clause}
             GROUP BY tool_name"
        ),
        &bind(&params),
    );
    let mut entries: Vec<Value> = rows
        .iter()
        .map(|row| {
            let est_req = row["total_req_chars"].as_i64().unwrap_or(0) / divisor;
            let est_resp = row["total_resp_chars"].as_i64().unwrap_or(0) / divisor;
            let total = est_req + est_resp;
            let calls = row["calls"].as_i64().unwrap_or(0);
            let truncated_calls = row["truncated_calls"].as_i64().unwrap_or(0);
            let has_evidence = truncated_calls > 0;
            json!({
                "tool_name": row["tool_name"],
                "calls": calls,
                "est_req_tokens": est_req,
                "est_resp_tokens": est_resp,
                "total_tokens": total,
                "mean_tokens": if calls > 0 { total as f64 / calls as f64 } else { 0.0 },
                "truncated_calls": if has_evidence { row["truncated_calls"].clone() } else { Value::Null },
                "truncated_chars": if has_evidence { row["truncated_chars"].clone() } else { Value::Null },
            })
        })
        .collect();
    entries.sort_by(|a, b| {
        let at = a["total_tokens"].as_i64().unwrap_or(0);
        let bt = b["total_tokens"].as_i64().unwrap_or(0);
        bt.cmp(&at).then(
            a["tool_name"]
                .as_str()
                .unwrap_or("")
                .cmp(b["tool_name"].as_str().unwrap_or("")),
        )
    });
    json!({
        "entries": entries,
        "token_mode": HEURISTIC_MODE,
        "calibrated": false,
        "chars_per_token": divisor,
    })
}

/// Bounded session chains split only on known timestamp gaps.
pub fn get_session_chains(
    conn: &Connection,
    since: Option<f64>,
    session_id: Option<&str>,
    expand: Option<&str>,
) -> Value {
    let mut clauses: Vec<String> = Vec::new();
    let mut params: Vec<P> = Vec::new();
    if let Some(session) = session_id {
        clauses.push("session_id = ?".into());
        params.push(P::S(session.to_string()));
    }
    if let Some(since) = since {
        clauses.push("invoked_at >= ?".into());
        params.push(P::F(since));
    }
    let where_clause = if clauses.is_empty() {
        String::new()
    } else {
        format!(" WHERE {}", clauses.join(" AND "))
    };
    let rows = query_rows(
        conn,
        &format!(
            "SELECT id, tool_name, session_id, invoked_at, duration_ms, status
             FROM tool_metrics{where_clause}
             ORDER BY session_id, invoked_at"
        ),
        &bind(&params),
    );

    // Group by session_id preserving first-seen order. Rows arrive ORDER BY
    // session_id, so the hash index keeps the grouping linear on large
    // tool_metrics tables.
    let mut grouped: Vec<(Value, Vec<&Value>)> = Vec::new();
    let mut session_index: std::collections::HashMap<String, usize> = Default::default();
    for row in &rows {
        let sid = row["session_id"].as_str().unwrap_or("").to_string();
        let index = match session_index.get(&sid) {
            Some(index) => *index,
            None => {
                grouped.push((row["session_id"].clone(), Vec::new()));
                session_index.insert(sid, grouped.len() - 1);
                grouped.len() - 1
            }
        };
        grouped[index].1.push(row);
    }

    struct ChainOut {
        chain: Value,
    }
    let mut sessions: Vec<(Option<f64>, Vec<Value>)> = Vec::new();
    for (sid, calls) in &grouped {
        let mut chains: Vec<Value> = Vec::new();
        let mut last_ts: Option<f64> = None;
        for row in calls {
            let ts = row["invoked_at"].as_f64();
            let split = !chains.is_empty()
                && last_ts.is_some()
                && ts.is_some()
                && (ts.unwrap() - last_ts.unwrap()) > SESSION_GAP_S as f64;
            if split || chains.is_empty() {
                chains.push(json!({"session_id": sid, "calls": []}));
            }
            let chain = chains.last_mut().unwrap();
            chain["calls"].as_array_mut().unwrap().push(json!({
                "id": row["id"],
                "tool_name": row["tool_name"],
                "invoked_at": row["invoked_at"],
                "duration_ms": row["duration_ms"],
                "status": row["status"],
            }));
            if let Some(ts) = ts {
                last_ts = Some(ts);
            }
        }
        for chain in &mut chains {
            let timestamps: Vec<f64> = chain["calls"]
                .as_array()
                .unwrap()
                .iter()
                .filter_map(|c| c["invoked_at"].as_f64())
                .collect();
            chain["started_at"] = timestamps.first().map(|t| json!(t)).unwrap_or(Value::Null);
            chain["ended_at"] = timestamps.last().map(|t| json!(t)).unwrap_or(Value::Null);
            chain["call_count"] = Value::from(chain["calls"].as_array().unwrap().len());
        }
        sessions.push((last_ts, chains));
    }
    sessions.sort_by(|a, b| {
        let key = |s: &(Option<f64>, Vec<Value>)| (s.0.is_some(), s.0.unwrap_or(0.0));
        key(b)
            .partial_cmp(&key(a))
            .unwrap_or(std::cmp::Ordering::Equal)
    });

    // Total chain count without materializing every session's chains.
    let total: usize = sessions.iter().map(|(_, chains)| chains.len()).sum();
    let mut chains_out: Vec<ChainOut> = sessions
        .iter()
        .flat_map(|(_, chains)| chains.iter())
        .take(CHAINS_MAX_CHAINS)
        .map(|chain| ChainOut {
            chain: chain.clone(),
        })
        .collect();
    for out in &mut chains_out {
        let chain = &mut out.chain;
        let calls = chain["calls"].as_array().unwrap().clone();
        let session_id = chain["session_id"].as_str().map(String::from);
        let expanded = expand.is_some() && session_id.as_deref() == expand;
        let calls = if !expanded && calls.len() > CHAINS_CALLS_PER_CHAIN {
            calls[calls.len() - CHAINS_CALLS_PER_CHAIN..].to_vec()
        } else {
            calls
        };
        chain["shown_calls"] = Value::from(calls.len());
        let truncated = chain["call_count"].as_i64().unwrap_or(0) > calls.len() as i64;
        chain["truncated_calls"] = Value::from(truncated);
        if truncated {
            let timestamps: Vec<f64> = calls
                .iter()
                .filter_map(|c| c["invoked_at"].as_f64())
                .collect();
            chain["started_at"] = timestamps.first().map(|t| json!(t)).unwrap_or(Value::Null);
        }
        chain["calls"] = Value::from(calls);
    }
    let chains: Vec<Value> = chains_out.into_iter().map(|c| c.chain).collect();
    json!({
        "chains": chains,
        "total_chains": total,
        "truncated": total > CHAINS_MAX_CHAINS,
    })
}

// --- communities ---

fn table_present(conn: &Connection, table: &str) -> bool {
    scalar_i64(
        conn,
        "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name = ?",
        &[&table],
    )
    .unwrap_or(0)
        > 0
}

const COMMUNITY_HUB_CAP: usize = 3;

/// Persisted communities with size and top hubs in stored id order.
pub fn get_communities(conn: &Connection) -> Vec<Value> {
    if !table_present(conn, "communities") {
        return Vec::new();
    }
    let rows = query_rows(
        conn,
        "SELECT c.id, c.label, c.size,
                s.qualified_name AS qualified_name,
                sc.structural_degree AS structural_degree
         FROM communities c
         JOIN symbol_communities sc ON sc.community_id = c.id
         JOIN symbols s ON s.id = sc.symbol_id
         JOIN files f ON f.id = s.file_id
         ORDER BY c.id, sc.structural_degree DESC, f.path, s.qualified_name",
        &[],
    );
    let mut communities: Vec<Value> = Vec::new();
    let mut by_id: std::collections::HashMap<i64, usize> = std::collections::HashMap::new();
    for row in &rows {
        let id = row["id"].as_i64().unwrap_or(0);
        let index = match by_id.get(&id) {
            Some(index) => *index,
            None => {
                communities.push(json!({
                    "id": id,
                    "label": row["label"],
                    "size": row["size"],
                    "hubs": [],
                }));
                by_id.insert(id, communities.len() - 1);
                communities.len() - 1
            }
        };
        let hubs = communities[index]["hubs"].as_array_mut().unwrap();
        if hubs.len() < COMMUNITY_HUB_CAP {
            hubs.push(json!({
                "qualified_name": row["qualified_name"],
                "structural_degree": row["structural_degree"],
            }));
        }
    }
    communities
}

/// One community with its degree-ranked member symbols.
pub fn get_community_members(conn: &Connection, community_id: i64) -> Option<Value> {
    if !table_present(conn, "communities") {
        return None;
    }
    let row = query_rows(
        conn,
        "SELECT id, label, size FROM communities WHERE id = ?",
        &[&community_id],
    )
    .into_iter()
    .next()?;
    let members = query_rows(
        conn,
        "SELECT s.qualified_name AS qualified_name,
                f.path AS path,
                sc.structural_degree AS structural_degree
         FROM symbol_communities sc
         JOIN symbols s ON s.id = sc.symbol_id
         JOIN files f ON f.id = s.file_id
         WHERE sc.community_id = ?
         ORDER BY sc.structural_degree DESC, f.path, s.qualified_name",
        &[&community_id],
    );
    Some(json!({
        "id": row["id"],
        "label": row["label"],
        "size": row["size"],
        "members": members,
    }))
}

// --- editor JSON API ---

pub const EDITOR_DEFAULT_DEPTH: i64 = 2;
pub const EDITOR_MAX_DEPTH: i64 = 10;

/// Effective blast-radius depth for the `depth` query param.
pub fn parse_editor_depth(raw: Option<&str>) -> i64 {
    match raw {
        Some(raw) if raw.trim().bytes().all(|b| b.is_ascii_digit()) && !raw.trim().is_empty() => {
            raw.trim()
                .parse::<i64>()
                .unwrap_or(EDITOR_DEFAULT_DEPTH)
                .clamp(1, EDITOR_MAX_DEPTH)
        }
        _ => EDITOR_DEFAULT_DEPTH,
    }
}

fn symbol_identity(conn: &Connection, name: &str) -> Option<Value> {
    query_rows(
        conn,
        "SELECT s.name, s.kind, s.qualified_name, f.path AS file
         FROM symbols s JOIN files f ON s.file_id = f.id
         WHERE s.name = ? ORDER BY f.path ASC, s.id ASC LIMIT 1",
        &[&name],
    )
    .into_iter()
    .next()
}

fn calls_edge_count(conn: &Connection, name: &str, outgoing: bool) -> i64 {
    // graph.traversal parity: precise callees count only resolved targets
    // (the traversal's `AND e.target_id IS NOT NULL`); callers resolve
    // through the target-id set and need no extra clause.
    let resolution = if outgoing {
        "e.source_id IN (SELECT id FROM symbols WHERE name = ?) AND e.target_id IS NOT NULL"
    } else {
        "e.target_id IN (SELECT id FROM symbols WHERE name = ?)"
    };
    scalar_i64(
        conn,
        &format!("SELECT COUNT(*) FROM edges e WHERE {resolution} AND e.kind = 'calls'"),
        &[&name],
    )
    .unwrap_or(0)
}

/// Identity, calls-only counts, and the depth-limited blast radius for one
/// symbol name (editor._symbol_payload port).
pub fn editor_symbol_payload(conn: &Connection, name: &str, depth: i64) -> Value {
    let stripped = name.trim();
    if stripped.is_empty() {
        return editor_symbol_zero(name, depth);
    }
    let Some(row) = symbol_identity(conn, stripped) else {
        return editor_symbol_zero(name, depth);
    };
    let callers = calls_edge_count(conn, stripped, false);
    let callees = calls_edge_count(conn, stripped, true);
    let impact =
        query_graph::impact_analysis(conn, stripped, depth - 1, false, 500).unwrap_or_default();
    json!({
        "found": true,
        "symbol": row["name"],
        "kind": row["kind"].as_str().unwrap_or(""),
        "qualified_name": row["qualified_name"].as_str().unwrap_or(""),
        "file": row["file"].as_str().unwrap_or(""),
        "callers": {"count": callers},
        "callees": {"count": callees},
        "blast_radius": {
            "depth": depth,
            "total": impact.total,
            "truncated": impact.truncated,
            "symbols": impact.impacted.iter().map(|r| {
                json!({"symbol": r.symbol, "file": r.file, "repo": r.repo, "depth": r.depth})
            }).collect::<Vec<_>>(),
        },
    })
}

/// The populated zero shape for an unknown or blank symbol name.
pub fn editor_symbol_zero(name: &str, depth: i64) -> Value {
    json!({
        "found": false,
        "symbol": name,
        "kind": "",
        "qualified_name": "",
        "file": "",
        "callers": {"count": 0},
        "callees": {"count": 0},
        "blast_radius": {"depth": depth, "total": 0, "truncated": false, "symbols": []},
    })
}

/// Indexed-store and freshness state; the zero shape on an unindexed store.
pub fn editor_status_payload(conn: &Connection) -> Value {
    let rows = query_rows(
        conn,
        "SELECT COUNT(*) AS n, MAX(indexed_at) AS latest FROM files",
        &[],
    );
    let Some(row) = rows.into_iter().next() else {
        return json!({"indexed": false, "files": 0, "symbols": 0, "indexed_at": ""});
    };
    let files = row["n"].as_i64().unwrap_or(0);
    let symbols = scalar_i64(conn, "SELECT COUNT(*) AS n FROM symbols", &[]).unwrap_or(0);
    json!({
        "indexed": files > 0,
        "files": files,
        "symbols": symbols,
        "indexed_at": if row["latest"].is_null() { Value::from("") } else { row["latest"].clone() },
    })
}

/// `Path(db).exists()` guard around a read-only open (MissingDatabaseError port).
pub fn open_store(db_path: Option<&str>) -> Result<Connection, String> {
    let path: PathBuf = match db_path {
        Some(db) if !db.is_empty() => PathBuf::from(db),
        _ => crate::store_paths::resolve_store(None).db,
    };
    if !path.exists() {
        return Err(path.to_string_lossy().into_owned());
    }
    cairn_store::conn::open_read_only(&path).map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn memory_conn() -> Connection {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE tool_metrics (
                id INTEGER PRIMARY KEY, tool_name TEXT, session_id TEXT,
                source TEXT DEFAULT 'mcp', invoked_at REAL, duration_ms REAL,
                status TEXT, error_message TEXT, req_chars INTEGER, resp_chars INTEGER,
                args_summary TEXT, truncated_from_chars INTEGER, truncated_to_chars INTEGER);
             CREATE TABLE symbols (id TEXT PRIMARY KEY, name TEXT, kind TEXT,
                qualified_name TEXT, file_id INTEGER, line_start INTEGER,
                line_end INTEGER, docstring TEXT);
             CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT, repo_id INTEGER,
                indexed_at TEXT);
             CREATE TABLE edges (id INTEGER PRIMARY KEY, source_id INTEGER,
                target_id INTEGER, target_name TEXT, kind TEXT,
                line INTEGER, resolution TEXT);
             CREATE TABLE repos (id INTEGER PRIMARY KEY, name TEXT, path TEXT,
                language TEXT, indexed_at TEXT);
             CREATE TABLE embeddings (symbol_id INTEGER, model TEXT, embedded_at TEXT);",
        )
        .unwrap();
        conn
    }

    fn insert_call(conn: &Connection, tool: &str, session: &str, ts: f64, req: i64, resp: i64) {
        conn.execute(
            "INSERT INTO tool_metrics (tool_name, session_id, invoked_at, duration_ms, status, req_chars, resp_chars)
             VALUES (?, ?, ?, 12.5, 'ok', ?, ?)",
            rusqlite::params![tool, session, ts, req, resp],
        )
        .unwrap();
    }

    #[test]
    fn history_pages_newest_first_with_cursors() {
        let conn = memory_conn();
        for i in 0..6 {
            insert_call(&conn, "search_symbols", "s1", 100.0 + i as f64, 400, 800);
        }
        let page = list_history(
            &conn,
            HistoryQuery {
                tool_name: None,
                session_id: None,
                source: None,
                since: None,
                before: None,
                after: None,
                limit: 4,
            },
        );
        let rows = page["rows"].as_array().unwrap();
        assert_eq!(rows.len(), 4);
        assert_eq!(rows[0]["invoked_at"].as_f64(), Some(105.0));
        assert_eq!(
            page["next"],
            json!("102.0,3"),
            "cursor names the page's oldest row"
        );
        assert_eq!(page["prev"], Value::Null);
        assert_eq!(rows[0]["est_req_tokens"], json!(100));
        // Walk forward through the cursor.
        let next = list_history(
            &conn,
            HistoryQuery {
                tool_name: None,
                session_id: None,
                source: None,
                since: None,
                before: page["next"].as_str(),
                after: None,
                limit: 4,
            },
        );
        let rows = next["rows"].as_array().unwrap();
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[0]["invoked_at"].as_f64(), Some(101.0));
        assert_eq!(next["prev"], json!("101.0,2"));
        // Backward fetch: newest-first page over the rows after the cursor.
        let prev = list_history(
            &conn,
            HistoryQuery {
                tool_name: None,
                session_id: None,
                source: None,
                since: None,
                before: None,
                after: next["prev"].as_str(),
                limit: 4,
            },
        );
        let rows = prev["rows"].as_array().unwrap();
        assert_eq!(rows[0]["invoked_at"].as_f64(), Some(105.0));
        assert_eq!(rows[3]["invoked_at"].as_f64(), Some(102.0));
    }

    #[test]
    fn history_filters_narrow_by_prefix_and_session() {
        let conn = memory_conn();
        insert_call(&conn, "search_symbols", "s1", 10.0, 400, 800);
        insert_call(&conn, "search_symbols:xxx", "s2", 20.0, 400, 800);
        insert_call(&conn, "explore", "s1", 30.0, 400, 800);
        let page = list_history(
            &conn,
            HistoryQuery {
                tool_name: Some("search_symbols"),
                session_id: Some("s1"),
                source: None,
                since: None,
                before: None,
                after: None,
                limit: 50,
            },
        );
        assert_eq!(page["rows"].as_array().unwrap().len(), 1);
        let windowed = list_history(
            &conn,
            HistoryQuery {
                tool_name: None,
                session_id: None,
                source: None,
                since: Some(15.0),
                before: None,
                after: None,
                limit: 50,
            },
        );
        let rows = windowed["rows"].as_array().unwrap();
        assert_eq!(rows.len(), 2);
    }

    #[test]
    fn tool_tokens_aggregate_and_sort() {
        let conn = memory_conn();
        insert_call(&conn, "b_tool", "s1", 1.0, 800, 400);
        insert_call(&conn, "b_tool", "s1", 2.0, 800, 400);
        insert_call(&conn, "a_tool", "s1", 3.0, 100, 100);
        let tokens = get_tool_tokens(&conn, None);
        let entries = tokens["entries"].as_array().unwrap();
        assert_eq!(entries.len(), 2);
        assert_eq!(entries[0]["tool_name"], "b_tool");
        assert_eq!(entries[0]["calls"], 2);
        assert_eq!(entries[0]["total_tokens"], 600);
        assert_eq!(entries[0]["truncated_calls"], Value::Null);
        assert_eq!(tokens["token_mode"], HEURISTIC_MODE);
        assert_eq!(tokens["chars_per_token"], 4);
    }

    #[test]
    fn session_chains_split_on_the_gap() {
        let conn = memory_conn();
        insert_call(&conn, "t", "s1", 0.0, 0, 0);
        insert_call(&conn, "t", "s1", 60.0, 0, 0);
        insert_call(&conn, "t", "s1", 60.0 + 2000.0, 0, 0);
        let result = get_session_chains(&conn, None, None, None);
        assert_eq!(result["total_chains"], 2);
        assert_eq!(result["truncated"], false);
        let chains = result["chains"].as_array().unwrap();
        assert_eq!(chains[0]["call_count"], 2, "most recent session first");
        assert_eq!(chains[1]["call_count"], 1);
    }

    #[test]
    fn editor_depth_parses_and_clamps() {
        assert_eq!(parse_editor_depth(None), 2);
        assert_eq!(parse_editor_depth(Some("")), 2);
        assert_eq!(parse_editor_depth(Some("abc")), 2);
        assert_eq!(parse_editor_depth(Some("5")), 5);
        assert_eq!(parse_editor_depth(Some("99")), 10);
        assert_eq!(parse_editor_depth(Some("0")), 1);
    }

    #[test]
    fn editor_symbol_zero_shape_and_found_shape() {
        let conn = memory_conn();
        assert_eq!(
            editor_symbol_payload(&conn, "missing", 2),
            editor_symbol_zero("missing", 2)
        );
        conn.execute(
            "INSERT INTO files (id, path, repo_id) VALUES (1, 'src/a.py', 'r1')",
            [],
        )
        .unwrap();
        // Real .kg ids are TEXT uuids; the query crate reads them as strings.
        conn.execute(
            "INSERT INTO symbols (id, name, kind, qualified_name, file_id) VALUES ('s1', 'fn_a', 'function', 'mod.fn_a', 1)",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO symbols (id, name, kind, qualified_name, file_id) VALUES ('s2', 'fn_b', 'function', 'mod.fn_b', 1)",
            [],
        )
        .unwrap();
        conn.execute(
            "INSERT INTO edges (source_id, target_id, kind) VALUES ('s2', 's1', 'calls')",
            [],
        )
        .unwrap();
        let payload = editor_symbol_payload(&conn, "fn_a", 2);
        assert_eq!(payload["found"], true);
        assert_eq!(payload["callers"]["count"], 1);
        assert_eq!(payload["callees"]["count"], 0);
        assert_eq!(payload["blast_radius"]["total"], 1);
        assert_eq!(payload["blast_radius"]["symbols"][0]["symbol"], "fn_b");
    }

    #[test]
    fn window_resolution_validates_presets() {
        let now = 1_000_000.0;
        let (preset, cutoff) = resolve_window(Some("7d"), now);
        assert_eq!(preset, "7d");
        assert_eq!(cutoff, Some(now - 7.0 * 86_400.0));
        let (preset, cutoff) = resolve_window(Some("bogus"), now);
        assert_eq!(preset, "all");
        assert_eq!(cutoff, None);
        let (preset, cutoff) = resolve_window(None, now);
        assert_eq!(preset, "all");
        assert_eq!(cutoff, None);
    }
}
