//! Navigation-family tools: `find_definition`, callers/callees, impact.

use std::collections::{HashMap, HashSet};

use rusqlite::{Connection, ToSql};

/// Edge kinds the traversal follows unless service edges are opted in.
pub const STRUCTURAL_EDGE_KINDS: &[&str] = &["calls", "call", "extends", "implements"];

/// Cap on traversal depth in the recursive impact walk.
pub const MAX_TRAVERSAL_DEPTH: i64 = 100;

/// Fallback import-namespace -> repo map when no env/config override resolves.
pub const DEFAULT_REPO_NAMESPACES: &[(&str, &str)] = &[
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
];

// --- shared helpers ----------------------------------------------------------

/// Clamp an LLM-supplied int to `[lo, hi]`.
pub fn clamp(value: i64, lo: i64, hi: i64) -> i64 {
    value.max(lo).min(hi)
}

fn prepend_banner(text: &str, banner: &str) -> String {
    if banner.is_empty() {
        text.to_string()
    } else {
        format!("{banner}\n{text}")
    }
}

/// Render an optional SQL line the way an f-string renders `None`.
fn line_text(line: Option<i64>) -> String {
    line.map(|l| l.to_string())
        .unwrap_or_else(|| "None".to_string())
}

fn escape_like(value: &str) -> String {
    value
        .replace('\\', "\\\\")
        .replace('%', "\\%")
        .replace('_', "\\_")
}

// --- find_definition ---------------------------------------------------------

/// One `find_definition` result row.
pub struct DefRow {
    pub id: String,
    pub kind: String,
    pub name: String,
    pub qualified_name: Option<String>,
    pub line_start: Option<i64>,
    pub file_path: String,
    pub repo: String,
}

fn def_rows(
    conn: &Connection,
    where_clause: &str,
    bind: &[&dyn ToSql],
) -> rusqlite::Result<Vec<DefRow>> {
    let sql = format!(
        "SELECT s.id AS id, s.kind AS kind, s.name AS name, \
         s.qualified_name AS qualified_name, s.line_start AS line_start, \
         f.path AS file_path, f.repo_id AS repo \
         FROM symbols s JOIN files f ON s.file_id = f.id {where_clause}"
    );
    let mut stmt = conn.prepare(&sql)?;
    let rows = stmt.query_map(bind, |row| {
        Ok(DefRow {
            id: row.get("id")?,
            kind: row.get("kind")?,
            name: row.get("name")?,
            qualified_name: row.get("qualified_name")?,
            line_start: row.get("line_start")?,
            file_path: row.get("file_path")?,
            repo: row.get("repo")?,
        })
    })?;
    rows.collect()
}

/// Resolve a symbol by exact name, then qualified name, then substring-LIKE.
pub fn find_definition(conn: &Connection, name: &str, limit: i64) -> rusqlite::Result<Vec<DefRow>> {
    let exact = def_rows(
        conn,
        "WHERE s.name = ? ORDER BY s.kind LIMIT ?",
        &[&name, &limit],
    )?;
    if !exact.is_empty() {
        return Ok(exact);
    }
    let qual = def_rows(conn, "WHERE s.qualified_name = ? LIMIT ?", &[&name, &limit])?;
    if !qual.is_empty() {
        return Ok(qual);
    }
    let pattern = format!("%{}%", escape_like(name));
    def_rows(
        conn,
        "WHERE s.name LIKE ? ESCAPE '\\' ORDER BY s.kind LIMIT ?",
        &[&pattern, &limit],
    )
}

/// Render the `find_definition` prose response.
pub fn render_find_definition(
    name: &str,
    rows: &[DefRow],
    limit: i64,
    freshness_banner: &str,
) -> String {
    if rows.is_empty() {
        return prepend_banner(
            &format!(
                "No definition found for '{name}'. The name may be misspelled, \
                 ambiguous, or the symbol lives outside the indexed workspace. \
                 Try search_symbols(\"{name}\") to find near matches."
            ),
            freshness_banner,
        );
    }
    let mut out: Vec<String> = Vec::with_capacity(rows.len() + 1);
    for r in rows {
        let qname = match r.qualified_name.as_deref() {
            Some(q) if !q.is_empty() => q.to_string(),
            _ => r.name.clone(),
        };
        out.push(format!(
            "{}:{}  {} {}  ({})",
            r.file_path,
            line_text(r.line_start),
            r.kind,
            qname,
            r.repo
        ));
    }
    if rows.len() as i64 >= limit {
        out.push("  ... hit the limit cap; pass a higher limit for more.".to_string());
    }
    prepend_banner(&out.join("\n"), freshness_banner)
}

