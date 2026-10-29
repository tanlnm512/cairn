//! Wiki generation pipeline: catalog planner, manifest, task queue
//!.

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};

use rusqlite::Connection;
use serde_json::{Value, json};
use yaml_rust2::Yaml;
use yaml_rust2::yaml::Hash;

use crate::tools_km::{
    Bundle, Concept, CriticResult, ToolError, critic_concept_opts, iso_utc_now, path_match_params,
    preserved_concept_notes,
};

const SEED_LIMIT: usize = 10;
const CATALOG_KIND: &str = "wiki-catalog";
const MANIFEST_SCHEMA: &str = "cairn-wiki-manifest-3";
const TEST_SEGMENTS: [&str; 4] = ["test", "tests", "spec", "specs"];

/// Why a wiki_generate run produced nothing.
#[derive(Debug)]
pub enum PipelineError {
    /// The plan could not be built (nothing indexed).
    Planner(String),
    /// The manifest is corrupt/malformed (the ValueError branch).
    Manifest(String),
    /// The manifest write failed; queued work is not recorded.
    Write,
}

/// One planned wiki page.
#[derive(Clone, Debug)]
pub struct PageEntry {
    pub page_id: String,
    pub title: String,
    pub description: String,
    pub module: String,
    pub seeds_files: Vec<String>,
    pub seeds_symbols: Vec<String>,
    pub input_hash: String,
}

impl PageEntry {
    /// The plan-entry seeds: files + symbols + the empty docs list.
    /// Key order is the canonicalization contract: the hash uses
    /// sort_keys, and serde_json (preserve_order) serializes insertion order.
    fn seeds_value(&self) -> Value {
        json!({
            "docs": [],
            "files": self.seeds_files,
            "symbols": self.seeds_symbols,
        })
    }

    /// The manifest/task row projection of the plan entry.
    fn row_value(&self, task_id: &str, queue_attempts: i64, diagrams: bool) -> Value {
        let mut row = json!({
            "page_id": self.page_id,
            "title": self.title,
            "description": self.description,
            "module": self.module,
            "source": "code",
            "seeds": self.seeds_value(),
            "input_hash": self.input_hash,
            "task_id": task_id,
            "queue_attempts": queue_attempts,
        });
        if diagrams {
            row["diagrams"] = json!(true);
        }
        row
    }
}

fn make_page(
    page_id: &str,
    title: &str,
    description: &str,
    module: &str,
    seeds_files: Vec<String>,
    seeds_symbols: Vec<String>,
) -> PageEntry {
    let mut entry = PageEntry {
        page_id: page_id.to_string(),
        title: title.to_string(),
        description: description.to_string(),
        module: module.to_string(),
        seeds_files,
        seeds_symbols,
        input_hash: String::new(),
    };
    entry.input_hash = page_input_hash(&entry);
    entry
}

/// The plan entry identity hash: sha256 of the canonical (sorted, compact) JSON.
fn page_input_hash(entry: &PageEntry) -> String {
    use sha2::{Digest, Sha256};
    let canonical = json!({
        "description": entry.description,
        "module": entry.module,
        "page_id": entry.page_id,
        "seeds": entry.seeds_value(),
        "source": "code",
        "title": entry.title,
    });
    let text = serde_json::to_string(&canonical).unwrap_or_default();
    let mut hasher = Sha256::new();
    hasher.update(text.as_bytes());
    format!("{:x}", hasher.finalize())
}

// --- catalog planning ---------------------------------

/// Module directory for a path; mirrors graph.stats.group_by_top_level exactly.
fn module_of(path: &str, repo_root: &str) -> String {
    let path = if !repo_root.is_empty() {
        path.strip_prefix(&format!("{repo_root}/")).unwrap_or(path)
    } else {
        path
    };
    let parts: Vec<&str> = path.split('/').collect();
    if parts.len() >= 4 {
        parts[..3].join("/")
    } else if parts.len() >= 2 {
        parts[..parts.len() - 1].join("/")
    } else {
        parts[0].to_string()
    }
}

fn is_test_majority(paths: &[&str]) -> bool {
    let test_count = paths
        .iter()
        .filter(|p| p.split('/').any(|segment| TEST_SEGMENTS.contains(&segment)))
        .count();
    test_count * 2 > paths.len()
}

fn like_under_prefix(prefix: &str) -> String {
    let escaped = prefix
        .replace('\\', "\\\\")
        .replace('%', "\\%")
        .replace('_', "\\_");
    format!("{escaped}/%")
}

fn module_incoming_degree(
    conn: &Connection,
    repo: &str,
    prefix: &str,
) -> Result<i64, rusqlite::Error> {
    let like = like_under_prefix(prefix);
    conn.query_row(
        "SELECT COUNT(e.id) AS incoming \
         FROM edges e \
         JOIN symbols ts ON e.target_id = ts.id \
         JOIN files tf ON ts.file_id = tf.id \
         JOIN symbols ss ON e.source_id = ss.id \
         JOIN files sf ON ss.file_id = sf.id \
         WHERE tf.repo_id = ?1 \
           AND (tf.path = ?2 OR tf.path LIKE ?3 ESCAPE '\\') \
           AND NOT (sf.path = ?2 OR sf.path LIKE ?3 ESCAPE '\\')",
        rusqlite::params![repo, prefix, like],
        |row| row.get(0),
    )
}

fn top_symbols(
    conn: &Connection,
    repo: &str,
    prefix: &str,
) -> Result<Vec<String>, rusqlite::Error> {
    let mut sql = String::from(
        "SELECT s.name, COUNT(e.id) AS incoming \
         FROM symbols s \
         LEFT JOIN edges e ON e.target_id = s.id \
         JOIN files f ON s.file_id = f.id \
         WHERE f.repo_id = ?1",
    );
    let mut params: Vec<Box<dyn rusqlite::ToSql>> = vec![Box::new(repo.to_string())];
    let mut limit_param = 2;
    if !prefix.is_empty() {
        sql.push_str(" AND (f.path = ?2 OR f.path LIKE ?3 ESCAPE '\\')");
        params.push(Box::new(prefix.to_string()));
        params.push(Box::new(like_under_prefix(prefix)));
        limit_param = 4;
    }
    sql.push_str(&format!(
        " GROUP BY s.id ORDER BY incoming DESC, s.name ASC LIMIT ?{limit_param}"
    ));
    params.push(Box::new(SEED_LIMIT as i64));
    let mut stmt = conn.prepare(&sql)?;
    let refs: Vec<&dyn rusqlite::ToSql> = params.iter().map(|p| p.as_ref()).collect();
    let rows = stmt.query_map(refs.as_slice(), |r| r.get::<_, String>(0))?;
    rows.collect()
}

fn top_files(conn: &Connection, repo: &str) -> Result<Vec<String>, rusqlite::Error> {
    let mut stmt = conn.prepare(
        "SELECT f.path, COUNT(s.id) AS symbols \
         FROM files f LEFT JOIN symbols s ON s.file_id = f.id \
         WHERE f.repo_id = ?1 GROUP BY f.id \
         ORDER BY symbols DESC, f.path ASC LIMIT ?2",
    )?;
    let rows = stmt.query_map(rusqlite::params![repo, SEED_LIMIT as i64], |r| {
        r.get::<_, String>(0)
    })?;
    rows.collect()
}

