//! Seeded Louvain communities over the store's structural edges (D-R01).

use std::collections::HashMap;

use rusqlite::Connection;

use crate::closure::{self, Graph};

/// Structural edge weights per closure::STRUCTURAL_EDGE_KINDS slot.
const STRUCTURAL_EDGE_WEIGHTS: [f64; closure::STRUCTURAL_EDGE_KINDS.len()] = [1.0, 1.0, 2.0, 2.0];
const _: () = assert!(closure::STRUCTURAL_EDGE_KINDS.len() == STRUCTURAL_EDGE_WEIGHTS.len());

/// Dominant first-path-segment share a community label needs.
const DOMINANT_SEGMENT_SHARE: f64 = 0.60;
const HUB_LABEL_PREFIX: &str = "hub:";
/// Modularity gain below which a level counts as converged (float tie-cycle guard).
const LEVEL_GAIN_EPS: f64 = 1e-12;

/// Hub summary row: member qualified name and structural degree.
pub struct HubRow {
    pub qualified_name: String,
    pub structural_degree: i64,
}

/// One computed community: label plus members in persisted order.
struct CommunityRecord {
    label: String,
    members: Vec<(String, i64)>,
}

/// A partition member: symbol attrs in sorted (qualified_name, id) node order.
struct Member {
    id: String,
    qualified_name: Option<String>,
    path: String,
    degree: i64,
}

/// The structural projection Louvain runs over: sorted members and weighted
/// undirected pairs sorted by (min node, max node).
pub(crate) struct Projection {
    members: Vec<Member>,
    edges: Vec<(usize, usize, f64)>,
}

/// Full-refresh the communities tables from a seeded Louvain partition; returns the community count.
pub fn refresh_communities(conn: &mut Connection) -> Result<usize, String> {
    let graph = closure::load_graph(conn)?;
    let projection = load_projection(conn, &graph)?;
    let labels = louvain_partition(projection.members.len(), &projection.edges);
    let communities = describe(&projection, &labels);

    let tx = conn.transaction().map_err(str_err)?;
    tx.execute("DELETE FROM symbol_communities", [])
        .map_err(str_err)?;
    tx.execute("DELETE FROM communities", []).map_err(str_err)?;
    {
        let mut community_stmt = tx
            .prepare("INSERT INTO communities (id, label, size) VALUES (?1, ?2, ?3)")
            .map_err(str_err)?;
        let mut member_stmt = tx
            .prepare(
                "INSERT INTO symbol_communities (community_id, symbol_id, structural_degree) \
                 VALUES (?1, ?2, ?3)",
            )
            .map_err(str_err)?;
        for (idx, community) in communities.iter().enumerate() {
            let cid = (idx + 1) as i64;
            community_stmt
                .execute(rusqlite::params![
                    cid,
                    community.label,
                    community.members.len() as i64
                ])
                .map_err(str_err)?;
            for (symbol_id, degree) in &community.members {
                member_stmt
                    .execute(rusqlite::params![cid, symbol_id, degree])
                    .map_err(str_err)?;
            }
        }
    }
    tx.commit().map_err(str_err)?;
    Ok(communities.len())
}

/// Top-K hubs by (-degree, path, qualified_name, symbol_id); all communities when `community_id` is None.
pub fn hub_rows(
    conn: &Connection,
    community_id: Option<i64>,
    top_k: usize,
) -> Result<Vec<HubRow>, String> {
    let base = "SELECT s.qualified_name, sc.structural_degree \
                FROM symbol_communities sc \
                JOIN symbols s ON s.id = sc.symbol_id \
                JOIN files f ON f.id = s.file_id ";
    let sql = match community_id {
        Some(_) => format!(
            "{base}WHERE sc.community_id = ?1 \
                            ORDER BY sc.structural_degree DESC, f.path, s.qualified_name, sc.symbol_id \
                            LIMIT ?2"
        ),
        None => format!(
            "{base}ORDER BY sc.structural_degree DESC, f.path, s.qualified_name, sc.symbol_id \
                         LIMIT ?1"
        ),
    };
    let mut stmt = conn.prepare(&sql).map_err(str_err)?;
    let map = |row: &rusqlite::Row<'_>| {
        Ok(HubRow {
            qualified_name: row.get::<_, Option<String>>(0)?.unwrap_or_default(),
            structural_degree: row.get(1)?,
        })
    };
    let rows = match community_id {
        Some(cid) => stmt.query_map(rusqlite::params![cid, top_k as i64], map),
        None => stmt.query_map(rusqlite::params![top_k as i64], map),
    }
    .map_err(str_err)?;
    rows.map(|r| r.map_err(str_err)).collect()
}