/// Tool-level `find_definition`: clamp, query, render.
pub fn find_definition_text(
    conn: &Connection,
    name: &str,
    limit: i64,
    freshness_banner: &str,
) -> rusqlite::Result<String> {
    let limit = clamp(limit, 1, 1000);
    let rows = find_definition(conn, name, limit)?;
    Ok(render_find_definition(name, &rows, limit, freshness_banner))
}

// --- callers / callees -------------------------------------------------------

/// One caller edge row.
#[derive(Clone)]
pub struct CallerRow {
    pub caller_id: String,
    pub caller_name: String,
    pub caller_kind: String,
    pub file_path: String,
    pub repo: String,
    pub edge_line: Option<i64>,
    pub edge_kind: String,
    pub resolution: Option<String>,
}

/// One callee edge row; `resolved` mirrors a non-null `edges.target_id`.
pub struct CalleeRow {
    pub name: Option<String>,
    pub kind: String,
    pub resolved: bool,
    pub file_path: String,
    pub repo: String,
    pub edge_line: Option<i64>,
    pub edge_kind: String,
    pub resolution: Option<String>,
}

/// Edges whose target is the symbol named `name` (precise = resolved ids only).
pub fn get_callers(
    conn: &Connection,
    name: &str,
    fuzzy: bool,
    limit: i64,
    kind: Option<&str>,
    symbol_id: Option<&str>,
) -> rusqlite::Result<Vec<CallerRow>> {
    let target_clause;
    let mut sbind: Vec<String> = Vec::new();
    match (fuzzy, symbol_id) {
        (true, None) => {
            target_clause = "(e.target_name = ? OR e.target_id IN \
             (SELECT id FROM symbols WHERE name = ?))";
            sbind.push(name.to_string());
            sbind.push(name.to_string());
        }
        (true, Some(id)) => {
            target_clause = "(e.target_name = ? OR e.target_id = ?)";
            sbind.push(name.to_string());
            sbind.push(id.to_string());
        }
        (false, Some(id)) => {
            target_clause = "e.target_id = ?";
            sbind.push(id.to_string());
        }
        (false, None) => {
            target_clause = "e.target_id IN (SELECT id FROM symbols WHERE name = ?)";
            sbind.push(name.to_string());
        }
    }
    let kind_clause = if kind.is_some() { "AND e.kind = ?" } else { "" };
    if let Some(k) = kind {
        sbind.push(k.to_string());
    }
    let mut bind: Vec<&dyn ToSql> = sbind.iter().map(|s| s as &dyn ToSql).collect();
    bind.push(&limit);
    let sql = format!(
        "SELECT e.line AS edge_line, e.\"column\" AS edge_column, \
                e.kind AS edge_kind, s.name AS caller_name, s.kind AS caller_kind, \
                s.id AS caller_id, f.path AS file_path, f.repo_id AS repo, \
                e.resolution AS resolution \
         FROM edges e \
         JOIN symbols s ON e.source_id = s.id \
         JOIN files f ON s.file_id = f.id \
         WHERE {target_clause} {kind_clause} \
         ORDER BY f.repo_id, f.path, e.line, s.name \
         LIMIT ?"
    );
    let mut stmt = conn.prepare(&sql)?;
    let rows = stmt.query_map(bind.as_slice(), |row| {
        Ok(CallerRow {
            caller_id: row.get("caller_id")?,
            caller_name: row.get("caller_name")?,
            caller_kind: row.get("caller_kind")?,
            file_path: row.get("file_path")?,
            repo: row.get("repo")?,
            edge_line: row.get("edge_line")?,
            edge_kind: row.get("edge_kind")?,
            resolution: row.get("resolution")?,
        })
    })?;
    rows.collect()
}