/// Filesystem-safe page id: lowercase, non-alphanumeric runs to '-', stripped.
fn slug(module: &str) -> String {
    let mut out = String::new();
    for c in module.to_lowercase().chars() {
        if c.is_ascii_alphanumeric() {
            out.push(c);
        } else if !out.ends_with('-') {
            out.push('-');
        }
    }
    out.trim_matches('-').to_string()
}

/// `base_id` de-collided against `used_ids` with a numeric suffix.
fn next_page_id(base_id: &str, used_ids: &mut BTreeSet<String>) -> String {
    let mut page_id = base_id.to_string();
    let mut n = 2;
    while used_ids.contains(&page_id) {
        page_id = format!("{base_id}-{n}");
        n += 1;
    }
    used_ids.insert(page_id.clone());
    page_id
}

fn slug_or_module(module: &str) -> String {
    let slugged = slug(module);
    if slugged.is_empty() {
        "module".to_string()
    } else {
        slugged
    }
}

/// Plan the wiki pages for `repo` from the graph: the overview first, then
/// modules ranked by cross-module incoming degree.
pub fn build_page_plan(
    conn: &Connection,
    repo: &str,
    pages_cap: usize,
) -> Result<Vec<PageEntry>, PipelineError> {
    let repo_root: String = conn
        .query_row("SELECT path FROM repos WHERE id = ?1", [repo], |row| {
            row.get::<_, Option<String>>(0)
        })
        .unwrap_or(None)
        .unwrap_or_default();

    let paths: Vec<String> = {
        let mut stmt = conn
            .prepare("SELECT path FROM files WHERE repo_id = ?1")
            .map_err(|e| PipelineError::Planner(e.to_string()))?;
        let rows = stmt
            .query_map([repo], |r| r.get::<_, String>(0))
            .map_err(|e| PipelineError::Planner(e.to_string()))?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(|e| PipelineError::Planner(e.to_string()))?
    };
    if paths.is_empty() {
        return Err(PipelineError::Planner(format!(
            "no indexed files for repo '{repo}'; run 'cairn build' first"
        )));
    }

    let mut module_files: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for p in &paths {
        module_files
            .entry(module_of(p, &repo_root))
            .or_default()
            .push(p.clone());
    }
    let module_files: BTreeMap<String, Vec<String>> = module_files
        .into_iter()
        .filter_map(|(module, mut members)| {
            members.sort();
            let refs: Vec<&str> = members.iter().map(String::as_str).collect();
            if is_test_majority(&refs) {
                None
            } else {
                Some((module, members))
            }
        })
        .collect();
    if module_files.is_empty() {
        return Err(PipelineError::Planner(format!(
            "no product-code modules for repo '{repo}': every indexed file \
             is majority test files"
        )));
    }

    let mut degrees: HashMap<String, i64> = HashMap::new();
    let mut ranked: Vec<String> = Vec::new();
    for module in module_files.keys() {
        let degree = module_incoming_degree(conn, repo, module)
            .map_err(|e| PipelineError::Planner(e.to_string()))?;
        degrees.insert(module.clone(), degree);
        ranked.push(module.clone());
    }
    ranked.sort_by(|a, b| degrees[b].cmp(&degrees[a]).then_with(|| a.cmp(b)));

    let mut plan = vec![make_page(
        "overview",
        &format!("{repo} architecture overview"),
        &format!(
            "Architecture overview of {repo} across {} modules.",
            ranked.len()
        ),
        "",
        top_files(conn, repo).map_err(|e| PipelineError::Planner(e.to_string()))?,
        top_symbols(conn, repo, "").map_err(|e| PipelineError::Planner(e.to_string()))?,
    )];
    let mut used_ids = BTreeSet::from(["overview".to_string()]);
    for module in ranked.iter().take(pages_cap.saturating_sub(1)) {
        let page_id = next_page_id(&slug_or_module(module), &mut used_ids);
        plan.push(make_page(
            &page_id,
            module,
            &format!(
                "Module {module}: {} indexed files.",
                module_files[module].len()
            ),
            module,
            module_files[module].clone(),
            top_symbols(conn, repo, module).map_err(|e| PipelineError::Planner(e.to_string()))?,
        ));
    }
    Ok(plan)
}

// --- refined-outline validation ------------------------

fn module_in_graph(conn: &Connection, module: &str, repo: &str) -> Result<bool, rusqlite::Error> {
    let like = like_under_prefix(module);
    let hit = conn.query_row(
        "SELECT 1 FROM files WHERE repo_id = ?1 \
         AND (path = ?2 OR path LIKE ?3 ESCAPE '\\') LIMIT 1",
        rusqlite::params![repo, module, like],
        |_| Ok(()),
    );
    Ok(hit.is_ok())
}

/// True when `ref` resolves within `repo`: the segment-anchored arms of
/// file_exists, constrained to the repo.
fn file_in_repo(conn: &Connection, repo: &str, ref_: &str) -> Result<bool, rusqlite::Error> {
    let ref_ = ref_.trim_matches('/');
    if ref_.is_empty() {
        return Ok(false);
    }
    let in_repo = |target: &str| -> Result<bool, rusqlite::Error> {
        let (a, b, c, d) = path_match_params(target);
        let hit = conn.query_row(
            "SELECT 1 FROM files WHERE repo_id = ?5 \
             AND (path = ?1 OR path LIKE ?2 ESCAPE '\\' OR path LIKE ?3 ESCAPE '\\' \
              OR path LIKE ?4 ESCAPE '\\') LIMIT 1",
            rusqlite::params![a, b, c, d, repo],
            |_| Ok(()),
        );
        Ok(hit.is_ok())
    };
    if in_repo(ref_)? {
        return Ok(true);
    }
    let Some((rid, rest)) = ref_.split_once('/') else {
        return Ok(false);
    };
    if rid != repo {
        return Ok(false);
    }
    in_repo(rest)
}

