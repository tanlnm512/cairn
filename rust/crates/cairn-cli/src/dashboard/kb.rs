//! Bundle-driven data: memories, tasks, knowledge docs, wiki pages.

use std::collections::BTreeMap;
use std::path::Path;

use serde_json::{Value, json};
use yaml_rust2::Yaml;

use cairn_query::tools_km::{Bundle, Concept};

use super::markdown::render_markdown_with_toc;

const TASK_DIR: &str = "_tasks";
const MANIFEST_DIR: &str = "_wiki";
const MANIFEST_FILENAME: &str = "manifest.json";
const MANIFEST_SCHEMA: &str = "cairn-wiki-manifest-3";

/// Wiki lifecycle vocabulary, derived at read time, never stored.
pub const DERIVED_STATES: [&str; 6] = [
    "planned",
    "queued",
    "in-progress",
    "promoted",
    "failed",
    "dropped",
];

/// Concept id for a page's promoted content.
pub fn page_concept_id(repo: &str, page_id: &str) -> String {
    format!("wiki/pages/{repo}/{page_id}")
}

/// Split a concept id into family and slug, keeping malformed ids renderable.
pub fn split_doc_id(doc_id: &str) -> (String, String) {
    let parts: Vec<&str> = doc_id.split('/').collect();
    if parts.len() < 3 {
        return (
            parts.last().copied().unwrap_or("").to_string(),
            String::new(),
        );
    }
    (parts[1].to_string(), parts[2..].join("/"))
}

fn ext_list(concept: &Concept, key: &str) -> Vec<String> {
    match concept.ext(key) {
        Some(Yaml::Array(items)) => items
            .iter()
            .map(|item| match item {
                Yaml::String(s) => s.clone(),
                Yaml::Integer(i) => i.to_string(),
                Yaml::Real(s) => s.clone(),
                Yaml::Boolean(b) => b.to_string(),
                _ => String::new(),
            })
            .collect(),
        _ => Vec::new(),
    }
}

fn ext_hashes(concept: &Concept, key: &str) -> Vec<BTreeMap<String, Yaml>> {
    match concept.ext(key) {
        Some(Yaml::Array(items)) => items
            .iter()
            .filter_map(|item| match item {
                Yaml::Hash(hash) => Some(
                    hash.iter()
                        .filter_map(|(k, v)| k.as_str().map(|k| (k.to_string(), v.clone())))
                        .collect(),
                ),
                _ => None,
            })
            .collect(),
        _ => Vec::new(),
    }
}

fn concept_row(concept: &Concept, id: &str, kind_key: &str) -> Value {
    let (tier, slug) = split_doc_id(id);
    let entry_type = concept
        .ext_str(kind_key)
        .map(String::from)
        .unwrap_or_else(|| concept.type_.clone());
    json!({
        "id": id,
        "type": entry_type,
        "title": concept.title.clone().unwrap_or_else(|| id.to_string()),
        "tier": tier,
        "slug": slug,
        "timestamp": concept.timestamp.clone().unwrap_or_default(),
    })
}

fn epoch() -> i64 {
    0
}

fn ts_or_epoch(timestamp: &str) -> i64 {
    super::env::parse_iso_utc(timestamp).unwrap_or_else(epoch)
}

// --- memories ---

/// Readable recent memories, newest first, filtered before the limit.
pub fn get_recent_memories(knowledge_dir: &str, memory_type: Option<&str>) -> Vec<Value> {
    let bundle = Bundle::new(knowledge_dir);
    let mut entries: Vec<Value> = Vec::new();
    for cid in bundle.list_concepts(Some("memory/")) {
        let Ok(concept) = bundle.read_concept(&cid) else {
            continue;
        };
        let entry_type = concept
            .ext_str("memory_type")
            .map(String::from)
            .unwrap_or_else(|| concept.type_.clone());
        if let Some(want) = memory_type
            && entry_type != want
        {
            continue;
        }
        entries.push(concept_row(&concept, &cid, "memory_type"));
    }
    entries.sort_by(|a, b| {
        let at = ts_or_epoch(a["timestamp"].as_str().unwrap_or(""));
        let bt = ts_or_epoch(b["timestamp"].as_str().unwrap_or(""));
        bt.cmp(&at)
    });
    entries.truncate(20);
    entries
}

/// A supersedes-chain neighbor as `{id, tier, slug, title}`; unreadable
/// neighbors keep their bare id as the title.
fn memory_neighbor(bundle: &Bundle, memory_id: &str) -> Value {
    let (tier, slug) = split_doc_id(memory_id);
    let title = bundle
        .read_concept(memory_id)
        .ok()
        .and_then(|c| c.title)
        .unwrap_or_else(|| memory_id.to_string());
    json!({"id": memory_id, "tier": tier, "slug": slug, "title": title})
}

/// The memory detail payload, or None for an unknown/non-memory id.
pub fn get_memory_detail(knowledge_dir: &str, memory_id: &str) -> Option<Value> {
    if !memory_id.starts_with("memory/") {
        return None;
    }
    let bundle = Bundle::new(knowledge_dir);
    let concept = bundle.read_concept(memory_id).ok()?;
    let (html, toc) = render_markdown_with_toc(&concept.body, &Default::default());
    let (tier, slug) = split_doc_id(memory_id);
    let supersedes: Vec<Value> = ext_list(&concept, "memory_supersedes")
        .iter()
        .map(|older| memory_neighbor(&bundle, older))
        .collect();
    let superseded_by = concept
        .ext_str("memory_superseded_by")
        .map(|newer| memory_neighbor(&bundle, newer));
    Some(json!({
        "id": memory_id,
        "tier": tier,
        "slug": slug,
        "type": concept.ext_str("memory_type").map(String::from).unwrap_or_else(|| concept.type_.clone()),
        "title": concept.title.clone().unwrap_or_else(|| memory_id.to_string()),
        "description": concept.description.clone().unwrap_or_default(),
        "status": concept.ext_str("memory_status").map(String::from).unwrap_or_else(|| tier.clone()),
        "score": concept.ext_f64("memory_score").map(Value::from).unwrap_or(Value::Null),
        "signals": yaml_to_value(concept.ext("memory_signals")),
        "tags": concept.tags,
        "session_origin": concept.ext_str("session_origin").unwrap_or(""),
        "recorded": concept.timestamp.clone().unwrap_or_default(),
        "valid_from": concept.ext_str("valid_from").unwrap_or(""),
        "valid_until": concept.ext_str("valid_until").unwrap_or(""),
        "is_latest": concept.ext_bool("memory_is_latest").unwrap_or(true),
        "promotion_history": yaml_to_value(concept.ext("promotion_history")),
        "supersedes": supersedes,
        "superseded_by": superseded_by,
        "html": html,
        "toc": toc,
    }))
}

fn yaml_to_value(yaml: Option<&Yaml>) -> Value {
    match yaml {
        None => Value::Null,
        Some(Yaml::String(s)) => Value::from(s.clone()),
        Some(Yaml::Integer(i)) => Value::from(*i),
        Some(Yaml::Real(s)) => s.parse::<f64>().map(Value::from).unwrap_or(Value::Null),
        Some(Yaml::Boolean(b)) => Value::from(*b),
        Some(Yaml::Array(items)) => {
            Value::Array(items.iter().map(|item| yaml_to_value(Some(item))).collect())
        }
        Some(Yaml::Hash(hash)) => {
            let mut map = serde_json::Map::new();
            for (key, value) in hash {
                if let Some(key) = key.as_str() {
                    map.insert(key.to_string(), yaml_to_value(Some(value)));
                }
            }
            Value::Object(map)
        }
        _ => Value::Null,
    }
}