/// Edges whose source is the symbol named `name` (fuzzy adds unresolved).
pub fn get_callees(
    conn: &Connection,
    name: &str,
    fuzzy: bool,
    limit: i64,
    kind: Option<&str>,
    symbol_id: Option<&str>,
) -> rusqlite::Result<Vec<CalleeRow>> {
    let source_clause = if symbol_id.is_some() {
        "s.id = ?"
    } else {
        "s.name = ?"
    };
    let target_clause = if fuzzy {
        ""
    } else {
        "AND e.target_id IS NOT NULL"
    };
    let kind_clause = if kind.is_some() { "AND e.kind = ?" } else { "" };
    let mut sbind: Vec<String> = Vec::new();
    match symbol_id {
        Some(id) => sbind.push(id.to_string()),
        None => sbind.push(name.to_string()),
    };
    if let Some(k) = kind {
        sbind.push(k.to_string());
    }
    let mut bind: Vec<&dyn ToSql> = sbind.iter().map(|s| s as &dyn ToSql).collect();
    bind.push(&limit);
    let sql = format!(
        "SELECT e.line AS edge_line, e.\"column\" AS edge_column, \
                e.kind AS edge_kind, COALESCE(t.name, e.target_name) AS callee_name, \
                COALESCE(t.kind, 'unknown') AS callee_kind, e.target_id AS target_id, \
                e.resolution AS resolution, f.path AS file_path, f.repo_id AS repo \
         FROM edges e \
         JOIN symbols s ON e.source_id = s.id \
         JOIN files f ON s.file_id = f.id \
         LEFT JOIN symbols t ON e.target_id = t.id \
         WHERE {source_clause} {target_clause} {kind_clause} \
         ORDER BY f.repo_id, f.path, e.line, COALESCE(t.name, e.target_name) \
         LIMIT ?"
    );
    let mut stmt = conn.prepare(&sql)?;
    let rows = stmt.query_map(bind.as_slice(), |row| {
        Ok(CalleeRow {
            name: row.get("callee_name")?,
            kind: row.get("callee_kind")?,
            resolved: row.get::<_, Option<String>>("target_id")?.is_some(),
            file_path: row.get("file_path")?,
            repo: row.get("repo")?,
            edge_line: row.get("edge_line")?,
            edge_kind: row.get("edge_kind")?,
            resolution: row.get("resolution")?,
        })
    })?;
    rows.collect()
}

/// Shared precise-then-fuzzy neighbor query outcome.
pub struct NeighborData<T> {
    pub symbol: String,
    pub rows: Vec<T>,
    pub used_fallback: bool,
    pub hit_limit: bool,
    pub stale_banner: String,
}

fn neighbor_rows<T, F, P>(
    conn: &Connection,
    name: &str,
    fuzzy: bool,
    limit: i64,
    query: F,
    file_path: P,
    freshness_banner: &str,
) -> rusqlite::Result<NeighborData<T>>
where
    F: Fn(&Connection, &str, bool, i64) -> rusqlite::Result<Vec<T>>,
    P: Fn(&T) -> &str,
{
    let limit = clamp(limit, 1, 1000);
    let mut rows = query(conn, name, fuzzy, limit)?;
    let mut used_fallback = false;
    if rows.is_empty() && !fuzzy {
        rows = query(conn, name, true, limit)?;
        used_fallback = true;
    }
    let banner = if !freshness_banner.is_empty() {
        freshness_banner.to_string()
    } else if rows.is_empty() {
        String::new()
    } else {
        let paths: Vec<&str> = rows.iter().map(file_path).collect();
        staleness_banner(conn, &paths)
    };
    // hit_limit only makes sense on the precise path: after a fuzzy fallback a
    // higher limit would not surface more precise results.
    let hit_limit = !used_fallback && rows.len() as i64 >= limit;
    Ok(NeighborData {
        symbol: name.to_string(),
        rows,
        used_fallback,
        hit_limit,
        stale_banner: banner,
    })
}

/// Structured core of `get_callers`.
pub fn callers_data(
    conn: &Connection,
    name: &str,
    fuzzy: bool,
    limit: i64,
    freshness_banner: &str,
) -> rusqlite::Result<NeighborData<CallerRow>> {
    neighbor_rows(
        conn,
        name,
        fuzzy,
        limit,
        |conn, n, fuzzy, limit| get_callers(conn, n, fuzzy, limit, None, None),
        |r| r.file_path.as_str(),
        freshness_banner,
    )
}

