//! Import-aware edge resolver: exact/ambiguous/unresolved triage over the `.kg` tables.

use std::collections::{HashMap, HashSet};

use rusqlite::Connection;

/// Languages whose `calls` edges stay name-based on the repair path.
pub const GENERIC_TIER_LANGUAGES: &[&str] = &["rust"];

/// Symbol kinds that are members of a type (never types themselves).
const MEMBER_KINDS: &[&str] = &[
    "method", "function", "property", "variable", "field", "constant",
];

const REPAIR_SQL: &str =
    "UPDATE edges SET target_id = ?, target_name = ?, resolution = ? WHERE id = ?";

const RESOLVE_SQL: &str = "UPDATE edges SET target_id = ?, target_name = ?, resolution = ?, \
     ref_name = ?, receiver_type = ?, call_arity = ? WHERE id = ?";

/// Resolution label stored in `edges.resolution`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Label {
    Exact,
    Ambiguous,
    Unresolved,
}

impl Label {
    /// The label string written to the store.
    pub fn as_str(self) -> &'static str {
        match self {
            Label::Exact => "exact",
            Label::Ambiguous => "ambiguous",
            Label::Unresolved => "unresolved",
        }
    }
}

/// Per-label resolution counts.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Stats {
    pub exact: usize,
    pub ambiguous: usize,
    pub unresolved: usize,
}

impl Stats {
    /// Increment the counter for `label`.
    pub fn record(&mut self, label: Label) {
        match label {
            Label::Exact => self.exact += 1,
            Label::Ambiguous => self.ambiguous += 1,
            Label::Unresolved => self.unresolved += 1,
        }
    }

    /// Count for `label`.
    pub fn get(&self, label: Label) -> usize {
        match label {
            Label::Exact => self.exact,
            Label::Ambiguous => self.ambiguous,
            Label::Unresolved => self.unresolved,
        }
    }
}

/// One bare-name candidate: (symbol_id, repo, file_id, qualified_name, arity).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Cand {
    pub id: String,
    pub repo: String,
    pub file_id: String,
    pub qname: Option<String>,
    pub arity: Option<i64>,
}

/// Edge-resolution inputs: global symbol index plus repo-scoped side indexes.
#[derive(Debug, Clone, Default)]
pub struct ResolverIndexes {
    pub symbols_by_name: HashMap<String, Vec<Cand>>,
    pub imports_by_file: HashMap<String, Vec<String>>,
    pub aliases_by_file: HashMap<String, HashMap<String, String>>,
    pub members_by_type: HashMap<(String, String), Vec<String>>,
    pub ancestors: HashMap<String, Vec<String>>,
}

/// One edge awaiting resolution (the builder's in-memory signal slots).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EdgeSignal {
    pub edge_id: String,
    pub target_name: String,
    pub receiver_type: Option<String>,
    pub call_arity: Option<i64>,
    pub generic_tier: bool,
}

impl ResolverIndexes {
    /// Build all indexes; `repo_id` scopes imports/members/ancestors, symbols stay global.
    pub fn build(conn: &Connection, repo_id: Option<&str>) -> rusqlite::Result<Self> {
        let symbols_by_name = build_symbol_index(conn)?;
        let (imports_by_file, aliases_by_file) = build_import_index(conn, repo_id)?;
        let members_by_type = build_members_index(conn, repo_id)?;
        let ancestors = build_ancestor_index(conn, repo_id)?;
        Ok(Self {
            symbols_by_name,
            imports_by_file,
            aliases_by_file,
            members_by_type,
            ancestors,
        })
    }
}

/// Build a global bare-name -> candidates index over non-module symbols.
pub fn build_symbol_index(conn: &Connection) -> rusqlite::Result<HashMap<String, Vec<Cand>>> {
    let mut index: HashMap<String, Vec<Cand>> = HashMap::new();
    let mut stmt = conn.prepare(
        "SELECT s.id AS sid, s.name AS name, s.qualified_name AS qname,
                f.repo_id AS repo, f.id AS file_id, s.arity AS arity
         FROM symbols s JOIN files f ON s.file_id = f.id
         WHERE s.kind != 'module'",
    )?;
    let rows = stmt.query_map([], |r| {
        Ok((
            r.get::<_, String>("name")?,
            Cand {
                id: r.get("sid")?,
                repo: r.get("repo")?,
                file_id: r.get("file_id")?,
                qname: r.get("qname")?,
                arity: r.get("arity")?,
            },
        ))
    })?;
    for row in rows {
        let (name, cand) = row?;
        index.entry(name).or_default().push(cand);
    }
    Ok(index)
}

