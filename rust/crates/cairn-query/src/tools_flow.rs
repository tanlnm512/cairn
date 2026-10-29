//! Flow-family tools: `explore`, `trace_flow`, taint paths, `visualize_graph`, `cross_repo_deps`.

use std::collections::HashSet;
use std::path::Path;

use rusqlite::Connection;

use crate::graph::{
    TaintPath, cross_repo_deps, find_paths, find_symbol_paths, format_taint_warning, get_callees,
    get_callers, get_deps_graph, get_impact_graph, get_module_graph, get_repo_graph,
    get_symbol_graph, intersect_seeds, lexical_search_symbols, resolve_namespaces,
    resolve_pattern_symbols, to_dot, to_json, to_mermaid, trace_flow,
};

/// Default source-reading budget for one explore call.
const EXPLORE_MAX_NODES: usize = 20;
const EXPLORE_MAX_SOURCE_LINES: usize = 400;
const EXPLORE_EDGE_LIMIT: i64 = 20;
const BLAST_TOP_CALLERS: usize = 5;
const CALL_PATH_SHOWN: usize = 20;
const BLAST_DEPTH: i64 = 2;

fn clamp(value: i64, lo: i64, hi: i64) -> i64 {
    value.max(lo).min(hi)
}

fn basename(path: &str) -> &str {
    path.rsplit('/').next().unwrap_or(path)
}

// --- explore ----------------------------------------------------------------

/// One source-row tuple: (id, name, kind, line_start, line_end, file_path, repo).
type SourceRow = (
    String,
    String,
    String,
    Option<i64>,
    Option<i64>,
    String,
    String,
);

/// One per-file span group and its symbol tuples.
type FileSpans = (String, String, Vec<(String, String, String, i64, i64)>);

/// One deduped call-path row: (from, to, file_path, line, resolution).
type CallPathRow = (String, String, String, Option<i64>, Option<String>);

/// Verbatim source spans for the given symbol ids, grouped by file in
/// (path, line_start) order.
fn read_source_spans(
    conn: &Connection,
    workspace: &Path,
    symbol_ids: &HashSet<String>,
    budget: usize,
) -> Result<Vec<(String, Vec<SourceEntry>)>, rusqlite::Error> {
    if symbol_ids.is_empty() {
        return Ok(vec![]);
    }
    let ids: Vec<String> = symbol_ids.iter().cloned().collect();
    let ph = vec!["?"; ids.len()].join(",");
    let sql = format!(
        "SELECT s.id, s.name, s.kind, s.line_start, s.line_end, \
         f.path AS file_path, f.repo_id AS repo \
         FROM symbols s JOIN files f ON s.file_id = f.id \
         WHERE s.id IN ({ph}) \
         ORDER BY f.path, s.line_start"
    );
    let refs: Vec<&dyn rusqlite::ToSql> = ids.iter().map(|i| i as &dyn rusqlite::ToSql).collect();
    let mut stmt = conn.prepare(&sql)?;
    let rows: Vec<SourceRow> = stmt
        .query_map(refs.as_slice(), |r| {
            Ok((
                r.get(0)?,
                r.get(1)?,
                r.get(2)?,
                r.get(3)?,
                r.get(4)?,
                r.get(5)?,
                r.get(6)?,
            ))
        })?
        .collect::<Result<_, _>>()?;

    // Group symbol metadata by (repo, file_path); each file opens at most once.
    let mut by_file: Vec<FileSpans> = Vec::new();
    let mut index: std::collections::HashMap<(String, String), usize> =
        std::collections::HashMap::new();
    for (id, name, kind, line_start, line_end, file_path, repo) in rows {
        let key = (repo.clone(), file_path.clone());
        let slot = match index.get(&key) {
            Some(&i) => i,
            None => {
                by_file.push((repo.clone(), file_path.clone(), Vec::new()));
                let slot = by_file.len() - 1;
                index.insert(key, slot);
                slot
            }
        };
        by_file[slot].2.push((
            id,
            name,
            kind,
            line_start.unwrap_or(0),
            line_end.unwrap_or(0),
        ));
    }

    let mut out: Vec<(String, Vec<SourceEntry>)> = Vec::new();
    let mut used = 0usize;
    for (repo, file_path, syms) in by_file {
        // Stored paths are repo-relative; single-repo stores resolve against
        // the workspace root (multi-repo resolution is a build-side concern).
        let abs_path = if Path::new(&file_path).is_absolute() {
            file_path.clone()
        } else {
            workspace.join(&file_path).to_string_lossy().to_string()
        };
        let _ = &repo;
        let Ok(all_lines) = std::fs::read_to_string(&abs_path) else {
            continue; // file deleted/moved since index — skip silently
        };
        let lines: Vec<&str> = all_lines.split_inclusive('\n').collect();
        let mut entries: Vec<SourceEntry> = Vec::new();
        for (_id, name, kind, ls, le) in syms {
            if ls < 1 || le < ls {
                continue;
            }
            let span: Vec<String> = lines
                .get((ls - 1) as usize..(le as usize).min(lines.len()))
                .unwrap_or(&[])
                .iter()
                .map(|l| l.trim_end_matches('\n').to_string())
                .collect();
            used += span.len();
            entries.push(SourceEntry {
                symbol: name,
                kind,
                line_start: ls,
                line_end: le,
                lines: span,
            });
        }
        if !entries.is_empty() {
            out.push((file_path, entries));
        }
        if used >= budget {
            break;
        }
    }
    Ok(out)
}