/// Structured core of `get_callees`.
pub fn callees_data(
    conn: &Connection,
    name: &str,
    fuzzy: bool,
    limit: i64,
    freshness_banner: &str,
) -> rusqlite::Result<NeighborData<CalleeRow>> {
    neighbor_rows(
        conn,
        name,
        fuzzy,
        limit,
        |conn, n, fuzzy, limit| get_callees(conn, n, fuzzy, limit, None, None),
        |r| r.file_path.as_str(),
        freshness_banner,
    )
}

/// Render the structured callers result as the prose return.
pub fn render_callers(data: &NeighborData<CallerRow>) -> String {
    if data.rows.is_empty() {
        return format!(
            "No callers found for '{}' (checked precise and fuzzy).",
            data.symbol
        );
    }
    let mut out = if data.used_fallback {
        vec![format!(
            "0 precise callers for '{}'; {} fuzzy candidates (name-match only \
             -- verify each against actual code before treating it as a real \
             caller):",
            data.symbol,
            data.rows.len()
        )]
    } else {
        vec![format!("{} callers of '{}':", data.rows.len(), data.symbol)]
    };
    for c in &data.rows {
        out.push(format!(
            "  {} {}  {}:{}  ({})",
            c.caller_kind,
            c.caller_name,
            c.file_path,
            line_text(c.edge_line),
            c.repo
        ));
    }
    if data.hit_limit {
        out.push("  ... hit the limit cap; pass a higher limit for more.".to_string());
    }
    prepend_banner(&out.join("\n"), &data.stale_banner)
}

/// Render the structured callees result as the prose return.
pub fn render_callees(data: &NeighborData<CalleeRow>) -> String {
    if data.rows.is_empty() {
        return prepend_banner(
            &format!(
                "No callees found for '{}' (checked precise and fuzzy).",
                data.symbol
            ),
            &data.stale_banner,
        );
    }
    let mut out = if data.used_fallback {
        vec![format!(
            "0 precise callees for '{}'; {} fuzzy candidates (includes \
             unresolved/external calls -- verify each before treating it as a \
             real callee):",
            data.symbol,
            data.rows.len()
        )]
    } else {
        vec![format!("{} callees of '{}':", data.rows.len(), data.symbol)]
    };
    for c in &data.rows {
        let tag = if c.resolved { "" } else { " (unresolved)" };
        out.push(format!(
            "  {}{}  {}:{}",
            c.name.clone().unwrap_or_else(|| "None".to_string()),
            tag,
            c.file_path,
            line_text(c.edge_line)
        ));
    }
    if data.hit_limit {
        out.push("  ... hit the limit cap; pass a higher limit for more.".to_string());
    }
    prepend_banner(&out.join("\n"), &data.stale_banner)
}

/// Tool-level `get_callers` prose return.
pub fn callers_text(
    conn: &Connection,
    name: &str,
    fuzzy: bool,
    limit: i64,
    freshness_banner: &str,
) -> rusqlite::Result<String> {
    Ok(render_callers(&callers_data(
        conn,
        name,
        fuzzy,
        limit,
        freshness_banner,
    )?))
}

/// Tool-level `get_callees` prose return.
pub fn callees_text(
    conn: &Connection,
    name: &str,
    fuzzy: bool,
    limit: i64,
    freshness_banner: &str,
) -> rusqlite::Result<String> {
    Ok(render_callees(&callees_data(
        conn,
        name,
        fuzzy,
        limit,
        freshness_banner,
    )?))
}

// --- staleness banner --------------------------------------------------------