type ImportIndex = (
    HashMap<String, Vec<String>>,
    HashMap<String, HashMap<String, String>>,
);

/// Build per-file import and alias indexes over the `imports` table.
pub fn build_import_index(
    conn: &Connection,
    repo_id: Option<&str>,
) -> rusqlite::Result<ImportIndex> {
    fn row(r: &rusqlite::Row) -> rusqlite::Result<(String, String, Option<String>)> {
        Ok((
            r.get("file_id")?,
            r.get("imported_path")?,
            r.get("local_alias")?,
        ))
    }
    let mut rows: Vec<(String, String, Option<String>)> = match repo_id {
        Some(repo) => {
            let mut stmt = conn.prepare(
                "SELECT i.file_id AS file_id, i.imported_path AS imported_path,
                        i.local_alias AS local_alias
                 FROM imports i JOIN files f ON i.file_id = f.id
                 WHERE f.repo_id = ?",
            )?;
            stmt.query_map([repo], row)?
                .collect::<rusqlite::Result<Vec<_>>>()?
        }
        None => {
            let mut stmt =
                conn.prepare("SELECT file_id, imported_path, local_alias FROM imports")?;
            stmt.query_map([], row)?
                .collect::<rusqlite::Result<Vec<_>>>()?
        }
    };
    let mut imports: HashMap<String, Vec<String>> = HashMap::new();
    let mut aliases: HashMap<String, HashMap<String, String>> = HashMap::new();
    for (file_id, path, alias) in rows.drain(..) {
        imports
            .entry(file_id.clone())
            .or_default()
            .push(path.clone());
        if let Some(alias) = alias {
            aliases.entry(file_id).or_default().insert(alias, path);
        }
    }
    Ok((imports, aliases))
}

