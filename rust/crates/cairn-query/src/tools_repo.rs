//! Repository orientation surfaces: `repo_map` and `file_api`.

use std::collections::{BTreeMap, BTreeSet, HashMap};

use rusqlite::Connection;
use serde_json::{Value, json};

use crate::tools_km::ToolError;

const DEFAULT_CLUSTER_CAP: usize = 16;
const DEFAULT_HUB_CAP: usize = 3;
const DEFAULT_HOTSPOT_CAP: usize = 12;
const DOMINANT_SEGMENT_SHARE: f64 = 0.60;

// --- projection --------------------------------------------------------------

#[derive(Clone)]
pub(crate) struct SymbolRow {
    repo: String,
    path: String,
    pub(crate) qualified_name: String,
    pub(crate) incoming: i64,
    pub(crate) outgoing: i64,
}

pub(crate) struct Projection {
    pub(crate) symbols: Vec<SymbolRow>,
    /// Repo ids in sorted order.
    repos: Vec<String>,
    paths: BTreeMap<String, BTreeSet<String>>,
}

pub(crate) fn projection(conn: &Connection) -> Result<Projection, rusqlite::Error> {
    let mut stmt = conn.prepare(
        "SELECT \
            repos.id AS repo_id, \
            files.id AS file_id, \
            files.path AS path, \
            symbols.id AS symbol_id, \
            COALESCE(NULLIF(symbols.qualified_name, ''), symbols.name) \
                AS qualified_name, \
            ( \
                SELECT COUNT(*) \
                FROM edges AS outgoing \
                WHERE outgoing.source_id = symbols.id \
            ) AS outgoing_edges, \
            ( \
                SELECT COUNT(*) \
                FROM edges AS incoming \
                WHERE incoming.target_id = symbols.id \
            ) AS incoming_edges \
        FROM repos \
        LEFT JOIN files ON files.repo_id = repos.id \
        LEFT JOIN symbols ON symbols.file_id = files.id \
        ORDER BY repos.id, files.path, qualified_name, symbols.id",
    )?;
    let rows = stmt.query_map([], |r| {
        Ok((
            r.get::<_, String>(0)?,
            r.get::<_, Option<String>>(1)?,
            r.get::<_, Option<String>>(2)?,
            r.get::<_, Option<String>>(3)?,
            r.get::<_, Option<String>>(4)?,
            r.get::<_, Option<i64>>(5)?,
            r.get::<_, Option<i64>>(6)?,
        ))
    })?;

    let mut repos_set: BTreeSet<String> = BTreeSet::new();
    let mut paths: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    let mut symbols: Vec<SymbolRow> = Vec::new();
    for row in rows {
        let (repo_id, file_id, path, symbol_id, qualified_name, outgoing, incoming) = row?;
        repos_set.insert(repo_id.clone());
        paths.entry(repo_id.clone()).or_default();
        if let (Some(_file_id), Some(path)) = (&file_id, &path) {
            paths
                .get_mut(&repo_id)
                .expect("seeded above")
                .insert(path.clone());
        }
        if symbol_id.is_some() {
            symbols.push(SymbolRow {
                repo: repo_id,
                path: path.unwrap_or_default(),
                qualified_name: qualified_name.unwrap_or_default(),
                incoming: incoming.unwrap_or(0),
                outgoing: outgoing.unwrap_or(0),
            });
        }
    }
    Ok(Projection {
        symbols,
        repos: repos_set.into_iter().collect(),
        paths,
    })
}

// --- clustering --------------------------------------------------------------

fn cluster_path(path: &str, split_dominant_segment: bool) -> String {
    let parts: Vec<&str> = path.split('/').collect();
    if parts.len() == 1 {
        return ".".to_string();
    }
    let depth = if split_dominant_segment && parts.len() > 2 {
        2
    } else {
        1
    };
    parts[..depth].join("/")
}

fn cluster_key(path: &str, dominant_segment: Option<&str>) -> String {
    let is_dominant =
        dominant_segment.is_some_and(|segment| path.starts_with(&format!("{segment}/")));
    cluster_path(path, is_dominant)
}