/// Effective record for a refined entry (None = rejected).
fn effective_entry(
    entry: &Value,
    det_by_module: &HashMap<String, PageEntry>,
    conn: &Connection,
    repo: &str,
    used_ids: &mut BTreeSet<String>,
) -> Result<Option<PageEntry>, rusqlite::Error> {
    let Some(module) = entry.get("module").and_then(Value::as_str) else {
        return Ok(None);
    };
    if !module.is_empty() && !module_in_graph(conn, module, repo)? {
        return Ok(None);
    }
    let empty_seeds = json!({"files": [], "symbols": [], "docs": []});
    let seeds = match entry.get("seeds") {
        None => det_by_module
            .get(module)
            .map_or_else(|| empty_seeds.clone(), PageEntry::seeds_value),
        Some(raw) if raw.is_object() => json!({
            "files": raw.get("files").cloned().unwrap_or(json!([])),
            "symbols": raw.get("symbols").cloned().unwrap_or(json!([])),
            "docs": raw.get("docs").cloned().unwrap_or(json!([])),
        }),
        Some(_) => return Ok(None),
    };
    let empty_files: Vec<Value> = Vec::new();
    let mut files: Vec<String> = Vec::new();
    for p in seeds["files"].as_array().unwrap_or(&empty_files) {
        match p.as_str() {
            Some(s) if file_in_repo(conn, repo, s)? => files.push(s.to_string()),
            _ => return Ok(None),
        }
    }
    let symbols: Vec<String> = seeds["symbols"]
        .as_array()
        .map(|a| {
            a.iter()
                .filter_map(|s| s.as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default();
    let page_id = if module.is_empty() {
        "overview".to_string()
    } else {
        next_page_id(&slug_or_module(module), used_ids)
    };
    Ok(Some(make_page(
        &page_id,
        entry.get("title").and_then(Value::as_str).unwrap_or(""),
        entry
            .get("description")
            .and_then(Value::as_str)
            .unwrap_or(""),
        module,
        files,
        symbols,
    )))
}

/// Validate a refined outline: effective entries in order, rejects revert,
/// ids unique.
fn validate_refined_outline(
    refined: &[Value],
    deterministic_plan: Vec<PageEntry>,
    conn: &Connection,
    repo: &str,
) -> Result<Vec<PageEntry>, rusqlite::Error> {
    let det_by_module: HashMap<String, PageEntry> = deterministic_plan
        .iter()
        .map(|e| (e.module.clone(), e.clone()))
        .collect();
    let mut effective: Vec<PageEntry> = Vec::new();
    let mut used_ids = BTreeSet::new();
    let mut covered = BTreeSet::new();
    for entry in refined {
        let Some(record) = effective_entry(entry, &det_by_module, conn, repo, &mut used_ids)?
        else {
            continue;
        };
        if covered.contains(&record.module) {
            continue;
        }
        covered.insert(record.module.clone());
        effective.push(record);
    }
    for entry in deterministic_plan {
        if covered.contains(&entry.module) {
            continue;
        }
        if used_ids.contains(&entry.page_id) {
            // Rebuild (rehashing the record) under the collision-free id.
            let page_id = next_page_id(&entry.page_id, &mut used_ids);
            effective.push(make_page(
                &page_id,
                &entry.title,
                &entry.description,
                &entry.module,
                entry.seeds_files.clone(),
                entry.seeds_symbols.clone(),
            ));
        } else {
            used_ids.insert(entry.page_id.clone());
            effective.push(entry);
        }
    }
    Ok(effective)
}

// --- manifest ----------------------------------------

fn manifest_path(bundle: &Bundle) -> std::path::PathBuf {
    bundle.root.join("_wiki").join("manifest.json")
}

/// Load the manifest; malformed shapes surface as the ValueError tool branch.
pub fn load_manifest(bundle: &Bundle) -> Result<Value, PipelineError> {
    let path = manifest_path(bundle);
    if !path.is_file() {
        return Ok(json!({"schema": MANIFEST_SCHEMA, "pages": {}}));
    }
    let text = std::fs::read_to_string(&path)
        .map_err(|e| PipelineError::Manifest(format!("wiki manifest {}: {e}", path.display())))?;
    let doc: Value = serde_json::from_str(&text).map_err(|e| {
        PipelineError::Manifest(format!(
            "wiki manifest {} is not valid JSON: {e}",
            path.display()
        ))
    })?;
    if !doc.is_object() {
        return Err(PipelineError::Manifest(format!(
            "wiki manifest {} must contain a JSON object",
            path.display()
        )));
    }
    let mut doc = doc;
    if doc.get("schema").and_then(Value::as_str) != Some(MANIFEST_SCHEMA) {
        // Schema-1 rows (bare page-id keys) are dropped: repo recovery needs
        // task/concept reads the 1b daemon does not own.
        let migrated: serde_json::Map<String, Value> = doc
            .get("pages")
            .and_then(Value::as_object)
            .map(|m| {
                m.iter()
                    .filter(|(k, _)| k.contains('/'))
                    .map(|(k, v)| (k.clone(), v.clone()))
                    .collect()
            })
            .unwrap_or_default();
        doc["schema"] = json!(MANIFEST_SCHEMA);
        doc["pages"] = Value::Object(migrated);
    }
    if !doc.get("pages").is_some_and(Value::is_object) {
        return Err(PipelineError::Manifest(format!(
            "wiki manifest {} pages must be a mapping keyed by \
             {{repo}}/{{page_id}}",
            path.display()
        )));
    }
    Ok(doc)
}

/// Render `json.dump(..., sort_keys=True, indent=2)`-format output.
fn sorted_json(value: &Value) -> Value {
    match value {
        Value::Object(map) => {
            let mut pairs: Vec<(&String, &Value)> = map.iter().collect();
            pairs.sort_by(|a, b| a.0.cmp(b.0));
            let mut out = serde_json::Map::new();
            for (k, v) in pairs {
                out.insert(k.clone(), sorted_json(v));
            }
            Value::Object(out)
        }
        Value::Array(items) => Value::Array(items.iter().map(sorted_json).collect()),
        other => other.clone(),
    }
}

/// Write the manifest atomically; Ok(false) mirrors save_manifest's OSError.
fn save_manifest(bundle: &Bundle, manifest: &Value) -> Result<bool, ToolError> {
    let path = manifest_path(bundle);
    let Some(directory) = path.parent() else {
        return Ok(false);
    };
    if std::fs::create_dir_all(directory).is_err() {
        return Ok(false);
    }
    let body = match serde_json::to_string_pretty(&sorted_json(manifest)) {
        Ok(body) => format!("{body}\n"),
        Err(e) => return Err(ToolError(e.to_string())),
    };
    let tmp = directory.join(format!(
        ".manifest-{}.{}.tmp",
        std::process::id(),
        iso_utc_now().replace([':', '-'], "")
    ));
    if std::fs::write(&tmp, body).is_err() {
        let _ = std::fs::remove_file(&tmp);
        return Ok(false);
    }
    match std::fs::rename(&tmp, &path) {
        Ok(()) => Ok(true),
        Err(_) => {
            let _ = std::fs::remove_file(&tmp);
            Ok(false)
        }
    }
}

// --- task queue ----------------------

/// One listed task record (only the fields the pipeline consumes).
#[derive(Clone, Debug)]
pub struct TaskRec {
    pub id: String,
    pub task_kind: String,
    pub resource: String,
    pub facts_input_hash: String,
    pub facts_repo: String,
    pub status: String,
    pub created_at: String,
    pub attempt: i64,
}

fn yaml_str(v: Option<&Yaml>) -> String {
    match v {
        Some(Yaml::String(s)) => s.clone(),
        Some(Yaml::Real(s)) => s.clone(),
        Some(Yaml::Integer(i)) => i.to_string(),
        _ => String::new(),
    }
}

fn facts_field(facts: Option<&Yaml>, key: &str) -> String {
    match facts {
        Some(Yaml::Hash(h)) => yaml_str(h.get(&Yaml::String(key.to_string()))),
        _ => String::new(),
    }
}

fn list_tasks(bundle: &Bundle) -> Vec<TaskRec> {
    let mut out = Vec::new();
    for cid in bundle.list_concepts(Some("_tasks/")) {
        if cid.ends_with(".result") {
            continue;
        }
        let Ok(concept) = bundle.read_concept(&cid) else {
            continue;
        };
        if concept.type_ != "Task" {
            continue;
        }
        out.push(TaskRec {
            id: cid.rsplit('/').next().unwrap_or(&cid).to_string(),
            task_kind: concept.ext_str("task_kind").unwrap_or("").to_string(),
            resource: concept.resource.clone().unwrap_or_default(),
            facts_input_hash: facts_field(concept.ext("facts"), "input_hash"),
            facts_repo: facts_field(concept.ext("facts"), "repo"),
            status: concept
                .status
                .clone()
                .unwrap_or_else(|| "pending".to_string()),
            created_at: concept.timestamp.clone().unwrap_or_default(),
            attempt: concept.ext_f64("attempt").unwrap_or(1.0) as i64,
        });
    }
    out
}

fn is_write_kind(task_kind: &str) -> bool {
    task_kind.starts_with("wiki-page") && !task_kind.starts_with("wiki-page-enrich")
}

fn chain_key(task: &TaskRec) -> String {
    if task.resource.contains('/') {
        return task.resource.clone();
    }
    if task.facts_repo.is_empty() {
        task.resource.clone()
    } else {
        format!("{}/{}", task.facts_repo, task.resource)
    }
}

/// Composite key -> live (pending / in-progress) write-chain tasks.
fn live_generation_tasks(bundle: &Bundle) -> HashMap<String, Vec<TaskRec>> {
    let mut live: HashMap<String, Vec<TaskRec>> = HashMap::new();
    for task in list_tasks(bundle) {
        if !is_write_kind(&task.task_kind)
            || !matches!(task.status.as_str(), "pending" | "in-progress")
        {
            continue;
        }
        live.entry(chain_key(&task)).or_default().push(task);
    }
    live
}

/// Ids of every live (pending/in-progress) write-chain task.
pub fn live_generation_task_ids(bundle: &Bundle) -> BTreeSet<String> {
    live_generation_tasks(bundle)
        .into_values()
        .flat_map(|tasks| tasks.into_iter().map(|t| t.id))
        .collect()
}

static TASK_SEQ: AtomicU64 = AtomicU64::new(0);

/// A 12-hex task id (uuid4().hex[:12] shape) from time/pid/counter entropy.
fn new_task_id() -> String {
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos() as u64)
        .unwrap_or(0);
    let mut x = nanos
        ^ ((std::process::id() as u64) << 32)
        ^ TASK_SEQ
            .fetch_add(1, Ordering::Relaxed)
            .wrapping_mul(0x9e37_79b9_7f4a_7c15);
    for _ in 0..4 {
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
    }
    format!("{:012x}", x & 0x0000_ffff_ffff)
}

fn json_to_yaml(value: &Value) -> Yaml {
    match value {
        Value::Null => Yaml::Null,
        Value::Bool(b) => Yaml::Boolean(*b),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Yaml::Integer(i)
            } else {
                Yaml::Real(n.to_string())
            }
        }
        Value::String(s) => Yaml::String(s.clone()),
        Value::Array(items) => Yaml::Array(items.iter().map(json_to_yaml).collect()),
        Value::Object(map) => {
            let mut hash = Hash::new();
            for (k, v) in map {
                hash.insert(Yaml::String(k.clone()), json_to_yaml(v));
            }
            Yaml::Hash(hash)
        }
    }
}