/// Persisted communities in id order: (id, label, size).
pub fn community_rows(conn: &Connection) -> Result<Vec<(i64, String, i64)>, String> {
    let mut stmt = conn
        .prepare("SELECT id, label, size FROM communities ORDER BY id")
        .map_err(str_err)?;
    let rows = stmt
        .query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))
        .map_err(str_err)?;
    rows.map(|r| r.map_err(str_err)).collect()
}

/// The structural projection over the store's compact graph: endpoints as
/// sorted members, per-endpoint structural degrees, and the undirected
/// weighted pair list (mirrored edges summed, weights per kind).
pub(crate) fn load_projection(conn: &Connection, graph: &Graph) -> Result<Projection, String> {
    let id_count = graph.ids.len();

    // Structural degree per endpoint: every row counts its source; a row with
    // a resolved target counts that target too.
    let mut source_degree = vec![0i64; id_count];
    let mut target_degree = vec![0i64; id_count];
    for (id, degree_slot) in source_degree.iter_mut().enumerate() {
        let adjacency = graph.adjacency(id as u32);
        *degree_slot = adjacency.len() as i64;
        for entry in adjacency {
            if let Some(target) = closure::entry_target(*entry) {
                target_degree[target as usize] += 1;
            }
        }
    }
    let slot_of: HashMap<&str, usize> = graph
        .ids
        .iter()
        .enumerate()
        .map(|(slot, id)| (id.as_str(), slot))
        .collect();

    let mut members: Vec<Member> = Vec::new();
    {
        let mut stmt = conn
            .prepare(
                "SELECT s.id, s.qualified_name, f.path \
                 FROM symbols s JOIN files f ON f.id = s.file_id",
            )
            .map_err(str_err)?;
        let rows = stmt
            .query_map([], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, Option<String>>(1)?,
                    row.get::<_, String>(2)?,
                ))
            })
            .map_err(str_err)?;
        for row in rows {
            let (id, qualified_name, path) = row.map_err(str_err)?;
            let Some(&slot) = slot_of.get(id.as_str()) else {
                continue;
            };
            if source_degree[slot] == 0 && target_degree[slot] == 0 {
                continue;
            }
            members.push(Member {
                degree: source_degree[slot] + target_degree[slot],
                id,
                qualified_name,
                path,
            });
        }
    }
    members.sort_by(|a, b| (&a.qualified_name, &a.id).cmp(&(&b.qualified_name, &b.id)));

    let node_of: HashMap<&str, usize> = members
        .iter()
        .enumerate()
        .map(|(idx, member)| (member.id.as_str(), idx))
        .collect();

    let mut pair_weights: HashMap<(usize, usize), f64> = HashMap::new();
    for (slot, seeds) in graph.seeds.iter().enumerate() {
        for &(src, tgt, _name) in seeds {
            let Some(target) = tgt else { continue };
            let (Some(&a), Some(&b)) = (
                node_of.get(graph.ids[src as usize].as_str()),
                node_of.get(graph.ids[target as usize].as_str()),
            ) else {
                continue;
            };
            let key = if a <= b { (a, b) } else { (b, a) };
            *pair_weights.entry(key).or_insert(0.0) += STRUCTURAL_EDGE_WEIGHTS[slot];
        }
    }
    let mut edges: Vec<(usize, usize, f64)> = pair_weights
        .into_iter()
        .map(|((a, b), w)| (a, b, w))
        .collect();
    edges.sort_by_key(|x| (x.0, x.1));

    Ok(Projection { members, edges })
}

