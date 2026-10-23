//! Graph query core: definitions, edges, impact, trace, taint, viz scopes.

use rusqlite::Connection;

pub use cross_repo::{RepoDep, cross_repo_deps, resolve_namespaces};
pub use impact::{
    CLOSURE_MAX_DEPTH, ImpactEntry, ImpactResult, impact_analysis, impact_analysis_seeded,
};
pub use lexical::{is_fts_prefix_pattern, lexical_search_symbols, pattern_to_fts};
pub use taint::{
    PathHop, SymbolPath, TaintHop, TaintPath, TaintRegistryTables, find_paths, find_paths_registry,
    find_symbol_paths, format_taint_warning, intersect_seeds, intersect_seeds_registry,
    merged_registry, resolve_pattern_symbols,
};
pub use trace::{FlowNode, trace_flow};
pub use viz::{
    Graph, get_deps_graph, get_impact_graph, get_module_graph, get_repo_graph, get_symbol_graph,
    get_symbol_neighbors, get_symbol_overview, to_dot, to_html, to_json, to_mermaid, viz_embed,
};

/// Structural edges are followed by default; service edges require opt-in.
pub const STRUCTURAL_EDGE_KINDS: [&str; 4] = ["calls", "call", "extends", "implements"];

/// One symbol row joined with its file.
#[derive(Clone, Debug)]
pub struct SymbolRow {
    pub id: String,
    pub name: String,
    pub kind: String,
    pub qualified_name: Option<String>,
    pub line_start: Option<i64>,
    pub line_end: Option<i64>,
    pub file_path: String,
    pub repo: String,
}

const SYMBOL_SELECT: &str = "SELECT s.id, s.name, s.kind, s.qualified_name, \
     s.line_start, s.line_end, f.path AS file_path, f.repo_id AS repo \
     FROM symbols s JOIN files f ON s.file_id = f.id";

fn row_symbol(row: &rusqlite::Row<'_>) -> rusqlite::Result<SymbolRow> {
    Ok(SymbolRow {
        id: row.get(0)?,
        name: row.get(1)?,
        kind: row.get(2)?,
        qualified_name: row.get(3)?,
        line_start: row.get(4)?,
        line_end: row.get(5)?,
        file_path: row.get(6)?,
        repo: row.get(7)?,
    })
}

fn escape_like(value: &str) -> String {
    value
        .replace('\\', "\\\\")
        .replace('%', "\\%")
        .replace('_', "\\_")
}

/// Exact name, then qualified name, then substring — ordered by best match.
pub fn find_definition(
    conn: &Connection,
    name: &str,
    limit: i64,
) -> Result<Vec<SymbolRow>, rusqlite::Error> {
    let sql = format!("{SYMBOL_SELECT} WHERE s.name = ? ORDER BY s.kind LIMIT ?");
    let exact: Vec<SymbolRow> = conn
        .prepare(&sql)?
        .query_map(rusqlite::params![name, limit], row_symbol)?
        .collect::<Result<_, _>>()?;
    if !exact.is_empty() {
        return Ok(exact);
    }
    let sql = format!("{SYMBOL_SELECT} WHERE s.qualified_name = ? LIMIT ?");
    let qual: Vec<SymbolRow> = conn
        .prepare(&sql)?
        .query_map(rusqlite::params![name, limit], row_symbol)?
        .collect::<Result<_, _>>()?;
    if !qual.is_empty() {
        return Ok(qual);
    }
    let pattern = format!("%{}%", escape_like(name));
    let sql = format!("{SYMBOL_SELECT} WHERE s.name LIKE ? ESCAPE '\\' ORDER BY s.kind LIMIT ?");
    conn.prepare(&sql)?
        .query_map(rusqlite::params![pattern, limit], row_symbol)?
        .collect::<Result<_, _>>()
}

/// Find a symbol by its database ID; at most one row.
pub fn find_definition_by_id(conn: &Connection, sym_id: &str) -> Option<SymbolRow> {
    let sql = format!("{SYMBOL_SELECT} WHERE s.id = ? LIMIT 1");
    conn.prepare(&sql)
        .ok()?
        .query_row(rusqlite::params![sym_id], row_symbol)
        .ok()
}

/// One caller edge row.
#[derive(Clone, Debug)]
pub struct CallerRow {
    pub edge_line: Option<i64>,
    pub edge_kind: String,
    pub caller_name: String,
    pub caller_kind: String,
    pub caller_id: String,
    pub file_path: String,
    pub repo: String,
    pub resolution: Option<String>,
}

/// One callee edge row; `resolved` mirrors the target id (NULL = unresolved).
#[derive(Clone, Debug)]
pub struct CalleeRow {
    pub edge_line: Option<i64>,
    pub edge_kind: String,
    pub callee_name: Option<String>,
    pub callee_kind: String,
    pub resolved: Option<String>,
    pub resolution: Option<String>,
    pub file_path: String,
    pub repo: String,
}

/// Edges whose target is a symbol named `name` (precise by id, fuzzy by name).
pub fn get_callers(
    conn: &Connection,
    name: &str,
    fuzzy: bool,
    limit: i64,
    symbol_id: Option<&str>,
) -> Result<Vec<CallerRow>, rusqlite::Error> {
    let (target_clause, params): (&str, Vec<String>) = match (fuzzy, symbol_id) {
        (false, Some(id)) => ("e.target_id = ?", vec![id.to_string()]),
        (true, Some(id)) => (
            "(e.target_name = ? OR e.target_id = ?)",
            vec![name.to_string(), id.to_string()],
        ),
        (true, None) => (
            "(e.target_name = ? OR e.target_id IN (SELECT id FROM symbols WHERE name = ?))",
            vec![name.to_string(), name.to_string()],
        ),
        (false, None) => (
            "e.target_id IN (SELECT id FROM symbols WHERE name = ?)",
            vec![name.to_string()],
        ),
    };
    let sql = format!(
        "SELECT e.line AS edge_line, e.kind AS edge_kind, \
         s.name AS caller_name, s.kind AS caller_kind, s.id AS caller_id, \
         f.path AS file_path, f.repo_id AS repo, e.resolution AS resolution \
         FROM edges e \
         JOIN symbols s ON e.source_id = s.id \
         JOIN files f ON s.file_id = f.id \
         WHERE {target_clause} \
         ORDER BY f.repo_id, f.path, e.line, s.name \
         LIMIT ?"
    );
    let mut refs: Vec<&dyn rusqlite::ToSql> =
        params.iter().map(|p| p as &dyn rusqlite::ToSql).collect();
    refs.push(&limit);
    conn.prepare(&sql)?
        .query_map(refs.as_slice(), |row| {
            Ok(CallerRow {
                edge_line: row.get(0)?,
                edge_kind: row.get(1)?,
                caller_name: row.get(2)?,
                caller_kind: row.get(3)?,
                caller_id: row.get(4)?,
                file_path: row.get(5)?,
                repo: row.get(6)?,
                resolution: row.get(7)?,
            })
        })?
        .collect()
}

/// Edges whose source is a symbol named `name` (precise omits unresolved targets).
pub fn get_callees(
    conn: &Connection,
    name: &str,
    fuzzy: bool,
    limit: i64,
    symbol_id: Option<&str>,
) -> Result<Vec<CalleeRow>, rusqlite::Error> {
    let target_clause = if fuzzy {
        ""
    } else {
        "AND e.target_id IS NOT NULL"
    };
    let (source_clause, param): (&str, String) = match symbol_id {
        Some(id) => ("s.id = ?", id.to_string()),
        None => ("s.name = ?", name.to_string()),
    };
    let sql = format!(
        "SELECT e.line AS edge_line, e.kind AS edge_kind, \
         COALESCE(t.name, e.target_name) AS callee_name, \
         COALESCE(t.kind, 'unknown') AS callee_kind, \
         e.target_id AS resolved, e.resolution AS resolution, \
         f.path AS file_path, f.repo_id AS repo \
         FROM edges e \
         JOIN symbols s ON e.source_id = s.id \
         JOIN files f ON s.file_id = f.id \
         LEFT JOIN symbols t ON e.target_id = t.id \
         WHERE {source_clause} {target_clause} \
         ORDER BY f.repo_id, f.path, e.line, COALESCE(t.name, e.target_name) \
         LIMIT ?"
    );
    conn.prepare(&sql)?
        .query_map(rusqlite::params![param, limit], |row| {
            Ok(CalleeRow {
                edge_line: row.get(0)?,
                edge_kind: row.get(1)?,
                callee_name: row.get(2)?,
                callee_kind: row.get(3)?,
                resolved: row.get(4)?,
                resolution: row.get(5)?,
                file_path: row.get(6)?,
                repo: row.get(7)?,
            })
        })?
        .collect()
}

// --- lexical ----------------------------------------------------------------

mod lexical {
    //! FTS5 seed search with the LIKE fallback and merge.

    use std::collections::HashSet;

    use rusqlite::Connection;

    use super::{SymbolRow, escape_like};

    /// True iff `pattern` is a pure single-trailing-wildcard token (`"Api*"`).
    pub fn is_fts_prefix_pattern(pattern: &str) -> bool {
        let p = pattern.trim();
        let Some(stripped) = p.strip_suffix('*') else {
            return false;
        };
        if stripped.contains('*') || p.contains('_') {
            return false;
        }
        let token = stripped.trim();
        !token.is_empty() && !token.contains([' ', '"', ':', '(', ')'])
    }

    /// Convert a user pattern into an FTS5 MATCH query; `None` selects the LIKE path.
    pub fn pattern_to_fts(pattern: &str) -> Option<String> {
        let p = pattern.trim();
        if p.is_empty() {
            return None;
        }
        if is_fts_prefix_pattern(p) {
            return Some(format!(
                "{}*",
                p.strip_suffix('*').expect("suffix checked").trim()
            ));
        }
        let cleaned: String = p
            .chars()
            .map(|c| if c == '*' || c == '%' { ' ' } else { c })
            .collect();
        let tokens: Vec<&str> = cleaned
            .split(|c: char| !c.is_ascii_alphanumeric())
            .filter(|t| !t.is_empty())
            .collect();
        if tokens.is_empty() {
            return None;
        }
        Some(format!("\"{}\"*", tokens.join(" ")))
    }

    fn select_tail(kind: Option<&str>) -> &'static str {
        if kind.is_some() {
            " AND s.kind = ?"
        } else {
            ""
        }
    }

    fn fts_rows(
        conn: &Connection,
        fts_query: &str,
        kind: Option<&str>,
        limit: i64,
    ) -> Result<Vec<SymbolRow>, rusqlite::Error> {
        let sql = format!(
            "SELECT s.id, s.name, s.kind, s.qualified_name, s.line_start, s.line_end, \
             f.path AS file_path, f.repo_id AS repo, bm25(symbols_fts) AS rank \
             FROM symbols_fts \
             JOIN symbols s ON s.rowid = symbols_fts.rowid \
             JOIN files f ON s.file_id = f.id \
             WHERE symbols_fts MATCH ?{} ORDER BY rank LIMIT ?",
            select_tail(kind)
        );
        let mut stmt = conn.prepare(&sql)?;
        let map = |row: &rusqlite::Row<'_>| {
            Ok(SymbolRow {
                id: row.get(0)?,
                name: row.get(1)?,
                kind: row.get(2)?,
                qualified_name: row.get(3)?,
                line_start: row.get(4)?,
                line_end: row.get(5)?,
                file_path: row.get(6)?,
                repo: row.get(7)?,
            })
        };
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
        let escaped: Vec<String> = pattern.split('*').map(escape_like).collect();
        let mut sql_pattern = escaped.join("%");
        if !pattern.contains('*') {
            sql_pattern = format!("%{sql_pattern}%");
        }
        let sql = format!(
            "SELECT s.id, s.name, s.kind, s.qualified_name, s.line_start, s.line_end, \
             f.path AS file_path, f.repo_id AS repo \
             FROM symbols s JOIN files f ON s.file_id = f.id \
             WHERE s.name LIKE ? ESCAPE '\\'{} ORDER BY s.name LIMIT ?",
            select_tail(kind)
        );
        let mut stmt = conn.prepare(&sql)?;
        let map = |row: &rusqlite::Row<'_>| {
            Ok(SymbolRow {
                id: row.get(0)?,
                name: row.get(1)?,
                kind: row.get(2)?,
                qualified_name: row.get(3)?,
                line_start: row.get(4)?,
                line_end: row.get(5)?,
                file_path: row.get(6)?,
                repo: row.get(7)?,
            })
        };
        let rows = match kind {
            Some(k) => stmt.query_map(rusqlite::params![sql_pattern, k, limit], map)?,
            None => stmt.query_map(rusqlite::params![sql_pattern, limit], map)?,
        };
        rows.collect()
    }

    /// Search symbols by name/docstring, bm25-ranked, with the LIKE fallback
    /// and the non-prefix LIKE merge.
    pub fn lexical_search_symbols(
        conn: &Connection,
        pattern: &str,
        kind: Option<&str>,
        limit: i64,
    ) -> Vec<SymbolRow> {
        let Some(fts_query) = pattern_to_fts(pattern) else {
            return like_rows(conn, pattern, kind, limit).unwrap_or_default();
        };
        let mut rows = match fts_rows(conn, &fts_query, kind, limit) {
            Ok(rows) => rows,
            // FTS unavailable or malformed query: degrade to the unranked LIKE scan.
            Err(_) => return like_rows(conn, pattern, kind, limit).unwrap_or_default(),
        };
        if !is_fts_prefix_pattern(pattern) && (rows.len() as i64) < limit {
            let mut seen: HashSet<String> = rows.iter().map(|r| r.id.clone()).collect();
            if let Ok(extra) = like_rows(conn, pattern, kind, limit) {
                for r in extra {
                    let id = r.id.clone();
                    if seen.insert(id) {
                        rows.push(r);
                        if rows.len() as i64 >= limit {
                            break;
                        }
                    }
                }
            }
        }
        rows
    }
}

