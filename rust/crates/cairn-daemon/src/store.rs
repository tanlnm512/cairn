//! Read-only `.kg` access: connection factory and the `search_symbols` primitive.

use std::path::Path;

use cairn_query::tools_search::{self, SymbolRow};
use rusqlite::Connection;

/// Open a `.kg` store read-only (pragma parity + sqlite-vec via `cairn-store`).
pub fn open_read_only(path: &Path) -> Result<Connection, rusqlite::Error> {
    cairn_store::conn::open_read_only(path)
}

/// Search symbols by name/docstring; the canonical port lives in `cairn-query`.
pub fn search_symbols(
    conn: &Connection,
    pattern: &str,
    kind: Option<&str>,
    limit: i64,
) -> Result<Vec<SymbolRow>, rusqlite::Error> {
    tools_search::search_symbols(conn, pattern, kind, limit)
}