/// Staleness banner when result files carry unindexed pending edits; empty otherwise.
pub fn staleness_banner(conn: &Connection, file_paths: &[&str]) -> String {
    let paths: Vec<&str> = file_paths
        .iter()
        .copied()
        .filter(|p| !p.is_empty())
        .take(200)
        .collect();
    if paths.is_empty() {
        return String::new();
    }
    let placeholders = vec!["?"; paths.len()].join(",");
    let sql = format!("SELECT path FROM pending_sync WHERE path IN ({placeholders})");
    let rows: Vec<String> = match conn.prepare(&sql) {
        Ok(mut stmt) => {
            let mapped =
                stmt.query_map(rusqlite::params_from_iter(paths), |r| r.get::<_, String>(0));
            match mapped {
                Ok(rows) => match rows.collect::<Result<Vec<_>, _>>() {
                    Ok(rows) => rows,
                    Err(_) => return String::new(),
                },
                // Table missing on an unmigrated DB: staleness stays best-effort.
                Err(_) => return String::new(),
            }
        }
        Err(_) => return String::new(),
    };
    let mut stale: Vec<String> = rows.into_iter().filter(|p| !p.is_empty()).collect();
    stale.sort();
    stale.dedup();
    if stale.is_empty() {
        return String::new();
    }
    let shown: Vec<String> = stale
        .iter()
        .take(3)
        .map(|p| p.rsplit('/').next().unwrap_or(p).to_string())
        .collect();
    let more = if stale.len() > 3 {
        format!(" (+{} more)", stale.len() - 3)
    } else {
        String::new()
    };
    format!(
        "⚠ Stale graph: {} file(s) in this result have unindexed edits pending \
         reindex ({}{}). Results may be incomplete until `cairn update` runs. ",
        stale.len(),
        shown.join(", "),
        more
    )
}

// --- test-symbol detection ---------------------------------------------------

const TEST_PATH_PATTERNS: &[&str] = &[
    "/src/test/",
    "/src/androidTest/",
    "/test/",
    "/tests/",
    "/__tests__/",
    "/spec/",
    "Test.kt",
    "Test.java",
    "Tests.java",
    "_test.go",
    "_test.dart",
    "_test.py",
    ".spec.ts",
    ".spec.js",
    ".test.ts",
    ".test.js",
    "Test.swift",
    "Spec.swift",
];

const STRONG_TEST_PATH_PATTERNS: &[&str] = &[
    "/src/test/",
    "/src/androidTest/",
    "/test/",
    "/tests/",
    "/__tests__/",
    "/spec/",
    "_test.go",
    "_test.dart",
    "_test.py",
    ".spec.ts",
    ".spec.js",
    ".test.ts",
    ".test.js",
];

const TEST_NAME_SUFFIXES: &[&str] = &["Test", "Spec", "Tests", "Specs"];

fn posix_basename(file_path: &str) -> String {
    file_path
        .replace('\\', "/")
        .trim_end_matches('/')
        .split('/')
        .rfind(|s| !s.is_empty())
        .unwrap_or("")
        .to_string()
}

/// Classify a symbol as a test; `Some` carries the detection method.
pub fn is_test_symbol(
    file_path: &str,
    symbol_name: &str,
    qualified_name: &str,
) -> Option<&'static str> {
    let test_basename = posix_basename(file_path).starts_with("test_");
    let mut path_hit = !file_path.is_empty()
        && (TEST_PATH_PATTERNS.iter().any(|p| file_path.contains(p)) || test_basename);
    if path_hit && file_path.contains("/src/main/") {
        let strong = STRONG_TEST_PATH_PATTERNS
            .iter()
            .any(|p| file_path.contains(p))
            || test_basename;
        if !strong {
            path_hit = false;
        }
    }
    let mut name_hit = false;
    if !symbol_name.is_empty() || !qualified_name.is_empty() {
        let final_seg = if qualified_name.is_empty() {
            String::new()
        } else {
            qualified_name
                .replace('/', ".")
                .rsplit('.')
                .next()
                .unwrap_or("")
                .to_string()
        };
        let probe = if !final_seg.is_empty() {
            final_seg
        } else if !symbol_name.is_empty() {
            symbol_name.to_string()
        } else {
            String::new()
        };
        name_hit = TEST_NAME_SUFFIXES.iter().any(|s| probe.ends_with(s));
    }
    if name_hit && !file_path.is_empty() && file_path.contains("/src/main/") && !path_hit {
        name_hit = false;
    }
    match (path_hit, name_hit) {
        (true, true) => Some("path+name"),
        (true, false) => Some("path"),
        (false, true) => Some("name"),
        (false, false) => None,
    }
}

// --- cross-repo dependents ---------------------------------------------------

/// One cross-repo dependent repo and its matching-import count.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Dependent {
    pub repo: String,
    pub count: i64,
}