fn dominant_segment(paths: &[&str]) -> Option<String> {
    if paths.is_empty() {
        return None;
    }
    let mut counts: HashMap<&str, i64> = HashMap::new();
    for path in paths {
        let segment = path.split('/').next().unwrap_or(path);
        *counts.entry(segment).or_insert(0) += 1;
    }
    let (segment, count) = counts
        .into_iter()
        .max_by(|a, b| a.1.cmp(&b.1).then_with(|| a.0.cmp(b.0)))?;
    if (count as f64) / (paths.len() as f64) > DOMINANT_SEGMENT_SHARE {
        Some(segment.to_string())
    } else {
        None
    }
}

/// One rendered cluster record.
struct Cluster {
    path: String,
    files: usize,
    symbols: usize,
    edges: i64,
    hubs: Vec<(String, String, i64)>,
    dropped_hubs: i64,
}

fn clusters(
    symbols: &[SymbolRow],
    paths: &BTreeSet<String>,
    hub_cap: usize,
    cluster_cap: usize,
) -> (Vec<Cluster>, usize) {
    let path_refs: Vec<&str> = paths.iter().map(String::as_str).collect();
    let dominant = dominant_segment(&path_refs);
    let mut grouped: BTreeMap<String, Vec<&SymbolRow>> = BTreeMap::new();
    for symbol in symbols {
        grouped
            .entry(cluster_key(&symbol.path, dominant.as_deref()))
            .or_default()
            .push(symbol);
    }
    let mut file_groups: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for path in paths {
        file_groups
            .entry(cluster_key(path, dominant.as_deref()))
            .or_default()
            .insert(path.clone());
    }

    let mut keys: Vec<&String> = grouped.keys().chain(file_groups.keys()).collect();
    keys.sort_by(|a, b| {
        grouped
            .get(*b)
            .map_or(0, Vec::len)
            .cmp(&grouped.get(*a).map_or(0, Vec::len))
            .then_with(|| a.cmp(b))
    });
    keys.dedup();

    let mut ordered: Vec<Cluster> = Vec::new();
    for key in keys {
        let members = grouped.get(key).cloned().unwrap_or_default();
        let mut hubs: Vec<&SymbolRow> =
            members.iter().filter(|s| s.incoming > 0).copied().collect();
        hubs.sort_by(|a, b| {
            b.incoming
                .cmp(&a.incoming)
                .then_with(|| a.path.cmp(&b.path))
                .then_with(|| a.qualified_name.cmp(&b.qualified_name))
        });
        let hub_count = members.iter().filter(|s| s.incoming > 0).count() as i64;
        ordered.push(Cluster {
            path: key.clone(),
            files: file_groups.get(key).map_or(0, BTreeSet::len),
            symbols: members.len(),
            edges: members.iter().map(|s| s.outgoing).sum(),
            hubs: hubs
                .iter()
                .take(hub_cap)
                .map(|s| (s.qualified_name.clone(), s.path.clone(), s.incoming))
                .collect(),
            dropped_hubs: (hub_count - hub_cap as i64).max(0),
        });
    }
    let dropped = ordered.len().saturating_sub(cluster_cap);
    ordered.truncate(cluster_cap);
    (ordered, dropped)
}

/// One rendered repository scope.
struct RepoScope {
    repo: String,
    clusters: Vec<Cluster>,
    dropped_clusters: usize,
}

struct Hotspot {
    repo: String,
    path: String,
    qualified_name: String,
    incoming: i64,
}

struct RepoMap {
    repos: Vec<RepoScope>,
    hotspots: Vec<Hotspot>,
    dropped_hotspots: i64,
}

fn build_repo_map_capped(
    conn: &Connection,
    cluster_cap: usize,
    hub_cap: usize,
    hotspot_cap: usize,
) -> Result<RepoMap, rusqlite::Error> {
    let proj = projection(conn)?;
    let mut by_repo: BTreeMap<String, Vec<SymbolRow>> = BTreeMap::new();
    for repo in &proj.repos {
        by_repo.insert(repo.clone(), Vec::new());
    }
    for row in proj.symbols.iter().cloned() {
        by_repo.entry(row.repo.clone()).or_default().push(row);
    }

    let mut scopes: Vec<RepoScope> = Vec::new();
    for (repo_id, rows) in &by_repo {
        let empty = BTreeSet::new();
        let paths = proj.paths.get(repo_id).unwrap_or(&empty);
        let (clusters_, dropped_clusters) = clusters(rows, paths, hub_cap, cluster_cap);
        scopes.push(RepoScope {
            repo: repo_id.clone(),
            clusters: clusters_,
            dropped_clusters,
        });
    }

    let mut hotspots: Vec<&SymbolRow> = proj.symbols.iter().filter(|r| r.incoming > 0).collect();
    hotspots.sort_by(|a, b| {
        b.incoming
            .cmp(&a.incoming)
            .then_with(|| a.repo.cmp(&b.repo))
            .then_with(|| a.path.cmp(&b.path))
            .then_with(|| a.qualified_name.cmp(&b.qualified_name))
    });
    let hotspot_count = hotspots.len() as i64;
    Ok(RepoMap {
        repos: scopes,
        hotspots: hotspots
            .iter()
            .take(hotspot_cap)
            .map(|r| Hotspot {
                repo: r.repo.clone(),
                path: r.path.clone(),
                qualified_name: r.qualified_name.clone(),
                incoming: r.incoming,
            })
            .collect(),
        dropped_hotspots: (hotspot_count - hotspot_cap as i64).max(0),
    })
}

