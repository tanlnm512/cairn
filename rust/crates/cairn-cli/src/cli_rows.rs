//! CLI-shaped graph row queries rendering rows as dictionaries.

use rusqlite::types::ValueRef;
use rusqlite::{Connection, OptionalExtension, Row};
use serde_json::{Map, Value};

/// One row as an insertion-ordered {column: value} map (`dict(sqlite3.Row)`).
pub type PyRow = Map<String, Value>;

fn row_to_pyrow(row: &Row<'_>, names: &[String]) -> rusqlite::Result<PyRow> {
    let mut map = Map::new();
    for (i, name) in names.iter().enumerate() {
        let value = match row.get_ref(i)? {
            ValueRef::Null => Value::Null,
            ValueRef::Integer(n) => Value::from(n),
            ValueRef::Real(f) => serde_json::Number::from_f64(f)
                .map(Value::Number)
                .unwrap_or(Value::Null),
            ValueRef::Text(s) => Value::from(String::from_utf8_lossy(s).to_string()),
            ValueRef::Blob(b) => Value::from(String::from_utf8_lossy(b).to_string()),
        };
        map.insert(name.clone(), value);
    }
    Ok(map)
}

fn query_rows(
    conn: &Connection,
    sql: &str,
    params: &[&dyn rusqlite::ToSql],
) -> rusqlite::Result<Vec<PyRow>> {
    let mut stmt = conn.prepare(sql)?;
    let names: Vec<String> = stmt.column_names().iter().map(|s| s.to_string()).collect();
    let mapped = stmt.query_map(params, |row| row_to_pyrow(row, &names))?;
    mapped.collect()
}

/// `SELECT s.*, f.path AS file_path, f.repo_id AS repo` — the shared symbol join.
const SYMBOL_STAR: &str = "SELECT s.*, f.path AS file_path, f.repo_id AS repo \
     FROM symbols s JOIN files f ON s.file_id = f.id";

/// Exact name, then qualified name, then substring — the `def` row shape.
pub fn find_definition_rows(
    conn: &Connection,
    name: &str,
    limit: i64,
) -> rusqlite::Result<Vec<PyRow>> {
    for (where_clause, order) in [
        ("WHERE s.name = ?", "ORDER BY s.kind"),
        ("WHERE s.qualified_name = ?", ""),
    ] {
        let sql = format!("{SYMBOL_STAR} {where_clause} {order} LIMIT ?");
        let rows = query_rows(conn, &sql, &[&name, &limit])?;
        if !rows.is_empty() {
            return Ok(rows);
        }
    }
    let escaped = name
        .replace('\\', "\\\\")
        .replace('%', "\\%")
        .replace('_', "\\_");
    let pattern = format!("%{escaped}%");
    let sql = format!("{SYMBOL_STAR} WHERE s.name LIKE ? ESCAPE '\\' ORDER BY s.kind LIMIT ?");
    query_rows(conn, &sql, &[&pattern, &limit])
}

/// One row by symbol id (`find_definition_by_id`); at most one row.
pub fn find_definition_by_id_row(conn: &Connection, sym_id: &str) -> Option<PyRow> {
    let sql = format!("{SYMBOL_STAR} WHERE s.id = ? LIMIT 1");
    query_rows(conn, &sql, &[&sym_id]).ok()?.into_iter().next()
}

/// Edges whose target is a symbol named `name` — the `callers` row shape.
pub fn get_callers_rows(
    conn: &Connection,
    name: &str,
    limit: i64,
    fuzzy: bool,
    kind: Option<&str>,
    symbol_id: Option<&str>,
) -> rusqlite::Result<Vec<PyRow>> {
    let (target_clause, mut params): (&str, Vec<String>) = match (fuzzy, symbol_id) {
        (false, Some(id)) => ("e.target_id = ?", vec![id.to_string()]),
        (true, Some(id)) => (
            "(e.target_name = ? OR e.target_id = ?)",
            vec![name.to_string(), id.to_string()],
        ),
        (true, None) => (
            "(e.target_name = ? OR e.target_id IN \
             (SELECT id FROM symbols WHERE name = ?))",
            vec![name.to_string(), name.to_string()],
        ),
        (false, None) => (
            "e.target_id IN (SELECT id FROM symbols WHERE name = ?)",
            vec![name.to_string()],
        ),
    };
    let mut sql = format!(
        "SELECT e.line AS edge_line, e.column AS edge_column, e.kind AS edge_kind, \
         s.name AS caller_name, s.kind AS caller_kind, s.id AS caller_id, \
         f.path AS file_path, f.repo_id AS repo, e.resolution AS resolution \
         FROM edges e \
         JOIN symbols s ON e.source_id = s.id \
         JOIN files f ON s.file_id = f.id \
         WHERE {target_clause}"
    );
    if let Some(kind) = kind {
        sql.push_str(" AND e.kind = ?");
        params.push(kind.to_string());
    }
    sql.push_str(" ORDER BY f.repo_id, f.path, e.line, s.name LIMIT ?");
    let mut refs: Vec<&dyn rusqlite::ToSql> = params.iter().map(|p| p as _).collect();
    refs.push(&limit);
    query_rows(conn, &sql, &refs)
}