/// Distinct repos defining `name` (exact-first resolution), sorted.
pub fn definer_repos(conn: &Connection, name: &str) -> rusqlite::Result<Vec<String>> {
    let rows = find_definition(conn, name, 200)?;
    let mut repos: Vec<String> = Vec::new();
    for r in rows {
        if !r.repo.is_empty() && !repos.contains(&r.repo) {
            repos.push(r.repo);
        }
    }
    repos.sort();
    Ok(repos)
}

/// Repos whose imports resolve into `repo`'s namespaces, count-desc (stable).
pub fn cross_repo_dependents(
    conn: &Connection,
    repo: &str,
    namespaces: &[(&str, &str)],
) -> rusqlite::Result<Vec<Dependent>> {
    let my_namespaces: Vec<&str> = namespaces
        .iter()
        .filter(|(_ns, owner)| *owner == repo)
        .map(|(ns, _owner)| *ns)
        .collect();
    if my_namespaces.is_empty() {
        return Ok(Vec::new());
    }
    let mut clauses: Vec<String> = Vec::new();
    let mut params: Vec<String> = Vec::new();
    for ns in &my_namespaces {
        clauses.push("(imported_path = ? OR substr(imported_path, 1, ?) = ?)".to_string());
        params.push((*ns).to_string());
        params.push((ns.len() + 1).to_string());
        params.push(format!("{ns}."));
    }
    let sql = format!(
        "SELECT imported_path, repo_id \
         FROM imports JOIN files ON imports.file_id = files.id \
         WHERE files.repo_id != ? AND ({})",
        clauses.join(" OR ")
    );
    let mut sbind: Vec<String> = vec![repo.to_string()];
    sbind.extend(params.iter().cloned());
    let bind: Vec<&dyn ToSql> = sbind.iter().map(|s| s as &dyn ToSql).collect();
    let mut stmt = conn.prepare(&sql)?;
    let rows = stmt.query_map(bind.as_slice(), |row| {
        Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
    })?;
    let mut out: Vec<Dependent> = Vec::new();
    for row in rows {
        let (_imported_path, dep_repo) = row?;
        match out.iter_mut().find(|d| d.repo == dep_repo) {
            Some(d) => d.count += 1,
            None => out.push(Dependent {
                repo: dep_repo,
                count: 1,
            }),
        }
    }
    out.sort_by_key(|d| std::cmp::Reverse(d.count));
    Ok(out)
}

/// Cross-repo dependents merged over every repo defining `name`.
pub fn cross_repo_xref(
    conn: &Connection,
    name: &str,
    namespaces: &[(&str, &str)],
) -> rusqlite::Result<Vec<Dependent>> {
    let mut merged: Vec<Dependent> = Vec::new();
    for repo in definer_repos(conn, name)? {
        for d in cross_repo_dependents(conn, &repo, namespaces)? {
            match merged.iter_mut().find(|m| m.repo == d.repo) {
                Some(m) => m.count += d.count,
                None => merged.push(d),
            }
        }
    }
    Ok(merged)
}

// --- impact analysis ---------------------------------------------------------

/// One impacted symbol reached by the caller walk.
pub struct ImpactEntry {
    pub symbol: String,
    pub file: String,
    pub repo: String,
    pub depth: i64,
}

/// Recursive caller traversal outcome.
pub struct ImpactResult {
    pub impacted: Vec<ImpactEntry>,
    pub cycles: Vec<(String, i64)>,
    pub total: usize,
    pub truncated: bool,
}

struct Dfs<'a> {
    conn: &'a Connection,
    allowed: Option<&'static [&'static str]>,
    fuzzy: bool,
    seed_id: Option<&'a str>,
    max_depth: i64,
    limit: usize,
    min_depth: HashMap<String, i64>,
    result_index: HashMap<String, usize>,
    results: Vec<ImpactEntry>,
    on_path: HashSet<String>,
    cycles_seen: HashSet<String>,
    cycles: Vec<(String, i64)>,
    truncated: bool,
    callers_memo: HashMap<String, Vec<CallerRow>>,
}