/// Queue a new task concept; returns its id.
fn create_task(
    bundle: &Bundle,
    task_kind: &str,
    resource: &str,
    facts: &Value,
) -> Result<String, ToolError> {
    let id = new_task_id();
    let mut body = format!("# Task: {task_kind}\n\n");
    body.push_str(&format!("**Resource:** `{resource}`  \n"));
    body.push_str("**Status:** pending  \n");
    body.push_str("**Attempt:** 1/4\n\n");
    if let Some(map) = facts.as_object()
        && !map.is_empty()
    {
        body.push_str("## Facts (graph-grounded — do not invent beyond these)\n");
        for (key, value) in map {
            body.push_str(&format!(
                "**{key}:** {}\n",
                serde_json::to_string(value).unwrap_or_default()
            ));
        }
        body.push('\n');
    }
    body.push_str("## Output spec\n");
    body.push_str(&format!(
        "Write your result, then run: `cairn task complete {id}`\n"
    ));
    let mut concept = Concept {
        type_: "Task".to_string(),
        title: Some(format!("{task_kind}: {resource}")),
        description: None,
        resource: Some(resource.to_string()),
        tags: vec![task_kind.to_string()],
        timestamp: Some(iso_utc_now()),
        generated_by: None,
        status: Some("pending".to_string()),
        stale_after: None,
        concept_id: format!("_tasks/{id}"),
        body,
        extensions: vec![
            ("task_kind".into(), Yaml::String(task_kind.to_string())),
            ("assigned_to".into(), Yaml::String(String::new())),
            ("attempt".into(), Yaml::Integer(1)),
            ("result_path".into(), Yaml::String(String::new())),
            ("completed_at".into(), Yaml::String(String::new())),
            ("claimed_at".into(), Yaml::String(String::new())),
            ("facts".into(), json_to_yaml(facts)),
        ],
    };
    bundle.write_concept(&mut concept)?;
    Ok(id)
}

/// The work-order projection of a plan entry.
fn plan_facts(entry: &PageEntry, repo: &str, diagrams: bool) -> Value {
    let mut facts = json!({
        "title": entry.title,
        "description": entry.description,
        "module": entry.module,
        "seeds": entry.seeds_value(),
        "input_hash": entry.input_hash,
        "repo": repo,
    });
    if diagrams {
        facts["diagrams"] = json!(true);
    }
    facts
}

fn is_promoted(bundle: &Bundle, repo: &str, page_id: &str) -> bool {
    bundle
        .read_concept(&format!("wiki/pages/{repo}/{page_id}"))
        .map(|c| c.type_ == "Wiki-Article")
        .unwrap_or(false)
}

// --- pipeline ----------------------------------------

/// One run outcome: the (effective) plan plus what got queued.
pub struct WikiOutcome {
    pub plan: Vec<PageEntry>,
    pub queued_task_ids: Vec<String>,
    pub catalog_task_id: Option<String>,
    pub catalog_pending: bool,
}