struct SourceEntry {
    symbol: String,
    kind: String,
    line_start: i64,
    line_end: i64,
    lines: Vec<String>,
}

/// `resolution` suffix shared by the call-path rows: `""` or `  [label]`.
fn resolution_suffix(resolution: &Option<String>) -> String {
    match resolution {
        Some(r) => {
            let label = if r.is_empty() { "unknown" } else { r.as_str() };
            format!("  [{label}]")
        }
        None => String::new(),
    }
}

/// One explore seed.
struct Seed {
    id: Option<String>,
    name: Option<String>,
}

/// `explore`: source, call paths, blast radius, dispatch, tribal memory,
/// taint warning; `bundle`/`read_only`/`session_id` gate the tribal section.
pub fn explore_text(
    conn: &Connection,
    bundle: Option<&crate::tools_km::Bundle>,
    workspace: &Path,
    query: &str,
    read_only: bool,
    session_id: Option<&str>,
) -> Result<String, rusqlite::Error> {
    let seed_rows = lexical_search_symbols(conn, query, None, EXPLORE_MAX_NODES as i64);
    let mut seeds: Vec<Seed> = seed_rows
        .iter()
        .map(|r| Seed {
            id: Some(r.id.clone()),
            name: Some(r.name.clone()),
        })
        .collect();

    // RRF fusion (or a thin FTS5 seed set) expands the seeds with semantic
    // hits before any downstream step (explore.py's fusion block).
    let env = crate::tools_search::semantic_env(None);
    if env.fusion_enabled || seeds.len() < 3 {
        let embed_count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM embeddings WHERE model = ?",
                [&env.model],
                |row| row.get(0),
            )
            .unwrap_or(0);
        if env.backend != crate::tools_search::EffectiveBackend::Unavailable && embed_count > 0 {
            let mut seen: HashSet<String> = seeds.iter().filter_map(|s| s.id.clone()).collect();
            for m in crate::tools_search::semantic_search(conn, query, EXPLORE_MAX_NODES, 0.3, &env)
            {
                if seen.insert(m.id.clone()) {
                    seeds.push(Seed {
                        id: Some(m.id.clone()),
                        name: Some(m.name.clone()),
                    });
                }
            }
        }
    }

    if seeds.is_empty() {
        return Ok(format!(
            "No symbols matching '{query}'. Try a broader query or use search_symbols."
        ));
    }

    // --- 1-hop neighborhood per seed, deduped on (from, to, file, line) ------
    let mut neighbor_ids: HashSet<String> = HashSet::new();
    let mut callers_out: Vec<CallPathRow> = Vec::new();
    let mut callees_out: Vec<CallPathRow> = Vec::new();
    let mut seen_caller: HashSet<(String, String, String, Option<i64>)> = HashSet::new();
    let mut seen_callee: HashSet<(String, String, String, Option<i64>)> = HashSet::new();
    for seed in &seeds {
        let Some(name) = seed.name.clone() else {
            continue;
        };
        for c in get_callers(conn, &name, false, EXPLORE_EDGE_LIMIT, None)? {
            let key = (
                c.caller_name.clone(),
                name.clone(),
                c.file_path.clone(),
                c.edge_line,
            );
            if seen_caller.insert(key) {
                callers_out.push((
                    c.caller_name.clone(),
                    name.clone(),
                    c.file_path.clone(),
                    c.edge_line,
                    c.resolution.clone(),
                ));
                neighbor_ids.insert(c.caller_id.clone());
            }
        }
        for c in get_callees(conn, &name, false, EXPLORE_EDGE_LIMIT, None)? {
            let key = (
                name.clone(),
                c.callee_name.clone().unwrap_or_default(),
                c.file_path.clone(),
                c.edge_line,
            );
            if seen_callee.insert(key) {
                callees_out.push((
                    name.clone(),
                    c.callee_name.clone().unwrap_or_default(),
                    c.file_path.clone(),
                    c.edge_line,
                    c.resolution.clone(),
                ));
            }
        }
    }

    // --- source spans for seeds + neighbors ---------------------------------
    let seed_ids: HashSet<String> = seeds.iter().filter_map(|s| s.id.clone()).collect();
    let files = read_source_spans(
        conn,
        workspace,
        &(seed_ids.union(&neighbor_ids).cloned().collect()),
        EXPLORE_MAX_SOURCE_LINES,
    )?;

    // --- blast radius per seed (shallow depth 2) -----------------------------
    let mut blast: Vec<(String, i64, Vec<String>, usize)> = Vec::new();
    for seed in &seeds {
        let Some(name) = seed.name.clone() else {
            continue;
        };
        let result = crate::graph::impact_analysis(conn, &name, BLAST_DEPTH, false, 500)?;
        let mut top: Vec<String> = Vec::new();
        let mut seen_top: HashSet<String> = HashSet::new();
        for entry in &result.impacted {
            if !entry.symbol.is_empty() && seen_top.insert(entry.symbol.clone()) {
                top.push(entry.symbol.clone());
            }
            if top.len() >= BLAST_TOP_CALLERS {
                break;
            }
        }
        let repos: Vec<String> = {
            let mut set: HashSet<String> = result
                .impacted
                .iter()
                .filter(|e| !e.repo.is_empty())
                .map(|e| e.repo.clone())
                .collect();
            let mut v: Vec<String> = set.drain().collect();
            v.sort();
            v
        };
        let repo_count = repos.len();
        // Seeds are keyed by name: duplicate names collapse
        // into one entry at the first insertion position.
        match blast.iter_mut().find(|(n, _, _, _)| *n == name) {
            Some(slot) => *slot = (name, result.total as i64, top, repo_count),
            None => blast.push((name, result.total as i64, top, repo_count)),
        }
    }

    // --- ambiguous dispatch hops for seed names ------------------------------
    let seed_names: Vec<String> = seeds.iter().filter_map(|s| s.name.clone()).collect();
    let dispatch_hops = ambiguous_dispatch(conn, &seed_names)?;

    // --- taint paths intersecting the seeds ----------------------------------
    let seed_name_set: HashSet<String> = seed_names.iter().cloned().collect();
    let taint_paths: Vec<TaintPath> = intersect_seeds(conn, &seed_name_set, false)?;

    // --- render ---------------------------------------------------------------
    let mut out: Vec<String> = Vec::new();
    out.push(format!("=== explore: \"{query}\" ==="));
    out.push(format!("{} symbol(s) matched.", seeds.len()));
    out.push(String::new());

    out.push(format!(
        "=== Source ({} file(s), {} line(s)) ===",
        files.len(),
        files
            .iter()
            .map(|(_, es)| es
                .iter()
                .map(|e| e.line_end - e.line_start + 1)
                .sum::<i64>())
            .sum::<i64>()
    ));
    if !files.is_empty() {
        for (file_path, entries) in &files {
            out.push(file_path.clone());
            for e in entries {
                out.push(format!(
                    "  [{} {}  lines {}-{}]",
                    e.kind, e.symbol, e.line_start, e.line_end
                ));
                let width = e.line_end.to_string().len();
                for (i, line) in e.lines.iter().enumerate() {
                    let lineno = e.line_start + i as i64;
                    out.push(format!("  {lineno:>width$}  {line}"));
                }
            }
            out.push(String::new());
        }
    } else {
        out.push("  (source unavailable — files moved since index)".to_string());
        out.push(String::new());
    }

    out.push("=== Call paths ===".to_string());
    if !callers_out.is_empty() || !callees_out.is_empty() {
        for c in callers_out.iter().take(CALL_PATH_SHOWN) {
            let (from, to, file_path, line, resolution) = c;
            out.push(format!(
                "  {to}  <- called by  {from}  ({}:{}){}",
                basename(file_path),
                line.map(|l| l.to_string())
                    .unwrap_or_else(|| "None".to_string()),
                resolution_suffix(resolution),
            ));
        }
        for c in callees_out.iter().take(CALL_PATH_SHOWN) {
            let (from, to, file_path, line, resolution) = c;
            out.push(format!(
                "  {from}  -> calls  {to}  ({}:{}){}",
                basename(file_path),
                line.map(|l| l.to_string())
                    .unwrap_or_else(|| "None".to_string()),
                resolution_suffix(resolution),
            ));
        }
        if callers_out.len() > CALL_PATH_SHOWN {
            out.push(format!(
                "  ... and {} more caller edges",
                callers_out.len() - CALL_PATH_SHOWN
            ));
        }
        if callees_out.len() > CALL_PATH_SHOWN {
            out.push(format!(
                "  ... and {} more callee edges",
                callees_out.len() - CALL_PATH_SHOWN
            ));
        }
    } else {
        out.push("  (no resolved call edges among the matched symbols)".to_string());
    }
    out.push(String::new());

    out.push("=== Blast radius (depth 2) ===".to_string());
    if !blast.is_empty() {
        for (name, total, top, repo_count) in blast.iter().take(5) {
            let repos_str = if *repo_count > 0 {
                format!(", {repo_count} repo(s)")
            } else {
                String::new()
            };
            out.push(format!("  {name}: {total} caller(s){repos_str}"));
            if !top.is_empty() {
                out.push(format!(
                    "    top: {}",
                    top.iter().take(5).cloned().collect::<Vec<_>>().join(", ")
                ));
            }
        }
    } else {
        out.push("  (no callers within depth 2)".to_string());
    }
    out.push(String::new());

    out.push("=== Ambiguous dispatch ===".to_string());
    if !dispatch_hops.is_empty() {
        for (target, candidates) in &dispatch_hops {
            out.push(format!(
                "  \"{target}\" could dispatch to: {}",
                candidates.join(", ")
            ));
        }
    } else {
        out.push("  (none — all call edges were precisely resolved)".to_string());
    }

    // Tribal memory renders its header even when the memory layer is absent.
    let tribal = if let Some(bundle) = bundle {
        crate::tools_km::search_memory(
            conn,
            bundle,
            &seed_names
                .iter()
                .take(5)
                .cloned()
                .collect::<Vec<_>>()
                .join(" "),
            Some("tribal"),
            None,
            false,
            None,
        )
        .unwrap_or_default()
    } else {
        Vec::new()
    };
    let tribal: Vec<_> = tribal.into_iter().take(3).collect();
    if !read_only && let Some(session_id) = session_id {
        let refs: Vec<(String, String)> = tribal
            .iter()
            .map(|c| (c.concept_id.clone(), query.to_string()))
            .collect();
        // Ref counting is analytics: a lock collision degrades to an
        // uncredited surface, never a failed tool call.
        crate::tools_km::record_references_batch(conn, &refs, session_id);
    }
    out.push(format!("=== Tribal memory ({}) ===", tribal.len()));
    if tribal.is_empty() {
        out.push("  (none)".to_string());
    } else {
        for c in &tribal {
            let mut title = c.title.clone().unwrap_or_else(|| c.concept_id.clone());
            let stance = c.ext_str("memory_stance").unwrap_or("");
            if !stance.is_empty() {
                title.push_str(&format!(", stance={stance}"));
                let peer = c.ext_str("memory_stance_peer").unwrap_or("");
                if stance == "contested" && !peer.is_empty() {
                    title.push_str(&format!(" (peer: {peer})"));
                }
            }
            out.push(format!("  {title}"));
            let apply_line = how_to_apply_line(&c.body)
                .or_else(|| c.description.as_deref().map(str::trim))
                .filter(|s| !s.is_empty());
            if let Some(apply) = apply_line {
                out.push(format!("    How to apply: {apply}"));
            }
        }
    }

    if !taint_paths.is_empty() {
        out.push("=== Taint paths ===".to_string());
        out.push(format_taint_warning(&taint_paths));
        out.push(String::new());
    }
    Ok(out.join("\n"))
}