/// Community records in persisted order: (-size, label), members by (path, qualified_name, id).
fn describe(projection: &Projection, labels: &[usize]) -> Vec<CommunityRecord> {
    let mut groups: Vec<Vec<usize>> = Vec::new();
    let mut slot_of: HashMap<usize, usize> = HashMap::new();
    for (idx, &label) in labels.iter().enumerate() {
        match slot_of.entry(label) {
            std::collections::hash_map::Entry::Vacant(v) => {
                v.insert(groups.len());
                groups.push(vec![idx]);
            }
            std::collections::hash_map::Entry::Occupied(o) => groups[*o.get()].push(idx),
        }
    }

    let mut records: Vec<CommunityRecord> = groups
        .into_iter()
        .map(|mut group| {
            group.sort_by(|&a, &b| {
                let (ma, mb) = (&projection.members[a], &projection.members[b]);
                (
                    ma.path.as_str(),
                    ma.qualified_name.as_deref(),
                    ma.id.as_str(),
                )
                    .cmp(&(
                        mb.path.as_str(),
                        mb.qualified_name.as_deref(),
                        mb.id.as_str(),
                    ))
            });
            let mut hubs = group.clone();
            hubs.sort_by(|&a, &b| {
                let (ma, mb) = (&projection.members[a], &projection.members[b]);
                mb.degree.cmp(&ma.degree).then_with(|| {
                    (ma.path.as_str(), ma.qualified_name.as_deref())
                        .cmp(&(mb.path.as_str(), mb.qualified_name.as_deref()))
                })
            });
            let paths: Vec<&str> = group
                .iter()
                .map(|&i| projection.members[i].path.as_str())
                .collect();
            let label = dominant_segment(&paths).unwrap_or_else(|| {
                format!(
                    "{HUB_LABEL_PREFIX}{}",
                    projection.members[hubs[0]]
                        .qualified_name
                        .as_deref()
                        .unwrap_or_default()
                )
            });
            CommunityRecord {
                label,
                members: group
                    .iter()
                    .map(|&i| {
                        (
                            projection.members[i].id.clone(),
                            projection.members[i].degree,
                        )
                    })
                    .collect(),
            }
        })
        .collect();
    records.sort_by(|a, b| {
        b.members
            .len()
            .cmp(&a.members.len())
            .then_with(|| a.label.cmp(&b.label))
    });
    records
}

/// First path segment owning more than `DOMINANT_SEGMENT_SHARE` of the paths (ties by segment).
fn dominant_segment(paths: &[&str]) -> Option<String> {
    if paths.is_empty() {
        return None;
    }
    let mut counts: HashMap<&str, usize> = HashMap::new();
    for path in paths {
        *counts
            .entry(path.split('/').next().unwrap_or_default())
            .or_insert(0) += 1;
    }
    let (segment, count) = counts
        .into_iter()
        .max_by(|a, b| (a.1, a.0).cmp(&(b.1, b.0)))
        .expect("non-empty paths");
    (count as f64 / paths.len() as f64 > DOMINANT_SEGMENT_SHARE).then(|| segment.to_string())
}

/// Deterministic seeded Louvain: community label per node (dense 0..k-1).
pub fn louvain_partition(n: usize, edges: &[(usize, usize, f64)]) -> Vec<usize> {
    let mut labels: Vec<usize> = (0..n).collect();
    let mut graph = CsrGraph::build(n, edges);
    let mut node_of_orig: Vec<usize> = (0..n).collect();
    loop {
        let (part, gain) = one_level(&graph);
        if gain < LEVEL_GAIN_EPS {
            break;
        }
        let (dense, communities) = dense_remap(&part);
        if communities == graph.n {
            break;
        }
        for (label, &current) in labels.iter_mut().zip(node_of_orig.iter()) {
            *label = dense[part[current]];
        }
        if communities == 1 {
            break;
        }
        let (next, super_of) = graph.aggregate(&part, &dense, communities);
        for slot in &mut node_of_orig {
            *slot = super_of[*slot];
        }
        graph = next;
    }
    labels
}

/// Weighted modularity of a partition: Q = Σ_c [L_c/m − (D_c/2m)²].
pub fn modularity(n: usize, edges: &[(usize, usize, f64)], labels: &[usize]) -> f64 {
    let mut degree = vec![0.0f64; n];
    for &(u, v, w) in edges {
        if u == v {
            degree[u] += 2.0 * w;
        } else {
            degree[u] += w;
            degree[v] += w;
        }
    }
    let two_m: f64 = degree.iter().sum();
    if two_m == 0.0 {
        return 0.0;
    }
    let m = two_m / 2.0;
    let communities = labels.iter().copied().max().map_or(0, |v| v + 1);
    let mut intra = vec![0.0f64; communities];
    let mut degree_sum = vec![0.0f64; communities];
    for (node, &label) in labels.iter().enumerate() {
        degree_sum[label] += degree[node];
    }
    for &(u, v, w) in edges {
        if labels[u] == labels[v] {
            intra[labels[u]] += w;
        }
    }
    intra
        .iter()
        .zip(degree_sum.iter())
        .map(|(&l_c, &d_c)| l_c / m - (d_c / two_m) * (d_c / two_m))
        .sum()
}

