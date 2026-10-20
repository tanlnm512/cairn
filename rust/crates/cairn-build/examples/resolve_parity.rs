//! Parity oracle side: replay captured resolver signals into a store copy and print stats.

use std::path::PathBuf;

use serde::Deserialize;
use serde_json::json;

use cairn_build::resolver::{EdgeSignal, Stats, resolve_repo_edges};

#[derive(Deserialize)]
struct Signals {
    repos: Vec<RepoSignals>,
}

#[derive(Deserialize)]
struct RepoSignals {
    repo: String,
    files: Vec<FileSignals>,
}

#[derive(Deserialize)]
struct FileSignals {
    file_id: String,
    edges: Vec<JsonEdge>,
}

#[derive(Deserialize)]
struct JsonEdge {
    edge_id: String,
    target_name: String,
    receiver_type: Option<String>,
    call_arity: Option<i64>,
    generic_tier: bool,
}

impl From<&JsonEdge> for EdgeSignal {
    fn from(e: &JsonEdge) -> Self {
        EdgeSignal {
            edge_id: e.edge_id.clone(),
            target_name: e.target_name.clone(),
            receiver_type: e.receiver_type.clone(),
            call_arity: e.call_arity,
            generic_tier: e.generic_tier,
        }
    }
}

fn stats_json(stats: &Stats) -> serde_json::Value {
    json!({
        "exact": stats.exact,
        "ambiguous": stats.ambiguous,
        "unresolved": stats.unresolved,
    })
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = std::env::args().collect();
    if args.len() != 3 {
        return Err("usage: resolve_parity <store.kg> <signals.json>".into());
    }
    let db = PathBuf::from(&args[1]);
    let signals: Signals = serde_json::from_str(&std::fs::read_to_string(&args[2])?)?;

    let mut conn = cairn_store::conn::open_writable(&db)?;
    let mut total = Stats::default();
    let mut repos = Vec::new();
    for repo_signals in &signals.repos {
        let edges_by_file: Vec<(String, Vec<EdgeSignal>)> = repo_signals
            .files
            .iter()
            .map(|f| {
                (
                    f.file_id.clone(),
                    f.edges.iter().map(EdgeSignal::from).collect(),
                )
            })
            .collect();
        let stats = resolve_repo_edges(&mut conn, &repo_signals.repo, &edges_by_file)?;
        total.exact += stats.exact;
        total.ambiguous += stats.ambiguous;
        total.unresolved += stats.unresolved;
        repos.push(json!({
            "repo": repo_signals.repo,
            "edges": stats.exact + stats.ambiguous + stats.unresolved,
            "stats": stats_json(&stats),
        }));
    }
    println!("{}", json!({"repos": repos, "total": stats_json(&total)}));
    Ok(())
}