/// Edges whose source is a symbol named `name` — the `callees` row shape.
pub fn get_callees_rows(
    conn: &Connection,
    name: &str,
    limit: i64,
    fuzzy: bool,
    kind: Option<&str>,
    symbol_id: Option<&str>,
) -> rusqlite::Result<Vec<PyRow>> {
    let mut params: Vec<String> = vec![symbol_id.unwrap_or(name).to_string()];
    let mut sql = format!(
        "SELECT e.line AS edge_line, e.column AS edge_column, e.kind AS edge_kind, \
         COALESCE(t.name, e.target_name) AS callee_name, \
         COALESCE(t.kind, 'unknown') AS callee_kind, \
         e.target_id AS resolved, \
         e.resolution AS resolution, \
         f.path AS file_path, f.repo_id AS repo \
         FROM edges e \
         JOIN symbols s ON e.source_id = s.id \
         JOIN files f ON s.file_id = f.id \
         LEFT JOIN symbols t ON e.target_id = t.id \
         WHERE {} {}",
        if symbol_id.is_some() {
            "s.id = ?"
        } else {
            "s.name = ?"
        },
        if fuzzy {
            ""
        } else {
            "AND e.target_id IS NOT NULL"
        },
    );
    if let Some(kind) = kind {
        sql.push_str(" AND e.kind = ?");
        params.push(kind.to_string());
    }
    sql.push_str(" ORDER BY f.repo_id, f.path, e.line, COALESCE(t.name, e.target_name) LIMIT ?");
    let mut refs: Vec<&dyn rusqlite::ToSql> = params.iter().map(|p| p as _).collect();
    refs.push(&limit);
    query_rows(conn, &sql, &refs)
}

/// `bm25`-ranked FTS rows; LIKE rows carry no `rank` key.
pub fn search_symbol_rows(
    conn: &Connection,
    pattern: &str,
    kind: Option<&str>,
    limit: i64,
) -> Vec<PyRow> {
    let Some(fts_query) = cairn_query::graph::pattern_to_fts(pattern) else {
        return like_symbol_rows(conn, pattern, kind, limit).unwrap_or_default();
    };
    let mut rows = match fts_symbol_rows(conn, &fts_query, kind, limit) {
        Ok(rows) => rows,
        Err(_) => return like_symbol_rows(conn, pattern, kind, limit).unwrap_or_default(),
    };
    if !cairn_query::graph::is_fts_prefix_pattern(pattern) && (rows.len() as i64) < limit {
        let mut seen: std::collections::HashSet<String> = rows
            .iter()
            .filter_map(|r| r.get("id").and_then(Value::as_str).map(String::from))
            .collect();
        if let Ok(extra) = like_symbol_rows(conn, pattern, kind, limit) {
            for row in extra {
                let id = row
                    .get("id")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string();
                if seen.insert(id) {
                    rows.push(row);
                    if rows.len() as i64 >= limit {
                        break;
                    }
                }
            }
        }
    }
    rows
}

fn fts_symbol_rows(
    conn: &Connection,
    fts_query: &str,
    kind: Option<&str>,
    limit: i64,
) -> rusqlite::Result<Vec<PyRow>> {
    let mut sql = String::from(
        "SELECT s.*, f.path AS file_path, f.repo_id AS repo, \
         bm25(symbols_fts) AS rank \
         FROM symbols_fts \
         JOIN symbols s ON s.rowid = symbols_fts.rowid \
         JOIN files f ON s.file_id = f.id \
         WHERE symbols_fts MATCH ?",
    );
    let kind_buf: Option<String> = kind.map(String::from);
    let mut params: Vec<&dyn rusqlite::ToSql> = vec![&fts_query];
    if let Some(kind) = &kind_buf {
        sql.push_str(" AND s.kind = ?");
        params.push(kind);
    }
    sql.push_str(" ORDER BY rank LIMIT ?");
    params.push(&limit);
    query_rows(conn, &sql, &params)
}