/// One level's local-moving phase over a fixed node order; returns the
/// partition and the level's total modularity gain.
fn one_level(graph: &CsrGraph) -> (Vec<usize>, f64) {
    let n = graph.n;
    let mut comm: Vec<usize> = (0..n).collect();
    let mut sigma = graph.degree.clone();
    let mut weights_to = vec![0.0f64; n];
    let mut touched: Vec<usize> = Vec::new();
    let mut total_gain = 0.0f64;
    if graph.m == 0.0 {
        return (comm, 0.0);
    }
    let two_m_sq = 2.0 * graph.m * graph.m;
    loop {
        let mut moves = 0;
        let mut improvement = 0.0f64;
        for i in 0..n {
            let home = comm[i];
            sigma[home] -= graph.degree[i];
            for (j, w) in graph.neighbors(i) {
                if j == i {
                    continue;
                }
                let c = comm[j];
                if weights_to[c] == 0.0 {
                    touched.push(c);
                }
                weights_to[c] += w;
            }
            let mut best = home;
            let stay_gain = weights_to[home] / graph.m - sigma[home] * graph.degree[i] / two_m_sq;
            let mut best_gain = stay_gain;
            for &c in &touched {
                if c == home {
                    continue;
                }
                let gain = weights_to[c] / graph.m - sigma[c] * graph.degree[i] / two_m_sq;
                if gain > best_gain {
                    best_gain = gain;
                    best = c;
                }
            }
            if best != home {
                comm[i] = best;
                sigma[best] += graph.degree[i];
                moves += 1;
                improvement += best_gain - stay_gain;
            } else {
                sigma[home] += graph.degree[i];
            }
            for &c in &touched {
                weights_to[c] = 0.0;
            }
            touched.clear();
        }
        total_gain += improvement;
        // Tie-noise moves can cycle a pass forever; a gain below the
        // threshold ends the level (networkx's default convergence bound).
        if moves == 0 || improvement < LEVEL_GAIN_EPS {
            break;
        }
    }
    (comm, total_gain)
}

/// Community -> dense index by first appearance over node order, plus the dense count.
fn dense_remap(part: &[usize]) -> (Vec<usize>, usize) {
    let mut map = vec![usize::MAX; part.len()];
    let mut next = 0;
    for &c in part {
        if map[c] == usize::MAX {
            map[c] = next;
            next += 1;
        }
    }
    (map, next)
}

/// Weighted undirected CSR graph; neighbor lists keep edge insertion order.
struct CsrGraph {
    n: usize,
    m: f64,
    degree: Vec<f64>,
    adj_off: Vec<u32>,
    adj: Vec<u32>,
    weight: Vec<f64>,
}

impl CsrGraph {
    fn build(n: usize, edges: &[(usize, usize, f64)]) -> Self {
        let mut degree = vec![0.0f64; n];
        let mut counts = vec![0u32; n];
        for &(u, v, w) in edges {
            counts[u] += 1;
            degree[u] += w;
            if u != v {
                counts[v] += 1;
                degree[v] += w;
            } else {
                degree[u] += w;
            }
        }
        let mut adj_off = vec![0u32; n + 1];
        for (id, &count) in counts.iter().enumerate() {
            adj_off[id + 1] = adj_off[id] + count;
        }
        let slots = *adj_off.last().unwrap_or(&0) as usize;
        let mut adj = vec![0u32; slots];
        let mut weight = vec![0.0f64; slots];
        let mut cursor = adj_off.clone();
        for &(u, v, w) in edges {
            let slot = cursor[u] as usize;
            adj[slot] = v as u32;
            weight[slot] = w;
            cursor[u] += 1;
            if u != v {
                let slot = cursor[v] as usize;
                adj[slot] = u as u32;
                weight[slot] = w;
                cursor[v] += 1;
            }
        }
        let m: f64 = degree.iter().sum::<f64>() / 2.0;
        CsrGraph {
            n,
            m,
            degree,
            adj_off,
            adj,
            weight,
        }
    }