/// Queue a `wiki-page` task per unskipped page; persist the manifest.
fn queue_pages(
    bundle: &Bundle,
    repo: &str,
    plan: &[PageEntry],
    force: bool,
    diagrams: bool,
) -> Result<Vec<String>, ToolError> {
    let _lock = bundle.lock()?;
    let mut manifest = load_manifest(bundle).map_err(pipeline_tool_error)?;
    if manifest.get("pages").is_none() {
        manifest["pages"] = json!({});
    }
    let mut pages: BTreeMap<String, Value> = manifest["pages"]
        .as_object()
        .map(|m| m.iter().map(|(k, v)| (k.clone(), v.clone())).collect())
        .unwrap_or_default();
    let live = live_generation_tasks(bundle);

    let mut queued_task_ids: Vec<String> = Vec::new();
    for entry in plan {
        let key = format!("{repo}/{}", entry.page_id);
        let row = pages.get(&key).cloned();
        let hash_matches = row
            .as_ref()
            .and_then(|r| r.get("input_hash"))
            .and_then(Value::as_str)
            == Some(entry.input_hash.as_str());
        let skip =
            hash_matches && (is_promoted(bundle, repo, &entry.page_id) || live.contains_key(&key));
        if !force && row.is_some() && skip {
            continue;
        }
        let facts = plan_facts(entry, repo, diagrams);
        let match_task = if force {
            None
        } else {
            live.get(&key).and_then(|tasks| {
                tasks
                    .iter()
                    .find(|t| t.facts_input_hash == entry.input_hash)
                    .cloned()
            })
        };
        let mut queue_attempts = row
            .as_ref()
            .and_then(|r| r.get("queue_attempts"))
            .and_then(Value::as_i64)
            .unwrap_or(0);
        let task_id = match match_task {
            Some(task) => task.id,
            None => {
                let id = create_task(bundle, "wiki-page", &key, &facts)?;
                queued_task_ids.push(id.clone());
                queue_attempts += 1;
                id
            }
        };
        pages.insert(key, entry.row_value(&task_id, queue_attempts, diagrams));
    }

    manifest["pages"] = Value::Object(pages.into_iter().collect());
    if !save_manifest(bundle, &manifest)? {
        return Err(ToolError(
            "failed to write the wiki manifest; queued tasks are not recorded".into(),
        ));
    }
    Ok(queued_task_ids)
}

/// Parse the refined outline JSON array, or None when the result never landed.
fn parse_outline(result: Option<String>) -> Option<Vec<Value>> {
    let parsed: Value = serde_json::from_str(&result?).ok()?;
    if parsed.is_array() {
        Some(parsed.as_array().cloned().unwrap_or_default())
    } else {
        None
    }
}

/// The result body for a completed task.
fn read_result(bundle: &Bundle, task_id: &str) -> Option<String> {
    bundle
        .read_concept(&format!("_tasks/{task_id}.result"))
        .ok()
        .map(|c| c.body)
}

/// The result's critic verdict, or "failed" when the result never landed.
fn result_critic_status(bundle: &Bundle, task_id: &str) -> String {
    bundle
        .read_concept(&format!("_tasks/{task_id}.result"))
        .ok()
        .and_then(|c| c.ext_str("critic_status").map(str::to_string))
        .unwrap_or_else(|| "failed".to_string())
}

/// Newest task by (created_at, attempt) — the head of a revise chain.
fn latest_task(tasks: &[TaskRec]) -> Option<&TaskRec> {
    tasks
        .iter()
        .max_by(|a, b| (&a.created_at, a.attempt).cmp(&(&b.created_at, b.attempt)))
}

/// The outline projection one catalog task carries (facts["outline"]).
fn page_outline_value(entry: &PageEntry) -> Value {
    json!({
        "page_id": entry.page_id,
        "title": entry.title,
        "description": entry.description,
        "module": entry.module,
        "source": "code",
        "seeds": entry.seeds_value(),
        "input_hash": entry.input_hash,
    })
}

/// One refine-catalog step: queue the catalog task, or consume its completed
/// result and queue the page tasks it validates to.
fn refine_catalog_step(
    conn: &Connection,
    bundle: &Bundle,
    repo: &str,
    plan: Vec<PageEntry>,
    force: bool,
    diagrams: bool,
) -> Result<WikiOutcome, ToolError> {
    let tasks: Vec<TaskRec> = list_tasks(bundle)
        .into_iter()
        .filter(|t| t.task_kind.starts_with(CATALOG_KIND) && t.resource == repo)
        .collect();
    let pending: Vec<TaskRec> = tasks
        .iter()
        .filter(|t| matches!(t.status.as_str(), "pending" | "in-progress"))
        .cloned()
        .collect();
    if let Some(latest) = latest_task(&pending) {
        return Ok(WikiOutcome {
            plan,
            queued_task_ids: vec![],
            catalog_task_id: Some(latest.id.clone()),
            catalog_pending: true,
        });
    }
    let done: Vec<TaskRec> = tasks
        .iter()
        .filter(|t| t.status == "done")
        .cloned()
        .collect();
    let mut refined: Option<Vec<Value>> = None;
    if let Some(latest) = latest_task(&done)
        && result_critic_status(bundle, &latest.id) != "failed"
    {
        refined = parse_outline(read_result(bundle, &latest.id));
    }
    if refined.is_none() {
        if tasks.is_empty() {
            let outline =
                serde_json::to_string(&plan.iter().map(page_outline_value).collect::<Vec<_>>())
                    .unwrap_or_else(|_| "[]".to_string());
            let task_id = create_task(
                bundle,
                CATALOG_KIND,
                repo,
                &json!({"repo": repo, "outline": outline}),
            )?;
            return Ok(WikiOutcome {
                plan,
                queued_task_ids: vec![],
                catalog_task_id: Some(task_id),
                catalog_pending: false,
            });
        }
        let queued = queue_pages(bundle, repo, &plan, force, diagrams)?;
        return Ok(WikiOutcome {
            plan,
            queued_task_ids: queued,
            catalog_task_id: None,
            catalog_pending: false,
        });
    }
    let effective = validate_refined_outline(&refined.unwrap_or_default(), plan, conn, repo)
        .map_err(|e| ToolError(e.to_string()))?;
    let queued = queue_pages(bundle, repo, &effective, force, diagrams)?;
    Ok(WikiOutcome {
        plan: effective,
        queued_task_ids: queued,
        catalog_task_id: None,
        catalog_pending: false,
    })
}

fn pipeline_tool_error(error: PipelineError) -> ToolError {
    match error {
        PipelineError::Planner(message) | PipelineError::Manifest(message) => ToolError(message),
        PipelineError::Write => {
            ToolError("failed to write the wiki manifest; queued tasks are not recorded".into())
        }
    }
}

/// Plan pages for `repo` and queue the work behind them.
pub fn run_wiki_generate(
    conn: &Connection,
    bundle: &Bundle,
    repo: &str,
    pages_cap: usize,
    force: bool,
    diagrams: bool,
    refine_catalog: bool,
) -> Result<WikiOutcome, PipelineError> {
    let plan = build_page_plan(conn, repo, pages_cap)?;
    if refine_catalog {
        return refine_catalog_step(conn, bundle, repo, plan, force, diagrams)
            .map_err(|e| PipelineError::Planner(e.0));
    }
    let queued_task_ids = queue_pages(bundle, repo, &plan, force, diagrams).map_err(|e| {
        match pipeline_tool_error_content(&e) {
            true => PipelineError::Write,
            false => PipelineError::Manifest(e.0),
        }
    })?;
    Ok(WikiOutcome {
        plan,
        queued_task_ids,
        catalog_task_id: None,
        catalog_pending: false,
    })
}

fn pipeline_tool_error_content(error: &ToolError) -> bool {
    error.0.starts_with("failed to write the wiki manifest")
}