// --- impact -----------------------------------------------------------------

pub(crate) mod impact {
    //! Recursive caller traversal with cycle detection, closure fast path.

    use std::collections::{HashMap, HashSet};

    use rusqlite::Connection;

    use super::{CallerRow, STRUCTURAL_EDGE_KINDS, find_definition, get_callers};

    /// Maximum closure distance materialized in `transitive_edges`.
    pub const CLOSURE_MAX_DEPTH: i64 = 4;

    #[derive(Clone, Debug)]
    pub struct ImpactEntry {
        pub symbol: String,
        pub file: String,
        pub repo: String,
        pub depth: i64,
    }

    #[derive(Debug, Default)]
    pub struct ImpactResult {
        pub impacted: Vec<ImpactEntry>,
        pub cycles: Vec<(String, i64)>,
        pub total: usize,
        pub truncated: bool,
    }

    pub(crate) fn closure_available(conn: &Connection) -> bool {
        conn.query_row("SELECT 1 FROM transitive_edges LIMIT 1", [], |r| {
            r.get::<_, i64>(0)
        })
        .is_ok()
    }

    fn closure_has_seed_cycle(conn: &Connection, seed_ids: &[String]) -> bool {
        let ph = vec!["?"; seed_ids.len()].join(",");
        let sql = format!(
            "SELECT 1 FROM transitive_edges \
             WHERE source_id IN ({ph}) AND target_id IN ({ph}) LIMIT 1"
        );
        let refs: Vec<&dyn rusqlite::ToSql> = seed_ids
            .iter()
            .chain(seed_ids.iter())
            .map(|s| s as &dyn rusqlite::ToSql)
            .collect();
        conn.query_row(sql.as_str(), refs.as_slice(), |r| r.get::<_, i64>(0))
            .is_ok()
    }

    fn impact_from_closure(
        conn: &Connection,
        seed_ids: &[String],
        max_depth: i64,
        limit: usize,
    ) -> Option<ImpactResult> {
        if seed_ids.is_empty() {
            return Some(ImpactResult::default());
        }
        let ph = vec!["?"; seed_ids.len()].join(",");
        let sql = format!(
            "SELECT s.name AS symbol, f.path AS file, f.repo_id AS repo, \
             MIN(t.distance) - 1 AS depth \
             FROM transitive_edges t \
             JOIN symbols s ON s.id = t.source_id \
             JOIN files f ON s.file_id = f.id \
             WHERE t.target_id IN ({ph}) \
               AND t.source_id NOT IN ({ph}) \
               AND t.distance <= ? \
             GROUP BY t.source_id \
             ORDER BY depth, symbol, file \
             LIMIT ?"
        );
        let mut refs: Vec<&dyn rusqlite::ToSql> = seed_ids
            .iter()
            .chain(seed_ids.iter())
            .map(|s| s as &dyn rusqlite::ToSql)
            .collect();
        let max_distance = max_depth + 1;
        let row_limit = (limit + 1) as i64;
        refs.push(&max_distance);
        refs.push(&row_limit);
        let rows: Vec<ImpactEntry> = conn
            .prepare(&sql)
            .ok()?
            .query_map(refs.as_slice(), |r| {
                Ok(ImpactEntry {
                    symbol: r.get(0)?,
                    file: r.get(1)?,
                    repo: r.get(2)?,
                    depth: r.get(3)?,
                })
            })
            .ok()?
            .collect::<Result<_, _>>()
            .ok()?;
        let truncated = rows.len() > limit;
        let impacted: Vec<ImpactEntry> = rows.into_iter().take(limit).collect();
        Some(ImpactResult {
            total: impacted.len(),
            impacted,
            cycles: vec![],
            truncated,
        })
    }

    /// Recursive caller traversal (closure fast path, DFS fallback).
    pub fn impact_analysis(
        conn: &Connection,
        name: &str,
        max_depth: i64,
        fuzzy: bool,
        limit: usize,
    ) -> Result<ImpactResult, rusqlite::Error> {
        if !fuzzy && max_depth < CLOSURE_MAX_DEPTH {
            let exact = conn
                .prepare("SELECT id FROM symbols WHERE name = ? LIMIT 1")?
                .query_row(rusqlite::params![name], |r| r.get::<_, String>(0))
                .is_ok();
            let seeds: Vec<String> = if exact {
                find_definition(conn, name, limit as i64)?
                    .into_iter()
                    .map(|s| s.id)
                    .collect()
            } else {
                Vec::new()
            };
            if !seeds.is_empty()
                && closure_available(conn)
                && !closure_has_seed_cycle(conn, &seeds)
                && let Some(result) = impact_from_closure(conn, &seeds, max_depth, limit)
            {
                return Ok(result);
            }
        }
        Ok(impact_dfs(conn, name, max_depth, fuzzy, limit))
    }

    /// Id-pinned traversal: the seed symbol is exact, and every hop resolves
    /// callers by symbol id (`impact_analysis(..., seed_id=...)`).
    pub fn impact_analysis_seeded(
        conn: &Connection,
        name: &str,
        seed_id: &str,
        max_depth: i64,
        limit: usize,
        fuzzy: bool,
    ) -> Result<ImpactResult, rusqlite::Error> {
        if !fuzzy
            && max_depth < CLOSURE_MAX_DEPTH
            && closure_available(conn)
            && !closure_has_seed_cycle(conn, std::slice::from_ref(&seed_id.to_string()))
            && let Some(result) = impact_from_closure(
                conn,
                std::slice::from_ref(&seed_id.to_string()),
                max_depth,
                limit,
            )
        {
            return Ok(result);
        }
        let mut state = DfsState {
            conn,
            fuzzy,
            max_depth,
            limit,
            seeded: true,
            allowed: Some(STRUCTURAL_EDGE_KINDS.iter().copied().collect()),
            min_depth: HashMap::new(),
            rows_by_id: HashMap::new(),
            on_path: HashSet::new(),
            results: Vec::new(),
            cycles: Vec::new(),
            cycles_seen: HashSet::new(),
            truncated: false,
            callers_memo: HashMap::new(),
        };
        state.on_path.insert(seed_id.to_string());
        let root_callers = state.callers(Some(seed_id), name);
        for c in root_callers {
            if state
                .allowed
                .as_ref()
                .is_some_and(|allowed| !allowed.contains(c.edge_kind.as_str()))
            {
                continue;
            }
            state.record_caller(&c, 0);
            if state.truncated {
                break;
            }
        }
        Ok(ImpactResult {
            impacted: state.results,
            cycles: state.cycles,
            total: 0,
            truncated: state.truncated,
        }
        .with_total())
    }

    struct DfsState<'a> {
        conn: &'a Connection,
        fuzzy: bool,
        max_depth: i64,
        limit: usize,
        /// Id-pinned mode: each hop resolves callers by the symbol's exact id.
        seeded: bool,
        allowed: Option<HashSet<&'static str>>,
        min_depth: HashMap<String, i64>,
        rows_by_id: HashMap<String, usize>,
        on_path: HashSet<String>,
        results: Vec<ImpactEntry>,
        cycles: Vec<(String, i64)>,
        cycles_seen: HashSet<String>,
        truncated: bool,
        callers_memo: HashMap<String, Vec<CallerRow>>,
    }

    impl<'a> DfsState<'a> {
        fn callers(&mut self, sym_id: Option<&str>, name: &str) -> Vec<CallerRow> {
            let key = sym_id
                .map(|id| format!("{id}\u{0}{name}"))
                .unwrap_or_else(|| name.to_string());
            if let Some(rows) = self.callers_memo.get(&key) {
                return rows.clone();
            }
            let rows = get_callers(self.conn, name, self.fuzzy, 200, sym_id).unwrap_or_default();
            self.callers_memo.insert(key, rows.clone());
            rows
        }

        fn record_caller(&mut self, c: &CallerRow, depth: i64) {
            // Record caller c at depth -- new row, or a strictly closer depth
            // relaxed in place -- then walk into it either way so back-edges
            // still surface as cycles.
            let existing = self.rows_by_id.get(&c.caller_id).copied();
            match existing {
                None => {
                    if self.results.len() >= self.limit {
                        self.truncated = true;
                        return;
                    }
                    self.results.push(ImpactEntry {
                        symbol: c.caller_name.clone(),
                        file: c.file_path.clone(),
                        repo: c.repo.clone(),
                        depth,
                    });
                    let idx = self.results.len() - 1;
                    self.rows_by_id.insert(c.caller_id.clone(), idx);
                }
                Some(idx) => {
                    if depth < self.results[idx].depth {
                        self.results[idx].depth = depth;
                    }
                }
            }
            self.traverse(&c.caller_id.clone(), &c.caller_name.clone(), depth + 1);
        }

        fn traverse(&mut self, sym_id: &str, sym_name: &str, depth: i64) {
            if self.truncated || depth > self.max_depth {
                return;
            }
            if self.on_path.contains(sym_id) {
                // Genuine back-edge: a caller that's already on our DFS path.
                if self.cycles_seen.insert(sym_id.to_string()) {
                    self.cycles.push((sym_name.to_string(), depth));
                }
                return;
            }
            if self.min_depth.get(sym_id).is_some_and(|prev| *prev < depth) {
                return;
            }
            self.min_depth.insert(sym_id.to_string(), depth - 1);
            self.on_path.insert(sym_id.to_string());
            // Name-keyed lookup: the unseeded DFS resolves callers of every
            // same-named symbol; the seeded DFS pins the exact id.
            let callers = if self.seeded {
                self.callers(Some(sym_id), sym_name)
            } else {
                self.callers(None, sym_name)
            };
            for c in callers {
                if self
                    .allowed
                    .as_ref()
                    .is_some_and(|allowed| !allowed.contains(c.edge_kind.as_str()))
                {
                    continue;
                }
                self.record_caller(&c, depth);
                if self.truncated {
                    break;
                }
            }
            self.on_path.remove(sym_id);
        }
    }

    fn impact_dfs(
        conn: &Connection,
        name: &str,
        max_depth: i64,
        fuzzy: bool,
        limit: usize,
    ) -> ImpactResult {
        let mut state = DfsState {
            conn,
            fuzzy,
            max_depth,
            limit,
            seeded: false,
            allowed: Some(STRUCTURAL_EDGE_KINDS.iter().copied().collect()),
            min_depth: HashMap::new(),
            rows_by_id: HashMap::new(),
            on_path: HashSet::new(),
            results: Vec::new(),
            cycles: Vec::new(),
            cycles_seen: HashSet::new(),
            truncated: false,
            callers_memo: HashMap::new(),
        };
        // Seed: every matching id is marked on-path so the seed is never re-entered.
        if let Ok(seeds) = find_definition(conn, name, limit as i64) {
            for seed in seeds {
                state.on_path.insert(seed.id);
            }
        }
        let root_callers = state.callers(None, name);
        for c in root_callers {
            if state
                .allowed
                .as_ref()
                .is_some_and(|allowed| !allowed.contains(c.edge_kind.as_str()))
            {
                continue;
            }
            state.record_caller(&c, 0);
            if state.truncated {
                break;
            }
        }
        ImpactResult {
            impacted: state.results,
            cycles: state.cycles,
            total: 0,
            truncated: state.truncated,
        }
        .with_total()
    }

    impl ImpactResult {
        fn with_total(mut self) -> Self {
            self.total = self.impacted.len();
            self
        }
    }
}