/// First `How to apply:` line's remainder, trimmed (the memory body contract).
fn how_to_apply_line(body: &str) -> Option<&str> {
    body.lines()
        .find_map(|line| line.strip_prefix("How to apply:"))
        .map(|rest| rest.trim())
        .filter(|rest| !rest.is_empty())
}

/// `resolution='ambiguous'` edges grouped by target.
fn ambiguous_dispatch(
    conn: &Connection,
    target_names: &[String],
) -> Result<Vec<(String, Vec<String>)>, rusqlite::Error> {
    let names: Vec<String> = target_names
        .iter()
        .filter(|t| !t.is_empty())
        .cloned()
        .collect();
    if names.is_empty() {
        return Ok(vec![]);
    }
    let ph = vec!["?"; names.len()].join(",");
    let sql = format!(
        "SELECT e.target_name AS target, \
         GROUP_CONCAT(DISTINCT s.name) AS candidates \
         FROM edges e JOIN symbols s ON e.source_id = s.id \
         WHERE e.resolution = 'ambiguous' \
           AND e.target_name IN ({ph}) \
         GROUP BY e.target_name \
         ORDER BY e.target_name"
    );
    let refs: Vec<&dyn rusqlite::ToSql> = names.iter().map(|n| n as &dyn rusqlite::ToSql).collect();
    let mut stmt = conn.prepare(&sql)?;
    let rows: Vec<(String, Option<String>)> = stmt
        .query_map(refs.as_slice(), |r| Ok((r.get(0)?, r.get(1)?)))?
        .collect::<Result<_, _>>()?;
    let mut out = Vec::new();
    for (target, candidates) in rows {
        let cands: Vec<String> = candidates
            .unwrap_or_default()
            .split(',')
            .filter(|c| !c.is_empty())
            .map(str::to_string)
            .collect();
        if !cands.is_empty() {
            out.push((target, cands));
        }
    }
    Ok(out)
}