// --- task queue ---

pub struct Task {
    pub id: String,
    pub kind: String,
    pub resource: String,
    pub status: String,
    pub attempt: i64,
    pub created_at: String,
    facts_repo: Option<String>,
    pub row: Value,
}

fn concept_to_task(concept: &Concept) -> Task {
    let id = concept
        .concept_id
        .rsplit('/')
        .next()
        .unwrap_or("")
        .to_string();
    let status = concept
        .status
        .clone()
        .filter(|s| !s.is_empty())
        .or_else(|| concept.ext_str("status").map(String::from))
        .unwrap_or_else(|| "pending".into());
    let kind = concept.ext_str("task_kind").unwrap_or("").to_string();
    let resource = concept.resource.clone().unwrap_or_default();
    let facts_repo = match concept.ext("facts") {
        Some(Yaml::Hash(hash)) => hash
            .get(&yaml_rust2::yaml::Yaml::String("repo".into()))
            .and_then(|v| v.as_str())
            .map(String::from),
        _ => None,
    };
    let row = json!({
        "id": id,
        "kind": kind,
        "status": status,
        "resource": resource,
        "assigned_to": concept.ext_str("assigned_to").unwrap_or(""),
        "created_at": concept.timestamp.clone().unwrap_or_default(),
        "claimed_at": concept.ext_str("claimed_at").unwrap_or(""),
        "completed_at": concept.ext_str("completed_at").unwrap_or(""),
    });
    Task {
        id,
        kind,
        resource,
        status,
        attempt: concept.ext_f64("attempt").unwrap_or(1.0) as i64,
        created_at: concept.timestamp.clone().unwrap_or_default(),
        facts_repo,
        row,
    }
}

/// List tasks, optionally filtered by status (llm.tasks.list_tasks port).
pub fn list_tasks(bundle: &Bundle, status: Option<&str>) -> Vec<Task> {
    let mut out = Vec::new();
    for cid in bundle.list_concepts(Some(&format!("{TASK_DIR}/"))) {
        if cid.ends_with(".result") {
            continue;
        }
        let Ok(concept) = bundle.read_concept(&cid) else {
            continue;
        };
        if concept.type_ != "Task" {
            continue;
        }
        let task = concept_to_task(&concept);
        if let Some(want) = status
            && task.status != want
        {
            continue;
        }
        out.push(task);
    }
    out
}

/// LLM task-queue entries as plain dicts, optionally filtered by status.
pub fn get_task_queue(knowledge_dir: &str, status: Option<&str>) -> Vec<Value> {
    list_tasks(&Bundle::new(knowledge_dir), status)
        .into_iter()
        .map(|task| task.row)
        .collect()
}

/// The task result's critic verdict, or None when the result never landed.
fn result_critic_status(bundle: &Bundle, task_id: &str) -> Option<String> {
    bundle
        .read_concept(&format!("{TASK_DIR}/{task_id}.result"))
        .ok()
        .and_then(|c| c.ext_str("critic_status").map(String::from))
}

/// Newest task by (created_at, attempt) — the head of a revise chain.
fn latest_task<'a>(tasks: &[&'a Task]) -> Option<&'a Task> {
    tasks
        .iter()
        .max_by(|a, b| {
            a.created_at
                .cmp(&b.created_at)
                .then(a.attempt.cmp(&b.attempt))
        })
        .copied()
}

fn is_write_kind(task_kind: &str) -> bool {
    task_kind.starts_with("wiki-page") && !task_kind.starts_with("wiki-page-enrich")
}

fn chain_key(task: &Task) -> String {
    if task.resource.contains('/') {
        return task.resource.clone();
    }
    match &task.facts_repo {
        Some(repo) => format!("{repo}/{}", task.resource),
        None => task.resource.clone(),
    }
}

/// Composite key `{repo}/{resource}` -> the page's write-chain tasks.
fn page_chains(bundle: &Bundle) -> Vec<(String, Vec<Task>)> {
    let mut chains: Vec<(String, Vec<Task>)> = Vec::new();
    for task in list_tasks(bundle, None) {
        if !is_write_kind(&task.kind) {
            continue;
        }
        let key = chain_key(&task);
        match chains.iter_mut().find(|(k, _)| *k == key) {
            Some((_, chain)) => chain.push(task),
            None => chains.push((key, vec![task])),
        }
    }
    chains
}

/// The page's lifecycle state, derived at read time — never stored.
fn derived_state(bundle: &Bundle, repo: &str, page_id: &str, chain: &[Task]) -> String {
    if read_page_concept(bundle, repo, page_id).is_some() {
        return "promoted".into();
    }
    if chain.iter().any(|t| t.status == "in-progress") {
        return "in-progress".into();
    }
    if chain.iter().any(|t| t.status == "pending") {
        return "queued".into();
    }
    if chain.iter().any(|t| t.status == "dropped") {
        return "dropped".into();
    }
    let done: Vec<&Task> = chain.iter().filter(|t| t.status == "done").collect();
    if let Some(head) = latest_task(&done)
        && result_critic_status(bundle, &head.id).as_deref() != Some("passed")
    {
        return "failed".into();
    }
    "planned".into()
}

/// Content-only provenance sha; a page with no content has no sha.
fn recorded_sha(concept: &Concept) -> Option<String> {
    concept.ext_str("commit_sha").map(String::from)
}

/// fresh: recorded sha equals HEAD; stale: both present and differing.
fn staleness(recorded: Option<&str>, head: Option<&str>) -> &'static str {
    match (recorded, head) {
        (Some(recorded), Some(head)) if !recorded.is_empty() && !head.is_empty() => {
            if recorded == head { "fresh" } else { "stale" }
        }
        _ => "unknown",
    }
}

/// The promoted `Wiki-Article` concept for a page, or None.
fn read_page_concept(bundle: &Bundle, repo: &str, page_id: &str) -> Option<Concept> {
    let concept = bundle.read_concept(&page_concept_id(repo, page_id)).ok()?;
    if concept.type_ != "Wiki-Article" {
        return None;
    }
    Some(concept)
}

/// The named repo's HEAD commit hash under the workspace, or None.
fn get_repo_head(repo_name: &str, workspace: Option<&str>) -> Option<String> {
    let repo_path = workspace.map(|ws| Path::new(ws).join(repo_name))?;
    let output = std::process::Command::new("git")
        .arg("-C")
        .arg(&repo_path)
        .args(["rev-parse", "HEAD"])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let sha = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if sha.is_empty() { None } else { Some(sha) }
}

/// The workspace registered to the store owning `knowledge_dir`; None when
/// the store is not a keyed store under CAIRN_HOME.
fn store_workspace(knowledge_dir: &str) -> Option<String> {
    let home = crate::store_paths::current_cairn_home();
    let store_dir = crate::store_paths::resolve_path(Path::new(knowledge_dir).parent()?);
    let registry = crate::store_paths::load_registry();
    registry.into_iter().find_map(|(ws, key)| {
        if !key.is_empty() && home.join(&key) == store_dir {
            Some(ws)
        } else {
            None
        }
    })
}

