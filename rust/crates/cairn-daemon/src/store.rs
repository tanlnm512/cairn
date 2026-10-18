//! Read-only `.kg` access: connection factory and the `search_symbols` primitive.

use std::collections::HashSet;
use std::path::Path;

use rusqlite::Connection;

/// Open a `.kg` store read-only (pragma parity + sqlite-vec via `cairn-store`).
pub fn open_read_only(path: &Path) -> Result<Connection, rusqlite::Error> {
    cairn_store::conn::open_read_only(path)
}

/// One `search_symbols` result row.
pub struct SymbolRow {
    pub id: String,
    pub kind: String,
    pub name: String,
    pub file_path: String,
    pub line_start: Option<i64>,
    pub repo: String,
}

fn row_symbol(row: &rusqlite::Row<'_>) -> rusqlite::Result<SymbolRow> {
    Ok(SymbolRow {
        id: row.get("id")?,
        kind: row.get("kind")?,
        name: row.get("name")?,
        file_path: row.get("file_path")?,
        line_start: row.get("line_start")?,
        repo: row.get("repo")?,
    })
}

fn fts_rows(
    conn: &Connection,
    fts_query: &str,
    kind: Option<&str>,
    limit: i64,
) -> Result<Vec<SymbolRow>, rusqlite::Error> {
    let mut sql = String::from(
        "SELECT s.*, f.path AS file_path, f.repo_id AS repo, \
         bm25(symbols_fts) AS rank \
         FROM symbols_fts \
         JOIN symbols s ON s.rowid = symbols_fts.rowid \
         JOIN files f ON s.file_id = f.id \
         WHERE symbols_fts MATCH ?",
    );
    if kind.is_some() {
        sql.push_str(" AND s.kind = ?");
    }
    sql.push_str(" ORDER BY rank LIMIT ?");
    let mut stmt = conn.prepare(&sql)?;
    let map = |row: &rusqlite::Row<'_>| row_symbol(row);
    let rows = match kind {
        Some(k) => stmt.query_map(rusqlite::params![fts_query, k, limit], map)?,
        None => stmt.query_map(rusqlite::params![fts_query, limit], map)?,
    };
    rows.collect()
}

fn like_rows(
    conn: &Connection,
    pattern: &str,
    kind: Option<&str>,
    limit: i64,
) -> Result<Vec<SymbolRow>, rusqlite::Error> {
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
    let sql = if kind.is_some() {
        "SELECT s.*, f.path AS file_path, f.repo_id AS repo \
         FROM symbols s JOIN files f ON s.file_id = f.id \
         WHERE s.name LIKE ? ESCAPE '\\' AND s.kind = ? \
         ORDER BY s.name LIMIT ?"
    } else {
        "SELECT s.*, f.path AS file_path, f.repo_id AS repo \
         FROM symbols s JOIN files f ON s.file_id = f.id \
         WHERE s.name LIKE ? ESCAPE '\\' \
         ORDER BY s.name LIMIT ?"
    };
    let mut stmt = conn.prepare(sql)?;
    let map = |row: &rusqlite::Row<'_>| row_symbol(row);
    let rows = match kind {
        Some(k) => stmt.query_map(rusqlite::params![sql_pattern, k, limit], map)?,
        None => stmt.query_map(rusqlite::params![sql_pattern, limit], map)?,
    };
    rows.collect()
}

/// Search symbols by name/docstring, ranked by bm25, with the LIKE fallback and
/// non-prefix LIKE merge (the Python `lexical.search_symbols` contract).
pub fn search_symbols(
    conn: &Connection,
    pattern: &str,
    kind: Option<&str>,
    limit: i64,
) -> Result<Vec<SymbolRow>, rusqlite::Error> {
    let Some(fts_query) = crate::search::pattern_to_fts(pattern) else {
        return like_rows(conn, pattern, kind, limit);
    };
    let mut rows = match fts_rows(conn, &fts_query, kind, limit) {
        Ok(rows) => rows,
        // FTS unavailable or malformed query: degrade to the unranked LIKE scan.
        Err(_) => return like_rows(conn, pattern, kind, limit),
    };
    if !crate::search::is_fts_prefix_pattern(pattern) && (rows.len() as i64) < limit {
        let mut seen: HashSet<String> = rows.iter().map(|r| r.id.clone()).collect();
        for r in like_rows(conn, pattern, kind, limit)? {
            let id = r.id.clone();
            if seen.insert(id) {
                rows.push(r);
                if rows.len() as i64 >= limit {
                    break;
                }
            }
        }
    }
    Ok(rows)
}