fn build_repo_map(conn: &Connection) -> Result<RepoMap, rusqlite::Error> {
    build_repo_map_capped(
        conn,
        DEFAULT_CLUSTER_CAP,
        DEFAULT_HUB_CAP,
        DEFAULT_HOTSPOT_CAP,
    )
}

pub const CLI_DEFAULT_CLUSTER_CAP: usize = DEFAULT_CLUSTER_CAP;
pub const CLI_DEFAULT_HUB_CAP: usize = DEFAULT_HUB_CAP;
pub const CLI_DEFAULT_HOTSPOT_CAP: usize = DEFAULT_HOTSPOT_CAP;

/// The `cairn map` JSON payload (dict shape and key order).
pub fn repo_map_payload(
    conn: &Connection,
    cluster_cap: usize,
    hub_cap: usize,
    hotspot_cap: usize,
) -> Result<Value, rusqlite::Error> {
    let result = build_repo_map_capped(conn, cluster_cap, hub_cap, hotspot_cap)?;
    let mut repos = Vec::new();
    for scope in &result.repos {
        let mut clusters_json = Vec::new();
        for cluster in &scope.clusters {
            clusters_json.push(json!({
                "path": cluster.path,
                "files": cluster.files,
                "symbols": cluster.symbols,
                "edges": cluster.edges,
                "hubs": cluster.hubs.iter().map(|(qualified_name, path, incoming)| json!({
                    "qualified_name": qualified_name,
                    "path": path,
                    "incoming": incoming,
                })).collect::<Vec<_>>(),
                "dropped_hubs": cluster.dropped_hubs,
            }));
        }
        repos.push(json!({
            "repo": scope.repo,
            "clusters": clusters_json,
            "dropped_clusters": scope.dropped_clusters,
        }));
    }
    Ok(json!({
        "repos": repos,
        "hotspots": result.hotspots.iter().map(|h| json!({
            "repo": h.repo,
            "qualified_name": h.qualified_name,
            "path": h.path,
            "incoming": h.incoming,
        })).collect::<Vec<_>>(),
        "dropped_hotspots": result.dropped_hotspots,
    }))
}

/// `repo_map`: deterministic orientation map as bounded prose.
pub fn repo_map_text(conn: &Connection) -> Result<String, rusqlite::Error> {
    let result = build_repo_map(conn)?;
    let mut lines: Vec<String> = Vec::new();
    for scope in &result.repos {
        lines.push(format!("Repository {}", scope.repo));
        for cluster in &scope.clusters {
            lines.push(format!(
                "  {}: {} files, {} symbols, {} edges",
                cluster.path, cluster.files, cluster.symbols, cluster.edges
            ));
            for (qualified_name, path, incoming) in &cluster.hubs {
                lines.push(format!(
                    "    {qualified_name} ({path}, in-degree {incoming})"
                ));
            }
            lines.push(format!("    dropped hubs: {}", cluster.dropped_hubs));
        }
        lines.push(format!("  dropped clusters: {}", scope.dropped_clusters));
    }
    lines.push("Workspace hotspots".to_string());
    for hotspot in &result.hotspots {
        lines.push(format!(
            "  {}:{} {} (in-degree {})",
            hotspot.repo, hotspot.path, hotspot.qualified_name, hotspot.incoming
        ));
    }
    lines.push(format!("  dropped hotspots: {}", result.dropped_hotspots));
    Ok(lines.join("\n"))
}

