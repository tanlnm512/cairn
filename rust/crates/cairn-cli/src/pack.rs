//! `cairn pack`: the deterministic token-budgeted context pack (pack.py port).

use std::path::Path;
use std::process::ExitCode;

use clap::Args;
use rusqlite::Connection;
use serde_json::{Map, Value};

use crate::cli_rows;
use crate::py_json;
use crate::store_paths;

const CHARS_PER_TOKEN: usize = 4;
const HEURISTIC_MODE: &str = "heuristic (chars/4)";

const KIND_SOURCE: &str = "source";
const KIND_BLAST_RADIUS: &str = "blast-radius";
const KIND_COMPASS: &str = "compass";
const KIND_MEMORY: &str = "memory";
const REPORT_CLASSES: [&str; 3] = ["symbols", "compass", "memories"];

const SEED_LIMIT: usize = 10;
const SEMANTIC_SEED_LIMIT: usize = 5;
const EXPAND_LIMIT: usize = 8;
const CALL_EDGE_KINDS: [&str; 2] = ["calls", "call"];

#[derive(Args)]
pub struct PackArgs {
    /// Task description to pack context for.
    #[arg(long = "task")]
    pub task: Option<String>,
    /// Token budget for the rendered pack.
    #[arg(long = "budget")]
    pub budget: Option<i64>,
    /// SQLite DB path.
    #[arg(long = "db")]
    pub db: Option<String>,
    /// Knowledge store directory.
    #[arg(long = "knowledge")]
    pub knowledge: Option<String>,
    /// Emit the pack result as JSON.
    #[arg(long = "json")]
    pub as_json: bool,
}

/// One candidate symbol in the pipeline's pool.
#[derive(Clone, Debug)]
struct PackSymbol {
    symbol_id: String,
    name: String,
    qualified_name: String,
    kind: String,
    file_path: String,
    is_seed: bool,
}

/// One rendered content block, costed for budget fitting.
#[derive(Clone, Debug)]
struct PackItem {
    kind: &'static str,
    rank: usize,
    text: String,
    cost: usize,
}

impl PackItem {
    fn create(kind: &'static str, rank: usize, text: String) -> Self {
        Self {
            kind,
            rank,
            cost: item_cost(&text),
            text,
        }
    }
}

/// The pipeline output: ranked pool, fitted items, per-class dropped counts.
struct PackResult {
    task: String,
    budget: i64,
    symbols: Vec<PackSymbol>,
    items: Vec<PackItem>,
    dropped: Vec<(String, usize)>,
}

/// Conservative token cost for one rendered block (heuristic ceil(chars/4)).
fn item_cost(text: &str) -> usize {
    let chars = text.chars().count();
    chars.div_ceil(CHARS_PER_TOKEN)
}

fn class_of(kind: &str) -> &'static str {
    match kind {
        KIND_COMPASS => "compass",
        KIND_MEMORY => "memories",
        _ => "symbols",
    }
}

fn class_counts(items: &[&PackItem]) -> Vec<(String, usize)> {
    REPORT_CLASSES
        .iter()
        .map(|cls| {
            (
                cls.to_string(),
                items
                    .iter()
                    .filter(|item| class_of(item.kind) == *cls)
                    .count(),
            )
        })
        .collect()
}

/// Whether the embeddings table holds rows for the current model.
fn embeddings_present(conn: &Connection, model: &str) -> bool {
    conn.query_row(
        "SELECT 1 FROM embeddings WHERE model = ? LIMIT 1",
        [model],
        |_| Ok(()),
    )
    .is_ok()
}