// --- trace_flow -------------------------------------------------------------

/// `trace_flow`: ordered chain, branch points, terminal calls, cycles.
pub fn trace_flow_text(
    conn: &Connection,
    entry: &str,
    max_depth: i64,
) -> Result<String, rusqlite::Error> {
    let flow = trace_flow(conn, entry, max_depth);
    if flow.total <= 1 {
        return Ok(format!("No outgoing calls traced from '{entry}'."));
    }
    let mut out: Vec<String> = Vec::new();
    out.push(format!(
        "Traced {} step(s) from '{}' ({} branches, {} terminals):",
        flow.total,
        entry,
        flow.branches.len(),
        flow.leaves.len()
    ));
    for node in &flow.chain {
        let indent = "  ".repeat(node.depth.max(0) as usize);
        out.push(format!("{indent}- `{}` ({})", node.symbol, node.kind));
    }
    if !flow.branches.is_empty() {
        out.push("\nBranch points:".to_string());
        for (symbol, callees) in flow.branches.iter().take(8) {
            let shown: Vec<String> = callees.iter().take(4).map(|c| format!("`{c}`")).collect();
            out.push(format!("  `{symbol}` -> {}", shown.join(", ")));
        }
    }
    if !flow.leaves.is_empty() {
        let shown: Vec<String> = flow
            .leaves
            .iter()
            .take(8)
            .map(|l| format!("`{l}`"))
            .collect();
        out.push(format!("\nTerminal calls: {}", shown.join(", ")));
    }
    if !flow.cycles.is_empty() {
        let shown: Vec<String> = flow
            .cycles
            .iter()
            .take(3)
            .map(|(s, _)| format!("`{s}`"))
            .collect();
        out.push(format!("\nCyclic calls: {}", shown.join(", ")));
    }
    if flow.truncated {
        out.push("\n(trace truncated at node limit)".to_string());
    }
    Ok(out.join("\n"))
}

