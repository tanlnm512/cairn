//! Streaming transitive closure: level-BFS over interned ids with bounded memory.

use std::collections::HashMap;
use std::collections::hash_map::Entry;

use cairn_query::graph::STRUCTURAL_EDGE_KINDS;
use rusqlite::Connection;

/// Default closure depth.
pub const CLOSURE_MAX_DEPTH: usize = 4;

/// Rows buffered per transaction; each commit lets SQLite checkpoint the WAL.
const ROWS_PER_COMMIT: usize = 50_000;

/// Closure build result counts.
#[derive(Debug, Clone, Copy, Default, serde::Serialize)]
pub struct ClosureOutcome {
    pub rows: u64,
    pub levels: usize,
    pub peak_frontier: usize,
}

/// Rebuild the structural transitive closure table; rows stream to disk per
/// level so memory tracks the frontier, not the closure.
pub fn build_transitive_closure(
    conn: &mut Connection,
    max_depth: usize,
) -> Result<ClosureOutcome, String> {
    // Bulk-load tuning for this connection only: the rebuild touches every
    // index page of the table, so keep them hot between batch commits.
    conn.execute_batch(
        "PRAGMA cache_size = -262144;
         CREATE INDEX IF NOT EXISTS idx_transitive_distance ON transitive_edges(distance);",
    )
    .map_err(str_err)?;
    let tx = conn.transaction().map_err(str_err)?;
    tx.execute("DELETE FROM transitive_edges", [])
        .map_err(str_err)?;
    tx.commit().map_err(str_err)?;

    let graph = load_graph(conn)?;

    let mut batch: Vec<Row> = Vec::with_capacity(ROWS_PER_COMMIT);
    let mut outcome = ClosureOutcome::default();

    let mut level = Level::default();
    for seeds in &graph.seeds {
        for &(src, tgt, name) in seeds {
            level.or_insert(Key::new(src, name), tgt);
        }
    }
    if !level.is_empty() {
        emit(conn, &graph, &level, 1, &mut batch, &mut outcome)?;
    }

    for distance in 2..=max_depth {
        if level.is_empty() {
            break;
        }
        let mut next = Level::default();
        // Case 1: resolved frontier entries extend through their target's
        // adjacency; entries with an unstorable (None) name are skipped.
        for (key, &target) in level.entries() {
            if let Some(t) = target {
                for entry in graph.adjacency(t) {
                    let name = entry_name(*entry);
                    if let Some(n) = name {
                        next.or_insert(Key::new(key.src(), Some(n)), entry_target(*entry));
                    }
                }
            }
        }
        // Case 2: unresolved entries hop through the unique symbol named
        // target_name, in global edge-scan order; ambiguous names never hop.
        let mut unresolved: HashMap<u32, Vec<u32>> = HashMap::new();
        for (key, &target) in level.entries() {
            if target.is_none()
                && let Some(n) = key.name()
            {
                unresolved.entry(n).or_default().push(key.src());
            }
        }
        if !unresolved.is_empty() {
            for hop in &graph.case2 {
                if let Some(sources) = unresolved.get(&hop.mid) {
                    for &src in sources {
                        next.or_insert(Key::new(src, hop.name), hop.target);
                    }
                }
            }
        }
        if next.is_empty() {
            break;
        }
        emit(conn, &graph, &next, distance, &mut batch, &mut outcome)?;
        level = next;
    }

    if !batch.is_empty() {
        flush(conn, &graph, &mut batch)?;
    }
    Ok(outcome)
}

/// Frontier key: (source id, resolved target name); name `0` encodes None.
#[derive(Clone, Copy, PartialEq, Eq, Hash)]
struct Key(u64);

impl Key {
    fn new(src: u32, name: Option<u32>) -> Self {
        Self(((src as u64) << 32) | name.map_or(0, |n| u64::from(n) + 1))
    }
    fn src(&self) -> u32 {
        (self.0 >> 32) as u32
    }
    fn name(&self) -> Option<u32> {
        let low = self.0 & 0xffff_ffff;
        u32::try_from(low).ok().and_then(|v| v.checked_sub(1))
    }
}

/// Adjacency entry packed as (target + 1) << 32 | (name + 1); 0 half = None.
#[derive(Clone, Copy)]
pub(crate) struct AdjEntry(u64);

pub(crate) fn entry_target(e: AdjEntry) -> Option<u32> {
    u32::try_from(e.0 >> 32).ok().and_then(|v| v.checked_sub(1))
}

fn entry_name(e: AdjEntry) -> Option<u32> {
    u32::try_from(e.0 & 0xffff_ffff)
        .ok()
        .and_then(|v| v.checked_sub(1))
}