fn load_manifest(knowledge_dir: &str) -> Result<serde_json::Map<String, Value>, String> {
    let path = Path::new(knowledge_dir)
        .join(MANIFEST_DIR)
        .join(MANIFEST_FILENAME);
    if !path.is_file() {
        let doc = json!({"schema": MANIFEST_SCHEMA, "pages": {}});
        return Ok(doc.as_object().cloned().unwrap_or_default());
    }
    let text = std::fs::read_to_string(&path)
        .map_err(|e| format!("wiki manifest {} cannot be read: {e}", path.display()))?;
    let mut doc: Value = serde_json::from_str(&text)
        .map_err(|e| format!("wiki manifest {} is not valid JSON: {e}", path.display()))?;
    if !doc.is_object() {
        return Err(format!(
            "wiki manifest {} must contain a JSON object",
            path.display()
        ));
    }
    if doc.get("pages").is_none() {
        doc["pages"] = Value::Object(serde_json::Map::new());
    }
    if doc["schema"].as_str() != Some(MANIFEST_SCHEMA) {
        // Schema-1 rows (page_id alone) re-derive their repo from the task
        // facts or the wiki/pages tree; rows with neither are dropped.
        let bundle = Bundle::new(knowledge_dir);
        let pages = doc["pages"].as_object().cloned().unwrap_or_default();
        let pages_dir = Path::new(knowledge_dir).join("wiki/pages");
        let mut migrated = serde_json::Map::new();
        for (key, row) in pages {
            if key.contains('/') {
                migrated.insert(key, row);
                continue;
            }
            let page_id = row
                .get("page_id")
                .and_then(Value::as_str)
                .unwrap_or(&key)
                .to_string();
            let mut repo = row
                .get("task_id")
                .and_then(Value::as_str)
                .and_then(|task_id| bundle.read_concept(&format!("{TASK_DIR}/{task_id}")).ok())
                .and_then(|task| task_ext_repo(&task));
            if repo.is_none()
                && let Ok(repos) = std::fs::read_dir(&pages_dir)
            {
                repo = repos
                    .flatten()
                    .filter(|entry| entry.path().is_dir())
                    .map(|entry| entry.file_name().to_string_lossy().into_owned())
                    .filter(|repo| pages_dir.join(repo).join(format!("{page_id}.md")).is_file())
                    .min();
            }
            if let Some(repo) = repo {
                migrated.insert(format!("{repo}/{page_id}"), row);
            }
        }
        doc["pages"] = Value::Object(migrated);
        doc["schema"] = Value::from(MANIFEST_SCHEMA);
    }
    let pages = doc
        .get("pages")
        .and_then(Value::as_object)
        .cloned()
        .ok_or_else(|| {
            format!(
                "wiki manifest {} pages must be a mapping keyed by {{repo}}/{{page_id}}",
                path.display()
            )
        })?;
    if let Some((key, _)) = pages.iter().find(|(_, row)| !row.is_object()) {
        return Err(format!(
            "wiki manifest {} page row {key:?} must be a JSON object",
            path.display()
        ));
    }
    Ok(pages)
}

fn task_ext_repo(task: &Concept) -> Option<String> {
    match task.ext("facts") {
        Some(Yaml::Hash(hash)) => hash
            .get(&yaml_rust2::yaml::Yaml::String("repo".into()))
            .and_then(|v| v.as_str())
            .map(String::from),
        _ => None,
    }
}

/// Manifest pages with derived lifecycle, promotion, and staleness state.
pub fn get_wiki_pages(knowledge_dir: &str, repo: Option<&str>) -> Result<Vec<Value>, String> {
    let bundle = Bundle::new(knowledge_dir);
    let pages_map = load_manifest(knowledge_dir)?;
    let chains = page_chains(&bundle);
    let workspace = store_workspace(knowledge_dir);
    let empty_chain: Vec<Task> = Vec::new();
    let mut pages = Vec::new();
    for (key, row) in &pages_map {
        let (key_repo, key_page) = match key.split_once('/') {
            Some(pair) => pair,
            None => continue,
        };
        if let Some(want) = repo
            && key_repo != want
        {
            continue;
        }
        let concept = read_page_concept(&bundle, key_repo, key_page);
        let head = get_repo_head(key_repo, workspace.as_deref());
        let chain = chains
            .iter()
            .find(|(k, _)| k == key)
            .map(|(_, chain)| chain)
            .unwrap_or(&empty_chain);
        pages.push(json!({
            "repo": key_repo,
            "page_id": key_page,
            "title": row.get("title").and_then(Value::as_str).filter(|t| !t.is_empty())
                .map(Value::from).unwrap_or_else(|| Value::from(key_page)),
            "description": row.get("description").and_then(Value::as_str).unwrap_or(""),
            "state": derived_state(&bundle, key_repo, key_page, chain),
            "promoted": concept.is_some(),
            "staleness": staleness(concept.as_ref().and_then(recorded_sha).as_deref(), head.as_deref()),
        }));
    }
    Ok(pages)
}

/// Deep link to the symbol's graph view (the /graph seam).
fn symbol_graph_href(symbol: &str, store_suffix: &str) -> String {
    format!(
        "/graph?scope=symbol&focus={}{}",
        super::shell::urlencode(symbol),
        store_suffix
    )
}

/// Map only verified symbol refs to graph links using escaped span keys,
/// keeping first-seen order.
fn wiki_ref_map(concept: &Concept, row: &Value, store_suffix: &str) -> Vec<(String, String)> {
    let mut refs: Vec<(String, String)> = Vec::new();
    let has = |key: &str, refs: &[(String, String)]| refs.iter().any(|(k, _)| k == key);
    for entry in ext_hashes(concept, "sources") {
        if let Some(Yaml::String(symbol)) = entry.get("symbol") {
            let key = super::markdown::html_escape(symbol, false);
            if !has(&key, &refs) {
                refs.push((key, symbol_graph_href(symbol, store_suffix)));
            }
        }
    }
    if let Some(seeds) = row.get("seeds").and_then(Value::as_object)
        && let Some(symbols) = seeds.get("symbols").and_then(Value::as_array)
    {
        for symbol in symbols {
            if let Some(symbol) = symbol.as_str() {
                let key = super::markdown::html_escape(symbol, false);
                if !has(&key, &refs) {
                    refs.push((key, symbol_graph_href(symbol, store_suffix)));
                }
            }
        }
    }
    refs
}

/// Ref pairs as the ordered map the template reads.
fn refs_to_value(refs: &[(String, String)]) -> Value {
    let mut map = serde_json::Map::new();
    for (key, href) in refs {
        map.insert(key.clone(), Value::from(href.clone()));
    }
    Value::Object(map)
}