/// LIKE scan; `*` splits wildcard segments, else a contains-match.
pub fn like_symbol_rows(
    conn: &Connection,
    pattern: &str,
    kind: Option<&str>,
    limit: i64,
) -> rusqlite::Result<Vec<PyRow>> {
    let escaped: Vec<String> = pattern
        .split('*')
        .map(|segment| {
            segment
                .replace('\\', "\\\\")
                .replace('%', "\\%")
                .replace('_', "\\_")
        })
        .collect();
    let mut sql_pattern = escaped.join("%");
    if !pattern.contains('*') {
        sql_pattern = format!("%{sql_pattern}%");
    }
    let mut sql = format!("{SYMBOL_STAR} WHERE s.name LIKE ? ESCAPE '\\'");
    let kind_buf: Option<String> = kind.map(String::from);
    let mut params: Vec<&dyn rusqlite::ToSql> = vec![&sql_pattern];
    if let Some(kind) = &kind_buf {
        sql.push_str(" AND s.kind = ?");
        params.push(kind);
    }
    sql.push_str(" ORDER BY s.name LIMIT ?");
    params.push(&limit);
    query_rows(conn, &sql, &params)
}

/// OR-combined per-term prefix search (`search_symbols_terms`): FTS only,
/// with the whole joined string as the LIKE degrade.
pub fn search_symbol_rows_terms(
    conn: &Connection,
    terms: &[&str],
    kind: Option<&str>,
    limit: i64,
) -> Vec<PyRow> {
    let Some(fts_query) = terms_to_fts(terms) else {
        let joined = terms.join(" ");
        return like_symbol_rows(conn, &joined, kind, limit).unwrap_or_default();
    };
    match fts_symbol_rows(conn, &fts_query, kind, limit) {
        Ok(rows) => rows,
        Err(_) => {
            let joined = terms.join(" ");
            like_symbol_rows(conn, &joined, kind, limit).unwrap_or_default()
        }
    }
}

fn terms_to_fts(terms: &[&str]) -> Option<String> {
    let mut tokens: Vec<String> = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for term in terms {
        for token in term.split(|c: char| !c.is_ascii_alphanumeric()) {
            if token.is_empty() {
                continue;
            }
            let key = token.to_lowercase();
            if !seen.insert(key) {
                continue;
            }
            tokens.push(token.to_string());
        }
    }
    if tokens.is_empty() {
        return None;
    }
    Some(
        tokens
            .iter()
            .map(|t| format!("\"{t}\"*"))
            .collect::<Vec<_>>()
            .join(" OR "),
    )
}

/// Directory/package symbol counts for a repo, count-descending (`get_tree`).
pub fn get_tree_rows(conn: &Connection, repo: &str) -> rusqlite::Result<Vec<(String, i64)>> {
    let legacy_repo_root: Option<String> = if repo.is_empty() {
        None
    } else {
        conn.query_row("SELECT path FROM repos WHERE id = ?", [repo], |row| {
            row.get(0)
        })
        .optional()?
    };
    let sql = if repo.is_empty() {
        "SELECT f.path AS path, COUNT(s.id) AS symbols \
         FROM files f LEFT JOIN symbols s ON s.file_id = f.id \
         GROUP BY f.id"
            .to_string()
    } else {
        "SELECT f.path AS path, COUNT(s.id) AS symbols \
         FROM files f LEFT JOIN symbols s ON s.file_id = f.id \
         WHERE f.repo_id = ? \
         GROUP BY f.id"
            .to_string()
    };
    let mut stmt = conn.prepare(&sql)?;
    let mapped = stmt.query_map(
        rusqlite::params_from_iter(std::iter::once(repo).filter(|r| !r.is_empty())),
        |row| {
            let path: String = row.get(0)?;
            let symbols: i64 = row.get(1)?;
            Ok((path, symbols))
        },
    )?;
    let mut buckets: std::collections::HashMap<String, i64> = std::collections::HashMap::new();
    let mut order: Vec<String> = Vec::new();
    for result in mapped {
        let (mut path, symbols) = result?;
        if let Some(root) = &legacy_repo_root {
            let prefix = format!("{root}/");
            if let Some(stripped) = path.strip_prefix(&prefix) {
                path = stripped.to_string();
            }
        }
        let parts: Vec<&str> = path.split('/').collect();
        let key = if parts.len() >= 4 {
            parts[..3].join("/")
        } else if parts.len() >= 2 {
            parts[..parts.len() - 1].join("/")
        } else {
            parts[0].to_string()
        };
        if !buckets.contains_key(&key) {
            order.push(key.clone());
        }
        *buckets.entry(key).or_insert(0) += symbols;
    }
    let mut rows: Vec<(String, i64)> = order
        .into_iter()
        .map(|key| {
            let count = buckets[&key];
            (key, count)
        })
        .collect();
    rows.sort_by_key(|(_, count)| std::cmp::Reverse(*count));
    Ok(rows)
}