impl Dfs<'_> {
    fn callers(&mut self, sym_id: Option<&str>, name: &str) -> Vec<CallerRow> {
        let key = match (sym_id, self.seed_id) {
            (Some(id), Some(_)) => format!("{id}\u{0}{name}"),
            _ => name.to_string(),
        };
        if !self.callers_memo.contains_key(&key) {
            let rows = get_callers(
                self.conn,
                name,
                self.fuzzy,
                200,
                None,
                sym_id.filter(|_| self.seed_id.is_some()),
            )
            .unwrap_or_default();
            self.callers_memo.insert(key.clone(), rows);
        }
        self.callers_memo.get(&key).cloned().unwrap_or_default()
    }

    fn record_caller(&mut self, c: &CallerRow, depth: i64) {
        let cid = c.caller_id.clone();
        match self.result_index.get(&cid) {
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
                self.result_index
                    .insert(cid.clone(), self.results.len() - 1);
            }
            Some(&idx) => {
                if depth < self.results[idx].depth {
                    self.results[idx].depth = depth;
                }
            }
        }
        // Walk into the caller either way so back-edges surface as cycles.
        self.traverse(&cid, &c.caller_name, depth + 1);
    }

    fn traverse(&mut self, sym_id: &str, sym_name: &str, depth: i64) {
        if self.truncated || depth > self.max_depth {
            return;
        }
        if self.on_path.contains(sym_id) {
            if !self.cycles_seen.contains(sym_id) {
                self.cycles_seen.insert(sym_id.to_string());
                self.cycles.push((sym_name.to_string(), depth));
            }
            return;
        }
        if let Some(&prev) = self.min_depth.get(sym_id)
            && prev < depth
        {
            return;
        }
        self.min_depth.insert(sym_id.to_string(), depth - 1);
        self.on_path.insert(sym_id.to_string());
        let callers = self.callers(Some(sym_id), sym_name);
        for c in &callers {
            if let Some(allowed) = self.allowed
                && !allowed.contains(&c.edge_kind.as_str())
            {
                continue;
            }
            self.record_caller(c, depth);
            if self.truncated {
                break;
            }
        }
        self.on_path.remove(sym_id);
    }
}

/// Recursive caller traversal with cycle detection (precise by default).
pub fn impact_analysis(
    conn: &Connection,
    name: &str,
    max_depth: i64,
    fuzzy: bool,
    limit: i64,
    include_service_edges: bool,
    seed_id: Option<&str>,
) -> rusqlite::Result<ImpactResult> {
    let max_depth = max_depth.min(MAX_TRAVERSAL_DEPTH);
    let limit = clamp(limit, 1, 1000);
    let allowed = if include_service_edges {
        None
    } else {
        Some(STRUCTURAL_EDGE_KINDS)
    };
    let mut dfs = Dfs {
        conn,
        allowed,
        fuzzy,
        seed_id,
        max_depth,
        limit: limit as usize,
        min_depth: HashMap::new(),
        result_index: HashMap::new(),
        results: Vec::new(),
        on_path: HashSet::new(),
        cycles_seen: HashSet::new(),
        cycles: Vec::new(),
        truncated: false,
        callers_memo: HashMap::new(),
    };

    // The entry name may resolve to several symbols; mark them all on-path so
    // the traversal never re-enters the seed.
    match seed_id {
        None => {
            for seed in find_definition(conn, name, limit)? {
                dfs.on_path.insert(seed.id);
            }
        }
        Some(id) => {
            dfs.on_path.insert(id.to_string());
        }
    }
    let callers = dfs.callers(None, name);
    for c in &callers {
        if let Some(allowed) = dfs.allowed
            && !allowed.contains(&c.edge_kind.as_str())
        {
            continue;
        }
        dfs.record_caller(c, 0);
        if dfs.truncated {
            break;
        }
    }
    Ok(ImpactResult {
        total: dfs.results.len(),
        truncated: dfs.truncated,
        impacted: dfs.results,
        cycles: dfs.cycles,
    })
}

/// One affected test with how it was detected.
pub struct ImpactTest {
    pub symbol: String,
    pub file: String,
    pub repo: String,
    pub detection_method: String,
}

/// Impact entries classified as tests, in traversal order.
pub fn filter_tests(impacted: &[ImpactEntry]) -> Vec<ImpactTest> {
    impacted
        .iter()
        .filter_map(|r| {
            is_test_symbol(&r.file, &r.symbol, "").map(|detection_method| ImpactTest {
                symbol: r.symbol.clone(),
                file: r.file.clone(),
                repo: r.repo.clone(),
                detection_method: detection_method.to_string(),
            })
        })
        .collect()
}