// --- trace ------------------------------------------------------------------

mod trace {
    //! Downward callee traversal from an entry symbol — the flow it executes.

    use std::collections::{HashMap, HashSet};

    use rusqlite::Connection;

    use super::{
        CalleeRow, STRUCTURAL_EDGE_KINDS, find_definition, find_definition_by_id, get_callees,
    };

    /// Maximum nodes visited before truncation.
    const VISIT_LIMIT: usize = 500;
    /// Per-node callee lookup cap.
    const CALLEES_LIMIT: i64 = 50;

    #[derive(Clone, Debug)]
    pub struct FlowNode {
        pub symbol: String,
        pub kind: String,
        pub file: String,
        pub repo: String,
        pub depth: i64,
        pub parent: Option<String>,
    }

    #[derive(Debug, Default)]
    pub struct Flow {
        pub entry: String,
        pub chain: Vec<FlowNode>,
        pub branches: Vec<(String, Vec<String>)>,
        pub leaves: Vec<String>,
        pub modules: Vec<String>,
        pub cycles: Vec<(String, i64)>,
        pub total: usize,
        pub truncated: bool,
    }

    struct TraceState<'a> {
        conn: &'a Connection,
        entry_row_id: String,
        visited: HashMap<String, FlowNode>,
        on_path: HashSet<String>,
        branches: Vec<(String, Vec<String>)>,
        leaves: Vec<String>,
        cycles: Vec<(String, i64)>,
        /// Index into `cycles` per id, kept at the shallowest re-entry.
        cycle_at: HashMap<String, usize>,
        branched: HashSet<String>,
        truncated: bool,
        callees_memo: HashMap<String, Vec<CalleeRow>>,
        defn_memo: HashMap<String, Option<super::SymbolRow>>,
    }

    impl<'a> TraceState<'a> {
        fn callees(&mut self, cid: &str, cname: &str) -> Vec<CalleeRow> {
            if let Some(rows) = self.callees_memo.get(cid) {
                return rows.clone();
            }
            let sym_id = if cid.starts_with("name:") {
                None
            } else {
                Some(cid)
            };
            let rows =
                get_callees(self.conn, cname, false, CALLEES_LIMIT, sym_id).unwrap_or_default();
            self.callees_memo.insert(cid.to_string(), rows.clone());
            rows
        }

        fn defn(&mut self, cid: &str, cname: &str) -> Option<super::SymbolRow> {
            if cid.starts_with("name:") {
                return find_definition(self.conn, cname, 1)
                    .ok()
                    .and_then(|rows| rows.into_iter().next());
            }
            if !self.defn_memo.contains_key(cid) {
                let row = find_definition_by_id(self.conn, cid);
                self.defn_memo.insert(cid.to_string(), row);
            }
            self.defn_memo.get(cid).cloned().flatten()
        }

        fn walk(&mut self, sym_id: &str, sym_name: &str, depth: i64, max_depth: i64) {
            if self.truncated || depth >= max_depth {
                return;
            }
            if self.on_path.contains(sym_id) {
                match self.cycle_at.get(sym_id) {
                    Some(&i) => self.cycles[i].1 = self.cycles[i].1.min(depth),
                    None => {
                        self.cycle_at.insert(sym_id.to_string(), self.cycles.len());
                        self.cycles.push((sym_name.to_string(), depth));
                    }
                }
                return;
            }
            self.on_path.insert(sym_id.to_string());
            let callees = self.callees(sym_id, sym_name);
            if callees.is_empty() {
                if sym_id != self.entry_row_id {
                    self.leaves.push(sym_name.to_string());
                }
                self.on_path.remove(sym_id);
                return;
            }
            // Dedup callees by identity (id when resolved, else name) within
            // this hop so one node is walked once even if reached via multiple edges.
            let mut seen_callees: HashSet<String> = HashSet::new();
            let mut outgoing: Vec<String> = Vec::new();
            for c in &callees {
                if !STRUCTURAL_EDGE_KINDS.contains(&c.edge_kind.as_str()) {
                    continue;
                }
                let Some(cname) = c.callee_name.clone() else {
                    continue;
                };
                if cname.is_empty() {
                    continue;
                }
                let cid = c
                    .resolved
                    .clone()
                    .unwrap_or_else(|| format!("name:{cname}"));
                if !seen_callees.insert(cid.clone()) {
                    continue;
                }
                outgoing.push(cname.clone());
                if self.visited.len() >= VISIT_LIMIT {
                    self.truncated = true;
                    break;
                }
                if !self.visited.contains_key(&cid) {
                    // Resolve the callee's DEFINITION location (not the call
                    // site): where the symbol is actually declared.
                    let (cfile, crepo, ckind) = match self.defn(&cid, &cname) {
                        Some(d) => (d.file_path, d.repo, d.kind),
                        None => (c.file_path.clone(), c.repo.clone(), c.callee_kind.clone()),
                    };
                    self.visited.insert(
                        cid.clone(),
                        FlowNode {
                            symbol: cname.clone(),
                            kind: ckind,
                            file: cfile,
                            repo: crepo,
                            depth: depth + 1,
                            parent: Some(sym_name.to_string()),
                        },
                    );
                    self.walk(&cid, &cname, depth + 1, max_depth);
                } else if self.on_path.contains(&cid) {
                    // Back-edge into the active DFS path: re-entering walk()
                    // records the cycle.
                    self.walk(&cid, &cname, depth + 1, max_depth);
                } else if let Some(node) = self.visited.get_mut(&cid)
                    && depth + 1 < node.depth
                {
                    // Shorter route to an explored id: re-walk so its subtree
                    // gets the full max_depth budget from its shortest depth.
                    node.depth = depth + 1;
                    node.parent = Some(sym_name.to_string());
                    self.walk(&cid, &cname, depth + 1, max_depth);
                }
            }
            if outgoing.len() > 1 && self.branched.insert(sym_id.to_string()) {
                self.branches.push((sym_name.to_string(), outgoing));
            }
            self.on_path.remove(sym_id);
        }
    }

    /// Downward structural callee walk with cycles, leaves, and depth cutoffs.
    pub fn trace_flow(conn: &Connection, entry: &str, max_depth: i64) -> Flow {
        let entry_row = find_definition(conn, entry, 1).unwrap_or_default();
        let entry_file = entry_row
            .first()
            .map(|r| r.file_path.clone())
            .unwrap_or_default();
        let entry_repo = entry_row
            .first()
            .map(|r| r.repo.clone())
            .unwrap_or_default();
        let entry_kind = entry_row
            .first()
            .map(|r| r.kind.clone())
            .unwrap_or_else(|| "function".to_string());
        let entry_row_id = entry_row
            .first()
            .map(|r| r.id.clone())
            .unwrap_or_else(|| format!("name:{entry}"));

        let mut state = TraceState {
            conn,
            entry_row_id: entry_row_id.clone(),
            visited: HashMap::new(),
            on_path: HashSet::new(),
            branches: Vec::new(),
            leaves: Vec::new(),
            cycles: Vec::new(),
            cycle_at: HashMap::new(),
            branched: HashSet::new(),
            truncated: false,
            callees_memo: HashMap::new(),
            defn_memo: HashMap::new(),
        };
        state.visited.insert(
            entry_row_id,
            FlowNode {
                symbol: entry.to_string(),
                kind: entry_kind,
                file: entry_file,
                repo: entry_repo,
                depth: 0,
                parent: None,
            },
        );
        let entry_id = state.entry_row_id.clone();
        state.walk(&entry_id, entry, 0, max_depth);

        let mut chain: Vec<FlowNode> = state.visited.into_values().collect();
        chain.sort_by(|x, y| (x.depth, x.symbol.as_str()).cmp(&(y.depth, y.symbol.as_str())));

        let mut dirs: HashSet<String> = HashSet::new();
        for node in &chain {
            let f = node.file.as_str();
            if f.is_empty() {
                continue;
            }
            let parts: Vec<&str> = f.split('/').collect();
            let m = if parts.len() >= 3 {
                parts[parts.len() - 3..parts.len() - 1].join("/")
            } else if parts.len() >= 2 {
                parts[parts.len() - 2].to_string()
            } else {
                f.to_string()
            };
            dirs.insert(if node.repo.is_empty() {
                m
            } else {
                format!("{}/{}", node.repo, m)
            });
        }
        let mut leaves = state.leaves;
        leaves.sort();
        leaves.dedup();
        let mut modules: Vec<String> = dirs.into_iter().collect();
        modules.sort();
        Flow {
            entry: entry.to_string(),
            total: chain.len(),
            chain,
            branches: state.branches,
            leaves,
            modules,
            cycles: state.cycles,
            truncated: state.truncated,
        }
    }
}

// --- taint ------------------------------------------------------------------

mod taint {
    //! Taint source/sink registry and precise-edge source->sink path trace.

    use std::collections::{HashMap, HashSet};

    use rusqlite::Connection;

    use super::STRUCTURAL_EDGE_KINDS;
    use super::impact::CLOSURE_MAX_DEPTH;

    const CALL_EDGE_KINDS: [&str; 2] = ["calls", "call"];
    const IN_CHUNK: usize = 400;

    fn default_sources() -> Vec<(&'static str, Vec<&'static str>)> {
        vec![
            ("http", vec!["urlopen"]),
            ("cli", vec!["input", "parse_args"]),
            ("env", vec!["getenv"]),
            (
                "file-read",
                vec!["open", "read_text", "read_bytes", "readlines", "readline"],
            ),
            ("api-response", vec!["json", "loads"]),
        ]
    }

    fn default_sinks() -> Vec<(&'static str, Vec<&'static str>)> {
        vec![
            ("sql", vec!["execute", "executemany", "executescript"]),
            (
                "shell",
                vec![
                    "system",
                    "popen",
                    "Popen",
                    "run",
                    "check_call",
                    "check_output",
                ],
            ),
            (
                "file-write",
                vec!["open", "write", "write_text", "write_bytes"],
            ),
            ("network-send", vec!["send", "sendall", "sendto"]),
            ("eval-deserialize", vec!["eval", "exec", "load", "loads"]),
        ]
    }