fn member_kind_params<'a>(repo_slot: Option<&'a [&'a str; 1]>) -> Vec<&'a dyn rusqlite::ToSql> {
    let mut params: Vec<&dyn rusqlite::ToSql> = MEMBER_KINDS
        .iter()
        .map(|k| k as &dyn rusqlite::ToSql)
        .collect();
    if let Some(slot) = repo_slot {
        params.push(&slot[0]);
    }
    params
}

/// Build a {(enclosing_type_simple_name, member_name): [symbol_id, ...]} index.
pub fn build_members_index(
    conn: &Connection,
    repo_id: Option<&str>,
) -> rusqlite::Result<HashMap<(String, String), Vec<String>>> {
    let repo_join = if repo_id.is_some() {
        " JOIN files f ON s.file_id = f.id"
    } else {
        ""
    };
    let repo_where = if repo_id.is_some() {
        " AND f.repo_id = ?"
    } else {
        ""
    };
    let member_ph = MEMBER_KINDS
        .iter()
        .map(|_| "?")
        .collect::<Vec<_>>()
        .join(",");
    let repo_slot: Option<[&str; 1]> = repo_id.map(|r| [r]);
    let params = member_kind_params(repo_slot.as_ref());

    let type_sql = format!(
        "SELECT DISTINCT s.name AS name FROM symbols s{repo_join} \
         WHERE s.kind NOT IN ({member_ph}) AND s.kind != 'module'{repo_where}"
    );
    let mut type_names: HashSet<String> = HashSet::new();
    let mut stmt = conn.prepare(&type_sql)?;
    let mapped = stmt.query_map(params.as_slice(), |r| r.get::<_, String>(0))?;
    for row in mapped {
        type_names.insert(row?);
    }

    let member_sql = format!(
        "SELECT s.id AS sid, s.name AS member, s.qualified_name AS qname \
         FROM symbols s{repo_join} WHERE s.kind IN ({member_ph}){repo_where}"
    );
    let mut idx: HashMap<(String, String), Vec<String>> = HashMap::new();
    let mut stmt = conn.prepare(&member_sql)?;
    let mapped = stmt.query_map(params.as_slice(), |r| {
        Ok((
            r.get::<_, String>("sid")?,
            r.get::<_, String>("member")?,
            r.get::<_, Option<String>>("qname")?,
        ))
    })?;
    for row in mapped {
        let (sid, member, qname) = row?;
        let q = qname.unwrap_or_default().replace('/', ".");
        let segs: Vec<&str> = if q.is_empty() {
            Vec::new()
        } else {
            q.split('.').collect()
        };
        if segs.len() >= 2 && type_names.contains(segs[segs.len() - 2]) {
            let key = (segs[segs.len() - 2].to_string(), member);
            idx.entry(key).or_default().push(sid);
        }
    }
    Ok(idx)
}

/// Build a {child_type_name: [parent_type_name, ...]} index from inheritance edges.
pub fn build_ancestor_index(
    conn: &Connection,
    repo_id: Option<&str>,
) -> rusqlite::Result<HashMap<String, Vec<String>>> {
    let mut anc: HashMap<String, Vec<String>> = HashMap::new();
    let mut rows: Vec<(Option<String>, Option<String>)> = match repo_id {
        Some(repo) => {
            let mut stmt = conn.prepare(
                "SELECT src.name AS child, COALESCE(e.ref_name, e.target_name) AS parent
                 FROM edges e JOIN symbols src ON e.source_id = src.id
                 JOIN files f ON src.file_id = f.id
                 WHERE f.repo_id = ?
                   AND e.kind IN ('extends', 'implements', 'embeds')
                   AND COALESCE(e.ref_name, e.target_name) IS NOT NULL",
            )?;
            stmt.query_map([repo], |r| Ok((r.get(0)?, r.get(1)?)))?
                .collect::<rusqlite::Result<Vec<_>>>()?
        }
        None => {
            let mut stmt = conn.prepare(
                "SELECT src.name AS child, COALESCE(e.ref_name, e.target_name) AS parent
                 FROM edges e JOIN symbols src ON e.source_id = src.id
                 WHERE e.kind IN ('extends', 'implements', 'embeds')
                   AND COALESCE(e.ref_name, e.target_name) IS NOT NULL",
            )?;
            stmt.query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?
                .collect::<rusqlite::Result<Vec<_>>>()?
        }
    };
    for (child, parent) in rows.drain(..) {
        let (Some(child), Some(parent)) = (child, parent) else {
            continue;
        };
        if child.is_empty() || parent.is_empty() {
            continue;
        }
        anc.entry(child).or_default().push(parent);
    }
    Ok(anc)
}

/// Symbol ids for `member` on `recv_type`, walking ancestors until found.
pub fn members_of(recv_type: &str, member: &str, ix: &ResolverIndexes) -> Vec<String> {
    fn walk(
        recv_type: &str,
        member: &str,
        ix: &ResolverIndexes,
        seen: &mut HashSet<String>,
    ) -> Vec<String> {
        if !seen.insert(recv_type.to_string()) {
            return Vec::new();
        }
        if let Some(hits) = ix
            .members_by_type
            .get(&(recv_type.to_string(), member.to_string()))
            && !hits.is_empty()
        {
            return hits.clone();
        }
        if let Some(parents) = ix.ancestors.get(recv_type) {
            for parent in parents {
                let hits = walk(parent, member, ix, seen);
                if !hits.is_empty() {
                    return hits;
                }
            }
        }
        Vec::new()
    }
    let mut seen = HashSet::new();
    walk(recv_type, member, ix, &mut seen)
}

/// The id `call_arity` uniquely picks from `cands`, else `None`.
fn arity_unique_match(cands: &[&Cand], call_arity: Option<i64>) -> Option<String> {
    let call_arity = call_arity?;
    let mut matches = cands.iter().filter(|c| c.arity == Some(call_arity));
    let first = matches.next()?;
    if matches.next().is_none() {
        Some(first.id.clone())
    } else {
        None
    }
}

/// Count matching trailing segments of `a` and `b` (longest run).
fn common_suffix_len(a: &[&str], b: &[&str]) -> usize {
    let n = a.len().min(b.len());
    let mut run = 0;
    for j in 1..=n {
        if a[a.len() - j] == b[b.len() - j] {
            run = j;
        } else {
            break;
        }
    }
    run
}

/// Narrow `cands` to those made reachable by one of the file's imports,
/// keeping only the highest-scoring (most specific) tier.
fn import_aware_candidates<'a>(
    target_name: &str,
    my_imports: &[String],
    cands: &'a [Cand],
) -> Vec<&'a Cand> {
    let import_paths: Vec<String> = my_imports.iter().map(|imp| imp.replace('/', ".")).collect();
    let import_tails: Vec<Vec<&str>> = import_paths
        .iter()
        .map(|imp| imp.split('.').collect())
        .collect();
    if import_tails.is_empty() {
        return Vec::new();
    }

    let mut best: Vec<(usize, &'a Cand)> = Vec::new();
    for cand in cands {
        let qname = cand.qname.as_deref().unwrap_or("").replace('/', ".");
        let mut qsegs: Vec<&str> = if qname.is_empty() {
            vec![target_name]
        } else {
            qname.split('.').collect()
        };
        if qsegs.is_empty() || (qsegs.len() == 1 && qsegs[0].is_empty()) {
            qsegs = vec![target_name];
        }
        let mut longest = 0usize;
        for tail in &import_tails {
            let suffix_len = common_suffix_len(&qsegs, tail);
            let mut prefix_len = 0usize;
            if !qsegs.is_empty() {
                if qsegs.windows(tail.len()).any(|w| w == tail.as_slice()) {
                    prefix_len = tail.len();
                }
                if prefix_len == 0 && tail[tail.len() - 1] == qsegs[0] {
                    prefix_len = 1;
                }
            }
            longest = longest.max(suffix_len.max(prefix_len));
        }
        if longest >= 1 {
            best.push((longest, cand));
        }
    }

    if best.is_empty() {
        return Vec::new();
    }
    let max_len = best.iter().map(|(score, _)| *score).max().unwrap_or(0);
    best.into_iter()
        .filter(|(score, _)| *score == max_len)
        .map(|(_, cand)| cand)
        .collect()
}