// --- taint ------------------------------------------------------------------

/// Precise-edge taint trace between a source and sink category-or-name pattern,
/// rendered as the canonical warning text (empty when no path exists).
pub fn taint_warning_text(
    conn: &Connection,
    from_pattern: &str,
    to_pattern: &str,
) -> Result<String, rusqlite::Error> {
    let paths = find_paths(
        conn,
        from_pattern,
        to_pattern,
        false,
        crate::graph::CLOSURE_MAX_DEPTH,
    )?;
    Ok(format_taint_warning(&paths))
}

// --- path -------------------------------------------------------------------

/// `path`: shortest structural paths between two symbol patterns; fuzzy
/// follows ambiguous hops. The freshness banner is a daemon-layer integration
/// and is not part of this render.
#[allow(clippy::too_many_arguments)]
pub fn path_text(
    conn: &Connection,
    from_pattern: &str,
    to_pattern: &str,
    fuzzy: bool,
    max_depth: i64,
    limit: i64,
) -> Result<String, rusqlite::Error> {
    let max_depth = clamp(max_depth, 1, 10);
    let limit = clamp(limit, 1, 1000);
    let from_ids: HashSet<String> = resolve_pattern_symbols(conn, from_pattern)?
        .into_iter()
        .map(|s| s.id)
        .collect();
    let to_ids: HashSet<String> = resolve_pattern_symbols(conn, to_pattern)?
        .into_iter()
        .map(|s| s.id)
        .collect();
    let paths = find_symbol_paths(conn, &from_ids, &to_ids, fuzzy, max_depth)?;

    if from_ids.is_empty() || to_ids.is_empty() {
        let mut missing: Vec<String> = Vec::new();
        if from_ids.is_empty() {
            missing.push(format!("no symbols match from pattern '{from_pattern}'"));
        }
        if to_ids.is_empty() {
            missing.push(format!("no symbols match to pattern '{to_pattern}'"));
        }
        return Ok(missing.join("\n"));
    }
    if paths.is_empty() {
        return Ok(format!(
            "no path within {max_depth} hops from '{from_pattern}' to '{to_pattern}'"
        ));
    }
    let mut out: Vec<String> = Vec::new();
    for (i, symbol_path) in paths.iter().take(limit as usize).enumerate() {
        out.push(format!(
            "path {} ({from_pattern} -> {to_pattern}, {} hops):",
            i + 1,
            symbol_path.hops.len()
        ));
        for hop in &symbol_path.hops {
            let line = hop
                .line
                .map(|l| l.to_string())
                .unwrap_or_else(|| "?".to_string());
            out.push(format!(
                "  {}:{line} {} [{}]",
                hop.file, hop.symbol, hop.resolution
            ));
        }
    }
    Ok(out.join("\n"))
}