/// `wiki_generate`: plan the deterministic wiki and queue one task per page.
pub fn wiki_generate_text(
    conn: &Connection,
    bundle: &Bundle,
    repo: &str,
    pages: i64,
    refine_catalog: bool,
    diagrams: bool,
    force: bool,
) -> Result<String, ToolError> {
    let pages = pages.clamp(1, 50) as usize;
    let result = match run_wiki_generate(conn, bundle, repo, pages, force, diagrams, refine_catalog)
    {
        Ok(result) => result,
        Err(PipelineError::Planner(message)) => {
            return Ok(format!("wiki_generate: {message} Nothing was queued."));
        }
        Err(PipelineError::Manifest(message)) => {
            return Ok(format!(
                "wiki_generate: {message} Nothing was recorded; re-running \
                 adopts the already-live tasks."
            ));
        }
        Err(PipelineError::Write) => {
            return Ok(
                "wiki_generate: failed to write the wiki manifest; queued tasks \
                 are not recorded Nothing was recorded; re-running adopts the \
                 already-live tasks."
                    .to_string(),
            );
        }
    };

    let mut out = vec![format!(
        "=== wiki_generate: {repo} ({} page(s) planned, {} task(s) queued) ===",
        result.plan.len(),
        result.queued_task_ids.len()
    )];
    for (i, page) in result.plan.iter().enumerate() {
        out.push(format!(
            "  {}. {}: {} [module {}]",
            i + 1,
            page.page_id,
            page.title,
            page.module
        ));
    }
    if !result.queued_task_ids.is_empty() {
        out.push(format!(
            "Queued wiki-page task ids (claim via the LLM task queue): {}",
            result.queued_task_ids.join(", ")
        ));
    }
    if let Some(catalog_task_id) = &result.catalog_task_id {
        let state = if result.catalog_pending {
            "Already pending"
        } else {
            "Queued"
        };
        out.push(format!(
            "{state} wiki-catalog task {catalog_task_id} — page tasks are only \
             queued from the validated refined outline. Re-run wiki_generate \
             after completing the catalog task."
        ));
    } else if result.queued_task_ids.is_empty() {
        out.push(
            "No new tasks queued — every planned page is already promoted and \
             unchanged (pass force=True to re-queue)."
                .to_string(),
        );
    }
    Ok(out.join("\n"))
}

// --- lifecycle read model ----------------------------

/// The lifecycle vocabulary every read model exposes, derived at read time.
pub const DERIVED_STATES: [&str; 6] = [
    "planned",
    "queued",
    "in-progress",
    "promoted",
    "failed",
    "dropped",
];

/// A manifest key `"{repo}/{page_id}"` -> `(repo, page_id)`.
pub fn split_page_key(key: &str) -> (String, String) {
    match key.split_once('/') {
        Some((repo, page_id)) => (repo.to_string(), page_id.to_string()),
        None => (String::new(), key.to_string()),
    }
}

/// The promoted `Wiki-Article` concept for a page, or None (never fatal).
pub fn read_page_concept(bundle: &Bundle, repo: &str, page_id: &str) -> Option<Concept> {
    bundle
        .read_concept(&format!("wiki/pages/{repo}/{page_id}"))
        .ok()
        .filter(|c| c.type_ == "Wiki-Article")
}

/// Composite key `{repo}/{resource}` -> the page's write-chain tasks.
pub fn page_chains(bundle: &Bundle) -> HashMap<String, Vec<TaskRec>> {
    let mut chains: HashMap<String, Vec<TaskRec>> = HashMap::new();
    for task in list_tasks(bundle) {
        if !is_write_kind(&task.task_kind) {
            continue;
        }
        chains.entry(chain_key(&task)).or_default().push(task);
    }
    chains
}

/// The page's lifecycle state, derived at read time — never stored.
pub fn derived_state(bundle: &Bundle, repo: &str, page_id: &str, chain: &[TaskRec]) -> String {
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
    // First maximal element wins on (created_at, attempt) ties.
    let mut latest: Option<&TaskRec> = None;
    for task in chain.iter().filter(|t| t.status == "done") {
        if latest.is_none_or(|l| (&task.created_at, task.attempt) > (&l.created_at, l.attempt)) {
            latest = Some(task);
        }
    }
    if let Some(latest) = latest
        && result_critic_status(bundle, &latest.id) != "passed"
    {
        return "failed".into();
    }
    "planned".into()
}

/// The page content's provenance sha (concept extension), or None.
pub fn recorded_sha(bundle: &Bundle, repo: &str, page_id: &str) -> Option<String> {
    read_page_concept(bundle, repo, page_id)?
        .ext_str("commit_sha")
        .filter(|s| !s.is_empty())
        .map(str::to_string)
}

/// fresh: recorded sha equals HEAD; stale: both present and differing; else unknown.
pub fn staleness(recorded: Option<&str>, head: Option<&str>) -> &'static str {
    match (
        recorded.filter(|s| !s.is_empty()),
        head.filter(|s| !s.is_empty()),
    ) {
        (Some(recorded), Some(head)) if recorded == head => "fresh",
        (Some(_), Some(_)) => "stale",
        _ => "unknown",
    }
}

// --- retry + enrich -------------------------------

/// One re-queued page: (task id, manifest key, new queue_attempts).
pub struct RequeuedPage {
    pub task_id: String,
    pub key: String,
    pub attempt: i64,
}

pub struct RetryOutcome {
    pub no_pages: bool,
    pub requeued: Vec<RequeuedPage>,
    pub write_failed: bool,
}

/// The work-order projection of a manifest row (plan_facts over JSON).
pub fn plan_facts_row(row: &Value, repo: &str, diagrams: bool) -> Value {
    let str_field = |key: &str| row.get(key).and_then(Value::as_str).unwrap_or("");
    let mut facts = json!({
        "title": str_field("title"),
        "description": str_field("description"),
        "module": str_field("module"),
        "seeds": row.get("seeds").cloned().unwrap_or(json!({})),
        "input_hash": str_field("input_hash"),
        "repo": repo,
    });
    if diagrams {
        facts["diagrams"] = json!(true);
    }
    facts
}

/// Re-queue derived-failed pages as fresh task chains; promoted pages untouched.
pub fn retry_failed_pages(
    bundle: &Bundle,
    repo: Option<&str>,
) -> Result<RetryOutcome, PipelineError> {
    let _lock = bundle.lock().map_err(|e| PipelineError::Manifest(e.0))?;
    let mut manifest = load_manifest(bundle)?;
    let pages: BTreeMap<String, Value> = manifest
        .get("pages")
        .and_then(Value::as_object)
        .map(|m| m.iter().map(|(k, v)| (k.clone(), v.clone())).collect())
        .unwrap_or_default();
    if pages.is_empty() {
        return Ok(RetryOutcome {
            no_pages: true,
            requeued: Vec::new(),
            write_failed: false,
        });
    }
    let chains = page_chains(bundle);
    let mut requeued = Vec::new();
    for (key, row) in &pages {
        let (page_repo, page_id) = split_page_key(key);
        if repo.is_some_and(|r| page_repo != r) {
            continue;
        }
        if derived_state(
            bundle,
            &page_repo,
            &page_id,
            chains.get(key).map(Vec::as_slice).unwrap_or(&[]),
        ) != "failed"
        {
            continue;
        }
        let diagrams = row.get("diagrams").and_then(Value::as_bool) == Some(true);
        let facts = plan_facts_row(row, &page_repo, diagrams);
        let task_id = create_task(bundle, "wiki-page", key, &facts)
            .map_err(|e| PipelineError::Manifest(e.0))?;
        let attempt = row
            .get("queue_attempts")
            .and_then(Value::as_i64)
            .unwrap_or(0)
            + 1;
        requeued.push(RequeuedPage {
            task_id,
            key: key.clone(),
            attempt,
        });
    }
    if requeued.is_empty() {
        return Ok(RetryOutcome {
            no_pages: false,
            requeued,
            write_failed: false,
        });
    }
    let mut updated = pages.clone();
    for page in &requeued {
        if let Some(row) = updated.get_mut(&page.key) {
            row["task_id"] = json!(page.task_id);
            row["queue_attempts"] = json!(page.attempt);
        }
    }
    manifest["pages"] = Value::Object(updated.into_iter().collect());
    let write_failed = !save_manifest(bundle, &manifest).unwrap_or(false);
    Ok(RetryOutcome {
        no_pages: false,
        requeued,
        write_failed,
    })
}