// --- file_api ----------------------------------------------------------------

fn signature(
    qualified_name: &str,
    parameters: Option<&str>,
    return_type: Option<&str>,
) -> Option<String> {
    let parameters = parameters
        .filter(|p| !p.is_empty())
        .map(|p| p.trim().to_string());
    let return_type = return_type
        .filter(|r| !r.is_empty())
        .map(|r| r.trim().to_string());
    if parameters.is_none() && return_type.is_none() {
        return None;
    }
    let mut sig = qualified_name.to_string();
    if let Some(p) = &parameters {
        sig = format!("{sig}({p})");
    }
    if let Some(r) = &return_type {
        sig = format!("{sig} -> {r}");
    }
    Some(sig)
}

/// One stored symbol in an indexed file.
pub struct FileSymbol {
    pub kind: String,
    pub signature: Option<String>,
    pub line_start: i64,
    pub line_end: i64,
}

/// Every stored symbol for one indexed file in source order.
pub fn file_api(
    conn: &Connection,
    path: &str,
    repo: Option<&str>,
) -> Result<Vec<FileSymbol>, ToolError> {
    let repos: Vec<String> = {
        let mut stmt = conn
            .prepare("SELECT DISTINCT repo_id FROM files WHERE path = ? ORDER BY repo_id")
            .map_err(|e| ToolError(e.to_string()))?;
        let rows = stmt
            .query_map([path], |r| r.get::<_, String>(0))
            .map_err(|e| ToolError(e.to_string()))?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(|e| ToolError(e.to_string()))?
    };
    if repo.is_none() && repos.len() > 1 {
        return Err(ToolError(format!(
            "Path '{}' exists in multiple repositories: {}. Pass repo with one of those ids.",
            path,
            repos.join(", ")
        )));
    }
    if let Some(repo) = repo
        && !repos.is_empty()
        && !repos.iter().any(|r| r == repo)
    {
        return Err(ToolError(format!(
            "Repository '{}' does not index '{}'. Valid repo ids for this path: {}.",
            repo,
            path,
            repos.join(", ")
        )));
    }
    let selected = repo.or_else(|| repos.first().map(String::as_str));
    let mut stmt = conn
        .prepare(
            "SELECT s.kind, s.line_start, s.line_end, s.parameters, s.return_type, \
             COALESCE(NULLIF(s.qualified_name, ''), s.name, '') AS qualified_name \
             FROM symbols AS s \
             JOIN files AS f ON f.id = s.file_id \
             WHERE f.path = ? AND (? IS NULL OR f.repo_id = ?) \
             ORDER BY s.line_start, s.line_end, qualified_name, s.name",
        )
        .map_err(|e| ToolError(e.to_string()))?;
    let rows = stmt
        .query_map(rusqlite::params![path, selected, selected], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, Option<i64>>(1)?,
                r.get::<_, Option<i64>>(2)?,
                r.get::<_, Option<String>>(3)?,
                r.get::<_, Option<String>>(4)?,
                r.get::<_, String>(5)?,
            ))
        })
        .map_err(|e| ToolError(e.to_string()))?;
    let mut out = Vec::new();
    for row in rows {
        let (kind, line_start, line_end, parameters, return_type, qualified_name) =
            row.map_err(|e| ToolError(e.to_string()))?;
        out.push(FileSymbol {
            kind,
            signature: signature(
                &qualified_name,
                parameters.as_deref(),
                return_type.as_deref(),
            ),
            line_start: line_start.unwrap_or(0),
            line_end: line_end.unwrap_or(0),
        });
    }
    Ok(out)
}

/// `file_api`: file symbols with signatures and spans, never bodies.
pub fn file_api_text(
    conn: &Connection,
    path: &str,
    repo: Option<&str>,
) -> Result<String, ToolError> {
    let symbols = file_api(conn, path, repo)?;
    if symbols.is_empty() {
        return Ok(format!("No indexed symbols in '{path}'."));
    }
    let mut lines = vec![format!("{} symbols in '{path}':", symbols.len())];
    for symbol in &symbols {
        let sig = symbol
            .signature
            .as_deref()
            .unwrap_or("signature unavailable");
        lines.push(format!(
            "  {} {} [{}, {}]",
            symbol.kind, sig, symbol.line_start, symbol.line_end
        ));
    }
    Ok(lines.join("\n"))
}