// --- visualize_graph --------------------------------------------------------

/// One visualize_graph query: scope plus its focal filters.
pub struct VizQuery<'a> {
    pub scope: &'a str,
    pub symbol: &'a str,
    pub module: &'a str,
    pub repo: &'a str,
    pub depth: i64,
    pub format: &'a str,
}

/// `visualize_graph`: a Mermaid/DOT/JSON diagram of a graph scope
/// (symbol|impact|module|repo|deps). JSON output stays banner-free.
pub fn visualize_graph_text(
    conn: &Connection,
    workspace: &Path,
    query: VizQuery<'_>,
) -> Result<String, rusqlite::Error> {
    let VizQuery {
        scope,
        symbol,
        module,
        repo,
        depth,
        format,
    } = query;
    let depth = clamp(depth, 1, 10);
    let graph = match scope {
        "symbol" => get_symbol_graph(conn, symbol)?,
        "impact" => get_impact_graph(conn, symbol, depth)?,
        "module" => get_module_graph(conn, module, false)?,
        "repo" => get_repo_graph(conn, repo)?,
        "deps" => {
            let namespaces = resolve_namespaces(workspace, None);
            get_deps_graph(conn, &namespaces)?
        }
        _ => {
            return Ok(format!(
                "Unknown scope '{scope}'. Available: symbol, impact, module, repo, deps"
            ));
        }
    };
    match format {
        "dot" => Ok(to_dot(&graph)),
        "json" => Ok(to_json(&graph)),
        _ => Ok(to_mermaid(&graph)),
    }
}