    fn flatten(table: &[(&'static str, Vec<&'static str>)]) -> Vec<&'static str> {
        let mut names: Vec<&'static str> =
            table.iter().flat_map(|(_, n)| n.iter().copied()).collect();
        names.dedup();
        names
    }

    /// Resolve a query pattern: category token, exact call name, or nothing.
    fn pattern_names(
        pattern: &str,
        table: &[(&'static str, Vec<&'static str>)],
    ) -> Option<Vec<&'static str>> {
        if let Some(item) = table.iter().find(|item| item.0 == pattern) {
            return Some(item.1.clone());
        }
        flatten(table)
            .into_iter()
            .find(|n| *n == pattern)
            .map(|n| vec![n])
    }

    #[derive(Clone, Debug)]
    pub struct TaintHop {
        pub file: String,
        pub symbol: String,
        pub resolution: String,
    }

    #[derive(Clone, Debug)]
    pub struct TaintPath {
        pub hops: Vec<TaintHop>,
    }

    #[derive(Clone, Debug)]
    struct EntrySymbol {
        id: String,
        name: String,
        file: String,
    }

    fn symbols_calling(
        conn: &Connection,
        names: &[String],
    ) -> Result<Vec<EntrySymbol>, rusqlite::Error> {
        let name_ph = vec!["?"; names.len()].join(",");
        let kind_ph = vec!["?"; CALL_EDGE_KINDS.len()].join(",");
        let sql = format!(
            "SELECT DISTINCT s.id, s.name, f.path AS file \
             FROM edges e \
             JOIN symbols s ON s.id = e.source_id \
             JOIN files f ON f.id = s.file_id \
             LEFT JOIN symbols t ON t.id = e.target_id \
             WHERE COALESCE(t.name, e.target_name) IN ({name_ph}) \
               AND e.kind IN ({kind_ph}) \
             ORDER BY f.path, s.name, s.id"
        );
        let mut refs: Vec<&dyn rusqlite::ToSql> =
            names.iter().map(|n| n as &dyn rusqlite::ToSql).collect();
        refs.extend(CALL_EDGE_KINDS.iter().map(|k| k as &dyn rusqlite::ToSql));
        let mut stmt = conn.prepare(&sql)?;
        let rows = stmt.query_map(refs.as_slice(), |r| {
            Ok(EntrySymbol {
                id: r.get(0)?,
                name: r.get(1)?,
                file: r.get(2)?,
            })
        })?;
        rows.collect()
    }

    #[derive(Clone, Debug)]
    struct NeighborRow {
        source_id: String,
        hop_id: String,
        hop_name: String,
        hop_file: String,
        /// Kept for query-shape parity; consumed by the `path` surface.
        #[allow(dead_code)]
        hop_line: Option<i64>,
        resolution: String,
    }

    fn neighbor_rows(
        conn: &Connection,
        source_ids: &[String],
        exact: bool,
    ) -> Result<Vec<NeighborRow>, rusqlite::Error> {
        let id_ph = vec!["?"; source_ids.len()].join(",");
        let kind_ph = vec!["?"; STRUCTURAL_EDGE_KINDS.len()].join(",");
        let (join, res_clause) = if exact {
            (
                "JOIN symbols s ON s.id = e.target_id",
                "e.resolution = 'exact'",
            )
        } else {
            (
                "JOIN symbols s ON s.name = e.target_name",
                "e.resolution IN ('ambiguous', 'unresolved') \
                 AND src_file.repo_id = f.repo_id",
            )
        };
        let sql = format!(
            "SELECT DISTINCT e.source_id, e.resolution, \
             s.id AS hop_id, s.name AS hop_name, f.path AS hop_file, \
             s.line_start AS hop_line \
             FROM edges e \
             {join} \
             JOIN files f ON f.id = s.file_id \
             JOIN symbols src ON src.id = e.source_id \
             JOIN files src_file ON src_file.id = src.file_id \
             WHERE e.source_id IN ({id_ph}) \
               AND {res_clause} \
               AND e.kind IN ({kind_ph}) \
             ORDER BY s.name, f.path, s.id, src.name, src_file.path, src.id"
        );
        let mut refs: Vec<&dyn rusqlite::ToSql> = source_ids
            .iter()
            .map(|s| s as &dyn rusqlite::ToSql)
            .collect();
        refs.extend(
            STRUCTURAL_EDGE_KINDS
                .iter()
                .map(|k| k as &dyn rusqlite::ToSql),
        );
        let mut stmt = conn.prepare(&sql)?;
        let rows = stmt.query_map(refs.as_slice(), |r| {
            Ok(NeighborRow {
                source_id: r.get(0)?,
                resolution: r.get(1)?,
                hop_id: r.get(2)?,
                hop_name: r.get(3)?,
                hop_file: r.get(4)?,
                hop_line: r.get(5)?,
            })
        })?;
        rows.collect()
    }

    fn hop_chain(
        hop_id: &str,
        parent: &HashMap<String, (Option<String>, TaintHop)>,
    ) -> Vec<TaintHop> {
        let mut hops = Vec::new();
        let mut cursor = Some(hop_id.to_string());
        while let Some(id) = cursor {
            match parent.get(&id) {
                Some((prev, hop)) => {
                    hops.push(hop.clone());
                    cursor = prev.clone();
                }
                None => break,
            }
        }
        hops.reverse();
        hops
    }

    fn paths_from_entry(
        conn: &Connection,
        entry: &EntrySymbol,
        terminator_ids: &HashSet<String>,
        fuzzy: bool,
        max_depth: i64,
    ) -> Result<Vec<Vec<TaintHop>>, rusqlite::Error> {
        let mut paths: Vec<Vec<TaintHop>> = Vec::new();
        let mut parent: HashMap<String, (Option<String>, TaintHop)> = HashMap::new();
        parent.insert(
            entry.id.clone(),
            (
                None,
                TaintHop {
                    file: entry.file.clone(),
                    symbol: entry.name.clone(),
                    resolution: "exact".to_string(),
                },
            ),
        );
        if terminator_ids.contains(&entry.id) {
            // The entry itself calls a sink: single-hop source-to-sink path.
            paths.push(hop_chain(&entry.id, &parent));
        }
        let mut visited: HashSet<String> = HashSet::from([entry.id.clone()]);
        let mut frontier: Vec<String> = vec![entry.id.clone()];
        let mut depth = 0i64;
        while !frontier.is_empty() && depth < max_depth {
            let mut next: Vec<String> = Vec::new();
            for chunk in frontier.chunks(IN_CHUNK) {
                let ids: Vec<String> = chunk.to_vec();
                for row in neighbor_rows(conn, &ids, true)? {
                    visit_row(
                        row,
                        terminator_ids,
                        &mut visited,
                        &mut parent,
                        &mut paths,
                        &mut next,
                    );
                }
                if fuzzy {
                    for row in neighbor_rows(conn, &ids, false)? {
                        visit_row(
                            row,
                            terminator_ids,
                            &mut visited,
                            &mut parent,
                            &mut paths,
                            &mut next,
                        );
                    }
                }
            }
            frontier = next;
            depth += 1;
        }
        Ok(paths)
    }

    fn visit_row(
        row: NeighborRow,
        terminator_ids: &HashSet<String>,
        visited: &mut HashSet<String>,
        parent: &mut HashMap<String, (Option<String>, TaintHop)>,
        paths: &mut Vec<Vec<TaintHop>>,
        next: &mut Vec<String>,
    ) {
        if visited.contains(&row.hop_id) {
            return;
        }
        visited.insert(row.hop_id.clone());
        parent.insert(
            row.hop_id.clone(),
            (
                Some(row.source_id.clone()),
                TaintHop {
                    file: row.hop_file,
                    symbol: row.hop_name,
                    resolution: row.resolution,
                },
            ),
        );
        if terminator_ids.contains(&row.hop_id) {
            paths.push(hop_chain(&row.hop_id, parent));
        } else {
            next.push(row.hop_id);
        }
    }

    fn discover_paths(
        conn: &Connection,
        from_names: &[String],
        to_names: &[String],
        fuzzy: bool,
        max_depth: i64,
    ) -> Result<Vec<TaintPath>, rusqlite::Error> {
        let entries = symbols_calling(conn, from_names)?;
        let terminator_ids: HashSet<String> = symbols_calling(conn, to_names)?
            .into_iter()
            .map(|e| e.id)
            .collect();
        if entries.is_empty() || terminator_ids.is_empty() {
            return Ok(vec![]);
        }
        let mut paths = Vec::new();
        for entry in &entries {
            for hops in paths_from_entry(conn, entry, &terminator_ids, fuzzy, max_depth)? {
                paths.push(TaintPath { hops });
            }
        }
        Ok(paths)
    }

    /// Inter-procedural taint paths over the call graph for one
    /// category-or-name source and sink pattern.
    pub fn find_paths(
        conn: &Connection,
        from_pattern: &str,
        to_pattern: &str,
        fuzzy: bool,
        max_depth: i64,
    ) -> Result<Vec<TaintPath>, rusqlite::Error> {
        let sources = default_sources();
        let sinks = default_sinks();
        let Some(from_names) = pattern_names(from_pattern, &sources) else {
            return Ok(vec![]);
        };
        let Some(to_names) = pattern_names(to_pattern, &sinks) else {
            return Ok(vec![]);
        };
        discover_paths(
            conn,
            &from_names.iter().map(|s| s.to_string()).collect::<Vec<_>>(),
            &to_names.iter().map(|s| s.to_string()).collect::<Vec<_>>(),
            fuzzy,
            max_depth,
        )
    }

    /// Category -> call-name tables (defaults merged with config overrides).
    pub type TaintRegistryTables = (Vec<(String, Vec<String>)>, Vec<(String, Vec<String>)>);

    /// Category -> call-name tables merged with overrides (`build_registry`).
    pub fn merged_registry(
        source_overrides: &[(String, Vec<String>)],
        sink_overrides: &[(String, Vec<String>)],
    ) -> TaintRegistryTables {
        fn merge(
            defaults: Vec<(&'static str, Vec<&'static str>)>,
            overrides: &[(String, Vec<String>)],
        ) -> Vec<(String, Vec<String>)> {
            let mut merged: Vec<(String, Vec<String>)> = defaults
                .into_iter()
                .map(|(category, names)| {
                    (
                        category.to_string(),
                        names.into_iter().map(String::from).collect(),
                    )
                })
                .collect();
            for (category, names) in overrides {
                match merged.iter_mut().find(|(c, _)| c == category) {
                    Some(item) => item.1 = names.clone(),
                    None => merged.push((category.clone(), names.clone())),
                }
            }
            merged
        }
        (
            merge(default_sources(), source_overrides),
            merge(default_sinks(), sink_overrides),
        )
    }

    fn string_pattern_names(pattern: &str, table: &[(String, Vec<String>)]) -> Option<Vec<String>> {
        if let Some(item) = table.iter().find(|item| item.0 == pattern) {
            return Some(item.1.clone());
        }
        table
            .iter()
            .flat_map(|(_, names)| names.iter())
            .find(|name| *name == pattern)
            .map(|name| vec![name.clone()])
    }

    /// Taint paths under an explicit registry (config-merged categories).
    pub fn find_paths_registry(
        conn: &Connection,
        sources: &[(String, Vec<String>)],
        sinks: &[(String, Vec<String>)],
        from_pattern: &str,
        to_pattern: &str,
        fuzzy: bool,
        max_depth: i64,
    ) -> Result<Vec<TaintPath>, rusqlite::Error> {
        let Some(from_names) = string_pattern_names(from_pattern, sources) else {
            return Ok(vec![]);
        };
        let Some(to_names) = string_pattern_names(to_pattern, sinks) else {
            return Ok(vec![]);
        };
        discover_paths(conn, &from_names, &to_names, fuzzy, max_depth)
    }

    /// Taint paths whose entry or sink symbol name is in `seeds`.
    pub fn intersect_seeds(
        conn: &Connection,
        seeds: &HashSet<String>,
        fuzzy: bool,
    ) -> Result<Vec<TaintPath>, rusqlite::Error> {
        let (sources, sinks) = merged_registry(&[], &[]);
        intersect_seeds_registry(conn, &(sources, sinks), seeds, fuzzy)
    }

    /// `intersect_seeds` under an explicit config-merged registry.
    pub fn intersect_seeds_registry(
        conn: &Connection,
        registry: &TaintRegistryTables,
        seeds: &HashSet<String>,
        fuzzy: bool,
    ) -> Result<Vec<TaintPath>, rusqlite::Error> {
        if seeds.is_empty() {
            return Ok(vec![]);
        }
        let from_names: Vec<String> = registry
            .0
            .iter()
            .flat_map(|(_, names)| names.iter().cloned())
            .collect();
        let to_names: Vec<String> = registry
            .1
            .iter()
            .flat_map(|(_, names)| names.iter().cloned())
            .collect();
        let paths = discover_paths(conn, &from_names, &to_names, fuzzy, CLOSURE_MAX_DEPTH)?;
        Ok(paths
            .into_iter()
            .filter(|p| {
                seeds.contains(&p.hops[0].symbol)
                    || seeds.contains(&p.hops[p.hops.len() - 1].symbol)
            })
            .collect())
    }

    /// Render taint paths as the warning text explore/blast surfaces display.
    pub fn format_taint_warning(paths: &[TaintPath]) -> String {
        paths
            .iter()
            .map(|p| {
                let chain = p
                    .hops
                    .iter()
                    .map(|h| format!("{}:{} [{}]", h.file, h.symbol, h.resolution))
                    .collect::<Vec<_>>()
                    .join(" -> ");
                format!("Taint path: {chain}")
            })
            .collect::<Vec<_>>()
            .join("\n")
    }

    // --- symbol paths (the `path` surface) -----------------------------------

    /// One symbol matching a `path` endpoint pattern.
    #[derive(Clone, Debug)]
    pub struct PatternSymbol {
        pub id: String,
        pub name: String,
        pub file: String,
    }

    /// Symbols whose name or qualified name contains `pattern`
    /// case-insensitively, in deterministic (file, name, id) order.
    pub fn resolve_pattern_symbols(
        conn: &Connection,
        pattern: &str,
    ) -> Result<Vec<PatternSymbol>, rusqlite::Error> {
        let mut stmt = conn.prepare(
            "SELECT s.id, s.name, f.path AS file \
             FROM symbols s \
             JOIN files f ON f.id = s.file_id \
             WHERE instr(lower(s.name), lower(?)) > 0 \
                OR instr(lower(coalesce(s.qualified_name, '')), lower(?)) > 0 \
             ORDER BY f.path, s.name, s.id",
        )?;
        let rows = stmt.query_map(rusqlite::params![pattern, pattern], |r| {
            Ok(PatternSymbol {
                id: r.get(0)?,
                name: r.get(1)?,
                file: r.get(2)?,
            })
        })?;
        rows.collect()
    }

    /// One hop of a symbol path: file, definition line, symbol name, and the
    /// resolution of the reaching edge (`exact` for the entry hop).
    #[derive(Clone, Debug)]
    pub struct PathHop {
        pub file: String,
        pub line: Option<i64>,
        pub symbol: String,
        pub resolution: String,
    }

    /// A seed-to-seed symbol path, hops ordered from origin to destination.
    #[derive(Clone, Debug)]
    pub struct SymbolPath {
        pub hops: Vec<PathHop>,
    }

    #[derive(Clone, Debug)]
    struct SeedSymbol {
        id: String,
        name: String,
        line_start: Option<i64>,
        file: String,
    }

    fn seed_rows(
        conn: &Connection,
        ids: &HashSet<String>,
    ) -> Result<Vec<SeedSymbol>, rusqlite::Error> {
        let mut rows: Vec<SeedSymbol> = Vec::new();
        let mut sorted: Vec<&String> = ids.iter().collect();
        sorted.sort();
        for chunk in sorted.chunks(IN_CHUNK) {
            let ph = vec!["?"; chunk.len()].join(",");
            let sql = format!(
                "SELECT s.id, s.name, s.line_start, f.path AS file \
                 FROM symbols s \
                 JOIN files f ON f.id = s.file_id \
                 WHERE s.id IN ({ph})"
            );
            let refs: Vec<&dyn rusqlite::ToSql> =
                chunk.iter().map(|i| *i as &dyn rusqlite::ToSql).collect();
            let mut stmt = conn.prepare(&sql)?;
            let chunk_rows = stmt.query_map(refs.as_slice(), |r| {
                Ok(SeedSymbol {
                    id: r.get(0)?,
                    name: r.get(1)?,
                    line_start: r.get(2)?,
                    file: r.get(3)?,
                })
            })?;
            for row in chunk_rows {
                rows.push(row?);
            }
        }
        rows.sort_by(|a, b| (&a.file, &a.name, &a.id).cmp(&(&b.file, &b.name, &b.id)));
        Ok(rows)
    }

    fn hop_chain_path(
        hop_id: &str,
        parent: &HashMap<String, (Option<String>, PathHop)>,
    ) -> Vec<PathHop> {
        let mut hops = Vec::new();
        let mut cursor = Some(hop_id.to_string());
        while let Some(id) = cursor {
            match parent.get(&id) {
                Some((prev, hop)) => {
                    hops.push(hop.clone());
                    cursor = prev.clone();
                }
                None => break,
            }
        }
        hops.reverse();
        hops
    }

    /// Shortest hop chain per terminator reachable from one seed symbol.
    /// Unlike the taint variant, terminators keep expanding: every shortest
    /// route through them can still seed further paths.
    fn paths_from_seed(
        conn: &Connection,
        entry: &SeedSymbol,
        terminator_ids: &HashSet<String>,
        fuzzy: bool,
        max_depth: i64,
    ) -> Result<Vec<Vec<PathHop>>, rusqlite::Error> {
        let mut paths: Vec<Vec<PathHop>> = Vec::new();
        let mut parent: HashMap<String, (Option<String>, PathHop)> = HashMap::new();
        parent.insert(
            entry.id.clone(),
            (
                None,
                PathHop {
                    file: entry.file.clone(),
                    line: entry.line_start,
                    symbol: entry.name.clone(),
                    resolution: "exact".to_string(),
                },
            ),
        );
        if terminator_ids.contains(&entry.id) {
            paths.push(hop_chain_path(&entry.id, &parent));
        }
        let mut visited: HashSet<String> = HashSet::from([entry.id.clone()]);
        let mut frontier: Vec<String> = vec![entry.id.clone()];
        let mut depth = 0i64;
        while !frontier.is_empty() && depth < max_depth {
            let mut next: Vec<String> = Vec::new();
            for chunk in frontier.chunks(IN_CHUNK) {
                let ids: Vec<String> = chunk.to_vec();
                for row in neighbor_rows(conn, &ids, true)? {
                    visit_seed_row(
                        row,
                        terminator_ids,
                        &mut visited,
                        &mut parent,
                        &mut paths,
                        &mut next,
                    );
                }
                if fuzzy {
                    for row in neighbor_rows(conn, &ids, false)? {
                        visit_seed_row(
                            row,
                            terminator_ids,
                            &mut visited,
                            &mut parent,
                            &mut paths,
                            &mut next,
                        );
                    }
                }
            }
            frontier = next;
            depth += 1;
        }
        Ok(paths)
    }

    fn visit_seed_row(
        row: NeighborRow,
        terminator_ids: &HashSet<String>,
        visited: &mut HashSet<String>,
        parent: &mut HashMap<String, (Option<String>, PathHop)>,
        paths: &mut Vec<Vec<PathHop>>,
        next: &mut Vec<String>,
    ) {
        if visited.contains(&row.hop_id) {
            return;
        }
        visited.insert(row.hop_id.clone());
        parent.insert(
            row.hop_id.clone(),
            (
                Some(row.source_id.clone()),
                PathHop {
                    file: row.hop_file,
                    line: row.hop_line,
                    symbol: row.hop_name,
                    resolution: row.resolution,
                },
            ),
        );
        if terminator_ids.contains(&row.hop_id) {
            paths.push(hop_chain_path(&row.hop_id, parent));
        }
        next.push(row.hop_id);
    }

    /// Shortest structural path per (from, to) symbol-id pair over `edges`.
    #[allow(clippy::too_many_arguments)]
    pub fn find_symbol_paths(
        conn: &Connection,
        from_ids: &HashSet<String>,
        to_ids: &HashSet<String>,
        fuzzy: bool,
        max_depth: i64,
    ) -> Result<Vec<SymbolPath>, rusqlite::Error> {
        if from_ids.is_empty() || to_ids.is_empty() {
            return Ok(vec![]);
        }
        let entries = seed_rows(conn, from_ids)?;
        let terminator_ids: HashSet<String> =
            seed_rows(conn, to_ids)?.into_iter().map(|r| r.id).collect();
        if entries.is_empty() || terminator_ids.is_empty() {
            return Ok(vec![]);
        }
        let mut paths: Vec<SymbolPath> = Vec::new();
        for entry in &entries {
            for hops in paths_from_seed(conn, entry, &terminator_ids, fuzzy, max_depth)? {
                paths.push(SymbolPath { hops });
            }
        }
        paths.sort_by_key(|path| {
            let last = path.hops.len() - 1;
            (
                path.hops[0].file.clone(),
                path.hops[0].symbol.clone(),
                path.hops[last].file.clone(),
                path.hops[last].symbol.clone(),
            )
        });
        Ok(paths)
    }
}