/// Seed candidates: semantic head + lexical tail, or lexical-only.
fn seed_symbols(conn: &Connection, task: &str, model: &str, limit: usize) -> Vec<PackSymbol> {
    let mut hits: Vec<cli_rows::PyRow> = Vec::new();
    if embeddings_present(conn, model) {
        let head_limit = SEMANTIC_SEED_LIMIT.min(limit);
        let env = cairn_query::tools_search::semantic_env(Some(false));
        for m in cairn_query::tools_search::semantic_search(conn, task, head_limit, 0.3, &env) {
            let mut row = Map::new();
            row.insert("id".into(), Value::from(m.id));
            row.insert("name".into(), Value::from(m.name));
            row.insert(
                "qualified_name".into(),
                m.qualified_name.map(Value::from).unwrap_or(Value::Null),
            );
            row.insert("kind".into(), Value::from(m.kind));
            row.insert("file_path".into(), Value::from(m.file_path));
            hits.push(row);
        }
        let terms: Vec<&str> = task.split_whitespace().collect();
        hits.extend(cli_rows::search_symbol_rows_terms(
            conn,
            &terms,
            None,
            limit as i64,
        ));
    } else {
        hits = cli_rows::search_symbol_rows(conn, task, None, limit as i64);
    }
    let mut seeds: Vec<PackSymbol> = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for hit in &hits {
        if seeds.len() >= limit {
            break;
        }
        let Some(symbol_id) = hit.get("id").and_then(Value::as_str) else {
            continue;
        };
        if !seen.insert(symbol_id.to_string()) {
            continue;
        }
        seeds.push(PackSymbol {
            symbol_id: symbol_id.to_string(),
            name: hit
                .get("name")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string(),
            qualified_name: hit
                .get("qualified_name")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string(),
            kind: hit
                .get("kind")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string(),
            file_path: hit
                .get("file_path")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string(),
            is_seed: true,
        });
    }
    seeds
}

/// Precise 1-hop call-edge neighbor ids for one seed, deduped, cap per direction.
fn hop_ids(conn: &Connection, seed: &PackSymbol) -> Vec<String> {
    let mut ids: Vec<String> = Vec::new();
    for (fetch_callees, field) in [(false, "caller_id"), (true, "resolved")] {
        let mut filled = 0usize;
        for kind in CALL_EDGE_KINDS {
            if filled >= EXPAND_LIMIT {
                break;
            }
            let remaining = (EXPAND_LIMIT - filled) as i64;
            let rows = if fetch_callees {
                cli_rows::get_callees_rows(
                    conn,
                    &seed.name,
                    remaining,
                    false,
                    Some(kind),
                    Some(&seed.symbol_id),
                )
            } else {
                cli_rows::get_callers_rows(
                    conn,
                    &seed.name,
                    remaining,
                    false,
                    Some(kind),
                    Some(&seed.symbol_id),
                )
            };
            let Ok(rows) = rows else { continue };
            filled += rows.len();
            for row in rows {
                let Some(neighbor) = row.get(field).and_then(Value::as_str) else {
                    continue;
                };
                if !neighbor.is_empty() && !ids.iter().any(|id| id == neighbor) {
                    ids.push(neighbor.to_string());
                }
            }
        }
    }
    ids
}

/// Union seeds with their precise 1-hop call-edge neighbors.
fn expand_symbols(conn: &Connection, seeds: Vec<PackSymbol>) -> Vec<PackSymbol> {
    let mut pool: Vec<PackSymbol> = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for seed in &seeds {
        if seen.insert(seed.symbol_id.clone()) {
            pool.push(seed.clone());
        }
    }
    for seed in &seeds {
        for neighbor_id in hop_ids(conn, seed) {
            if seen.contains(&neighbor_id) {
                continue;
            }
            let Some(row) = cli_rows::find_definition_by_id_row(conn, &neighbor_id) else {
                continue;
            };
            seen.insert(neighbor_id);
            pool.push(PackSymbol {
                symbol_id: row
                    .get("id")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string(),
                name: row
                    .get("name")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string(),
                qualified_name: row
                    .get("qualified_name")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string(),
                kind: row
                    .get("kind")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string(),
                file_path: row
                    .get("file_path")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string(),
                is_seed: false,
            });
        }
    }
    pool
}

fn closure_available(conn: &Connection) -> bool {
    conn.query_row("SELECT 1 FROM transitive_edges LIMIT 1", [], |_| Ok(()))
        .is_ok()
}

fn centrality(conn: &Connection, symbol_id: &str) -> i64 {
    if closure_available(conn) {
        return conn
            .query_row(
                "SELECT COUNT(DISTINCT source_id) FROM transitive_edges WHERE target_id = ?",
                [symbol_id],
                |row| row.get::<_, i64>(0),
            )
            .unwrap_or(0);
    }
    structural_indegree(conn, symbol_id)
}

fn structural_indegree(conn: &Connection, symbol_id: &str) -> i64 {
    conn.query_row(
        "SELECT COUNT(*) FROM edges \
         WHERE target_id = ? AND kind IN ('calls','call','extends','implements')",
        [symbol_id],
        |row| row.get::<_, i64>(0),
    )
    .unwrap_or(0)
}