/// Aggregate counts behind the `stats` command (`get_stats`).
#[derive(Debug, Default)]
pub struct GraphStats {
    pub repos: i64,
    pub files: i64,
    pub symbols: i64,
    pub edges: i64,
    pub imports: i64,
    pub by_kind: Vec<(String, i64)>,
    pub by_repo: Vec<(String, i64)>,
    pub edges_resolved: i64,
    pub resolution: (i64, i64, i64),
    pub exact_share: f64,
    pub ambiguous_share: f64,
    pub edge_sources: Vec<(String, i64)>,
    pub exact_share_by_language: Vec<(String, f64)>,
    pub skipped_total: i64,
    pub skipped_by_reason: Vec<(String, i64)>,
}

fn count(conn: &Connection, sql: &str) -> i64 {
    conn.query_row(sql, [], |row| row.get(0)).unwrap_or(0)
}

fn grouped(conn: &Connection, sql: &str) -> Vec<(String, i64)> {
    let Ok(mut stmt) = conn.prepare(sql) else {
        return vec![];
    };
    let Ok(rows) = stmt.query_map([], |row| {
        Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?))
    }) else {
        return vec![];
    };
    rows.collect::<Result<Vec<_>, _>>().unwrap_or_default()
}

/// Aggregate counts for repos, files, symbols, edges, imports.
pub fn get_stats(conn: &Connection) -> GraphStats {
    let mut stats = GraphStats {
        repos: count(conn, "SELECT COUNT(*) FROM repos"),
        files: count(conn, "SELECT COUNT(*) FROM files"),
        symbols: count(conn, "SELECT COUNT(*) FROM symbols"),
        edges: count(conn, "SELECT COUNT(*) FROM edges"),
        imports: count(conn, "SELECT COUNT(*) FROM imports"),
        by_kind: grouped(
            conn,
            "SELECT kind, COUNT(*) FROM symbols GROUP BY kind ORDER BY COUNT(*) DESC",
        ),
        by_repo: grouped(
            conn,
            "SELECT f.repo_id, COUNT(*) FROM symbols s \
             JOIN files f ON s.file_id = f.id GROUP BY f.repo_id ORDER BY COUNT(*) DESC",
        ),
        edges_resolved: count(
            conn,
            "SELECT COUNT(*) FROM edges WHERE target_id IS NOT NULL",
        ),
        ..GraphStats::default()
    };
    let resolution_counts = grouped(
        conn,
        "SELECT resolution, COUNT(*) FROM edges \
         WHERE kind IN ('calls','references') \
         AND resolution IN ('exact','ambiguous','unresolved') GROUP BY resolution",
    );
    let pick = |name: &str| {
        resolution_counts
            .iter()
            .find(|(key, _)| key == name)
            .map(|(_, n)| *n)
            .unwrap_or(0)
    };
    stats.resolution = (pick("exact"), pick("ambiguous"), pick("unresolved"));
    let pool = stats.resolution.0 + stats.resolution.1;
    stats.exact_share = if pool > 0 {
        stats.resolution.0 as f64 / pool as f64
    } else {
        0.0
    };
    stats.ambiguous_share = if pool > 0 {
        stats.resolution.1 as f64 / pool as f64
    } else {
        0.0
    };
    stats.edge_sources = grouped(
        conn,
        "SELECT COALESCE(source, 'tree_sitter') AS src, COUNT(*) \
         FROM edges GROUP BY 1 ORDER BY 1",
    );

    // Per-language exact share over the same resolution pool.
    if let Ok(mut stmt) = conn.prepare(
        "SELECT f.language AS language, \
         SUM(e.resolution = 'exact') AS exact_n, \
         SUM(e.resolution = 'ambiguous') AS ambiguous_n, \
         SUM(e.resolution = 'unresolved') AS unresolved_n \
         FROM files f \
         LEFT JOIN symbols s ON s.file_id = f.id \
         LEFT JOIN edges e ON e.source_id = s.id \
              AND e.kind IN ('calls','references') \
              AND e.resolution IN ('exact','ambiguous','unresolved') \
         GROUP BY f.language ORDER BY f.language",
    ) {
        let Ok(rows) = stmt.query_map([], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, Option<i64>>(1)?.unwrap_or(0),
                row.get::<_, Option<i64>>(2)?.unwrap_or(0),
                row.get::<_, Option<i64>>(3)?.unwrap_or(0),
            ))
        }) else {
            return stats;
        };
        for (language, exact, ambiguous, _unresolved) in
            rows.collect::<Result<Vec<_>, _>>().unwrap_or_default()
        {
            let lang_pool = exact + ambiguous;
            if lang_pool > 0 {
                stats
                    .exact_share_by_language
                    .push((language, exact as f64 / lang_pool as f64));
            }
        }
    }

    stats.skipped_total = count(conn, "SELECT COUNT(*) FROM skipped_files");
    stats.skipped_by_reason = grouped(
        conn,
        "SELECT reason, COUNT(*) FROM skipped_files GROUP BY reason ORDER BY COUNT(*) DESC",
    );
    stats
}