/// One promoted wiki page with rendered body, refs, and staleness.
pub fn get_wiki_page(
    knowledge_dir: &str,
    page_id: &str,
    repo: Option<&str>,
    store_key: &str,
) -> Result<Option<Value>, String> {
    let bundle = Bundle::new(knowledge_dir);
    let pages_map = load_manifest(knowledge_dir)?;
    let chains = page_chains(&bundle);
    let workspace = store_workspace(knowledge_dir);
    let empty_chain: Vec<Task> = Vec::new();
    for (key, row) in &pages_map {
        let Some((key_repo, key_page)) = key.split_once('/') else {
            continue;
        };
        if let Some(want) = repo
            && key_repo != want
        {
            continue;
        }
        if key_page != page_id {
            continue;
        }
        let Some(concept) = read_page_concept(&bundle, key_repo, key_page) else {
            continue;
        };
        let store_suffix = if store_key.is_empty() {
            String::new()
        } else {
            format!("&store={}", super::shell::urlencode(store_key))
        };
        let head = get_repo_head(key_repo, workspace.as_deref());
        let chain = chains
            .iter()
            .find(|(k, _)| k == key)
            .map(|(_, chain)| chain)
            .unwrap_or(&empty_chain);
        let ref_hrefs = wiki_ref_map(&concept, row, &store_suffix);
        let link_map: std::collections::HashMap<String, String> =
            ref_hrefs.iter().cloned().collect();
        let (html, toc) = render_markdown_with_toc(&concept.body, &link_map);
        let sources = yaml_to_value(concept.ext("sources"));
        return Ok(Some(json!({
            "repo": key_repo,
            "page_id": key_page,
            "title": row.get("title").and_then(Value::as_str).filter(|t| !t.is_empty())
                .map(Value::from)
                .or(concept.title.clone().map(Value::from))
                .unwrap_or_else(|| Value::from(key_page)),
            "state": derived_state(&bundle, key_repo, key_page, chain),
            "html": html,
            "toc": toc,
            "ref_hrefs": refs_to_value(&ref_hrefs),
            "sources": if sources.is_null() { Value::Array(Vec::new()) } else { sources },
            "staleness": staleness(recorded_sha(&concept).as_deref(), head.as_deref()),
        })));
    }
    Ok(None)
}

// --- knowledge docs ---

/// Normalize a doc id to its bare bundle-relative concept id.
fn normalize_doc_id(bundle: &Bundle, concept_id: &str) -> String {
    let cid = concept_id.strip_suffix(".md").unwrap_or(concept_id);
    let path = bundle.root.join(format!("{cid}.md"));
    let resolved = crate::store_paths::resolve_path(&path);
    let root = crate::store_paths::resolve_path(&bundle.root);
    match resolved.strip_prefix(&root) {
        Ok(rel) => rel
            .to_string_lossy()
            .trim_end_matches(".md")
            .replace('\\', "/"),
        Err(_) => cid.to_string(),
    }
}

/// Resolve a knowledge doc or reject missing and foreign ids.
fn resolve_knowledge_doc(bundle: &Bundle, doc_id: &str) -> Option<Concept> {
    let concept = bundle.read_concept(doc_id).ok()?;
    let normalized = normalize_doc_id(bundle, &concept.concept_id);
    if normalized == "knowledge" || normalized.starts_with("knowledge/") {
        return Some(concept);
    }
    None
}

/// Distinct bidirectional link counts, zero on stores without the index.
fn knowledge_link_counts(conn: Option<&rusqlite::Connection>) -> BTreeMap<String, i64> {
    let Some(conn) = conn else {
        return BTreeMap::new();
    };
    let present = rusqlite::Connection::query_row(
        conn,
        "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name = 'knowledge_edges'",
        [],
        |row| row.get::<_, i64>(0),
    )
    .unwrap_or(0);
    if present == 0 {
        return BTreeMap::new();
    }
    let mut stmt = match conn.prepare("SELECT doc_id, related_id FROM knowledge_edges") {
        Ok(stmt) => stmt,
        Err(_) => return BTreeMap::new(),
    };
    let pairs: Vec<(String, String)> = stmt
        .query_map([], |row| Ok((row.get(0)?, row.get(1)?)))
        .map(|rows| rows.filter_map(Result::ok).collect())
        .unwrap_or_default();
    let mut neighbors: BTreeMap<String, std::collections::BTreeSet<String>> = BTreeMap::new();
    for (doc_id, related_id) in pairs {
        neighbors
            .entry(doc_id.clone())
            .or_default()
            .insert(related_id.clone());
        neighbors.entry(related_id).or_default().insert(doc_id);
    }
    neighbors
        .into_iter()
        .map(|(doc_id, seen)| (doc_id, seen.len() as i64))
        .collect()
}

/// Knowledge docs once with corpus-wide filter counts before filtering.
pub fn list_knowledge_docs(
    conn: Option<&rusqlite::Connection>,
    knowledge_dir: &str,
    family: Option<&str>,
    status: Option<&str>,
    tag: Option<&str>,
) -> Value {
    let bundle = Bundle::new(knowledge_dir);
    let link_counts = knowledge_link_counts(conn);
    let mut docs: Vec<Value> = Vec::new();
    let mut families: serde_json::Map<String, Value> = serde_json::Map::new();
    let mut statuses: serde_json::Map<String, Value> = serde_json::Map::new();
    let bump = |map: &mut serde_json::Map<String, Value>, key: &str| {
        let count = map.get(key).and_then(Value::as_i64).unwrap_or(0) + 1;
        map.insert(key.to_string(), Value::from(count));
    };
    for cid in bundle.list_concepts(Some("knowledge/")) {
        let Ok(concept) = bundle.read_concept(&cid) else {
            continue;
        };
        let doc_id = normalize_doc_id(&bundle, &cid);
        let (doc_family, slug) = split_doc_id(&doc_id);
        let doc_status = concept
            .ext_str("doc_status")
            .unwrap_or("active")
            .to_string();
        bump(&mut families, &doc_family);
        bump(&mut statuses, &doc_status);
        if let Some(want) = family
            && doc_family != want
        {
            continue;
        }
        if let Some(want) = status
            && doc_status != want
        {
            continue;
        }
        if let Some(want) = tag
            && !concept.tags.iter().any(|t| t == want)
        {
            continue;
        }
        docs.push(json!({
            "id": doc_id,
            "family": doc_family,
            "slug": slug,
            "title": concept.title.clone().unwrap_or(doc_id.clone()),
            "status": doc_status,
            "tags": concept.tags,
            "links": link_counts.get(&doc_id).copied().unwrap_or(0),
            "source": concept.ext_str("doc_source").unwrap_or(""),
            "updated": concept.timestamp.clone().unwrap_or_default(),
        }));
    }
    docs.sort_by(|a, b| {
        a["family"]
            .as_str()
            .unwrap_or("")
            .cmp(b["family"].as_str().unwrap_or(""))
            .then(
                a["title"]
                    .as_str()
                    .unwrap_or("")
                    .cmp(b["title"].as_str().unwrap_or("")),
            )
    });
    let total: i64 = families.values().filter_map(Value::as_i64).sum();
    json!({
        "docs": docs,
        "families": Value::Object(families),
        "statuses": Value::Object(statuses),
        "total": total,
    })
}

/// One knowledge doc and rendered body, or None outside the namespace.
pub fn get_knowledge_doc(knowledge_dir: &str, doc_id: &str) -> Option<Value> {
    let bundle = Bundle::new(knowledge_dir);
    let concept = resolve_knowledge_doc(&bundle, doc_id)?;
    let (html, toc) = render_markdown_with_toc(&concept.body, &Default::default());
    let bare_id = normalize_doc_id(&bundle, &concept.concept_id);
    let (family, slug) = split_doc_id(&bare_id);
    Some(json!({
        "id": bare_id,
        "family": family,
        "slug": slug,
        "title": concept.title.clone().unwrap_or_else(|| bare_id.clone()),
        "status": concept.ext_str("doc_status").unwrap_or("active"),
        "tags": concept.tags,
        "source": concept.ext_str("doc_source").unwrap_or(""),
        "resource": concept.resource.clone().unwrap_or_default(),
        "updated": concept.timestamp.clone().unwrap_or_default(),
        "html": html,
        "toc": toc,
    }))
}