/// Seeds first, centrality descending, qualified-name tiebreak.
fn rank_symbols(conn: &Connection, mut pool: Vec<PackSymbol>) -> Vec<PackSymbol> {
    let weights: std::collections::HashMap<String, i64> = pool
        .iter()
        .map(|s| (s.symbol_id.clone(), centrality(conn, &s.symbol_id)))
        .collect();
    pool.sort_by(|a, b| {
        let key = |s: &PackSymbol| {
            (
                u8::from(!s.is_seed),
                std::cmp::Reverse(weights.get(&s.symbol_id).copied().unwrap_or(0)),
                s.qualified_name.clone(),
            )
        };
        key(a).cmp(&key(b))
    });
    pool
}

/// Verbatim source for one symbol, trimmed to the cap with a cut marker.
fn render_source(conn: &Connection, symbol_id: &str, line_cap: usize) -> String {
    let Some(row) = cli_rows::find_definition_by_id_row(conn, symbol_id) else {
        return String::new();
    };
    let ls = row.get("line_start").and_then(Value::as_i64).unwrap_or(0);
    let le = row.get("line_end").and_then(Value::as_i64).unwrap_or(ls);
    if ls < 1 || le < ls {
        return String::new();
    }
    let workspace = store_paths::resolve_workspace(None);
    let repo = row.get("repo").and_then(Value::as_str).unwrap_or_default();
    let stored = row
        .get("file_path")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let abs_path = if Path::new(stored).is_absolute() {
        std::path::PathBuf::from(stored)
    } else {
        cairn_build::scanner::resolve_repo_path(&workspace.to_string_lossy(), repo)
            .map(|root| root.join(stored))
            .unwrap_or_else(|_| std::path::PathBuf::from(stored))
    };
    let Ok(content) = std::fs::read_to_string(&abs_path) else {
        return String::new();
    };
    let all_lines: Vec<&str> = content.split('\n').collect();
    let start = (ls - 1) as usize;
    let end = (le as usize).min(all_lines.len());
    if start >= all_lines.len() {
        return String::new();
    }
    let span = &all_lines[start..end];
    let cap = line_cap.max(1);
    if span.len() <= cap {
        return span.join("\n");
    }
    let mut trimmed: Vec<String> = span[..cap].iter().map(|l| l.to_string()).collect();
    trimmed.push(format!("... (+{} more lines trimmed)", span.len() - cap));
    trimmed.join("\n")
}

/// One-line depth-2 precise blast-radius summary for one symbol.
fn blast_radius_line(
    conn: &Connection,
    qualified_name: &str,
    symbol_id: &str,
    limit: usize,
) -> String {
    let blast =
        cairn_query::graph::impact_analysis_seeded(conn, qualified_name, symbol_id, 2, limit)
            .unwrap_or_default();
    let mut by_depth = [0usize; 3];
    let mut top: Vec<String> = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for entry in &blast.impacted {
        if (0..=2).contains(&entry.depth) {
            by_depth[entry.depth as usize] += 1;
        }
        if !entry.symbol.is_empty() && seen.insert(entry.symbol.clone()) && top.len() < 5 {
            top.push(entry.symbol.clone());
        }
    }
    let mut line = format!(
        "blast radius (depth<=2, precise): total={}; depth 0: {}, depth 1: {}, depth 2: {}",
        blast.impacted.len(),
        by_depth[0],
        by_depth[1],
        by_depth[2]
    );
    if !top.is_empty() {
        line.push_str("; top: ");
        line.push_str(&top.join(", "));
    }
    if blast.truncated {
        line.push_str("; truncated");
    }
    line
}

/// Compass navigation guide covering `file_path`, or None.
fn compass_excerpt(bundle: &cairn_query::tools_km::Bundle, file_path: &str) -> Option<String> {
    for cid in bundle.list_concepts(Some("compass/")) {
        let Ok(concept) = bundle.read_concept(&cid) else {
            continue;
        };
        let resource = concept.resource.clone().unwrap_or_default();
        if !resource.is_empty() && (file_path.contains(&resource) || resource.contains(file_path)) {
            return Some(format!(
                "# {}\n\n{}",
                concept.title.clone().unwrap_or(cid),
                concept.body
            ));
        }
    }
    None
}

/// Top-k task-relevant memories as title-plus-description strings.
fn memory_picks(
    conn: &Connection,
    bundle: &cairn_query::tools_km::Bundle,
    task: &str,
    k: usize,
) -> Vec<String> {
    let Ok(concepts) =
        cairn_query::tools_km::search_memory(conn, bundle, task, None, None, false, None)
    else {
        return vec![];
    };
    concepts
        .into_iter()
        .take(k)
        .map(|concept| {
            let mut pick = concept
                .title
                .clone()
                .unwrap_or_else(|| concept.concept_id.clone());
            if let Some(description) = &concept.description {
                pick.push('\n');
                pick.push_str(description);
            }
            pick
        })
        .collect()
}