fn pack_entry(target: Option<u32>, name: Option<u32>) -> AdjEntry {
    let t = u64::from(target.map_or(0, |v| v.wrapping_add(1)));
    let n = u64::from(name.map_or(0, |v| v.wrapping_add(1)));
    AdjEntry(t << 32 | n)
}

/// Insertion-ordered frontier: first occurrence of a key wins its target.
#[derive(Default)]
struct Level {
    slots: HashMap<Key, usize>,
    keys: Vec<Key>,
    targets: Vec<Option<u32>>,
}

impl Level {
    fn or_insert(&mut self, key: Key, target: Option<u32>) {
        if let Entry::Vacant(v) = self.slots.entry(key) {
            v.insert(self.keys.len());
            self.keys.push(key);
            self.targets.push(target);
        }
    }
    fn entries(&self) -> impl Iterator<Item = (&Key, &Option<u32>)> {
        self.keys.iter().zip(self.targets.iter())
    }
    fn is_empty(&self) -> bool {
        self.keys.is_empty()
    }
}

/// A queued closure row awaiting its transaction batch.
struct Row {
    key: Key,
    target: Option<u32>,
    distance: i64,
}

/// Case-2 hop: unique mid name -> one of that symbol's eligible edges.
pub(crate) struct Case2Hop {
    mid: u32,
    target: Option<u32>,
    name: Option<u32>,
}

/// Eligible level-1 edge: (source id, target id, resolved name).
pub(crate) type Seed = (u32, Option<u32>, Option<u32>);

pub(crate) struct Graph {
    /// Id string per interned id.
    pub(crate) ids: Vec<String>,
    /// Name string per name id.
    pub(crate) names: Vec<String>,
    /// CSR adjacency over interned ids, scan order within each source.
    pub(crate) adj_off: Vec<u32>,
    pub(crate) adj: Vec<AdjEntry>,
    /// Eligible level-1 edges per kind, scan order within the kind.
    pub(crate) seeds: Vec<Vec<Seed>>,
    pub(crate) case2: Vec<Case2Hop>,
}

impl Graph {
    pub(crate) fn adjacency(&self, id: u32) -> &[AdjEntry] {
        let start = self.adj_off[id as usize] as usize;
        let end = self.adj_off[id as usize + 1] as usize;
        &self.adj[start..end]
    }
    fn id_str(&self, id: u32) -> &str {
        &self.ids[id as usize]
    }
    fn name_str(&self, name: u32) -> &str {
        &self.names[name as usize]
    }
}

#[derive(Default)]
struct Interner {
    ids: Vec<String>,
    index: HashMap<String, u32>,
}

impl Interner {
    fn intern(&mut self, id: &str) -> u32 {
        match self.index.get(id) {
            Some(&idx) => idx,
            None => {
                let idx = self.ids.len() as u32;
                self.ids.push(id.to_string());
                self.index.insert(id.to_string(), idx);
                idx
            }
        }
    }
}