/// Rows grouped under their relation, groups sorted by relation name.
fn group_related(related: Vec<Value>) -> Vec<Value> {
    let mut groups: BTreeMap<String, Vec<Value>> = BTreeMap::new();
    for row in related {
        let relation = row["relation"].as_str().unwrap_or("").to_string();
        groups.entry(relation).or_default().push(row);
    }
    groups
        .into_iter()
        .map(|(relation, rows)| json!({"relation": relation, "rows": rows}))
        .collect()
}

/// Deterministic normalized neighbors from both edge directions.
fn related_docs(conn: &rusqlite::Connection, bundle: &Bundle, doc_id: &str) -> Vec<Value> {
    let normalized = normalize_doc_id(bundle, doc_id);
    let mut neighbors: Vec<Value> = Vec::new();
    let mut seen: std::collections::BTreeSet<(String, String, String, String)> = Default::default();
    for (direction, column, order) in [
        ("outgoing", "doc_id", "related_id"),
        ("incoming", "related_id", "doc_id"),
    ] {
        let sql = format!(
            "SELECT doc_id, related_id, relation, kind, provenance, created_at \
             FROM knowledge_edges WHERE {column} = ? ORDER BY {order}"
        );
        let Ok(mut stmt) = conn.prepare(&sql) else {
            continue;
        };
        type EdgeRow = (
            String,
            String,
            String,
            String,
            Option<String>,
            Option<String>,
        );
        let rows: Vec<EdgeRow> = stmt
            .query_map([&normalized], |row| {
                Ok((
                    row.get(0)?,
                    row.get(1)?,
                    row.get(2)?,
                    row.get(3)?,
                    row.get(4)?,
                    row.get(5)?,
                ))
            })
            .map(|rows| rows.filter_map(Result::ok).collect())
            .unwrap_or_default();
        for (doc, related, relation, kind, provenance, created_at) in rows {
            let neighbor = if column == "doc_id" { related } else { doc };
            let key = (
                doc_id.to_string(),
                neighbor.clone(),
                relation.clone(),
                kind.clone(),
            );
            if seen.contains(&key) {
                continue;
            }
            let Ok(concept) = bundle.read_concept(&neighbor) else {
                continue;
            };
            seen.insert(key);
            neighbors.push(json!({
                "doc_id": neighbor,
                "title": concept.title.unwrap_or_default(),
                "relation": relation,
                "kind": kind,
                "provenance": provenance.unwrap_or_default(),
                "created_at": created_at.unwrap_or_default(),
                "direction": direction,
            }));
        }
    }
    neighbors
}

/// The ordered supersede chain, or [] when unavailable or malformed.
fn doc_chain(conn: Option<&rusqlite::Connection>, knowledge_dir: &str, doc_id: &str) -> Vec<Value> {
    let Some(conn) = conn else { return Vec::new() };
    let present = rusqlite::Connection::query_row(
        conn,
        "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name = 'knowledge_edges'",
        [],
        |row| row.get::<_, i64>(0),
    )
    .unwrap_or(0);
    if present == 0 {
        return Vec::new();
    }
    let bundle = Bundle::new(knowledge_dir);
    let Some(concept) = resolve_knowledge_doc(&bundle, doc_id) else {
        return Vec::new();
    };
    let start = normalize_doc_id(&bundle, &concept.concept_id);
    // Resolvable supersede adjacency over both mirrored relation spellings.
    let mut newer_of: BTreeMap<String, std::collections::BTreeSet<String>> = BTreeMap::new();
    let mut older_of: BTreeMap<String, std::collections::BTreeSet<String>> = BTreeMap::new();
    let mut concepts: BTreeMap<String, Option<Concept>> = BTreeMap::new();
    let Ok(mut stmt) =
        conn.prepare("SELECT doc_id, related_id, relation FROM knowledge_edges WHERE relation IN ('supersedes', 'superseded-by')")
    else {
        return Vec::new();
    };
    let pairs: Vec<(String, String, String)> = stmt
        .query_map([], |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)))
        .map(|rows| rows.filter_map(Result::ok).collect())
        .unwrap_or_default();
    for (doc, related, relation) in pairs {
        let (newer, older) = if relation == "supersedes" {
            (doc, related)
        } else {
            (related, doc)
        };
        for cid in [&newer, &older] {
            if !concepts.contains_key(cid) {
                concepts.insert(cid.clone(), bundle.read_concept(cid).ok());
            }
        }
        if concepts.get(&newer).map(Option::is_some).unwrap_or(false)
            && concepts.get(&older).map(Option::is_some).unwrap_or(false)
        {
            older_of
                .entry(newer.clone())
                .or_default()
                .insert(older.clone());
            newer_of.entry(older).or_default().insert(newer);
        }
    }
    concepts
        .entry(start.clone())
        .or_insert_with(|| Some(concept));

    let mut back: Vec<String> = Vec::new();
    let mut seen: std::collections::BTreeSet<String> = std::iter::once(start.clone()).collect();
    let mut current = start.clone();
    loop {
        let older = older_of
            .get(&current)
            .and_then(|set| set.iter().find(|c| !seen.contains(*c)))
            .cloned();
        let Some(older) = older else { break };
        back.push(older.clone());
        seen.insert(older.clone());
        current = older;
    }
    let mut forward: Vec<String> = Vec::new();
    let mut current = start.clone();
    loop {
        let newer = newer_of
            .get(&current)
            .and_then(|set| set.iter().find(|c| !seen.contains(*c)))
            .cloned();
        let Some(newer) = newer else { break };
        forward.push(newer.clone());
        seen.insert(newer.clone());
        current = newer;
    }
    let mut chain_ids: Vec<String> = back.into_iter().rev().collect();
    chain_ids.push(start);
    chain_ids.extend(forward);
    let total = chain_ids.len();
    chain_ids
        .iter()
        .enumerate()
        .filter_map(|(pos, cid)| {
            let concept = concepts.get(cid)?.clone()?;
            let (family, slug) = split_doc_id(cid);
            let relation = if pos + 1 < total {
                Value::from("superseded-by")
            } else {
                Value::Null
            };
            let title = concept.title.clone().unwrap_or_default();
            Some(json!({
                "concept_id": cid,
                "title": title,
                "doc_status": concept.ext_str("doc_status").unwrap_or("active"),
                "relation": relation,
                "family": family,
                "slug": slug,
            }))
        })
        .collect()
}

/// The doc's stored code refs with resolution status.
fn doc_refs(conn: Option<&rusqlite::Connection>, doc_id: &str, store_key: &str) -> Vec<Value> {
    let Some(conn) = conn else { return Vec::new() };
    let present = rusqlite::Connection::query_row(
        conn,
        "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name = 'knowledge_doc_refs'",
        [],
        |row| row.get::<_, i64>(0),
    )
    .unwrap_or(0);
    if present == 0 {
        return Vec::new();
    }
    let store_suffix = if store_key.is_empty() {
        String::new()
    } else {
        format!("&store={}", super::shell::urlencode(store_key))
    };
    let Ok(mut stmt) = conn.prepare(
        "SELECT ref, ref_kind, verified FROM knowledge_doc_refs WHERE doc_id = ? ORDER BY ref_kind, ref",
    ) else {
        return Vec::new();
    };
    let rows: Vec<(String, String, i64)> = stmt
        .query_map([doc_id], |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)))
        .map(|rows| rows.filter_map(Result::ok).collect())
        .unwrap_or_default();
    rows.into_iter()
        .map(|(ref_, ref_kind, verified)| {
            json!({
                "ref": ref_,
                "ref_kind": ref_kind,
                "verified": verified != 0,
                "href": if ref_kind == "symbol" {
                    Value::from(symbol_graph_href(&ref_, &store_suffix))
                } else {
                    Value::from("")
                },
            })
        })
        .collect()
}