// --- cross-repo -------------------------------------------------------------

mod cross_repo {
    //! Import-namespace cross-repo dependency analysis.

    use std::path::Path;

    use rusqlite::Connection;

    /// Built-in fallback mapping be-workspace import namespaces to repo names,
    /// in declaration order (the order matters only for stable tie-breaking).
    fn default_namespaces() -> Vec<(String, String)> {
        [
            ("xyz.be.utils", "be-sdk"),
            ("xyz.be.customer.networking", "be-sdk"),
            ("xyz.be.customer.common", "be-sdk"),
            ("xyz.be.partner.common", "be-sdk"),
            ("xyz.be.common", "be-sdk"),
            ("xyz.be.networking", "be-sdk"),
            ("xyz.be.coreui", "be-core-ui"),
            ("xyz.be.newcoreui", "be-core-ui"),
            ("xyz.be.core_ui_v4", "be-core-ui"),
            ("xyz.be.core.ui", "be-core-ui"),
        ]
        .iter()
        .map(|(ns, owner)| (ns.to_string(), owner.to_string()))
        .collect()
    }

    /// Resolved namespace map: env override, then cairn.json, then defaults.
    pub fn resolve_namespaces(workspace: &Path, env_raw: Option<&str>) -> Vec<(String, String)> {
        let from_json = |value: &serde_json::Value| -> Option<Vec<(String, String)>> {
            let map = value.as_object()?;
            let clean: Vec<(String, String)> = map
                .iter()
                .filter_map(|(k, v)| {
                    let vs = v.as_str()?;
                    if k.trim().is_empty() || vs.trim().is_empty() {
                        None
                    } else {
                        Some((k.clone(), vs.to_string()))
                    }
                })
                .collect();
            if clean.is_empty() { None } else { Some(clean) }
        };
        if let Some(raw) = env_raw
            && let Ok(parsed) = serde_json::from_str::<serde_json::Value>(raw)
            && let Some(clean) = from_json(&parsed)
        {
            return clean;
        }
        if let Ok(text) = std::fs::read_to_string(workspace.join("cairn.json"))
            && let Ok(parsed) = serde_json::from_str::<serde_json::Value>(&text)
            && let Some(clean) = parsed.get("repo_namespaces").and_then(from_json)
        {
            return clean;
        }
        default_namespaces()
    }

    #[derive(Clone, Debug)]
    pub struct RepoDep {
        pub repo: String,
        pub evidence: String,
        pub count: i64,
    }

    #[derive(Clone, Debug, Default)]
    pub struct CrossRepoDeps {
        pub dependencies: Vec<RepoDep>,
        pub dependents: Vec<RepoDep>,
    }