    fn neighbors(&self, i: usize) -> impl Iterator<Item = (usize, f64)> + '_ {
        let start = self.adj_off[i] as usize;
        let end = self.adj_off[i + 1] as usize;
        self.adj[start..end]
            .iter()
            .map(|&j| j as usize)
            .zip(self.weight[start..end].iter().copied())
    }

    /// One aggregated level: super-node graph plus each community's super-node id.
    fn aggregate(
        &self,
        part: &[usize],
        dense: &[usize],
        communities: usize,
    ) -> (CsrGraph, Vec<usize>) {
        let mut acc: HashMap<(usize, usize), f64> = HashMap::new();
        for u in 0..self.n {
            for (v, w) in self.neighbors(u) {
                if v < u {
                    continue; // symmetric adjacency: take each pair once
                }
                let cu = dense[part[u]];
                let cv = dense[part[v]];
                let key = if cu <= cv { (cu, cv) } else { (cv, cu) };
                *acc.entry(key).or_insert(0.0) += w;
            }
        }
        let mut pairs: Vec<(usize, usize, f64)> =
            acc.into_iter().map(|((a, b), w)| (a, b, w)).collect();
        pairs.sort_by_key(|x| (x.0, x.1));
        let super_of: Vec<usize> = (0..self.n).map(|node| dense[part[node]]).collect();
        (CsrGraph::build(communities, &pairs), super_of)
    }
}