/// Ingest provenance, preferring a staged manifest row over recorded resource.
fn doc_provenance(doc: &Value, workspace: Option<&str>) -> Value {
    let mut manifest_path = String::new();
    let mut manifest_exists = false;
    let mut row: Value = Value::Null;
    if let Some(workspace) = workspace {
        let path = Path::new(workspace).join(".cairn/ingest-outbox/manifest.json");
        manifest_path = path.to_string_lossy().into_owned();
        if let Ok(text) = std::fs::read_to_string(&path) {
            if let Ok(manifest) = serde_json::from_str::<Value>(&text) {
                manifest_exists = true;
                for candidate in manifest
                    .get("rows")
                    .and_then(Value::as_array)
                    .map(Vec::as_slice)
                    .unwrap_or_default()
                {
                    if candidate.get("concept_id").and_then(Value::as_str) == doc["id"].as_str() {
                        row = candidate.clone();
                        break;
                    }
                }
            }
        } else {
            manifest_exists = path.exists();
        }
    }
    let resource = doc
        .get("resource")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim();
    let mut repo = resource.split('/').next().unwrap_or("").to_string();
    let mut source_path = resource.to_string();
    if !row.is_null() {
        repo = row
            .get("repo")
            .and_then(Value::as_str)
            .filter(|r| !r.is_empty())
            .map(String::from)
            .unwrap_or(repo);
        source_path = row
            .get("source_path")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .map(String::from)
            .unwrap_or(source_path);
    }
    json!({
        "doc_source": doc.get("source").and_then(Value::as_str).unwrap_or(""),
        "repo": repo,
        "source_path": source_path,
        "manifest": {"path": manifest_path, "exists": manifest_exists, "row": row},
        "staged": !resource.is_empty() || !row.is_null(),
    })
}

/// Knowledge identity, body, relationships, refs, and provenance.
pub fn get_knowledge_doc_detail(
    conn: Option<&rusqlite::Connection>,
    knowledge_dir: &str,
    doc_id: &str,
    workspace: Option<&str>,
    store_key: &str,
) -> Option<Value> {
    let mut doc = get_knowledge_doc(knowledge_dir, doc_id)?;
    let related = match conn {
        Some(conn) => {
            let bundle = Bundle::new(knowledge_dir);
            related_docs(conn, &bundle, doc["id"].as_str().unwrap_or(""))
                .into_iter()
                .map(|mut neighbor| {
                    let (family, slug) = split_doc_id(neighbor["doc_id"].as_str().unwrap_or(""));
                    neighbor["family"] = Value::from(family);
                    neighbor["slug"] = Value::from(slug);
                    neighbor
                })
                .collect::<Vec<_>>()
        }
        None => Vec::new(),
    };
    doc["related"] = Value::from(related.clone());
    doc["related_groups"] = Value::from(group_related(related));
    doc["chain"] = Value::from(doc_chain(
        conn,
        knowledge_dir,
        doc["id"].as_str().unwrap_or(""),
    ));
    doc["refs"] = Value::from(doc_refs(conn, doc["id"].as_str().unwrap_or(""), store_key));
    doc["provenance"] = doc_provenance(&doc, workspace);
    Some(doc)
}

/// Resolvable knowledge/memory nodes and deduplicated relationship edges.
pub fn get_knowledge_graph(conn: Option<&rusqlite::Connection>, knowledge_dir: &str) -> Value {
    let bundle = Bundle::new(knowledge_dir);
    let mut nodes: Vec<Value> = Vec::new();
    let mut memories: Vec<(String, Concept)> = Vec::new();
    for cid in bundle.list_concepts(Some("knowledge/")) {
        let Ok(concept) = bundle.read_concept(&cid) else {
            continue;
        };
        let doc_id = normalize_doc_id(&bundle, &cid);
        let (family, _slug) = split_doc_id(&doc_id);
        nodes.push(json!({
            "id": doc_id,
            "title": concept.title.clone().unwrap_or_else(|| doc_id.clone()),
            "family": family,
            "status": concept.ext_str("doc_status").unwrap_or("active"),
            "kind": "doc",
        }));
    }
    for cid in bundle.list_concepts(Some("memory/")) {
        let Ok(concept) = bundle.read_concept(&cid) else {
            continue;
        };
        let (tier, _slug) = split_doc_id(&cid);
        nodes.push(json!({
            "id": cid,
            "title": concept.title.clone().unwrap_or_else(|| cid.clone()),
            "family": tier.clone(),
            "status": concept.ext_str("memory_status").map(String::from).unwrap_or(tier),
            "kind": "memory",
        }));
        memories.push((cid, concept));
    }
    nodes.sort_by(|a, b| {
        a["id"]
            .as_str()
            .unwrap_or("")
            .cmp(b["id"].as_str().unwrap_or(""))
    });
    let node_ids: std::collections::BTreeSet<String> = nodes
        .iter()
        .filter_map(|n| n["id"].as_str().map(String::from))
        .collect();
    let mut edges: Vec<Value> = Vec::new();
    let mut seen: std::collections::BTreeSet<(String, String, String, String)> = Default::default();
    let mut add_edge = |source: &str, target: &str, relation: &str, kind: &str| {
        let key = (
            source.to_string(),
            target.to_string(),
            relation.to_string(),
            kind.to_string(),
        );
        if seen.contains(&key) || !node_ids.contains(source) || !node_ids.contains(target) {
            return;
        }
        seen.insert(key);
        edges.push(json!({
            "source": source, "target": target,
            "relation": relation, "kind": kind,
        }));
    };
    if let Some(conn) = conn {
        let present = rusqlite::Connection::query_row(
            conn,
            "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name = 'knowledge_edges'",
            [],
            |row| row.get::<_, i64>(0),
        )
        .unwrap_or(0);
        if present > 0 {
            let Ok(mut stmt) = conn.prepare(
                "SELECT doc_id, related_id, relation, kind FROM knowledge_edges \
                 ORDER BY doc_id, related_id, relation, kind",
            ) else {
                return Value::Null;
            };
            let rows: Vec<(String, String, String, String)> = stmt
                .query_map([], |row| {
                    Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?))
                })
                .map(|rows| rows.filter_map(Result::ok).collect())
                .unwrap_or_default();
            for (doc_id, related_id, relation, kind) in rows {
                add_edge(&doc_id, &related_id, &relation, &kind);
            }
        }
    }
    // Memory supersedes pairs drawn two-way, matching the index's mirrored rows.
    for (cid, concept) in &memories {
        let mut pairs: Vec<(String, String)> = ext_list(concept, "memory_supersedes")
            .into_iter()
            .map(|older| (cid.clone(), older))
            .collect();
        if let Some(newer) = concept.ext_str("memory_superseded_by") {
            pairs.push((newer.to_string(), cid.clone()));
        }
        for (newer, older) in pairs {
            add_edge(&newer, &older, "supersedes", "extracted");
            add_edge(&older, &newer, "superseded-by", "extracted");
        }
    }
    let count = edges.len();
    json!({
        "nodes": nodes,
        "edges": edges,
        "metadata": {"node_count": nodes.len(), "edge_count": count},
    })
}