// --- cross_repo_deps --------------------------------------------------------

/// `cross_repo_deps`: what the repo depends on and what depends on it.
pub fn cross_repo_deps_text(
    conn: &Connection,
    workspace: &Path,
    repo: &str,
    limit: i64,
) -> Result<String, rusqlite::Error> {
    let limit = clamp(limit, 1, 1000);
    let namespaces = resolve_namespaces(workspace, None);
    let result = cross_repo_deps(conn, repo, &namespaces)?;
    let mut out: Vec<String> = Vec::new();
    out.push(format!("=== {repo} depends on ==="));
    let deps = &result.dependencies;
    if !deps.is_empty() {
        let take = (limit as usize).min(deps.len());
        for d in &deps[..take] {
            out.push(format!("  {} ({}) x{}", d.repo, d.evidence, d.count));
        }
        if deps.len() > limit as usize {
            out.push(format!("  ... and {} more", deps.len() - limit as usize));
        }
    } else {
        out.push("  (none)".to_string());
    }
    out.push(format!("=== Dependents of {repo} ==="));
    let dependents = &result.dependents;
    if !dependents.is_empty() {
        let take = (limit as usize).min(dependents.len());
        for d in &dependents[..take] {
            out.push(format!("  {} x{}", d.repo, d.count));
        }
        if dependents.len() > limit as usize {
            out.push(format!(
                "  ... and {} more",
                dependents.len() - limit as usize
            ));
        }
    } else {
        out.push("  (none)".to_string());
    }
    Ok(out.join("\n"))
}