fn str_err(e: rusqlite::Error) -> String {
    e.to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    type Fixture = (
        &'static [(&'static str, &'static str)],
        &'static [(&'static str, &'static str, &'static str)],
        &'static [(&'static str, Option<&'static str>, &'static str)],
    );

    // networkx 3.6.1 louvain_communities(seed=0) modularity references over the
    // sidecar's projection (D-R01 property acceptance; tolerance absorbs float noise).
    const MODULARITY_TOLERANCE: f64 = 1e-6;

    const ISLANDS: Fixture = (
        &[("f1", "islands/a.py")],
        &[
            ("s1", "a", "f1"),
            ("s2", "b", "f1"),
            ("s3", "c", "f1"),
            ("s4", "d", "f1"),
        ],
        &[("s1", Some("s2"), "calls"), ("s3", Some("s4"), "calls")],
    );

    const STAR: Fixture = (
        &[
            ("f1", "alpha/one.py"),
            ("f2", "beta/two.py"),
            ("f3", "gamma/three.py"),
            ("f4", "delta/four.py"),
        ],
        &[
            ("s1", "entry", "f1"),
            ("s2", "left", "f2"),
            ("s3", "right", "f3"),
            ("s4", "sink", "f4"),
        ],
        &[
            ("s1", Some("s2"), "calls"),
            ("s1", Some("s3"), "calls"),
            ("s1", Some("s4"), "calls"),
        ],
    );

    const CLUSTERED: Fixture = (
        &[
            ("fa1", "alpha/one.py"),
            ("fa2", "alpha/two.py"),
            ("fb1", "beta/one.py"),
            ("fb2", "beta/two.py"),
            ("fg1", "gamma/one.py"),
            ("fg2", "gamma/two.py"),
        ],
        &[
            ("a1", "alpha.API", "fa1"),
            ("a2", "alpha.handle", "fa1"),
            ("a3", "alpha.parse", "fa2"),
            ("a4", "alpha.render", "fa2"),
            ("a5", "alpha.Edge", "fa2"),
            ("b1", "beta.Client", "fb1"),
            ("b2", "beta.send", "fb1"),
            ("b3", "beta.encode", "fb2"),
            ("b4", "beta.retry", "fb2"),
            ("b5", "beta.Transport", "fb2"),
            ("g1", "gamma.Entry", "fg1"),
            ("g2", "gamma.step", "fg1"),
            ("g3", "gamma.fold", "fg1"),
            ("g4", "gamma.State", "fg2"),
        ],
        &[
            ("a1", Some("a2"), "calls"),
            ("a2", Some("a3"), "calls"),
            ("a3", Some("a1"), "calls"),
            ("a3", Some("a4"), "calls"),
            ("a4", Some("a5"), "calls"),
            ("a5", Some("a1"), "extends"),
            ("b1", Some("b2"), "calls"),
            ("b2", Some("b1"), "calls"),
            ("b2", Some("b3"), "calls"),
            ("b3", Some("b1"), "calls"),
            ("b3", Some("b4"), "calls"),
            ("b4", Some("b5"), "calls"),
            ("b5", Some("b3"), "implements"),
            ("g1", Some("g2"), "calls"),
            ("g2", Some("g3"), "calls"),
            ("g3", Some("g4"), "calls"),
            ("g4", Some("g1"), "calls"),
            ("a1", Some("b1"), "calls"),
            ("b1", Some("g1"), "calls"),
        ],
    );

    const DANGLER: Fixture = (
        &[
            ("fx", "dang/one.py"),
            ("fy", "dang/two.py"),
            ("fz", "dang/solo.py"),
        ],
        &[
            ("x1", "dang.one", "fx"),
            ("x2", "dang.two", "fy"),
            ("solo", "dang.solo", "fz"),
        ],
        &[
            ("x1", Some("x2"), "calls"),
            ("x2", None, "calls"),
            ("solo", None, "calls"),
        ],
    );

    fn fixture_conn(f: Fixture) -> Connection {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE files (id TEXT PRIMARY KEY, repo_id TEXT NOT NULL, path TEXT NOT NULL, language TEXT NOT NULL);
             CREATE TABLE symbols (id TEXT PRIMARY KEY, file_id TEXT NOT NULL, name TEXT NOT NULL, qualified_name TEXT, kind TEXT NOT NULL);
             CREATE TABLE edges (id TEXT PRIMARY KEY, source_id TEXT NOT NULL, target_id TEXT, target_name TEXT, kind TEXT NOT NULL);
             CREATE TABLE communities (id INTEGER PRIMARY KEY, label TEXT NOT NULL, size INTEGER NOT NULL);
             CREATE TABLE symbol_communities (community_id INTEGER NOT NULL, symbol_id TEXT NOT NULL, structural_degree INTEGER NOT NULL);",
        )
        .unwrap();
        for (id, path) in f.0 {
            conn.execute(
                "INSERT INTO files (id, repo_id, path, language) VALUES (?1, 'r1', ?2, 'python')",
                rusqlite::params![id, path],
            )
            .unwrap();
        }
        for (id, qn, fid) in f.1 {
            conn.execute(
                "INSERT INTO symbols (id, file_id, name, qualified_name, kind) VALUES (?1, ?2, ?3, ?3, 'function')",
                rusqlite::params![id, fid, qn],
            )
            .unwrap();
        }
        for (idx, (src, tgt, kind)) in f.2.iter().enumerate() {
            conn.execute(
                "INSERT INTO edges (id, source_id, target_id, target_name, kind) VALUES (?1, ?2, ?3, NULL, ?4)",
                rusqlite::params![format!("e{idx}"), src, tgt, kind],
            )
            .unwrap();
        }
        conn
    }

    fn community_table(conn: &Connection) -> Vec<(i64, String, i64)> {
        let mut stmt = conn
            .prepare("SELECT id, label, size FROM communities ORDER BY id")
            .unwrap();
        let rows = stmt
            .query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))
            .unwrap();
        rows.map(|r| r.unwrap()).collect()
    }

    fn member_table(conn: &Connection) -> Vec<(i64, String, i64)> {
        let mut stmt = conn
            .prepare(
                "SELECT community_id, symbol_id, structural_degree FROM symbol_communities \
                 ORDER BY community_id, symbol_id",
            )
            .unwrap();
        let rows = stmt
            .query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))
            .unwrap();
        rows.map(|r| r.unwrap()).collect()
    }

    fn projected_fixture(f: Fixture) -> (Vec<&'static str>, Vec<(usize, usize, f64)>) {
        let weight = |kind: &str| match kind {
            "call" | "calls" => 1.0,
            _ => 2.0,
        };
        let nodes: Vec<&str> = {
            let mut ids: Vec<&str> = f.1.iter().map(|(id, _, _)| *id).collect();
            let endpoint = |id: &str| f.2.iter().any(|(s, t, _)| *s == id || (*t == Some(id)));
            ids.retain(|id| endpoint(id));
            ids.sort_by_key(|id| f.1.iter().find(|(sid, _, _)| sid == id).unwrap().1);
            ids
        };
        let node_of = |id: &str| nodes.iter().position(|n| *n == id).unwrap();
        let mut acc: HashMap<(usize, usize), f64> = HashMap::new();
        for (src, tgt, kind) in f.2 {
            let Some(tgt) = tgt else { continue };
            let (a, b) = (node_of(src), node_of(tgt));
            let key = if a <= b { (a, b) } else { (b, a) };
            *acc.entry(key).or_insert(0.0) += weight(kind);
        }
        let mut edges: Vec<(usize, usize, f64)> =
            acc.into_iter().map(|((a, b), w)| (a, b, w)).collect();
        edges.sort_by_key(|x| (x.0, x.1));
        (nodes, edges)
    }

    #[test]
    fn louvain_modularity_matches_networkx_reference() {
        let references = [
            (ISLANDS, 0.5),
            (STAR, 0.0),
            (CLUSTERED, 23.0 / 42.0),
            (DANGLER, 0.0),
        ];
        for (fixture, nx_q) in references {
            let (nodes, edges) = projected_fixture(fixture);
            let labels = louvain_partition(nodes.len(), &edges);
            let q = modularity(nodes.len(), &edges, &labels);
            assert!(
                q >= nx_q - MODULARITY_TOLERANCE,
                "{:?}: modularity {q} below networkx reference {nx_q}",
                fixture.1[0]
            );
        }
    }

    #[test]
    fn louvain_partition_is_deterministic() {
        let (nodes, edges) = projected_fixture(CLUSTERED);
        assert_eq!(
            louvain_partition(nodes.len(), &edges),
            louvain_partition(nodes.len(), &edges)
        );
    }

    #[test]
    fn louvain_separates_connected_components_and_danglers() {
        let (nodes, edges) = projected_fixture(DANGLER);
        let labels = louvain_partition(nodes.len(), &edges);
        // dang.one, dang.two share a community; dang.solo is a singleton.
        let (x1, x2, solo) = (
            nodes.iter().position(|n| *n == "x1").unwrap(),
            nodes.iter().position(|n| *n == "x2").unwrap(),
            nodes.iter().position(|n| *n == "solo").unwrap(),
        );
        assert_eq!(labels[x1], labels[x2]);
        assert_ne!(labels[x1], labels[solo]);
    }

    #[test]
    fn projection_weights_kinds_and_mirrors() {
        let conn = fixture_conn(CLUSTERED);
        let graph = closure::load_graph(&conn).unwrap();
        let projection = load_projection(&conn, &graph).unwrap();
        let node_of = |id: &str| projection.members.iter().position(|m| m.id == id).unwrap();
        // Nodes sorted by (qualified_name, id).
        let names: Vec<&str> = projection
            .members
            .iter()
            .map(|m| m.qualified_name.as_deref().unwrap())
            .collect();
        let mut sorted = names.clone();
        sorted.sort();
        assert_eq!(names, sorted);
        let weight = |a: &str, b: &str| {
            let (x, y) = (node_of(a), node_of(b));
            let key = if x <= y { (x, y) } else { (y, x) };
            projection
                .edges
                .iter()
                .find(|(u, v, _)| (*u, *v) == key)
                .map(|(_, _, w)| *w)
                .unwrap()
        };
        assert_eq!(weight("a1", "a5"), 2.0); // extends
        assert_eq!(weight("b5", "b3"), 2.0); // implements
        assert_eq!(weight("b1", "b2"), 2.0); // mirrored calls summed
        assert_eq!(weight("a1", "b1"), 1.0); // bridge
        assert_eq!(weight("b1", "g1"), 1.0); // bridge
    }

    #[test]
    fn refresh_populates_labels_sizes_and_degrees() {
        let mut conn = fixture_conn(CLUSTERED);
        assert_eq!(refresh_communities(&mut conn).unwrap(), 3);
        assert_eq!(
            community_table(&conn),
            vec![
                (1, "alpha".to_string(), 5),
                (2, "beta".to_string(), 5),
                (3, "gamma".to_string(), 4),
            ]
        );
        let members = member_table(&conn);
        let degree = |id: &str| {
            members
                .iter()
                .find(|(_, sid, _)| sid == id)
                .map(|(_, _, d)| *d)
                .unwrap()
        };
        for (id, expected) in [
            ("a1", 4),
            ("a2", 2),
            ("a3", 3),
            ("a4", 2),
            ("a5", 2),
            ("b1", 5),
            ("b2", 3),
            ("b3", 4),
            ("b4", 2),
            ("b5", 2),
            ("g1", 3),
            ("g2", 2),
            ("g3", 2),
            ("g4", 2),
        ] {
            assert_eq!(degree(id), expected, "degree of {id}");
        }
        // Each community holds exactly one module's symbols.
        let community_of = |id: &str| {
            members
                .iter()
                .find(|(_, sid, _)| sid == id)
                .map(|(cid, _, _)| *cid)
                .unwrap()
        };
        for prefix in ["a", "b", "g"] {
            let ids: Vec<&str> = ["1", "2", "3", "4", "5"]
                .iter()
                .filter_map(|n| {
                    CLUSTERED
                        .1
                        .iter()
                        .find(|(id, _, _)| id == &format!("{prefix}{n}"))
                        .map(|(id, _, _)| *id)
                })
                .collect();
            let cids: Vec<i64> = ids.iter().map(|id| community_of(id)).collect();
            assert!(
                cids.iter().all(|c| *c == cids[0]),
                "{prefix} split: {cids:?}"
            );
        }
        assert_ne!(community_of("a1"), community_of("b1"));
        assert_ne!(community_of("b1"), community_of("g1"));
    }

    #[test]
    fn refresh_double_run_is_byte_equal() {
        let mut conn = fixture_conn(CLUSTERED);
        refresh_communities(&mut conn).unwrap();
        let first = (community_table(&conn), member_table(&conn));
        refresh_communities(&mut conn).unwrap();
        assert_eq!((community_table(&conn), member_table(&conn)), first);
    }

    #[test]
    fn zero_edge_store_clears_stale_rows_and_returns_zero() {
        let mut conn = fixture_conn((&[("f1", "a.py")], &[("s1", "solo", "f1")], &[]));
        conn.execute(
            "INSERT INTO communities (id, label, size) VALUES (1, 'stale', 1)",
            [],
        )
        .unwrap();
        conn.execute("INSERT INTO symbol_communities VALUES (1, 's1', 0)", [])
            .unwrap();
        assert_eq!(refresh_communities(&mut conn).unwrap(), 0);
        assert_eq!(community_table(&conn), Vec::new());
        assert_eq!(member_table(&conn), Vec::new());
    }

    #[test]
    fn disconnected_pairs_form_two_degree_one_communities() {
        let mut conn = fixture_conn(ISLANDS);
        assert_eq!(refresh_communities(&mut conn).unwrap(), 2);
        assert_eq!(
            community_table(&conn),
            vec![(1, "islands".to_string(), 2), (2, "islands".to_string(), 2),]
        );
        assert!(member_table(&conn).iter().all(|(_, _, d)| *d == 1));
    }

    #[test]
    fn label_falls_back_to_top_hub_without_dominant_segment() {
        let mut conn = fixture_conn(STAR);
        assert_eq!(refresh_communities(&mut conn).unwrap(), 1);
        assert_eq!(
            community_table(&conn),
            vec![(1, "hub:entry".to_string(), 4)]
        );
    }

    #[test]
    fn dangling_targets_add_degree_but_no_nodes() {
        let mut conn = fixture_conn(DANGLER);
        assert_eq!(refresh_communities(&mut conn).unwrap(), 2);
        assert_eq!(
            community_table(&conn),
            vec![(1, "dang".to_string(), 2), (2, "dang".to_string(), 1),]
        );
        let members = member_table(&conn);
        let degree = |id: &str| {
            members
                .iter()
                .find(|(_, sid, _)| sid == id)
                .map(|(_, _, d)| *d)
                .unwrap()
        };
        assert_eq!(degree("x1"), 1);
        assert_eq!(degree("x2"), 2);
        assert_eq!(degree("solo"), 1);
    }

    #[test]
    fn hub_rows_rank_by_degree_then_path_then_name() {
        let mut conn = fixture_conn(CLUSTERED);
        refresh_communities(&mut conn).unwrap();
        let hubs: Vec<(String, i64)> = hub_rows(&conn, None, 5)
            .unwrap()
            .into_iter()
            .map(|h| (h.qualified_name, h.structural_degree))
            .collect();
        assert_eq!(
            hubs,
            vec![
                ("beta.Client".to_string(), 5),
                ("alpha.API".to_string(), 4),
                ("beta.encode".to_string(), 4),
                ("alpha.parse".to_string(), 3),
                ("beta.send".to_string(), 3),
            ]
        );
        let alpha_hubs: Vec<(String, i64)> = hub_rows(&conn, Some(1), 2)
            .unwrap()
            .into_iter()
            .map(|h| (h.qualified_name, h.structural_degree))
            .collect();
        assert_eq!(
            alpha_hubs,
            vec![("alpha.API".to_string(), 4), ("alpha.parse".to_string(), 3),]
        );
    }
}