/// One queued enrichment: (task id, resource, repo fact).
pub struct EnrichTask {
    pub task_id: String,
    pub resource: String,
    pub repo: String,
}

/// Queue one `wiki-page-enrich` task per promoted manifest page with readable
/// content and no live enrich or write task.
pub fn queue_enrich_tasks(
    bundle: &Bundle,
    repo: Option<&str>,
    page_id: Option<&str>,
) -> Result<Vec<EnrichTask>, ToolError> {
    let manifest = load_manifest(bundle).map_err(pipeline_tool_error)?;
    let live_enrich: BTreeSet<String> = list_tasks(bundle)
        .into_iter()
        .filter(|t| {
            t.task_kind.starts_with("wiki-page-enrich")
                && matches!(t.status.as_str(), "pending" | "in-progress")
        })
        .map(|t| t.resource)
        .collect();
    let live_writes = live_generation_tasks(bundle);
    let mut queued = Vec::new();
    let mut keys: Vec<&String> = manifest
        .get("pages")
        .and_then(Value::as_object)
        .map(|m| m.keys().collect())
        .unwrap_or_default();
    keys.sort();
    for key in keys {
        let row = &manifest["pages"][key];
        let (row_repo, row_page) = split_page_key(key);
        if repo.is_some_and(|r| row_repo != r) {
            continue;
        }
        if page_id.is_some_and(|p| row_page != p) {
            continue;
        }
        if live_enrich.contains(key) || live_writes.contains_key(key) {
            continue;
        }
        if read_page_concept(bundle, &row_repo, &row_page).is_none() {
            continue;
        }
        let diagrams = row.get("diagrams").and_then(Value::as_bool) == Some(true);
        let facts = plan_facts_row(row, &row_repo, diagrams);
        let task_id = create_task(bundle, "wiki-page-enrich", key, &facts)?;
        queued.push(EnrichTask {
            task_id,
            resource: key.clone(),
            repo: row_repo,
        });
    }
    Ok(queued)
}

// --- deterministic generator ---------------------------

const REPORT_SECTION_VOCAB: [&str; 5] = [
    "## Overview",
    "## Symbol Distribution",
    "## Core Abstractions (most-referenced classes)",
    "## Cross-Repo Dependencies",
    "## Dependents",
];

/// One generated architecture report with its critic verdict.
pub struct WikiReport {
    pub concept: Concept,
    pub critic: CriticResult,
}

/// The graph-derived architecture wiki for one repo, Notes spliced, critic run.
pub fn generate_wiki_with_critic(
    conn: &Connection,
    bundle: &Bundle,
    repo: &str,
    workspace: &Path,
) -> Result<Vec<WikiReport>, ToolError> {
    let report = graph_derived_wiki(conn, bundle, repo, workspace)?;
    let critic = critic_concept_opts(&report, conn, Some(&REPORT_SECTION_VOCAB), false)?;
    Ok(vec![WikiReport {
        concept: report,
        critic,
    }])
}