/// Memory identity and readable supersedes neighbors without phantoms.
fn memory_inspect(bundle: &Bundle, memory_id: &str) -> Option<Value> {
    let concept = bundle.read_concept(memory_id).ok()?;
    let (tier, slug) = split_doc_id(memory_id);
    let mut related: Vec<Value> = Vec::new();
    let mut add_neighbor = |neighbor_id: &str, relation: &str, direction: &str| {
        let Ok(neighbor) = bundle.read_concept(neighbor_id) else {
            return;
        };
        let (family, slug) = split_doc_id(neighbor_id);
        related.push(json!({
            "doc_id": neighbor_id,
            "title": neighbor.title.clone().unwrap_or_else(|| neighbor_id.to_string()),
            "relation": relation,
            "kind": "extracted",
            "direction": direction,
            "family": family,
            "slug": slug,
        }));
    };
    for older in ext_list(&concept, "memory_supersedes") {
        add_neighbor(&older, "supersedes", "outgoing");
    }
    if let Some(newer) = concept.ext_str("memory_superseded_by") {
        add_neighbor(newer, "superseded-by", "incoming");
    }
    let groups = group_related(related.clone());
    Some(json!({
        "found": true,
        "kind": "memory",
        "id": memory_id,
        "family": tier,
        "slug": slug,
        "title": concept.title.clone().unwrap_or_else(|| memory_id.to_string()),
        "status": concept.ext_str("memory_status").map(String::from).unwrap_or_else(|| tier.clone()),
        "tags": concept.tags,
        "related": related,
        "related_groups": groups,
    }))
}

/// One knowledge/memory inspect payload, None for an unknown id.
pub fn get_knowledge_graph_inspect(
    conn: Option<&rusqlite::Connection>,
    knowledge_dir: &str,
    doc_id: &str,
) -> Option<Value> {
    let bundle = Bundle::new(knowledge_dir);
    if doc_id.starts_with("memory/") {
        return memory_inspect(&bundle, doc_id);
    }
    let concept = resolve_knowledge_doc(&bundle, doc_id)?;
    let bare_id = normalize_doc_id(&bundle, &concept.concept_id);
    let (family, slug) = split_doc_id(&bare_id);
    let related = match conn {
        Some(conn) => related_docs(conn, &bundle, &bare_id)
            .into_iter()
            .map(|mut neighbor| {
                let (family, slug) = split_doc_id(neighbor["doc_id"].as_str().unwrap_or(""));
                neighbor["family"] = Value::from(family);
                neighbor["slug"] = Value::from(slug);
                neighbor
            })
            .collect::<Vec<_>>(),
        None => Vec::new(),
    };
    let groups = group_related(related.clone());
    Some(json!({
        "found": true,
        "kind": "doc",
        "id": bare_id,
        "family": family,
        "slug": slug,
        "title": concept.title.clone().unwrap_or_else(|| bare_id.clone()),
        "status": concept.ext_str("doc_status").unwrap_or("active"),
        "tags": concept.tags,
        "related": related,
        "related_groups": groups,
    }))
}

// --- editor file payload ---

/// The first sorted compass concept whose resource overlaps the module.
fn compass(bundle: &Bundle, module: &str) -> Value {
    if module.is_empty() {
        return json!({"found": false, "title": "", "body": ""});
    }
    for cid in bundle.list_concepts(Some("compass/")) {
        let Ok(concept) = bundle.read_concept(&cid) else {
            continue;
        };
        let resource = concept.resource.clone().unwrap_or_default();
        if !resource.is_empty() && (resource.contains(module) || module.contains(&resource)) {
            return json!({
                "found": true,
                "title": concept.title.clone().unwrap_or_default(),
                "body": concept.body,
            });
        }
    }
    json!({"found": false, "title": "", "body": ""})
}

/// Memories mentioning the path or module, newest first.
fn relevant_memories(bundle: &Bundle, path: &str, module: &str) -> Vec<Value> {
    let needles: Vec<String> = {
        let mut set: std::collections::BTreeSet<String> = Default::default();
        if !path.is_empty() {
            set.insert(path.to_string());
        }
        if !module.is_empty() {
            set.insert(module.to_string());
        }
        set.into_iter().collect()
    };
    if needles.is_empty() {
        return Vec::new();
    }
    let mut scored: Vec<(i64, String, Concept)> = Vec::new();
    for cid in bundle.list_concepts(Some("memory/")) {
        let Ok(concept) = bundle.read_concept(&cid) else {
            continue;
        };
        let haystacks = [
            concept.resource.clone().unwrap_or_default(),
            concept.title.clone().unwrap_or_default(),
            concept.body.clone(),
        ];
        if !needles
            .iter()
            .any(|needle| haystacks.iter().any(|hay| hay.contains(needle.as_str())))
        {
            continue;
        }
        let ts = concept
            .timestamp
            .as_deref()
            .and_then(super::env::parse_iso_utc)
            .unwrap_or_else(epoch);
        scored.push((ts, cid, concept));
    }
    scored.sort_by_key(|entry| std::cmp::Reverse(entry.0));
    scored
        .into_iter()
        .map(|(_, cid, concept)| {
            json!({
                "id": cid,
                "type": concept.ext_str("memory_type").map(String::from).unwrap_or(concept.type_.clone()),
                "title": concept.title.clone().unwrap_or_default(),
                "body": concept.body,
            })
        })
        .collect()
}