    /// Dependencies of `repo` via import namespaces, and repos importing its
    /// namespaces. Ties keep first-encounter order, then sort by count desc.
    pub fn cross_repo_deps(
        conn: &Connection,
        repo: &str,
        namespaces: &[(String, String)],
    ) -> Result<CrossRepoDeps, rusqlite::Error> {
        // Dependencies: imports in `repo` that resolve to another repo's namespace.
        let mut deps: Vec<(String, String, i64)> = Vec::new(); // (owner, evidence, count)
        let mut deps_index: std::collections::HashMap<String, usize> =
            std::collections::HashMap::new();
        let mut stmt = conn.prepare(
            "SELECT imported_path FROM imports WHERE file_id IN \
             (SELECT id FROM files WHERE repo_id = ?)",
        )?;
        let paths: Vec<String> = stmt
            .query_map(rusqlite::params![repo], |r| r.get::<_, String>(0))?
            .collect::<Result<_, _>>()?;
        for path in paths {
            for (ns, owner) in namespaces {
                // Dot boundary: only the exact namespace or a segment extension
                // counts, so "xyz.be.common_extra" never matches "xyz.be.common".
                let ns_dot = format!("{ns}.");
                if (path.as_str() == ns.as_str() || path.starts_with(&ns_dot))
                    && owner.as_str() != repo
                {
                    match deps_index.get(owner) {
                        Some(&i) => deps[i].2 += 1,
                        None => {
                            deps_index.insert(owner.clone(), deps.len());
                            deps.push((owner.clone(), ns.clone(), 1));
                        }
                    }
                }
            }
        }

        // Dependent matching stays in SQL; substr keeps it case-sensitive like
        // the dependencies loop: exact ns or ns-prefix at a dot boundary.
        let my_namespaces: Vec<&String> = namespaces
            .iter()
            .filter(|(_, owner)| owner == repo)
            .map(|(ns, _)| ns)
            .collect();
        let mut dependents: Vec<(String, i64)> = Vec::new();
        let mut dependents_index: std::collections::HashMap<String, usize> =
            std::collections::HashMap::new();
        if !my_namespaces.is_empty() {
            let where_clause: String = my_namespaces
                .iter()
                .map(|_| "(imported_path = ? OR substr(imported_path, 1, ?) = ?)")
                .collect::<Vec<_>>()
                .join(" OR ");
            let sql = format!(
                "SELECT imported_path, repo_id \
                 FROM imports JOIN files ON imports.file_id = files.id \
                 WHERE files.repo_id != ? AND ({where_clause})"
            );
            let mut params: Vec<Box<dyn rusqlite::ToSql>> = vec![Box::new(repo.to_string())];
            for ns in &my_namespaces {
                params.push(Box::new(ns.to_string()));
                params.push(Box::new((ns.len() + 1) as i64));
                params.push(Box::new(format!("{ns}.")));
            }
            let refs: Vec<&dyn rusqlite::ToSql> = params
                .iter()
                .map(|p| p.as_ref() as &dyn rusqlite::ToSql)
                .collect();
            let mut stmt = conn.prepare(&sql)?;
            let rows = stmt.query_map(refs.as_slice(), |r| {
                Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?))
            })?;
            for row in rows {
                let (_, repo_id) = row?;
                match dependents_index.get(&repo_id) {
                    Some(&i) => dependents[i].1 += 1,
                    None => {
                        dependents_index.insert(repo_id.clone(), dependents.len());
                        dependents.push((repo_id, 1));
                    }
                }
            }
        }

        let mut dependencies: Vec<RepoDep> = deps
            .into_iter()
            .map(|(repo, evidence, count)| RepoDep {
                repo,
                evidence,
                count,
            })
            .collect();
        dependencies.sort_by_key(|d| std::cmp::Reverse(d.count));
        let mut dependents: Vec<RepoDep> = dependents
            .into_iter()
            .map(|(repo, count)| RepoDep {
                repo,
                evidence: String::new(),
                count,
            })
            .collect();
        dependents.sort_by_key(|d| std::cmp::Reverse(d.count));
        Ok(CrossRepoDeps {
            dependencies,
            dependents,
        })
    }
}

// --- viz --------------------------------------------------------------------

mod viz {
    //! Visualization query layer: gather nodes/edges by scope.

    use std::collections::{BTreeMap, HashMap, HashSet};

    use rusqlite::Connection;
    use serde_json::{Map, Value, json};

    use super::cross_repo::cross_repo_deps;
    use super::escape_like;
    use super::impact::impact_analysis;

    /// Callers/callees cap per focal row per direction.
    const NEIGHBOR_CAP: usize = 30;
    const SCOPE_CAP: usize = 50;
    const SCOPE_EDGE_CAP: usize = 100;
    const REPO_MAX_NODES: usize = 30;

    /// A gathered graph: nodes, edges, and scope metadata.
    pub type Graph = Value;

    fn add_node(
        nodes: &mut Map<String, Value>,
        name: &str,
        kind: &str,
        file: Option<&str>,
        repo: Option<&str>,
    ) {
        if !name.is_empty() && !nodes.contains_key(name) {
            nodes.insert(
                name.to_string(),
                json!({
                    "id": name, "kind": kind,
                    "file": file, "repo": repo,
                }),
            );
        }
    }

    fn sanitize(name: &str) -> String {
        let s: String = name
            .chars()
            .map(|c| if c.is_alphanumeric() { c } else { '_' })
            .collect();
        s.chars().take(30).collect()
    }

    /// Sanitized renderer ids keyed by node id; distinct nodes never share an id.
    fn node_ids(nodes: &[Value]) -> BTreeMap<String, String> {
        let mut used: HashSet<String> = HashSet::new();
        let mut safe = BTreeMap::new();
        for n in nodes {
            let id = n["id"].as_str().unwrap_or_default();
            let base = sanitize(id);
            let mut sid = base.clone();
            let mut i = 1;
            while used.contains(&sid) {
                let cut: String = base.chars().take(28).collect();
                sid = format!("{cut}_{i}");
                i += 1;
            }
            used.insert(sid.clone());
            safe.insert(id.to_string(), sid);
        }
        safe
    }

    fn esc(s: &str) -> String {
        s.replace('"', "\\\"")
    }

    fn mermaid_esc(text: &str) -> String {
        text.replace('\\', "\\\\")
            .replace('"', "\\\"")
            .replace('|', "\\|")
            .replace('[', "\\[")
            .replace(']', "\\]")
            .replace('\n', " ")
    }