/// One enriched block per pool symbol, in rank order, pre-costed.
fn symbol_items(conn: &Connection, pool: &[PackSymbol]) -> Vec<PackItem> {
    let mut items = Vec::new();
    for (index, symbol) in pool.iter().enumerate() {
        let position = index + 1;
        let mut lines = vec![
            String::new(),
            format!(
                "## {}. {}",
                position,
                if symbol.qualified_name.is_empty() {
                    &symbol.name
                } else {
                    &symbol.qualified_name
                }
            ),
            String::new(),
            format!("- kind: {}", symbol.kind),
            format!("- file: {}", symbol.file_path),
            format!("- seed: {}", if symbol.is_seed { "true" } else { "false" }),
        ];
        let source = render_source(conn, &symbol.symbol_id, 40);
        if !source.is_empty() {
            lines.push(String::new());
            lines.push(source);
        }
        lines.push(String::new());
        lines.push(format!(
            "- {}",
            blast_radius_line(conn, &symbol.qualified_name, &symbol.symbol_id, 100)
        ));
        items.push(PackItem::create(KIND_SOURCE, position, lines.join("\n")));
    }
    items
}

/// Task-level compass and memory sections, pre-costed for reserved-first admission.
fn reserved_items(
    conn: &Connection,
    bundle: &cairn_query::tools_km::Bundle,
    pool: &[PackSymbol],
    task: &str,
) -> Vec<PackItem> {
    let mut items = Vec::new();
    let mut excerpts: Vec<String> = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for symbol in pool {
        let Some(excerpt) = compass_excerpt(bundle, &symbol.file_path) else {
            continue;
        };
        if !seen.insert(excerpt.clone()) {
            continue;
        }
        excerpts.push(excerpt);
    }
    if !excerpts.is_empty() {
        let mut parts = vec![String::new(), "## Compass".to_string()];
        parts.extend(excerpts);
        items.push(PackItem::create(KIND_COMPASS, 0, parts.join("\n\n")));
    }
    let picks = memory_picks(conn, bundle, task, 3);
    if !picks.is_empty() {
        let mut parts = vec![String::new(), "## Memories".to_string()];
        parts.extend(picks);
        items.push(PackItem::create(KIND_MEMORY, 0, parts.join("\n\n")));
    }
    items
}

/// The pack header; `tokens` is filled in by the accounting loop.
fn header_lines(
    task: &str,
    budget: i64,
    mode: &str,
    tokens: usize,
    kept: &[(String, usize)],
    dropped: &[(String, usize)],
) -> Vec<String> {
    let fragment = |counts: &[(String, usize)]| {
        counts
            .iter()
            .map(|(cls, n)| format!("{cls}={n}"))
            .collect::<Vec<_>>()
            .join(" ")
    };
    vec![
        "# cairn context pack".to_string(),
        String::new(),
        format!("- task: {task}"),
        format!("- budget: {budget}"),
        format!("- tokenizer: {mode}"),
        format!("- tokens: {tokens}"),
        format!("- kept: {}", fragment(kept)),
        format!("- dropped: {}", fragment(dropped)),
    ]
}

/// Fit rendered blocks into the budget past the header reserve.
fn fit_items(
    items: Vec<PackItem>,
    budget: i64,
    header_reserve: usize,
) -> (Vec<PackItem>, Vec<(String, usize)>) {
    let mut remaining = budget - header_reserve as i64;
    let mut ranked: Vec<PackItem> = Vec::new();
    let mut reserved: Vec<PackItem> = Vec::new();
    let item_refs: Vec<&PackItem> = items.iter().collect();
    let totals = class_counts(&item_refs);
    for item in &items {
        if (item.kind == KIND_COMPASS || item.kind == KIND_MEMORY) && item.cost as i64 <= remaining
        {
            reserved.push(item.clone());
            remaining -= item.cost as i64;
        }
    }
    for item in &items {
        if item.kind == KIND_SOURCE || item.kind == KIND_BLAST_RADIUS {
            if item.cost as i64 > remaining {
                break;
            }
            ranked.push(item.clone());
            remaining -= item.cost as i64;
        }
    }
    let mut kept = ranked;
    kept.extend(reserved);
    let kept_refs: Vec<&PackItem> = kept.iter().collect();
    let kept_counts = class_counts(&kept_refs);
    let dropped = totals
        .iter()
        .zip(&kept_counts)
        .map(|((cls, total), (_, count))| (cls.clone(), total - count))
        .collect();
    (kept, dropped)
}