/// Structured core of the live `impact_analysis` path.
pub struct ImpactData {
    pub symbol: String,
    pub total: usize,
    pub truncated: bool,
    pub fuzzy: bool,
    pub by_depth: Vec<(i64, usize)>,
    pub cycles: Vec<String>,
    pub affected_tests: Vec<ImpactTest>,
    pub cross_repo_dependents: Vec<Dependent>,
    pub stale_banner: String,
}

/// Assemble the structured impact result from a traversal + cross-repo xref.
pub fn impact_data(
    result: &ImpactResult,
    cross_repo_dependents: Vec<Dependent>,
    name: &str,
    fuzzy: bool,
) -> ImpactData {
    let mut depths: Vec<i64> = result.impacted.iter().map(|r| r.depth).collect();
    depths.sort_unstable();
    depths.dedup();
    let by_depth: Vec<(i64, usize)> = depths
        .into_iter()
        .map(|d| (d, result.impacted.iter().filter(|r| r.depth == d).count()))
        .collect();
    ImpactData {
        symbol: name.to_string(),
        total: result.total,
        truncated: result.truncated,
        fuzzy,
        by_depth,
        cycles: result.cycles.iter().map(|(s, _d)| s.clone()).collect(),
        affected_tests: filter_tests(&result.impacted),
        cross_repo_dependents,
        stale_banner: String::new(),
    }
}

/// Render the structured impact result as the prose return.
pub fn render_impact(data: &ImpactData, limit: i64) -> String {
    let mut out = vec![format!(
        "Impact of '{}': {} total impacted symbols.",
        data.symbol, data.total
    )];
    if data.truncated {
        out.push(format!(
            "  (traversal stopped early at limit={limit}; pass a higher limit for the full count.)"
        ));
    }
    if !data.cycles.is_empty() {
        let cycles = data
            .cycles
            .iter()
            .map(|c| format!("'{c}'"))
            .collect::<Vec<_>>()
            .join(", ");
        out.push(format!("Cycles: [{cycles}]"));
    }
    for (depth, count) in &data.by_depth {
        out.push(format!("  Depth {depth}: {count} callers"));
    }
    if !data.affected_tests.is_empty() {
        out.push(format!(
            "Affected tests ({} — run these to verify the change):",
            data.affected_tests.len()
        ));
        for t in data.affected_tests.iter().take(15) {
            out.push(format!(
                "  {}  {}  ({}, {})",
                t.symbol, t.file, t.repo, t.detection_method
            ));
        }
        if data.affected_tests.len() > 15 {
            out.push(format!("  ... and {} more", data.affected_tests.len() - 15));
        }
    }
    if data.total == 0 && !data.fuzzy {
        out.push(
            "(0 precise within-repo callers — retry fuzzy=True before concluding unused.)"
                .to_string(),
        );
    }
    if !data.cross_repo_dependents.is_empty() {
        let consumers = data
            .cross_repo_dependents
            .iter()
            .take(5)
            .map(|d| format!("{} (x{})", d.repo, d.count))
            .collect::<Vec<_>>()
            .join(", ");
        out.push(format!(
            "Cross-repo: {} repo(s) depend — {consumers}",
            data.cross_repo_dependents.len()
        ));
    }
    prepend_banner(&out.join("\n"), &data.stale_banner)
}

/// Tool-level `impact_analysis` prose return (live traversal, no cache path).
pub fn impact_analysis_text(
    conn: &Connection,
    name: &str,
    depth: i64,
    fuzzy: bool,
    limit: i64,
    freshness_banner: &str,
    namespaces: &[(&str, &str)],
) -> rusqlite::Result<String> {
    let depth = clamp(depth, 1, 10);
    let limit = clamp(limit, 1, 1000);
    let result = impact_analysis(conn, name, depth, fuzzy, limit, false, None)?;
    let dependents = cross_repo_xref(conn, name, namespaces)?;
    let mut data = impact_data(&result, dependents, name, fuzzy);
    data.stale_banner = freshness_banner.to_string();
    Ok(render_impact(&data, limit))
}

#[cfg(test)]
mod tests;
