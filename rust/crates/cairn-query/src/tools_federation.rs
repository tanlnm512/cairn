//! Federated search across every registered workspace store (federation.py port).

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use rusqlite::Connection;

use crate::tools_search::{
    EffectiveBackend, SemanticMatch, SymbolRow, cairn_home, rrf_fuse, search_symbols, semantic_env,
    semantic_search,
};

/// One aggregated federated outcome.
struct FederatedResult {
    /// (hit, owning workspace path) — attribution rides beside the match.
    hits: Vec<(SemanticMatch, String)>,
    dropped: Vec<String>,
    states: Vec<(String, String)>,
}

/// `(workspace_path, store_key)` pairs, workspace-sorted (registry.py port).
fn iter_stores() -> Vec<(String, String)> {
    let Some(home) = cairn_home() else {
        return vec![];
    };
    let Ok(text) = std::fs::read_to_string(home.join("workspaces.json")) else {
        return vec![];
    };
    let Ok(parsed) = serde_json::from_str::<serde_json::Value>(&text) else {
        return vec![];
    };
    let Some(map) = parsed.as_object() else {
        return vec![];
    };
    let mut pairs: Vec<(String, String)> = map
        .iter()
        .filter_map(|(ws, key)| {
            let key = key.as_str()?;
            Some((ws.clone(), key.to_string()))
        })
        .collect();
    pairs.sort_by(|a, b| a.0.cmp(&b.0));
    pairs
}

/// `missing` / `unindexed` / `ok` from the filesystem alone.
fn classify_store(home: &Path, db: &Path) -> &'static str {
    if !home.is_dir() {
        return "missing";
    }
    if !db.is_file() {
        return "unindexed";
    }
    "ok"
}

/// One `search_symbols` row shaped like a semantic_search bm25 hit.
fn lexical_hit(row: &SymbolRow) -> SemanticMatch {
    SemanticMatch {
        id: row.id.clone(),
        kind: row.kind.clone(),
        name: row.name.clone(),
        qualified_name: row.qualified_name.clone(),
        file_path: row.file_path.clone(),
        repo: row.repo.clone(),
        score: 0.0,
        provenance: "bm25".to_string(),
        reranked: false,
        chunk: String::new(),
    }
}

/// One store's ranked hits: semantic search, or BM25-only when the store has
/// no embedding rows for the current model.
fn search_store(db: &Path, query: &str, limit: usize) -> Result<Vec<SemanticMatch>, String> {
    let conn = Connection::open_with_flags(db, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)
        .map_err(|e| e.to_string())?;
    let env = semantic_env(None);
    if env.backend == EffectiveBackend::Unavailable {
        return Err("embedding backend unavailable".to_string());
    }
    let has_embeddings: bool = conn
        .query_row(
            "SELECT 1 FROM embeddings WHERE model = ? LIMIT 1",
            [&env.model],
            |_| Ok(()),
        )
        .is_ok();
    if has_embeddings {
        return Ok(semantic_search(&conn, query, limit, 0.3, &env));
    }
    let rows = search_symbols(&conn, query, None, limit as i64).map_err(|e| e.to_string())?;
    Ok(rows.iter().map(lexical_hit).collect())
}

fn federated_search(query: &str, limit: usize) -> Result<FederatedResult, String> {
    let Some(home) = cairn_home() else {
        return Ok(FederatedResult {
            hits: vec![],
            dropped: vec![],
            states: vec![],
        });
    };
    let mut rankings: Vec<Vec<String>> = Vec::new();
    let mut hits_by_doc: HashMap<String, (SemanticMatch, String)> = HashMap::new();
    let mut result = FederatedResult {
        hits: vec![],
        dropped: vec![],
        states: vec![],
    };
    for (ws_path, key) in iter_stores() {
        let store_home: PathBuf = home.join(&key);
        let db = store_home.join(".kg");
        let state = classify_store(&store_home, &db);
        if state != "ok" {
            result.states.push((ws_path.clone(), state.to_string()));
            result.dropped.push(ws_path);
            continue;
        }
        let store_hits = match search_store(&db, query, limit) {
            Ok(hits) => hits,
            Err(_) => {
                result.states.push((ws_path.clone(), "error".to_string()));
                result.dropped.push(ws_path);
                continue;
            }
        };
        result.states.push((ws_path.clone(), "ok".to_string()));
        let mut ranking = Vec::new();
        for hit in store_hits {
            let dir_name = store_home
                .file_name()
                .map(|n| n.to_string_lossy().to_string())
                .unwrap_or_default();
            let doc_id = format!("{dir_name}:{}", hit.id);
            ranking.push(doc_id.clone());
            hits_by_doc.insert(doc_id, (hit, ws_path.clone()));
        }
        rankings.push(ranking);
    }
    for (doc_id, fused_score) in rrf_fuse(&rankings, 60) {
        if let Some((hit, workspace)) = hits_by_doc.get_mut(&doc_id) {
            let mut hit = hit.clone();
            hit.score = (fused_score * 10_000.0).round() / 10_000.0;
            result.hits.push((hit, workspace.clone()));
        }
    }
    result.hits.truncate(limit);
    Ok(result)
}

/// `federated_search`: rank-fused hits attributed per workspace; unavailable
/// stores are named, never fatal.
pub fn federated_search_text(query: &str, limit: i64) -> Result<String, String> {
    let limit = limit.clamp(1, 1000) as usize;
    if query.trim().is_empty() {
        return Ok(
            "Query must not be empty. Pass a natural-language phrase, e.g. \
             federated_search(\"where do we handle retries\")."
                .to_string(),
        );
    }
    let result = federated_search(query, limit)?;
    let ok_count = result
        .states
        .iter()
        .filter(|(_, state)| state == "ok")
        .count();
    let mut out = vec![format!(
        "=== federated_search: \"{query}\" ({} hit(s) from {ok_count}/{} store(s)) ===",
        result.hits.len(),
        result.states.len()
    )];
    for (hit, workspace) in &result.hits {
        let short = hit.file_path.rsplit('/').next().unwrap_or("");
        out.push(format!(
            "  [{} {:.4}] {} {}  ({short})  [{}]  @ {workspace}",
            hit.provenance,
            hit.score,
            hit.kind,
            hit.qualified_name.as_deref().unwrap_or(&hit.name),
            hit.repo,
        ));
    }
    if result.states.is_empty() {
        out.push(String::new());
        out.push(
            "No workspace stores are registered. Run `cairn init` once inside \
             each workspace to register its store, then retry."
                .to_string(),
        );
    } else {
        if !result.dropped.is_empty() {
            out.push(String::new());
            out.push(format!(
                "Unavailable stores ({}) -- named, not searched:",
                result.dropped.len()
            ));
            for ws in &result.dropped {
                let state = result
                    .states
                    .iter()
                    .find(|(w, _)| w == ws)
                    .map(|(_, s)| s.as_str())
                    .unwrap_or("unknown");
                out.push(format!("  {ws} ({state})"));
            }
        }
        if result.hits.is_empty() {
            out.push(String::new());
            out.push(
                "No matches across the searched stores. Try search_symbols on \
                 the specific store for a lexical match, or rephrase with \
                 more specific terms."
                    .to_string(),
            );
        }
    }
    Ok(out.join("\n"))
}
