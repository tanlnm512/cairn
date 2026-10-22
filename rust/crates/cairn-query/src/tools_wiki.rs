//! Wiki generation pipeline: catalog planner, manifest, task queue
//! (wiki/catalog.py + pipeline.py + manifest.py + lifecycle.py port).

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::sync::atomic::{AtomicU64, Ordering};

use rusqlite::Connection;
use serde_json::{Value, json};
use yaml_rust2::Yaml;
use yaml_rust2::yaml::Hash;

use crate::tools_km::{Bundle, Concept, ToolError, iso_utc_now, path_match_params};

const SEED_LIMIT: usize = 10;
const CATALOG_KIND: &str = "wiki-catalog";
const MANIFEST_SCHEMA: &str = "cairn-wiki-manifest-3";
const TEST_SEGMENTS: [&str; 4] = ["test", "tests", "spec", "specs"];

/// Why a wiki_generate run produced nothing.
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
    fn seeds_value(&self) -> Value {
        json!({
            "files": self.seeds_files,
            "symbols": self.seeds_symbols,
            "docs": [],
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

// --- catalog planning (wiki/catalog.py port) ---------------------------------

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

// --- refined-outline validation (wiki/refine.py port) ------------------------

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

// --- manifest (wiki/manifest.py port) ----------------------------------------

fn manifest_path(bundle: &Bundle) -> std::path::PathBuf {
    bundle.root.join("_wiki").join("manifest.json")
}

/// Load the manifest; malformed shapes surface as the ValueError tool branch.
fn load_manifest(bundle: &Bundle) -> Result<Value, PipelineError> {
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

/// Recreate Python `json.dump(..., sort_keys=True, indent=2)` output.
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

// --- task queue (llm/tasks.py + wiki/lifecycle.py port) ----------------------

/// One listed task record (only the fields the pipeline consumes).
#[derive(Clone, Debug)]
struct TaskRec {
    id: String,
    task_kind: String,
    resource: String,
    facts_input_hash: String,
    facts_repo: String,
    status: String,
    created_at: String,
    attempt: i64,
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

// --- pipeline (wiki/pipeline.py port) ----------------------------------------

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

/// The result body for a completed task (tasks.py read_result port).
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