/// Render the pack as one stdout-safe markdown block (fixed-point token count).
fn render_pack(result: &PackResult) -> String {
    let task = result.task.split_whitespace().collect::<Vec<_>>().join(" ");
    let item_refs: Vec<&PackItem> = result.items.iter().collect();
    let kept = class_counts(&item_refs);
    let mut tokens = 0usize;
    loop {
        let mut lines = header_lines(
            &task,
            result.budget,
            HEURISTIC_MODE,
            tokens,
            &kept,
            &result.dropped,
        );
        for item in &result.items {
            lines.extend(item.text.split('\n').map(String::from));
        }
        let block = lines.join("\n") + "\n";
        let counted = item_cost(&block);
        if counted == tokens {
            return block;
        }
        tokens = counted;
    }
}

/// pack handler: seed, expand, rank, enrich, fit, emit.
pub fn run(args: PackArgs) -> ExitCode {
    let task = args.task.unwrap_or_default();
    if task.trim().is_empty() {
        return crate::cmds_core::usage_error(
            "--task is required: the task description must not be empty",
        );
    }
    let budget = args.budget.unwrap_or(0);
    if budget <= 0 {
        return crate::cmds_core::usage_error(&format!(
            "--budget must be a positive token count, got {budget}"
        ));
    }
    let db = args
        .db
        .clone()
        .unwrap_or_else(crate::cmds_core::default_db_path);
    let knowledge = args.knowledge.clone().unwrap_or_else(|| {
        Path::new(&db)
            .parent()
            .map(|parent| parent.join(".knowledge").to_string_lossy().into_owned())
            .unwrap_or_else(|| ".knowledge".to_string())
    });
    let Ok(conn) = cairn_store::conn::open_read_only(Path::new(&db)) else {
        return crate::cmds_core::command_error(&format!("cannot open {db}"));
    };
    let bundle = cairn_query::tools_km::Bundle::new(&knowledge);

    let model = cairn_query::tools_search::semantic_env(None).model;
    let pool = rank_symbols(
        &conn,
        expand_symbols(&conn, seed_symbols(&conn, task.trim(), &model, SEED_LIMIT)),
    );
    let mut items = symbol_items(&conn, &pool);
    items.extend(reserved_items(&conn, &bundle, &pool, task.trim()));
    let item_refs: Vec<&PackItem> = items.iter().collect();
    let totals = class_counts(&item_refs);
    let header_reserve = {
        let lines = header_lines(
            task.trim(),
            budget,
            HEURISTIC_MODE,
            budget as usize,
            &totals,
            &totals,
        );
        item_cost(&(lines.join("\n") + "\n"))
    };
    let (kept, dropped) = fit_items(items, budget, header_reserve);
    let result = PackResult {
        task: task.trim().to_string(),
        budget,
        symbols: pool,
        items: kept,
        dropped,
    };

    if args.as_json {
        let mut object = Map::new();
        object.insert("task".into(), Value::from(result.task.clone()));
        object.insert("budget".into(), Value::from(result.budget));
        object.insert(
            "symbols".into(),
            Value::Array(
                result
                    .symbols
                    .iter()
                    .map(|s| {
                        serde_json::json!({
                            "symbol_id": s.symbol_id,
                            "name": s.name,
                            "qualified_name": s.qualified_name,
                            "kind": s.kind,
                            "file_path": s.file_path,
                            "is_seed": s.is_seed,
                        })
                    })
                    .collect(),
            ),
        );
        object.insert(
            "items".into(),
            Value::Array(
                result
                    .items
                    .iter()
                    .map(|item| {
                        serde_json::json!({
                            "kind": item.kind,
                            "rank": item.rank,
                            "text": item.text,
                            "cost": item.cost,
                        })
                    })
                    .collect(),
            ),
        );
        let mut dropped_map = Map::new();
        for (cls, count) in &result.dropped {
            dropped_map.insert(cls.clone(), Value::from(*count as i64));
        }
        object.insert("dropped".into(), Value::Object(dropped_map));
        println!("{}", py_json::dumps(&Value::Object(object)));
        return ExitCode::SUCCESS;
    }
    print!("{}", render_pack(&result));
    ExitCode::SUCCESS
}