    fn shape_for_kind(kind: &str) -> &'static str {
        match kind {
            "class" | "property" => "[{}]",
            "interface" | "repo" => "[[{}]]",
            "enum" | "function" | "method" => "({})",
            "module" => "[{}/]",
            _ => "[{}]",
        }
    }

    /// Render a graph as a Mermaid flowchart.
    pub fn to_mermaid(graph: &Graph) -> String {
        let nodes = graph["nodes"].as_array().cloned().unwrap_or_default();
        let edges = graph["edges"].as_array().cloned().unwrap_or_default();
        let scope = graph["metadata"]["scope"].as_str().unwrap_or("graph");
        let mut lines = vec![format!(
            "graph {}",
            if ["impact", "module", "repo"].contains(&scope) {
                "TB"
            } else {
                "LR"
            }
        )];
        let safe = node_ids(&nodes);
        for n in &nodes {
            let id = n["id"].as_str().unwrap_or_default();
            let sid = &safe[id];
            let label = mermaid_esc(id);
            let shape = shape_for_kind(n["kind"].as_str().unwrap_or_default());
            lines.push(format!("    {sid}{}", shape.replacen("{}", &label, 1)));
        }
        lines.push(String::new());
        for e in &edges {
            let source = e["source"].as_str().unwrap_or_default();
            let target = e["target"].as_str().unwrap_or_default();
            let s = safe
                .get(source)
                .cloned()
                .unwrap_or_else(|| sanitize(source));
            let t = safe
                .get(target)
                .cloned()
                .unwrap_or_else(|| sanitize(target));
            let label = e["label"].as_str().unwrap_or_default();
            let arrow = if label.is_empty() {
                " -->".to_string()
            } else {
                format!(" -->|\"{}\"|", mermaid_esc(label))
            };
            if !s.is_empty() && !t.is_empty() {
                lines.push(format!("    {s}{arrow}{t}"));
            }
        }
        format!("{}\n", lines.join("\n"))
    }

    /// Render a graph as Graphviz DOT.
    pub fn to_dot(graph: &Graph) -> String {
        let nodes = graph["nodes"].as_array().cloned().unwrap_or_default();
        let edges = graph["edges"].as_array().cloned().unwrap_or_default();
        let mut lines = vec![
            "digraph cairn {".to_string(),
            "    rankdir=LR;".to_string(),
            "    node [shape=box, style=rounded];".to_string(),
        ];
        let safe = node_ids(&nodes);
        for n in &nodes {
            let id = n["id"].as_str().unwrap_or_default();
            let sid = &safe[id];
            lines.push(format!("    {sid} [label=\"{}\"];", esc(id)));
        }
        for e in &edges {
            let source = e["source"].as_str().unwrap_or_default();
            let target = e["target"].as_str().unwrap_or_default();
            let s = safe
                .get(source)
                .cloned()
                .unwrap_or_else(|| sanitize(source));
            let t = safe
                .get(target)
                .cloned()
                .unwrap_or_else(|| sanitize(target));
            let label = e["label"].as_str().unwrap_or_default();
            let attr = if label.is_empty() {
                String::new()
            } else {
                format!(" [label=\"{}\"]", esc(label))
            };
            if !s.is_empty() && !t.is_empty() {
                lines.push(format!("    {s} -> {t}{attr};"));
            }
        }
        lines.push("}".to_string());
        format!("{}\n", lines.join("\n"))
    }

    /// Render a graph as pretty-printed JSON.
    pub fn to_json(graph: &Graph) -> String {
        serde_json::to_string_pretty(graph).unwrap_or_default()
    }

    /// Wrap Mermaid output in an OKF-compatible markdown block (`viz --embed`).
    pub fn viz_embed(graph: &Graph) -> String {
        let meta = &graph["metadata"];
        let focus = ["symbol", "module", "repo"]
            .iter()
            .find_map(|key| meta[*key].as_str())
            .unwrap_or("");
        let title = format!("{}: {}", meta["scope"].as_str().unwrap_or("graph"), focus);
        let ts = utc_today();
        let mut out = format!("## {title}\n\n```mermaid\n");
        out.push_str(to_mermaid(graph).trim_end());
        out.push_str("\n```\n\n");
        out.push_str(&format!("*Generated by `cairn viz` on {ts}*"));
        out.push('\n');
        out
    }

    /// One self-contained HTML document (the dashboard's local vis-network
    /// viewer, the graph JSON, and the Mermaid source; no network assets).
    /// `graph_json` is the compact python-separators serialization of `graph`.
    pub fn to_html(graph: &Graph, viewer_js: &str, graph_json: &str) -> String {
        let meta = &graph["metadata"];
        let focus = ["symbol", "module", "repo"]
            .iter()
            .find_map(|key| meta[*key].as_str())
            .unwrap_or("");
        let scope = meta["scope"].as_str().unwrap_or("graph");
        let title = if focus.is_empty() {
            format!("cairn viz — {scope}")
        } else {
            format!("cairn viz — {scope}: {focus}")
        };
        let html_title = html_escape(&title);
        let mermaid = to_mermaid(graph);
        let mut out = String::new();
        for line in [
            "<!doctype html>",
            "<html lang=\"en\">",
            "<head>",
            "<meta charset=\"utf-8\">",
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
        ] {
            out.push_str(line);
            out.push('\n');
        }
        out.push_str(&format!("<title>{html_title}</title>\n"));
        out.push_str(&format!("<style>{HTML_CSS}</style>\n"));
        out.push_str("</head>\n<body>\n");
        out.push_str(&format!("<h1>{html_title}</h1>\n"));
        out.push_str("<div id=\"cairn-graph\" aria-label=\"Selected graph scope\"></div>\n");
        out.push_str("<details>\n<summary>Mermaid source</summary>\n");
        out.push_str(&format!(
            "<pre class=\"mermaid-source\">{}</pre>\n",
            html_text(&mermaid)
        ));
        out.push_str("</details>\n");
        out.push_str("<script type=\"application/json\" id=\"cairn-graph-data\">\n");
        out.push_str(&script_json(graph_json));
        out.push_str("\n</script>\n<script>\n");
        out.push_str(viewer_js);
        out.push_str("\n</script>\n");
        out.push_str(&format!("<script>{HTML_BOOTSTRAP}</script>\n"));
        out.push_str("</body>\n</html>\n");
        out
    }

    fn empty(scope: &str, name: &str) -> Graph {
        json!({
            "nodes": [], "edges": [],
            "metadata": {"scope": scope, "symbol": name, "node_count": 0, "edge_count": 0}
        })
    }

    fn query_rows<F, T>(
        conn: &Connection,
        sql: &str,
        params: &[&dyn rusqlite::ToSql],
        map: F,
    ) -> Result<Vec<T>, rusqlite::Error>
    where
        F: FnMut(&rusqlite::Row<'_>) -> rusqlite::Result<T>,
    {
        conn.prepare(sql)?.query_map(params, map)?.collect()
    }

    /// A symbol + its immediate callers and callees.
    pub fn get_symbol_graph(conn: &Connection, name: &str) -> Result<Graph, rusqlite::Error> {
        let mut nodes: Map<String, Value> = Map::new();
        let mut edges: Vec<Value> = Vec::new();

        let focal: Option<(String, String, String, String)> = conn
            .query_row(
                "SELECT s.name, s.kind, f.path, f.repo_id FROM symbols s \
                 JOIN files f ON s.file_id = f.id WHERE s.name = ? LIMIT 1",
                rusqlite::params![name],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)),
            )
            .map(Some)
            .or_else(|e| match e {
                rusqlite::Error::QueryReturnedNoRows => Ok(None),
                other => Err(other),
            })?;
        let Some((fname, fkind, fpath, frepo)) = focal else {
            return Ok(empty("symbol", name));
        };
        add_node(&mut nodes, &fname, &fkind, Some(&fpath), Some(&frepo));

        let mut truncated = false;
        let caller_sql = format!(
            "SELECT s.name AS caller, s.kind AS ckind, f.path, f.repo_id, e.kind AS ekind \
             FROM edges e JOIN symbols s ON e.source_id = s.id \
             JOIN files f ON s.file_id = f.id \
             WHERE e.target_id IN (SELECT id FROM symbols WHERE name = ?) \
             LIMIT {}",
            NEIGHBOR_CAP + 1
        );
        let mut callers = query_rows(conn, &caller_sql, &[&name], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, String>(2)?,
                r.get::<_, String>(3)?,
                r.get::<_, String>(4)?,
            ))
        })?;
        if callers.len() > NEIGHBOR_CAP {
            truncated = true;
            callers.truncate(NEIGHBOR_CAP);
        }
        for (caller, ckind, path, repo, ekind) in callers {
            add_node(&mut nodes, &caller, &ckind, Some(&path), Some(&repo));
            edges.push(json!({"source": caller, "target": name, "kind": ekind}));
        }

        let callee_sql = format!(
            "SELECT COALESCE(t.name, e.target_name) AS callee, t.kind, f.path, f.repo_id, \
             e.target_id AS resolved, e.kind AS ekind \
             FROM edges e JOIN symbols s ON e.source_id = s.id \
             LEFT JOIN symbols t ON e.target_id = t.id \
             LEFT JOIN files f ON t.file_id = f.id \
             WHERE s.name = ? \
             LIMIT {}",
            NEIGHBOR_CAP + 1
        );
        let mut callees = query_rows(conn, &callee_sql, &[&name], |r| {
            Ok((
                r.get::<_, Option<String>>(0)?,
                r.get::<_, Option<String>>(1)?,
                r.get::<_, Option<String>>(2)?,
                r.get::<_, Option<String>>(3)?,
                r.get::<_, String>(5)?,
            ))
        })?;
        if callees.len() > NEIGHBOR_CAP {
            truncated = true;
            callees.truncate(NEIGHBOR_CAP);
        }
        for (callee, kind, path, repo, ekind) in callees {
            let Some(callee) = callee else { continue };
            if callee.is_empty() {
                continue;
            }
            add_node(
                &mut nodes,
                &callee,
                kind.as_deref().unwrap_or("external"),
                path.as_deref(),
                repo.as_deref(),
            );
            edges.push(json!({"source": name, "target": callee, "kind": ekind}));
        }

        Ok(json!({
            "nodes": nodes.values().cloned().collect::<Vec<_>>(),
            "edges": edges,
            "metadata": {
                "scope": "symbol", "symbol": name,
                "node_count": nodes.len(), "edge_count": edges.len(),
                "truncated": truncated,
            }
        }))
    }

    fn is_vendored_path(path: &str) -> bool {
        let normalized = path.replace('\\', "/");
        let file = normalized.rsplit('/').next().unwrap_or_default();
        if file.contains(".min.") {
            return true;
        }
        let parts: Vec<&str> = normalized.split('/').collect();
        parts[..parts.len().saturating_sub(1)].iter().any(|p| {
            matches!(
                p.to_ascii_lowercase().as_str(),
                "vendor" | "dist" | "node_modules"
            )
        })
    }

    // The canonical classifier (graph/tests.py port); viz keeps no local copy.
    use crate::tools_nav::is_test_symbol as shared_is_test_symbol;

    /// One curated scope: kept nodes, deduped internal edges, honest metadata.
    type ScopeGraph = (Map<String, Value>, Vec<Value>, Value);

    fn curated_scope_graph(
        conn: &Connection,
        include_tests: bool,
        path_filter: Option<&str>,
    ) -> Result<ScopeGraph, rusqlite::Error> {
        let where_clause = if path_filter.is_some() {
            "WHERE f.path LIKE ? ESCAPE '\\'"
        } else {
            ""
        };
        let sql = format!(
            "SELECT s.name, s.kind, f.path, f.repo_id, s.qualified_name, \
             (SELECT COUNT(*) FROM edges e WHERE e.target_id = s.id) + \
             (SELECT COUNT(*) FROM edges e WHERE e.source_id = s.id) AS degree \
             FROM symbols s JOIN files f ON s.file_id = f.id \
             {where_clause} \
             ORDER BY degree DESC, s.name ASC"
        );
        let map = |r: &rusqlite::Row<'_>| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, String>(2)?,
                r.get::<_, String>(3)?,
                r.get::<_, Option<String>>(4)?,
            ))
        };
        let rows = match path_filter {
            Some(f) => query_rows(conn, &sql, &[&f], map)?,
            None => query_rows(conn, &sql, &[], map)?,
        };
        let mut nodes: Map<String, Value> = Map::new();
        let mut eligible = 0usize;
        let mut vendored_excluded = 0usize;
        for (name, kind, path, repo, qualified) in rows {
            if is_vendored_path(&path) {
                vendored_excluded += 1;
                continue;
            }
            if !include_tests
                && shared_is_test_symbol(&path, &name, qualified.as_deref().unwrap_or("")).is_some()
            {
                continue;
            }
            eligible += 1;
            if eligible > SCOPE_CAP {
                continue;
            }
            add_node(&mut nodes, &name, &kind, Some(&path), Some(&repo));
        }
        let (edges, edges_truncated) = internal_edges(conn, &nodes)?;
        let mut edge_kinds: Map<String, Value> = Map::new();
        for e in &edges {
            let k = e["kind"].as_str().unwrap_or_default().to_string();
            let count = edge_kinds.get(&k).and_then(Value::as_i64).unwrap_or(0) + 1;
            edge_kinds.insert(k, Value::from(count));
        }
        let meta = json!({
            "node_count": nodes.len(),
            "edge_count": edges.len(),
            "edge_kinds": edge_kinds,
            "tests_included": include_tests,
            "truncated": eligible > SCOPE_CAP,
            "edges_truncated": edges_truncated,
            "vendored_excluded": vendored_excluded,
        });
        Ok((nodes, edges, meta))
    }

    fn internal_edges(
        conn: &Connection,
        nodes: &Map<String, Value>,
    ) -> Result<(Vec<Value>, bool), rusqlite::Error> {
        if nodes.is_empty() {
            return Ok((vec![], false));
        }
        let names: Vec<String> = nodes.keys().cloned().collect();
        let ph = vec!["?"; names.len()].join(",");
        let sql = format!(
            "SELECT s1.name AS src, COALESCE(s2.name, e.target_name) AS tgt, e.kind \
             FROM edges e JOIN symbols s1 ON e.source_id = s1.id \
             LEFT JOIN symbols s2 ON e.target_id = s2.id \
             WHERE s1.name IN ({ph}) \
             AND COALESCE(s2.name, e.target_name) IN ({ph}) \
             LIMIT {}",
            SCOPE_EDGE_CAP + 1
        );
        let refs: Vec<&dyn rusqlite::ToSql> = names
            .iter()
            .chain(names.iter())
            .map(|n| n as &dyn rusqlite::ToSql)
            .collect();
        let rows: Vec<(String, String, String)> = conn
            .prepare(&sql)?
            .query_map(refs.as_slice(), |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?
            .collect::<Result<_, _>>()?;
        let truncated = rows.len() > SCOPE_EDGE_CAP;
        let mut seen: HashSet<(String, String, String)> = HashSet::new();
        let mut edges = Vec::new();
        for (src, tgt, kind) in rows.into_iter().take(SCOPE_EDGE_CAP) {
            if nodes.contains_key(&tgt) && seen.insert((src.clone(), tgt.clone(), kind.clone())) {
                edges.push(json!({"source": src, "target": tgt, "kind": kind}));
            }
        }
        Ok((edges, truncated))
    }

    /// Symbols in a module plus internal edges, degree-ranked and curated.
    pub fn get_module_graph(
        conn: &Connection,
        module: &str,
        include_tests: bool,
    ) -> Result<Graph, rusqlite::Error> {
        let filter = format!("%{}%", escape_like(module));
        let (nodes, edges, meta) = curated_scope_graph(conn, include_tests, Some(filter.as_str()))?;
        let mut metadata = Map::new();
        metadata.insert("scope".into(), Value::from("module"));
        metadata.insert("module".into(), Value::from(module));
        if let Value::Object(m) = meta {
            for (k, v) in m {
                metadata.insert(k, v);
            }
        }
        Ok(json!({
            "nodes": nodes.values().cloned().collect::<Vec<_>>(),
            "edges": edges,
            "metadata": metadata,
        }))
    }

    fn callees_within(
        conn: &Connection,
        names: &[String],
    ) -> Result<HashMap<String, Vec<String>>, rusqlite::Error> {
        let mut callees: HashMap<String, Vec<String>> = HashMap::new();
        for chunk in names.chunks(200) {
            let ph = vec!["?"; chunk.len()].join(",");
            let sql = format!(
                "SELECT DISTINCT s1.name AS src, COALESCE(s2.name, e.target_name) AS tgt \
                 FROM edges e JOIN symbols s1 ON e.source_id = s1.id \
                 LEFT JOIN symbols s2 ON e.target_id = s2.id \
                 WHERE s1.name IN ({ph}) \
                 AND COALESCE(s2.name, e.target_name) IN ({ph})"
            );
            let refs: Vec<&dyn rusqlite::ToSql> = chunk
                .iter()
                .chain(chunk.iter())
                .map(|n| n as &dyn rusqlite::ToSql)
                .collect();
            let rows: Vec<(String, String)> = conn
                .prepare(&sql)?
                .query_map(refs.as_slice(), |r| Ok((r.get(0)?, r.get(1)?)))?
                .collect::<Result<_, _>>()?;
            for (src, tgt) in rows {
                callees.entry(src).or_default().push(tgt);
            }
        }
        Ok(callees)
    }

    /// Edge target for a depth>0 impact entry: its shallowest in-impact
    /// callee, never itself.
    fn parent_edge(
        entry: &str,
        entry_depth: i64,
        depths: &HashMap<String, i64>,
        callees: &HashMap<String, Vec<String>>,
        root: &str,
    ) -> String {
        let my_depth = depths.get(entry).copied().unwrap_or(entry_depth);
        let mut toward: Vec<(i64, String)> = Vec::new();
        let mut lateral: Vec<(i64, String)> = Vec::new();
        for callee in callees.get(entry).map(Vec::as_slice).unwrap_or(&[]) {
            if callee == entry {
                continue;
            }
            let Some(d) = depths.get(callee).copied() else {
                continue;
            };
            if d < my_depth {
                toward.push((d, callee.clone()));
            } else {
                lateral.push((d, callee.clone()));
            }
        }
        let pool = if !toward.is_empty() {
            &toward
        } else {
            &lateral
        };
        pool.iter()
            .min_by(|a, b| a.0.cmp(&b.0).then_with(|| a.1.cmp(&b.1)))
            .map(|(_, n)| n.clone())
            .unwrap_or_else(|| root.to_string())
    }

    /// Recursive caller tree for impact analysis.
    pub fn get_impact_graph(
        conn: &Connection,
        name: &str,
        max_depth: i64,
    ) -> Result<Graph, rusqlite::Error> {
        let result = impact_analysis(conn, name, max_depth, false, 500)?;
        let mut nodes: Map<String, Value> = Map::new();
        let mut edges: Vec<Value> = Vec::new();
        add_node(&mut nodes, name, "focus", Some(""), Some(""));
        let mut depths: HashMap<String, i64> = HashMap::from([(name.to_string(), 0)]);
        for entry in &result.impacted {
            match depths.get(&entry.symbol).copied() {
                Some(cur) if entry.depth >= cur => {}
                _ => {
                    depths.insert(entry.symbol.clone(), entry.depth);
                }
            }
        }
        let all_names: Vec<String> = depths.keys().cloned().collect();
        let callees = callees_within(conn, &all_names)?;
        for entry in &result.impacted {
            add_node(
                &mut nodes,
                &entry.symbol,
                "caller",
                Some(&entry.file),
                Some(&entry.repo),
            );
            let target = if entry.depth == 0 {
                name.to_string()
            } else {
                parent_edge(&entry.symbol, entry.depth, &depths, &callees, name)
            };
            edges.push(json!({"source": entry.symbol, "target": target, "kind": "calls"}));
        }
        Ok(json!({
            "nodes": nodes.values().cloned().collect::<Vec<_>>(),
            "edges": edges,
            "metadata": {
                "scope": "impact", "symbol": name, "depth": max_depth,
                "node_count": nodes.len(), "edge_count": edges.len(),
                "total": result.total,
            }
        }))
    }

    fn group_by_top_level(
        conn: &Connection,
        repo: &str,
    ) -> Result<Vec<(String, i64)>, rusqlite::Error> {
        let repo_root: Option<String> = if repo.is_empty() {
            None
        } else {
            conn.query_row(
                "SELECT path FROM repos WHERE id = ?",
                rusqlite::params![repo],
                |r| r.get(0),
            )
            .map(Some)
            .or_else(|e| match e {
                rusqlite::Error::QueryReturnedNoRows => Ok(None),
                other => Err(other),
            })?
        };
        let sql = if repo.is_empty() {
            "SELECT f.path AS path, COUNT(s.id) AS symbols \
             FROM files f LEFT JOIN symbols s ON s.file_id = f.id \
             GROUP BY f.id"
        } else {
            "SELECT f.path AS path, COUNT(s.id) AS symbols \
             FROM files f LEFT JOIN symbols s ON s.file_id = f.id \
             WHERE f.repo_id = ? \
             GROUP BY f.id"
        };
        let map = |r: &rusqlite::Row<'_>| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?));
        let rows: Vec<(String, i64)> = if repo.is_empty() {
            query_rows(conn, sql, &[], map)?
        } else {
            query_rows(conn, sql, &[&repo], map)?
        };
        let mut buckets: HashMap<String, i64> = HashMap::new();
        let mut order: Vec<String> = Vec::new();
        for (mut p, count) in rows {
            if let Some(root) = &repo_root {
                let prefix = format!("{root}/");
                if p.starts_with(&prefix) {
                    p = p[prefix.len()..].to_string();
                }
            }
            let parts: Vec<&str> = p.split('/').collect();
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
            *buckets.entry(key).or_insert(0) += count;
        }
        let mut out: Vec<(String, i64)> = order
            .into_iter()
            .map(|k| (k.clone(), buckets[&k]))
            .collect();
        out.sort_by_key(|(_, count)| std::cmp::Reverse(*count));
        Ok(out)
    }

    /// Module structure of a repo with symbol counts; empty `repo` buckets every repo.
    pub fn get_repo_graph(conn: &Connection, repo: &str) -> Result<Graph, rusqlite::Error> {
        let buckets = group_by_top_level(conn, repo)?;
        let mut nodes: Map<String, Value> = Map::new();
        for (key, count) in buckets.iter().take(REPO_MAX_NODES) {
            let label = format!("{key} ({count})");
            add_node(&mut nodes, &label, "module", Some(""), Some(repo));
        }
        Ok(json!({
            "nodes": nodes.values().cloned().collect::<Vec<_>>(),
            "edges": [],
            "metadata": {
                "scope": "repo", "repo": repo,
                "node_count": nodes.len(),
                "truncated": buckets.len() > REPO_MAX_NODES,
            }
        }))
    }

    /// The workspace's most-connected symbols as one canvas — the answer to
    /// an empty focal name for the scopes that dispatch here.
    pub fn get_symbol_overview(
        conn: &Connection,
        include_tests: bool,
        scope: &str,
    ) -> Result<Graph, rusqlite::Error> {
        let (nodes, edges, meta) = curated_scope_graph(conn, include_tests, None)?;
        let mut metadata = Map::new();
        metadata.insert("scope".into(), Value::from(scope));
        metadata.insert("focus".into(), Value::from(""));
        if let Value::Object(m) = meta {
            for (k, v) in m {
                metadata.insert(k, v);
            }
        }
        Ok(json!({
            "nodes": nodes.values().cloned().collect::<Vec<_>>(),
            "edges": edges,
            "metadata": metadata,
        }))
    }

    /// Each requested symbol (all its same-name rows) + 1-hop callers/callees,
    /// capped per row per direction; blank names yield an empty graph.
    pub fn get_symbol_neighbors(
        conn: &Connection,
        names: &[String],
    ) -> Result<Graph, rusqlite::Error> {
        let mut requested: Vec<String> = Vec::new();
        for name in names {
            let trimmed = name.trim();
            if !trimmed.is_empty() && !requested.iter().any(|n| n == trimmed) {
                requested.push(trimmed.to_string());
            }
        }
        let mut nodes: Map<String, Value> = Map::new();
        let mut edges: Vec<Value> = Vec::new();
        let mut truncated = false;
        if !requested.is_empty() {
            let ph = vec!["?"; requested.len()].join(",");
            let refs: Vec<&dyn rusqlite::ToSql> = requested
                .iter()
                .map(|n| n as &dyn rusqlite::ToSql)
                .collect();
            let focal_rows: Vec<(String, String, String, String, String)> = query_rows(
                conn,
                &format!(
                    "SELECT s.id AS sid, s.name, s.kind, f.path, f.repo_id \
                     FROM symbols s JOIN files f ON s.file_id = f.id \
                     WHERE s.name IN ({ph})"
                ),
                &refs,
                |r| {
                    Ok((
                        r.get(0)?,
                        r.get(1)?,
                        r.get(2)?,
                        r.get(3)?,
                        r.get::<_, Option<String>>(4)?.unwrap_or_default(),
                    ))
                },
            )?;
            for (sid, name, kind, path, repo) in focal_rows {
                add_node(&mut nodes, &name, &kind, Some(&path), Some(&repo));
                let caller_sql = format!(
                    "SELECT s.name AS caller, s.kind AS ckind, f.path, f.repo_id, e.kind AS ekind \
                     FROM edges e JOIN symbols s ON e.source_id = s.id \
                     JOIN files f ON s.file_id = f.id \
                     WHERE e.target_id = ? LIMIT {}",
                    NEIGHBOR_CAP + 1
                );
                let callers: Vec<(String, String, String, String, String)> =
                    query_rows(conn, &caller_sql, &[&sid], |r| {
                        Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?))
                    })?;
                let mut callers = callers;
                if callers.len() > NEIGHBOR_CAP {
                    truncated = true;
                    callers.truncate(NEIGHBOR_CAP);
                }
                for (caller, ckind, path, repo, ekind) in callers {
                    add_node(&mut nodes, &caller, &ckind, Some(&path), Some(&repo));
                    edges.push(json!({"source": caller, "target": name, "kind": ekind}));
                }
                let callee_sql = format!(
                    "SELECT COALESCE(t.name, e.target_name) AS callee, t.kind, f.path, f.repo_id, \
                     e.kind AS ekind \
                     FROM edges e LEFT JOIN symbols t ON e.target_id = t.id \
                     LEFT JOIN files f ON t.file_id = f.id \
                     WHERE e.source_id = ? LIMIT {}",
                    NEIGHBOR_CAP + 1
                );
                type CalleeRow = (
                    Option<String>,
                    Option<String>,
                    Option<String>,
                    Option<String>,
                    String,
                );
                let callees: Vec<CalleeRow> = query_rows(conn, &callee_sql, &[&sid], |r| {
                    Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?))
                })?;
                let mut callees = callees;
                if callees.len() > NEIGHBOR_CAP {
                    truncated = true;
                    callees.truncate(NEIGHBOR_CAP);
                }
                for (callee, kind, path, repo, ekind) in callees {
                    let Some(callee) = callee else { continue };
                    add_node(
                        &mut nodes,
                        &callee,
                        kind.as_deref().unwrap_or("external"),
                        path.as_deref(),
                        repo.as_deref(),
                    );
                    edges.push(json!({"source": name, "target": callee, "kind": ekind}));
                }
            }
        }
        Ok(json!({
            "nodes": nodes.values().cloned().collect::<Vec<_>>(),
            "edges": edges,
            "metadata": {
                "scope": "neighbors", "requested": requested,
                "node_count": nodes.len(), "edge_count": edges.len(),
                "truncated": truncated,
            }
        }))
    }

    /// Cross-repo dependency map over a resolved namespace map.
    pub fn get_deps_graph(
        conn: &Connection,
        namespaces: &[(String, String)],
    ) -> Result<Graph, rusqlite::Error> {
        let repos: Vec<String> = query_rows(conn, "SELECT id FROM repos", &[], |r| r.get(0))?;
        let mut nodes: Map<String, Value> = Map::new();
        let mut edges: Vec<Value> = Vec::new();
        for repo in &repos {
            add_node(&mut nodes, repo, "repo", Some(""), Some(repo));
            let deps = cross_repo_deps(conn, repo, namespaces)?;
            for d in &deps.dependencies {
                edges.push(json!({
                    "source": repo, "target": d.repo, "kind": "depends",
                    "label": d.evidence,
                }));
            }
        }
        Ok(json!({
            "nodes": nodes.values().cloned().collect::<Vec<_>>(),
            "edges": edges,
            "metadata": {"scope": "deps", "node_count": nodes.len(), "edge_count": edges.len()},
        }))
    }

    const HTML_CSS: &str = "\n:root { color-scheme: light dark; }\nbody { margin: 0; padding: 1rem; font: 14px/1.5 system-ui, sans-serif; }\n#cairn-graph { height: 75vh; border: 1px solid #888; border-radius: 8px; }\ndetails { margin-top: 1rem; }\npre { overflow-x: auto; padding: 0.75rem; border: 1px solid #888; border-radius: 8px; }\n";

    const HTML_BOOTSTRAP: &str = "\n(function () {\n  var payload = JSON.parse(\n    document.getElementById(\"cairn-graph-data\").textContent\n  );\n  var nodes = payload.nodes.map(function (n) {\n    var tip = n.kind ? n.id + \" (\" + n.kind + \")\" : n.id;\n    return { id: n.id, label: n.id, title: tip };\n  });\n  var edges = payload.edges\n    .filter(function (e) { return e.source && e.target; })\n    .map(function (e) {\n      return {\n        from: e.source,\n        to: e.target,\n        label: e.label || e.kind || undefined,\n        arrows: \"to\",\n      };\n    });\n  new vis.Network(\n    document.getElementById(\"cairn-graph\"),\n    { nodes: nodes, edges: edges },\n    { interaction: { hover: true } }\n  );\n})();\n";

    fn html_escape(text: &str) -> String {
        text.replace('&', "&amp;")
            .replace('<', "&lt;")
            .replace('>', "&gt;")
            .replace('"', "&quot;")
            .replace('\'', "&#x27;")
    }

    /// Escape for an HTML text node while keeping `-->` readable.
    fn html_text(text: &str) -> String {
        text.replace('&', "&amp;").replace('<', "&lt;")
    }

    /// The pre-serialized graph JSON escaped for a `<script>` element.
    fn script_json(graph_json: &str) -> String {
        graph_json.replace('<', "\\u003c")
    }

    fn utc_today() -> String {
        let days = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs() / 86_400)
            .unwrap_or(0);
        let z = days as i64 + 719_468;
        let era = z.div_euclid(146_097);
        let doe = z.rem_euclid(146_097);
        let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
        let y = yoe + era * 400;
        let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
        let mp = (5 * doy + 2) / 153;
        let d = doy - (153 * mp + 2) / 5 + 1;
        let m = if mp < 10 { mp + 3 } else { mp - 9 };
        let y = if m <= 2 { y + 1 } else { y };
        format!("{y:04}-{m:02}-{d:02}")
    }
}