/// Resolve one edge target and return its id plus resolution label.
pub fn resolve_edge(
    target_name: &str,
    source_file_id: &str,
    source_repo: &str,
    ix: &ResolverIndexes,
    receiver_type: Option<&str>,
    call_arity: Option<i64>,
) -> (Option<String>, Label) {
    let mut target = target_name.to_string();

    // Rewrite explicit local aliases before the tier walk.
    if let Some(imported) = ix
        .aliases_by_file
        .get(source_file_id)
        .and_then(|aliases| aliases.get(&target))
    {
        let dotted = imported.replace('/', ".");
        let segs: Vec<&str> = dotted.split('.').collect();
        if let Some(&last) = segs.last()
            && !last.is_empty()
        {
            target = last.to_string();
        }
    }

    let no_cands: Vec<Cand> = Vec::new();
    let cands = ix.symbols_by_name.get(&target).unwrap_or(&no_cands);
    if cands.is_empty() {
        return (None, Label::Unresolved);
    }

    // --- Tier 0: receiver dispatch ---
    if let Some(recv) = receiver_type.filter(|r| !r.is_empty()) {
        let typed = members_of(recv, &target, ix);
        // Never resolve to a symbol that isn't even named target_name.
        let typed_ids: HashSet<&str> = cands
            .iter()
            .map(|c| c.id.as_str())
            .collect::<HashSet<_>>()
            .intersection(&typed.iter().map(|s| s.as_str()).collect::<HashSet<_>>())
            .copied()
            .collect();
        if typed_ids.len() == 1 {
            return (
                Some(typed_ids.into_iter().next().unwrap_or_default().to_string()),
                Label::Exact,
            );
        }
        if typed_ids.len() > 1 {
            // A typed multi-match binds only a unique same-file survivor.
            let same_file_typed: HashSet<&str> = typed_ids
                .intersection(
                    &cands
                        .iter()
                        .filter(|c| c.file_id == source_file_id)
                        .map(|c| c.id.as_str())
                        .collect::<HashSet<_>>(),
                )
                .copied()
                .collect();
            if same_file_typed.len() == 1 {
                return (
                    Some(
                        same_file_typed
                            .into_iter()
                            .next()
                            .unwrap_or_default()
                            .to_string(),
                    ),
                    Label::Exact,
                );
            }
        }
        // no unique typed match -> fall through to the name-based tiers below
    }

    // --- Tier 1: same-file ---
    let same_file: Vec<&Cand> = cands
        .iter()
        .filter(|c| c.file_id == source_file_id)
        .collect();
    if same_file.len() == 1 {
        return (Some(same_file[0].id.clone()), Label::Exact);
    }
    if same_file.len() > 1 {
        if let Some(id) = arity_unique_match(&same_file, call_arity) {
            return (Some(id), Label::Exact);
        }
        return (None, Label::Ambiguous);
    }

    // --- Tier 2: import-aware ---
    if let Some(my_imports) = ix.imports_by_file.get(source_file_id)
        && !my_imports.is_empty()
    {
        let import_match = import_aware_candidates(&target, my_imports, cands);
        if import_match.len() == 1 {
            return (Some(import_match[0].id.clone()), Label::Exact);
        }
        if import_match.len() > 1 {
            if let Some(id) = arity_unique_match(&import_match, call_arity) {
                return (Some(id), Label::Exact);
            }
            return (None, Label::Ambiguous);
        }
        // import_match == [] : imports don't mention this name; fall through.
    }

    // --- Tier 3: same-repo ---
    let same_repo: Vec<&Cand> = cands.iter().filter(|c| c.repo == source_repo).collect();
    if same_repo.len() == 1 {
        return (Some(same_repo[0].id.clone()), Label::Exact);
    }
    if same_repo.len() > 1 {
        if let Some(id) = arity_unique_match(&same_repo, call_arity) {
            return (Some(id), Label::Exact);
        }
        return (None, Label::Ambiguous);
    }

    // --- Tier 4: global ---
    if cands.len() == 1 {
        return (Some(cands[0].id.clone()), Label::Exact);
    }
    let global: Vec<&Cand> = cands.iter().collect();
    if let Some(id) = arity_unique_match(&global, call_arity) {
        return (Some(id), Label::Exact);
    }
    (None, Label::Ambiguous)
}