/// Read symbols and structural edges into interned, CSR-shaped graph data.
pub(crate) fn load_graph(conn: &Connection) -> Result<Graph, String> {
    let mut interner = Interner::default();
    let mut names: Vec<String> = Vec::new();
    let mut name_index: HashMap<String, u32> = HashMap::new();
    let intern_name =
        |names: &mut Vec<String>, name_index: &mut HashMap<String, u32>, name: &str| -> u32 {
            match name_index.get(name) {
                Some(&idx) => idx,
                None => {
                    let idx = names.len() as u32;
                    names.push(name.to_string());
                    name_index.insert(name.to_string(), idx);
                    idx
                }
            }
        };

    let mut name_of: Vec<Option<u32>> = Vec::new();
    {
        let mut stmt = conn
            .prepare("SELECT id, name FROM symbols")
            .map_err(str_err)?;
        let mut rows = stmt.query([]).map_err(str_err)?;
        while let Some(row) = rows.next().map_err(str_err)? {
            let id: String = row.get(0).map_err(str_err)?;
            let name: Option<String> = row.get(1).map_err(str_err)?;
            interner.intern(&id);
            name_of.push(name.map(|n| intern_name(&mut names, &mut name_index, &n)));
        }
    }
    let symbol_count = name_of.len() as u32;

    // Unique names own exactly one live symbol; ambiguous names never hop.
    let mut name_counts: HashMap<u32, u32> = HashMap::new();
    for name in name_of.iter().flatten() {
        *name_counts.entry(*name).or_insert(0) += 1;
    }
    let mut name_owner: HashMap<u32, u32> = HashMap::new();
    for (idx, name) in name_of.iter().enumerate() {
        if let Some(n) = name
            && name_counts.get(n) == Some(&1)
        {
            name_owner.insert(*n, idx as u32);
        }
    }

    let mut adj_src: Vec<u32> = Vec::new();
    let mut adj_entry: Vec<AdjEntry> = Vec::new();
    let mut seeds: Vec<Vec<Seed>> = vec![Vec::new(); STRUCTURAL_EDGE_KINDS.len()];
    let mut case2: Vec<Case2Hop> = Vec::new();

    {
        let mut stmt = conn
            .prepare("SELECT source_id, target_id, target_name, kind FROM edges")
            .map_err(str_err)?;
        let mut rows = stmt.query([]).map_err(str_err)?;
        while let Some(row) = rows.next().map_err(str_err)? {
            let kind: String = row.get(3).map_err(str_err)?;
            let Some(kind_slot) = STRUCTURAL_EDGE_KINDS.iter().position(|k| *k == kind) else {
                continue;
            };
            let source_id: String = row.get(0).map_err(str_err)?;
            let target_id: Option<String> = row.get(1).map_err(str_err)?;
            let target_name: Option<String> = row.get(2).map_err(str_err)?;

            let src = interner.intern(&source_id);
            let tgt = target_id.as_ref().map(|t| interner.intern(t));
            // COALESCE(symbol.name, edges.target_name); a live target's symbol
            // name wins even when the column itself is NULL.
            let name = match tgt {
                Some(t) if t < symbol_count => name_of[t as usize],
                _ => target_name
                    .as_ref()
                    .map(|n| intern_name(&mut names, &mut name_index, n)),
            };
            let has_target =
                target_id.is_some() || matches!(&target_name, Some(n) if !n.is_empty());

            adj_src.push(src);
            adj_entry.push(pack_entry(tgt, name));
            if has_target {
                seeds[kind_slot].push((src, tgt, name));
                // Case-2 candidates: eligible edges of uniquely-named symbols.
                if let Some(Some(mid)) = name_of.get(src as usize)
                    && name_owner.contains_key(mid)
                {
                    case2.push(Case2Hop {
                        mid: *mid,
                        target: tgt,
                        name,
                    });
                }
            }
        }
    }

    // Scatter the scan-order edge list into CSR groups (order kept per source).
    let id_count = interner.ids.len();
    let mut adj_off = vec![0u32; id_count + 1];
    for &src in &adj_src {
        adj_off[src as usize + 1] += 1;
    }
    for i in 0..id_count {
        adj_off[i + 1] += adj_off[i];
    }
    let mut adj = vec![AdjEntry(0); adj_entry.len()];
    let mut cursor = adj_off.clone();
    for (src, entry) in adj_src.into_iter().zip(adj_entry) {
        adj[cursor[src as usize] as usize] = entry;
        cursor[src as usize] += 1;
    }

    Ok(Graph {
        ids: interner.ids,
        names,
        adj_off,
        adj,
        seeds,
        case2,
    })
}

/// Stream one level's rows in batches, tracking outcome counters.
fn emit(
    conn: &mut Connection,
    graph: &Graph,
    level: &Level,
    distance: usize,
    batch: &mut Vec<Row>,
    outcome: &mut ClosureOutcome,
) -> Result<(), String> {
    outcome.rows += level.keys.len() as u64;
    outcome.levels += 1;
    outcome.peak_frontier = outcome.peak_frontier.max(level.keys.len());
    for (key, target) in level.entries() {
        batch.push(Row {
            key: *key,
            target: *target,
            distance: distance as i64,
        });
        if batch.len() >= ROWS_PER_COMMIT {
            flush(conn, graph, batch)?;
        }
    }
    Ok(())
}

/// Insert one committed batch ordered by the global (source_id, target_name,
/// distance) sort so index inserts append. Rows are unique per level
/// (content-neutral).
fn flush(conn: &mut Connection, graph: &Graph, batch: &mut Vec<Row>) -> Result<(), String> {
    batch.sort_by(|a, b| {
        (a.key.src(), a.key.name(), a.distance).cmp(&(b.key.src(), b.key.name(), b.distance))
    });
    let tx = conn.transaction().map_err(str_err)?;
    {
        let mut stmt = tx
            .prepare(
                "INSERT OR IGNORE INTO transitive_edges
                   (source_id, target_name, target_id, distance)
                 VALUES (?1, ?2, ?3, ?4)",
            )
            .map_err(str_err)?;
        for row in batch.iter() {
            stmt.execute(rusqlite::params![
                graph.id_str(row.key.src()),
                row.key.name().map(|n| graph.name_str(n)),
                row.target.map(|t| graph.id_str(t)),
                row.distance,
            ])
            .map_err(str_err)?;
        }
    }
    tx.commit().map_err(str_err)?;
    batch.clear();
    Ok(())
}

fn str_err(e: rusqlite::Error) -> String {
    e.to_string()
}