fn graph_derived_wiki(
    conn: &Connection,
    bundle: &Bundle,
    repo: &str,
    workspace: &Path,
) -> Result<Concept, ToolError> {
    let symbols_in_repo: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM symbols s JOIN files f ON s.file_id = f.id \
             WHERE f.repo_id = ?1",
            [repo],
            |r| r.get(0),
        )
        .map_err(|e| ToolError(e.to_string()))?;
    let file_count: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM files WHERE repo_id = ?1",
            [repo],
            |r| r.get(0),
        )
        .map_err(|e| ToolError(e.to_string()))?;
    let by_kind: Vec<(String, i64)> = {
        let mut stmt = conn
            .prepare(
                "SELECT s.kind, COUNT(*) AS c FROM symbols s JOIN files f ON s.file_id = f.id \
                 WHERE f.repo_id = ?1 GROUP BY s.kind ORDER BY c DESC",
            )
            .map_err(|e| ToolError(e.to_string()))?;
        let rows = stmt
            .query_map([repo], |r| {
                Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?))
            })
            .map_err(|e| ToolError(e.to_string()))?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(|e| ToolError(e.to_string()))?
    };
    let top_classes: Vec<(String, i64)> = {
        let mut stmt = conn
            .prepare(
                "SELECT s.name, COUNT(e.id) AS incoming \
                 FROM symbols s LEFT JOIN edges e ON e.target_id = s.id \
                 JOIN files f ON s.file_id = f.id \
                 WHERE f.repo_id = ?1 AND s.kind IN ('class','interface') \
                 GROUP BY s.id ORDER BY incoming DESC LIMIT 10",
            )
            .map_err(|e| ToolError(e.to_string()))?;
        let rows = stmt
            .query_map([repo], |r| {
                Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?))
            })
            .map_err(|e| ToolError(e.to_string()))?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(|e| ToolError(e.to_string()))?
    };
    let namespaces = crate::graph::resolve_namespaces(
        workspace,
        std::env::var("CAIRN_REPO_NAMESPACES").ok().as_deref(),
    );
    let deps = crate::graph::cross_repo_deps(conn, repo, &namespaces)
        .map_err(|e| ToolError(e.to_string()))?;

    let mut parts = vec![
        format!("# {repo} Architecture\n"),
        "## Overview\n".to_string(),
        format!("{repo} contains {symbols_in_repo} symbols across {file_count} files.\n"),
        "\n## Symbol Distribution\n".to_string(),
    ];
    for (kind, count) in &by_kind {
        parts.push(format!("- {kind}: {count}"));
    }
    parts.push("\n## Core Abstractions (most-referenced classes)\n".to_string());
    for (name, incoming) in &top_classes {
        parts.push(format!("- `{name}` ({incoming} incoming references)"));
    }
    parts.push("\n## Cross-Repo Dependencies\n".to_string());
    if deps.dependencies.is_empty() {
        parts.push("(no cross-repo dependencies detected)".to_string());
    } else {
        parts.push("Depends on:".to_string());
        for d in &deps.dependencies {
            parts.push(format!("- {} ({}, x{})", d.repo, d.evidence, d.count));
        }
    }
    parts.push("\n## Dependents\n".to_string());
    if deps.dependents.is_empty() {
        parts.push("(none)".to_string());
    } else {
        for d in &deps.dependents {
            parts.push(format!("- {} (x{})", d.repo, d.count));
        }
    }
    let body = format!("{}\n", parts.join("\n"));
    let concept_id = format!("reports/architecture/{repo}");
    let spliced = preserved_concept_notes(bundle, &concept_id, &body);
    Ok(Concept {
        type_: "Architecture-Report".into(),
        title: Some(format!("{repo} Architecture")),
        description: Some(format!("Graph-derived architectural overview of {repo}")),
        resource: Some(repo.to_string()),
        tags: vec![repo.to_string(), "architecture".into()],
        timestamp: Some(iso_utc_now()),
        generated_by: None,
        status: None,
        stale_after: None,
        concept_id,
        body: spliced,
        extensions: Vec::new(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch_bundle(tag: &str) -> Bundle {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let dir =
            std::env::temp_dir().join(format!("cairn-wiki-{tag}-{}-{nanos}", std::process::id()));
        std::fs::create_dir_all(&dir).expect("bundle dir");
        Bundle::new(dir)
    }

    fn manifest_row(page_id: &str, task_id: &str, attempts: i64) -> Value {
        json!({
            "page_id": page_id,
            "title": page_id,
            "description": "",
            "module": "src/cairn/llm",
            "seeds": {"files": [], "symbols": [], "docs": []},
            "input_hash": format!("hash-{page_id}"),
            "task_id": task_id,
            "queue_attempts": attempts,
        })
    }

    /// A fixed-id wiki-page task concept with the shape list_tasks consumes.
    fn seed_task(bundle: &Bundle, id: &str, resource: &str, status: &str, critic: Option<&str>) {
        let mut concept = Concept {
            type_: "Task".into(),
            title: Some(format!("wiki-page: {resource}")),
            resource: Some(resource.into()),
            tags: vec!["wiki-page".into()],
            status: Some(status.into()),
            concept_id: format!("_tasks/{id}"),
            body: "# Task".into(),
            ..Default::default()
        };
        concept.extensions = vec![
            ("task_kind".into(), Yaml::String("wiki-page".into())),
            ("status".into(), Yaml::String(status.into())),
            ("attempt".into(), Yaml::Integer(1)),
            (
                "facts".into(),
                json_to_yaml(&json!({"repo": "cairn", "input_hash": "h"})),
            ),
        ];
        bundle.write_concept(&mut concept).expect("task write");
        if let Some(critic) = critic {
            let mut result = Concept {
                type_: "Task-Result".into(),
                title: Some(format!("Result for {id}")),
                concept_id: format!("_tasks/{id}.result"),
                body: "body".into(),
                ..Default::default()
            };
            result
                .extensions
                .push(("critic_status".into(), Yaml::String(critic.into())));
            bundle.write_concept(&mut result).expect("result");
        }
    }

    fn write_manifest(bundle: &Bundle, pages: Value) {
        let manifest = json!({"schema": MANIFEST_SCHEMA, "pages": pages});
        save_manifest(bundle, &manifest).expect("manifest write");
    }

    #[test]
    fn derived_state_precedence_matches_lifecycle() {
        let bundle = scratch_bundle("state");
        let chain: Vec<TaskRec> = Vec::new();
        assert_eq!(
            derived_state(&bundle, "cairn", "p", &chain),
            "planned",
            "no chain and no content is planned"
        );
        seed_task(&bundle, "t-done", "cairn/x", "done", Some("failed"));
        let chain: Vec<TaskRec> = list_tasks(&bundle);
        assert_eq!(
            derived_state(&bundle, "cairn", "x", &chain),
            "failed",
            "done chain without a passing critic verdict is failed"
        );
        let mut promoted = Concept {
            type_: "Wiki-Article".into(),
            title: Some("p".into()),
            concept_id: "wiki/pages/cairn/x".into(),
            body: "content".into(),
            ..Default::default()
        };
        bundle.write_concept(&mut promoted).expect("promoted");
        assert_eq!(
            derived_state(&bundle, "cairn", "x", &chain),
            "promoted",
            "readable content beats the failed chain"
        );
    }

    #[test]
    fn retry_requeues_only_failed_pages_and_bumps_attempts() {
        let bundle = scratch_bundle("retry");
        seed_task(
            &bundle,
            "t-failed",
            "cairn/failedpage",
            "done",
            Some("failed"),
        );
        seed_task(&bundle, "t-pending", "cairn/livepage", "pending", None);
        write_manifest(
            &bundle,
            json!({
                "cairn/failedpage": manifest_row("failedpage", "t-failed", 1),
                "cairn/livepage": manifest_row("livepage", "t-pending", 1),
            }),
        );
        let outcome = retry_failed_pages(&bundle, None).expect("retry");
        assert!(!outcome.no_pages);
        assert!(!outcome.write_failed);
        assert_eq!(outcome.requeued.len(), 1);
        let page = &outcome.requeued[0];
        assert_eq!(page.key, "cairn/failedpage");
        assert_eq!(page.attempt, 2);
        let manifest = load_manifest(&bundle).expect("reload");
        assert_eq!(
            manifest["pages"]["cairn/failedpage"]["queue_attempts"],
            json!(2)
        );
        let task = list_tasks(&bundle);
        assert!(
            task.iter()
                .any(|t| t.id == page.task_id && t.status == "pending")
        );
    }

    #[test]
    fn enrich_queues_only_promoted_pages_without_live_work() {
        let bundle = scratch_bundle("enrich");
        seed_task(&bundle, "t-live", "cairn/livepage", "pending", None);
        let mut promoted = Concept {
            type_: "Wiki-Article".into(),
            title: Some("p".into()),
            concept_id: "wiki/pages/cairn/promoted".into(),
            body: "content".into(),
            ..Default::default()
        };
        bundle.write_concept(&mut promoted).expect("promoted");
        write_manifest(
            &bundle,
            json!({
                "cairn/promoted": manifest_row("promoted", "t-done", 1),
                "cairn/livepage": manifest_row("livepage", "t-live", 1),
                "cairn/nocontent": manifest_row("nocontent", "t-none", 1),
            }),
        );
        let queued = queue_enrich_tasks(&bundle, None, None).expect("enrich");
        assert_eq!(queued.len(), 1);
        assert_eq!(queued[0].resource, "cairn/promoted");
        assert_eq!(queued[0].repo, "cairn");
    }
    #[test]
    fn page_input_hash_matches_canonicalization() {
        let entry = PageEntry {
            page_id: "overview".into(),
            title: "corpus architecture overview".into(),
            description: "Architecture overview of corpus across 2 modules.".into(),
            module: "".into(),
            seeds_files: vec!["src/graph/queries.py".into()],
            seeds_symbols: vec!["resolve_store".into()],
            input_hash: String::new(),
        };
        assert_eq!(
            page_input_hash(&entry),
            "51473a7d87151e26460a67cf973bb3f08182a77d3db66da4791dfe87803e9c88"
        );
    }
}