/// Resolve and persist edges for one repo; consumes `(file_id, edges)` pairs in order.
pub fn resolve_repo_edges(
    conn: &mut Connection,
    repo: &str,
    edges_by_file: &[(String, Vec<EdgeSignal>)],
) -> rusqlite::Result<Stats> {
    let ix = ResolverIndexes::build(conn, Some(repo))?;

    let mut stats = Stats::default();
    let tx = conn.transaction()?;
    {
        let mut stmt = tx.prepare(RESOLVE_SQL)?;
        for (file_id, edges) in edges_by_file {
            for edge in edges {
                let (target_id, label) = if edge.generic_tier {
                    (None, Label::Unresolved)
                } else {
                    resolve_edge(
                        &edge.target_name,
                        file_id,
                        repo,
                        &ix,
                        edge.receiver_type.as_deref(),
                        edge.call_arity,
                    )
                };
                // Resolved edges drop the bare name (queries join via target_id);
                // unresolved/ambiguous keep it so fuzzy matching can still use it.
                let stored_name = if target_id.is_some() {
                    None
                } else {
                    Some(edge.target_name.as_str())
                };
                stmt.execute(rusqlite::params![
                    target_id,
                    stored_name,
                    label.as_str(),
                    edge.target_name,
                    edge.receiver_type,
                    edge.call_arity,
                    edge.edge_id
                ])?;
                stats.record(label);
            }
        }
    }
    tx.commit()?;
    Ok(stats)
}