/// The file's module, compass excerpt, and relevant memories.
pub fn editor_file_payload(knowledge_dir: &str, path: &str) -> Value {
    let bundle = Bundle::new(knowledge_dir);
    let module = std::path::Path::new(path)
        .parent()
        .map(|p| p.to_string_lossy().replace('\\', "/"))
        .unwrap_or_default();
    json!({
        "path": path,
        "module": module,
        "compass": compass(&bundle, &module),
        "memories": relevant_memories(&bundle, path, &module),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bundle_dir(tag: &str) -> String {
        let root = std::env::temp_dir().join(format!("cairn-kb-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).unwrap();
        root.to_string_lossy().into_owned()
    }

    fn write_concept(root: &str, id: &str, frontmatter: &str, body: &str) {
        let path = std::path::Path::new(root).join(format!("{id}.md"));
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, format!("---\n{frontmatter}---\n{body}")).unwrap();
    }

    #[test]
    fn memories_list_filter_and_sort() {
        let root = bundle_dir("mem");
        write_concept(
            &root,
            "memory/tribal/alpha",
            "type: decision\ntitle: Alpha\ngenerated:\n  at: 2026-01-02T00:00:00Z\nmemory_type: decision\n",
            "alpha body",
        );
        write_concept(
            &root,
            "memory/raw/beta",
            "type: pattern\ntitle: Beta\ngenerated:\n  at: 2026-01-03T00:00:00Z\nmemory_type: pattern\n",
            "beta body",
        );
        let all = get_recent_memories(&root, None);
        assert_eq!(all.len(), 2);
        assert_eq!(all[0]["id"], "memory/raw/beta", "newest first");
        let decisions = get_recent_memories(&root, Some("decision"));
        assert_eq!(decisions.len(), 1);
        assert_eq!(decisions[0]["tier"], "tribal");
        assert_eq!(decisions[0]["slug"], "alpha");
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn memory_detail_renders_body_and_neighbors() {
        let root = bundle_dir("memdetail");
        write_concept(
            &root,
            "memory/tribal/new",
            "type: decision\ntitle: New\ngenerated:\n  at: 2026-01-02T00:00:00Z\nmemory_supersedes:\n  - memory/tribal/old\n",
            "# Heading\n\nbody text\n",
        );
        write_concept(
            &root,
            "memory/tribal/old",
            "type: decision\ntitle: Old\n",
            "old body",
        );
        let detail = get_memory_detail(&root, "memory/tribal/new").unwrap();
        assert_eq!(detail["title"], "New");
        assert!(
            detail["html"].as_str().unwrap().contains("<h1"),
            "{}",
            detail["html"]
        );
        assert_eq!(detail["supersedes"][0]["title"], "Old");
        assert_eq!(detail["supersedes"][0]["tier"], "tribal");
        assert_eq!(detail["status"], "tribal");
        assert!(get_memory_detail(&root, "knowledge/nope").is_none());
        assert!(get_memory_detail(&root, "memory/tribal/missing").is_none());
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn task_queue_lists_and_filters() {
        let root = bundle_dir("tasks");
        write_concept(
            &root,
            "_tasks/abc123",
            "type: Task\ntask_kind: wiki-page\nresource: repo/Page\nassigned_to: agent\ngenerated:\n  at: 2026-01-01T00:00:00Z\nclaimed_at: 2026-01-01T01:00:00Z\nattempt: 1\nstatus: in-progress\n",
            "",
        );
        write_concept(
            &root,
            "_tasks/def456.result",
            "type: TaskResult\ncritic_status: passed\n",
            "",
        );
        let all = get_task_queue(&root, None);
        assert_eq!(all.len(), 1);
        assert_eq!(all[0]["kind"], "wiki-page");
        assert_eq!(all[0]["status"], "in-progress");
        assert_eq!(all[0]["assigned_to"], "agent");
        assert!(get_task_queue(&root, Some("done")).is_empty());
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn knowledge_docs_catalog_counts_filters() {
        let root = bundle_dir("kbdocs");
        write_concept(
            &root,
            "knowledge/business-rule/rule-a",
            "type: business-rule\ntitle: Rule A\ndoc_status: active\ntags:\n  - auth\n",
            "body",
        );
        write_concept(
            &root,
            "knowledge/spec/spec-b",
            "type: spec\ntitle: Spec B\ndoc_status: superseded\n",
            "body",
        );
        let catalog = list_knowledge_docs(None, &root, None, None, None);
        assert_eq!(catalog["total"], 2);
        assert_eq!(catalog["families"]["business-rule"], 1);
        assert_eq!(catalog["statuses"]["superseded"], 1);
        let filtered = list_knowledge_docs(None, &root, Some("spec"), Some("superseded"), None);
        assert_eq!(filtered["docs"].as_array().unwrap().len(), 1);
        assert_eq!(filtered["docs"][0]["title"], "Spec B");
        let tagged = list_knowledge_docs(None, &root, None, None, Some("auth"));
        assert_eq!(tagged["docs"].as_array().unwrap().len(), 1);
        let doc = get_knowledge_doc(&root, "knowledge/business-rule/rule-a").unwrap();
        assert_eq!(doc["family"], "business-rule");
        assert_eq!(doc["slug"], "rule-a");
        assert!(
            get_knowledge_doc(&root, "memory/tribal/x").is_none(),
            "foreign namespace refused"
        );
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn wiki_pages_derive_state_and_staleness() {
        let root = bundle_dir("wiki");
        std::fs::create_dir_all(std::path::Path::new(&root).join("_wiki")).unwrap();
        std::fs::write(
            std::path::Path::new(&root).join("_wiki/manifest.json"),
            r#"{"schema": "cairn-wiki-manifest-3", "pages": {"repo-a/overview": {"title": "Overview", "description": "d", "input_hash": "h"}}, "schema_v": 3}"#,
        )
        .unwrap();
        write_concept(
            &root,
            "wiki/pages/repo-a/overview",
            "type: Wiki-Article\ntitle: Overview\ngenerated:\n  at: 2026-01-01T00:00:00Z\ncommit_sha: abc123\n",
            "# Overview\n\nWiki body with `ref`.\n",
        );
        let pages = get_wiki_pages(&root, None).unwrap();
        assert_eq!(pages.len(), 1);
        assert_eq!(pages[0]["state"], "promoted");
        assert_eq!(pages[0]["promoted"], true);
        assert_eq!(
            pages[0]["staleness"], "unknown",
            "no workspace registered: HEAD unknown"
        );
        let filtered = get_wiki_pages(&root, Some("repo-a")).unwrap();
        assert_eq!(filtered.len(), 1);
        assert!(get_wiki_pages(&root, Some("repo-b")).unwrap().is_empty());
        let page = get_wiki_page(&root, "overview", Some("repo-a"), "")
            .unwrap()
            .unwrap();
        assert_eq!(page["title"], "Overview");
        assert!(
            page["html"].as_str().unwrap().contains("id=\"overview\""),
            "{}",
            page["html"]
        );
        assert!(get_wiki_page(&root, "missing", None, "").unwrap().is_none());
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn wiki_manifest_errors_are_value_errors() {
        let root = bundle_dir("wikibad");
        std::fs::create_dir_all(std::path::Path::new(&root).join("_wiki")).unwrap();
        std::fs::write(
            std::path::Path::new(&root).join("_wiki/manifest.json"),
            "{not json",
        )
        .unwrap();
        assert!(get_wiki_pages(&root, None).is_err());
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn knowledge_graph_draws_supersede_pairs_two_way() {
        let root = bundle_dir("kg");
        write_concept(
            &root,
            "knowledge/decision/kd-1",
            "type: decision\ntitle: KD1\n",
            "body",
        );
        write_concept(
            &root,
            "memory/tribal/m1",
            "type: decision\ntitle: M1\nmemory_supersedes:\n  - memory/tribal/m2\n",
            "body",
        );
        write_concept(
            &root,
            "memory/tribal/m2",
            "type: decision\ntitle: M2\n",
            "body",
        );
        let graph = get_knowledge_graph(None, &root);
        assert_eq!(graph["metadata"]["node_count"], 3);
        assert_eq!(
            graph["metadata"]["edge_count"], 2,
            "supersedes + superseded-by"
        );
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn editor_file_finds_compass_and_memories() {
        let root = bundle_dir("editor");
        write_concept(
            &root,
            "compass/src-cairn",
            "type: Compass\ntitle: Dashboard\nresource: src/cairn/dashboard\n",
            "nav guide",
        );
        write_concept(
            &root,
            "memory/raw/m",
            "type: mistake\ntitle: M\nresource: src/cairn/dashboard/app.py\n",
            "watch out",
        );
        let payload = editor_file_payload(&root, "src/cairn/dashboard/app.py");
        assert_eq!(payload["module"], "src/cairn/dashboard");
        assert_eq!(payload["compass"]["found"], true);
        assert_eq!(payload["compass"]["title"], "Dashboard");
        assert_eq!(payload["memories"].as_array().unwrap().len(), 1);
        let _ = std::fs::remove_dir_all(&root);
    }
}