/// Re-resolve edges in every repo whose target name is in `changed_target_names`.
pub fn repair_incoming_edges(
    conn: &mut Connection,
    changed_target_names: &[&str],
) -> rusqlite::Result<Stats> {
    let mut stats = Stats::default();
    if changed_target_names.is_empty() {
        return Ok(stats);
    }

    // ref_name finds resolved edges; target_id IS NULL keeps structurally
    // pinned edges (contains) out of name re-resolution.
    let placeholders = changed_target_names
        .iter()
        .map(|_| "?")
        .collect::<Vec<_>>()
        .join(",");
    let sql = format!(
        "SELECT e.id AS eid, e.kind AS kind,
                COALESCE(e.ref_name, e.target_name) AS tname,
                e.receiver_type AS receiver_type, e.call_arity AS call_arity,
                f.id AS file_id, f.repo_id AS repo, f.language AS language
         FROM edges e
         JOIN symbols s ON e.source_id = s.id
         JOIN files f ON s.file_id = f.id
         WHERE e.ref_name IN ({placeholders})
            OR (e.target_id IS NULL AND e.target_name IN ({placeholders}))"
    );
    let params: Vec<&dyn rusqlite::ToSql> = changed_target_names
        .iter()
        .chain(changed_target_names.iter())
        .map(|name| name as &dyn rusqlite::ToSql)
        .collect();

    let mut rows: Vec<RepairRow> = Vec::new();
    {
        let mut stmt = conn.prepare(&sql)?;
        let mapped = stmt.query_map(params.as_slice(), |r| {
            Ok(RepairRow {
                edge_id: r.get("eid")?,
                kind: r.get("kind")?,
                target_name: r.get("tname")?,
                receiver_type: r.get("receiver_type")?,
                call_arity: r.get("call_arity")?,
                file_id: r.get("file_id")?,
                repo: r.get("repo")?,
                language: r.get("language")?,
            })
        })?;
        for row in mapped {
            rows.push(row?);
        }
    }
    if rows.is_empty() {
        return Ok(stats);
    }

    // Symbols and imports are keyed globally; members and ancestors stay
    // scoped to the source repo, mirroring resolve_repo_edges.
    let mut ix = ResolverIndexes {
        symbols_by_name: build_symbol_index(conn)?,
        ..ResolverIndexes::default()
    };
    (ix.imports_by_file, ix.aliases_by_file) = build_import_index(conn, None)?;
    let mut repos: Vec<&str> = Vec::new();
    for row in &rows {
        if !repos.contains(&row.repo.as_str()) {
            repos.push(&row.repo);
        }
    }

    // Every verdict is computed before the first write, so the type indexes
    // see the pre-repair edge state.
    let mut updates: Vec<(Option<String>, Option<&str>, Label, &str)> = Vec::new();
    for repo in repos {
        ix.members_by_type = build_members_index(conn, Some(repo))?;
        ix.ancestors = build_ancestor_index(conn, Some(repo))?;
        for row in rows.iter().filter(|r| r.repo == repo) {
            let Some(target_name) = row.target_name.as_deref().filter(|t| !t.is_empty()) else {
                continue;
            };
            let (target_id, label) =
                if row.kind == "calls" && GENERIC_TIER_LANGUAGES.contains(&row.language.as_str()) {
                    (None, Label::Unresolved)
                } else {
                    resolve_edge(
                        target_name,
                        &row.file_id,
                        repo,
                        &ix,
                        row.receiver_type.as_deref(),
                        row.call_arity,
                    )
                };
            let stored_name = if target_id.is_some() {
                None
            } else {
                Some(target_name)
            };
            updates.push((target_id, stored_name, label, &row.edge_id));
            stats.record(label);
        }
    }

    let tx = conn.transaction()?;
    {
        let mut stmt = tx.prepare(REPAIR_SQL)?;
        for (target_id, stored_name, label, edge_id) in &updates {
            stmt.execute(rusqlite::params![
                target_id,
                stored_name,
                label.as_str(),
                edge_id
            ])?;
        }
    }
    tx.commit()?;
    Ok(stats)
}

struct RepairRow {
    edge_id: String,
    kind: String,
    target_name: Option<String>,
    receiver_type: Option<String>,
    call_arity: Option<i64>,
    file_id: String,
    repo: String,
    language: String,
}
