//! LLM task queue: agent-decoupled synthesis via OKF Task concepts.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use regex::Regex;
use rusqlite::Connection;
use yaml_rust2::Yaml;
use yaml_rust2::yaml::Hash;

use crate::tools_km::{
    self, Bundle, Concept, CriticResult, ToolError, critic_concept_opts, extract_file_refs,
    file_exists, iso_utc_now, normalize_doc_id, parse_iso_ts, strip_private_data, symbol_exists,
};

pub const TASK_DIR: &str = "_tasks";
pub const MAX_REVISE_CYCLES: i64 = 3;
pub const CLAIM_STALE_SECONDS: i64 = 3600;

const RACE_LOST: &str = "task no longer in-progress; another writer finished it";

const DOC_LINK_LINE_SPEC: &str = "<concept_id> <relation> <related_id>";
const RELATION_CHOICES: &str = "relates-to | supersedes | superseded-by | references";
const DOC_LINK_OUTPUT_SPEC: &str = concat!(
    "Propose relationships between the isolated documents in facts.members. ",
    "Read each member document first. Output ONLY proposed-edge lines, one ",
    "per line, in this exact format:\n",
    "<concept_id> <relation> <related_id>\n",
    "where <relation> is one of: relates-to | supersedes | superseded-by | references. ",
    "<concept_id> and <related_id> must be existing knowledge document ids ",
    "(facts.members lists them). No prose, no code fences, no extra ",
    "formatting.",
);

fn mirror_relation(relation: &str) -> &'static str {
    match relation {
        "supersedes" => "superseded-by",
        "superseded-by" => "supersedes",
        "references" => "references",
        _ => "relates-to",
    }
}

fn ys(s: impl Into<String>) -> Yaml {
    Yaml::String(s.into())
}

fn ystr(v: Option<&Yaml>) -> String {
    match v {
        Some(Yaml::String(s)) => s.clone(),
        Some(Yaml::Real(s)) => s.clone(),
        Some(Yaml::Integer(i)) => i.to_string(),
        Some(Yaml::Boolean(b)) => if *b { "True" } else { "False" }.into(),
        _ => String::new(),
    }
}

/// Python str() of a loaded YAML value (containers render elements repr-style).
pub fn py_display(v: &Yaml) -> String {
    match v {
        Yaml::String(s) => s.clone(),
        Yaml::Array(_) | Yaml::Hash(_) => py_repr(v),
        other => py_repr(other),
    }
}

fn py_repr(v: &Yaml) -> String {
    match v {
        Yaml::String(s) => format!("'{}'", s.replace('\\', "\\\\").replace('\'', "\\'")),
        Yaml::Boolean(b) => if *b { "True" } else { "False" }.into(),
        Yaml::Integer(i) => i.to_string(),
        Yaml::Real(s) => s.clone(),
        Yaml::Null => "None".into(),
        Yaml::Array(items) => {
            let inner: Vec<String> = items.iter().map(py_repr).collect();
            format!("[{}]", inner.join(", "))
        }
        Yaml::Hash(h) => {
            let inner: Vec<String> = h
                .iter()
                .map(|(k, val)| format!("{}: {}", py_repr(k), py_repr(val)))
                .collect();
            format!("{{{}}}", inner.join(", "))
        }
        other => format!("{other:?}"),
    }
}

fn set_ext(extensions: &mut Vec<(String, Yaml)>, key: &str, value: Yaml) {
    extensions.retain(|(k, _)| k != key);
    extensions.push((key.to_string(), value));
}

fn now_secs() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

// --- task record + OKF (de)serialization --------------------------------------

#[derive(Clone, Debug)]
pub struct Task {
    pub id: String,
    pub task_kind: String,
    pub resource: String,
    pub facts: Hash,
    pub status: String,
    pub assigned_to: String,
    pub result_path: String,
    pub attempt: i64,
    pub created_at: String,
    pub completed_at: String,
    pub claimed_at: String,
}

impl Default for Task {
    fn default() -> Self {
        Self {
            id: String::new(),
            task_kind: String::new(),
            resource: String::new(),
            facts: Hash::new(),
            status: "pending".into(),
            assigned_to: String::new(),
            result_path: String::new(),
            attempt: 1,
            created_at: String::new(),
            completed_at: String::new(),
            claimed_at: String::new(),
        }
    }
}

impl Task {
    pub fn concept_id(&self) -> String {
        format!("{TASK_DIR}/{}", self.id)
    }

    pub fn result_concept_id(&self) -> String {
        format!("{TASK_DIR}/{}.result", self.id)
    }

    pub fn fact(&self, key: &str) -> Option<&Yaml> {
        self.facts.get(&ys(key))
    }

    pub fn fact_str(&self, key: &str) -> String {
        match self.fact(key) {
            Some(Yaml::String(s)) => s.clone(),
            Some(Yaml::Real(s)) => s.clone(),
            Some(Yaml::Integer(i)) => i.to_string(),
            _ => String::new(),
        }
    }
}

fn facts_hash(value: Option<&Yaml>) -> Hash {
    match value {
        Some(Yaml::Hash(h)) => h.clone(),
        _ => Hash::new(),
    }
}

fn task_to_concept(task: &Task) -> Concept {
    let mut lines = vec![format!("# Task: {}", task.task_kind), String::new()];
    lines.push(format!("**Resource:** `{}`  ", task.resource));
    lines.push(format!("**Status:** {}  ", task.status));
    lines.push(format!(
        "**Attempt:** {}/{}",
        task.attempt,
        MAX_REVISE_CYCLES + 1
    ));
    lines.push(String::new());
    if !task.facts.is_empty() {
        lines.push("## Facts (graph-grounded — do not invent beyond these)".into());
        for (key, val) in &task.facts {
            lines.push(format!("**{}:** {}", py_display(key), py_display(val)));
        }
        lines.push(String::new());
    }
    lines.push("## Output spec".into());
    lines.push(output_spec(&task.task_kind, &task.facts));
    lines.push(format!(
        "\nWrite your result, then run: `cairn task complete {}`",
        task.id
    ));
    let body = format!("{}\n", lines.join("\n"));
    Concept {
        type_: "Task".into(),
        title: Some(format!("{}: {}", task.task_kind, task.resource)),
        description: None,
        resource: Some(task.resource.clone()),
        tags: vec![task.task_kind.clone()],
        timestamp: Some(task.created_at.clone()),
        generated_by: None,
        status: Some(task.status.clone()),
        stale_after: None,
        concept_id: task.concept_id(),
        body,
        extensions: vec![
            ("task_kind".into(), ys(&task.task_kind)),
            ("assigned_to".into(), ys(&task.assigned_to)),
            ("attempt".into(), Yaml::Integer(task.attempt)),
            ("result_path".into(), ys(&task.result_path)),
            ("completed_at".into(), ys(&task.completed_at)),
            ("claimed_at".into(), ys(&task.claimed_at)),
            ("facts".into(), Yaml::Hash(task.facts.clone())),
        ],
    }
}

fn concept_to_task(concept: &Concept) -> Task {
    let get = |key: &str| concept.ext_str(key).unwrap_or_default().to_string();
    Task {
        id: concept
            .concept_id
            .rsplit('/')
            .next()
            .unwrap_or(&concept.concept_id)
            .to_string(),
        task_kind: get("task_kind"),
        resource: concept.resource.clone().unwrap_or_default(),
        facts: facts_hash(concept.ext("facts")),
        status: concept
            .status
            .clone()
            .or_else(|| concept.ext_str("status").map(String::from))
            .unwrap_or_else(|| "pending".into()),
        assigned_to: get("assigned_to"),
        result_path: get("result_path"),
        attempt: concept.ext_f64("attempt").unwrap_or(1.0) as i64,
        created_at: concept.timestamp.clone().unwrap_or_default(),
        completed_at: get("completed_at"),
        claimed_at: get("claimed_at"),
    }
}

fn read(bundle: &Bundle, task_id: &str) -> Result<Option<Task>, ToolError> {
    match bundle.read_concept(&format!("{TASK_DIR}/{task_id}")) {
        Ok(concept) if concept.type_ == "Task" => Ok(Some(concept_to_task(&concept))),
        Ok(_) => Ok(None),
        // an id escaping the bundle root or a missing file reads as not found
        Err(e)
            if e.0.starts_with("concept_id escapes bundle root")
                || e.0.contains("No such file or directory") =>
        {
            Ok(None)
        }
        Err(e) => Err(e),
    }
}

pub fn get_task(bundle: &Bundle, task_id: &str) -> Result<Option<Task>, ToolError> {
    read(bundle, task_id)
}

pub fn read_result(bundle: &Bundle, task_id: &str) -> Option<String> {
    bundle
        .read_concept(&format!("{TASK_DIR}/{task_id}.result"))
        .ok()
        .map(|c| c.body)
}

static TASK_SEQ: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

/// A 12-hex task id (uuid4().hex[:12] shape) from time/pid/counter entropy.
fn new_task_id() -> String {
    use std::sync::atomic::Ordering;
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
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

// --- queue operations ---------------------------------------------------------

/// Queue a new task; returns it already written to the bundle.
pub fn create_task(
    bundle: &Bundle,
    task_kind: &str,
    resource: &str,
    facts: Hash,
    parent_attempt: i64,
) -> Result<Task, ToolError> {
    // memory-* facts derive from session content: scrub secret-shaped
    // substrings; non-string facts pass through.
    let facts = if task_kind.starts_with("memory-") && !facts.is_empty() {
        facts
            .iter()
            .map(|(k, v)| {
                let scrubbed = match v {
                    Yaml::String(s) => ys(strip_private_data(s)),
                    other => other.clone(),
                };
                (k.clone(), scrubbed)
            })
            .collect::<Hash>()
    } else {
        facts
    };
    let task = Task {
        id: new_task_id(),
        task_kind: task_kind.to_string(),
        resource: resource.to_string(),
        attempt: parent_attempt + 1,
        created_at: iso_utc_now(),
        facts,
        ..Default::default()
    };
    let mut concept = task_to_concept(&task);
    bundle.write_concept(&mut concept)?;
    Ok(task)
}

/// List tasks, optionally filtered by status, exact kind, or kind prefix.
pub fn list_tasks(
    bundle: &Bundle,
    status: Option<&str>,
    kind: Option<&str>,
    kind_prefix: Option<&str>,
) -> Result<Vec<Task>, ToolError> {
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
        if status.is_some_and(|s| task.status != s)
            || kind.is_some_and(|k| task.task_kind != k)
            || kind_prefix.is_some_and(|p| !task.task_kind.starts_with(p))
        {
            continue;
        }
        out.push(task);
    }
    Ok(out)
}

fn claim_marker_path(bundle: &Bundle, task_id: &str) -> Option<PathBuf> {
    let marker = bundle.root.join(format!("{TASK_DIR}/{task_id}.claim"));
    let root = tools_km::resolve_lenient(&bundle.root);
    let resolved = tools_km::resolve_lenient(&marker);
    // a TASK_ID escaping the bundle root must not write outside it
    resolved.starts_with(&root).then_some(marker)
}

fn try_remove_stale_marker(marker: &Path) -> bool {
    let Ok(meta) = std::fs::metadata(marker) else {
        // vanished between create_new failure and stat: retry the create
        return true;
    };
    let Ok(mtime) = meta.modified() else {
        return false;
    };
    // signed: a future mtime (clock skew) reads as fresh, like time.time()-mtime
    let mtime_secs = mtime
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0);
    let age = now_secs() - mtime_secs;
    if age < CLAIM_STALE_SECONDS as f64 {
        return false;
    }
    std::fs::remove_file(marker).is_ok()
}

/// `claimed_at` in the exact `%Y-%m-%dT%H:%M:%SZ` shape, as epoch seconds.
fn parse_claimed_at(value: &str) -> Option<i64> {
    let b = value.as_bytes();
    let shape = b.len() == 20
        && b[4] == b'-'
        && b[7] == b'-'
        && b[10] == b'T'
        && b[13] == b':'
        && b[16] == b':'
        && b[19] == b'Z';
    if !shape {
        return None;
    }
    parse_iso_ts(value, false)
}

fn claim_is_stale(task: &Task) -> bool {
    match parse_claimed_at(&task.claimed_at) {
        None => true,
        Some(secs) => now_secs() - secs as f64 >= CLAIM_STALE_SECONDS as f64,
    }
}

fn create_marker_exclusive(marker: &Path) -> std::io::Result<std::fs::File> {
    std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(marker)
}

/// Claim a pending task under its marker; None when unclaimable or the
/// claimant is live.
pub fn claim_task(
    bundle: &Bundle,
    task_id: &str,
    assigned_to: &str,
) -> Result<Option<Task>, ToolError> {
    let Some(marker) = claim_marker_path(bundle, task_id) else {
        return Ok(None);
    };
    let mut stale_collision = false;
    match create_marker_exclusive(&marker) {
        Ok(_) => {}
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {
            // marker exists: only a leaked (stale) one may be reclaimed
            if !try_remove_stale_marker(&marker) {
                return Ok(None);
            }
            stale_collision = true;
            if create_marker_exclusive(&marker).is_err() {
                return Ok(None);
            }
        }
        Err(_) => return Ok(None),
    }
    let claimed = (|| -> Result<Option<Task>, ToolError> {
        // read-check-write under the bundle lock so a concurrent drop or
        // completion cannot interleave
        let _lock = bundle.lock()?;
        let task = read(bundle, task_id)?;
        let reclaimable = stale_collision
            && task.as_ref().is_some_and(|t| t.status == "in-progress")
            && task.as_ref().is_some_and(claim_is_stale);
        match task {
            Some(mut task) if task.status == "pending" || reclaimable => {
                task.status = "in-progress".into();
                task.assigned_to = assigned_to.to_string();
                task.claimed_at = iso_utc_now();
                let mut concept = task_to_concept(&task);
                bundle.write_concept(&mut concept)?;
                Ok(Some(task))
            }
            _ => Ok(None),
        }
    })();
    if claimed.is_err() {
        let _ = std::fs::remove_file(&marker);
    }
    match claimed {
        Ok(None) => {
            let _ = std::fs::remove_file(&marker);
            Ok(None)
        }
        other => other,
    }
}

// --- completion ---------------------------------------------------------------

#[derive(Clone, Debug)]
pub struct CompleteOutcome {
    pub task_id: String,
    pub promoted: bool,
    pub revised: bool,
    pub dropped: bool,
    pub refused: bool,
    pub errors: Vec<String>,
    pub quality: f64,
}

impl CompleteOutcome {
    fn refused(task_id: &str, errors: Vec<String>) -> Self {
        Self {
            task_id: task_id.to_string(),
            promoted: false,
            revised: false,
            dropped: false,
            refused: true,
            errors,
            quality: 0.0,
        }
    }

    fn held(task_id: &str, errors: Vec<String>) -> Self {
        Self {
            task_id: task_id.to_string(),
            promoted: false,
            revised: false,
            dropped: false,
            refused: false,
            errors,
            quality: 0.0,
        }
    }
}

/// Persist a terminal status iff the task is still in-progress; false when
/// another writer finished it first.
fn finish_terminal(bundle: &Bundle, task: &Task, status: &str) -> Result<bool, ToolError> {
    let _lock = bundle.lock()?;
    match read(bundle, &task.id)? {
        Some(current) if current.status == "in-progress" => {
            let mut finished = task.clone();
            finished.status = status.to_string();
            finished.completed_at = iso_utc_now();
            let mut concept = task_to_concept(&finished);
            bundle.write_concept(&mut concept)?;
            Ok(true)
        }
        _ => Ok(false),
    }
}

/// The enriched Wiki-Article: the promoted page's body read at completion
/// time with `new_sections` appended and sources merged.
#[allow(clippy::too_many_arguments)]
fn enriched_article(
    bundle: &Bundle,
    task: &Task,
    task_id: &str,
    repo: &str,
    page_id: &str,
    new_sections: &str,
    new_sources: Option<&[Yaml]>,
    commit_sha: Option<&str>,
) -> Result<Concept, ToolError> {
    let current = bundle.read_concept(&format!("wiki/pages/{repo}/{page_id}"))?;
    let base = current.body;
    let mut merged: Vec<Yaml> = current
        .extensions
        .iter()
        .filter(|(k, _)| k == "sources")
        .flat_map(|(_, v)| match v {
            Yaml::Array(items) => items.clone(),
            _ => Vec::new(),
        })
        .collect();
    if let Some(entries) = new_sources {
        for entry in entries {
            if !merged.contains(entry) {
                merged.push(entry.clone());
            }
        }
    }
    let body = if base.is_empty() {
        new_sections.to_string()
    } else {
        format!("{base}\n\n{new_sections}")
    };
    let mut extensions = vec![
        ("page_id".into(), ys(page_id)),
        ("repo".into(), ys(repo)),
        (
            "input_hash".into(),
            task.fact("input_hash").cloned().unwrap_or(Yaml::Null),
        ),
        ("task_id".into(), ys(task_id)),
    ];
    if let Some(sha) = commit_sha {
        extensions.push(("commit_sha".into(), ys(sha)));
    }
    Ok(Concept {
        type_: "Wiki-Article".into(),
        title: Some(format!("Wiki: {page_id}")),
        description: Some(format!("Wiki article for {repo}/{page_id}")),
        resource: Some(page_id.to_string()),
        tags: vec![repo.to_string(), "wiki".into()],
        timestamp: Some(iso_utc_now()),
        generated_by: None,
        status: None,
        stale_after: None,
        concept_id: format!("wiki/pages/{repo}/{page_id}"),
        body,
        extensions: {
            let mut ext = extensions;
            if !merged.is_empty() {
                ext.push(("sources".into(), Yaml::Array(merged)));
            }
            ext
        },
    })
}

fn derive_title(module_path: &str) -> String {
    let parts: Vec<&str> = module_path
        .trim_matches('/')
        .split('/')
        .filter(|p| !p.is_empty())
        .collect();
    if parts.len() >= 2 {
        parts[parts.len() - 2..].join(" / ")
    } else {
        parts.last().copied().unwrap_or(module_path).to_string()
    }
}

/// The named repo's HEAD commit hash under the workspace, or None. The repo
/// root comes from the store's repos table (the recorded discovery roots)
/// when a connection is given; absent a row, the workspace/repo-name and
/// single-repo fallbacks apply.
pub fn repo_head(
    conn: Option<&Connection>,
    workspace: Option<&Path>,
    repo: &str,
) -> Option<String> {
    let ws = workspace?;
    let mut repo_path = ws.join(repo);
    let row: Option<String> = conn
        .and_then(|conn| {
            conn.query_row(
                "SELECT path FROM repos WHERE name = ? OR id = ? LIMIT 1",
                rusqlite::params![repo, repo],
                |r| r.get(0),
            )
            .ok()
        })
        .flatten();
    if let Some(root) = row {
        repo_path = if root == "." {
            ws.to_path_buf()
        } else {
            ws.join(root)
        };
    } else {
        let single_repo = ws.join(".git").exists()
            && ws
                .read_dir()
                .ok()?
                .all(|e| e.ok().is_none_or(|e| !e.path().join(".git").exists()));
        if single_repo {
            repo_path = ws.to_path_buf();
        }
    }
    let out = std::process::Command::new("git")
        .arg("-C")
        .arg(&repo_path)
        .args(["rev-parse", "HEAD"])
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    let sha = String::from_utf8_lossy(&out.stdout).trim().to_string();
    (!sha.is_empty()).then_some(sha)
}

fn parse_sources_footer(body: &str) -> Vec<String> {
    static LINK_RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    let link_re =
        LINK_RE.get_or_init(|| Regex::new(r"\[[^\]]*\]\(([^)\s]+)\)").expect("link regex"));
    let lines: Vec<&str> = body.lines().collect();
    let Some(start) = lines
        .iter()
        .position(|l| l.trim() == "## Sources")
        .map(|i| i + 1)
    else {
        return Vec::new();
    };
    let mut entries = Vec::new();
    for line in &lines[start..] {
        let stripped = line.trim();
        if stripped.is_empty() {
            continue;
        }
        if stripped.starts_with('#') {
            break;
        }
        let targets: Vec<&str> = link_re
            .captures_iter(stripped)
            .filter_map(|c| c.get(1).map(|m| m.as_str()))
            .collect();
        if !targets.is_empty() {
            entries.extend(
                targets
                    .into_iter()
                    .map(|t| t.split('#').next().unwrap_or(t).to_string()),
            );
            continue;
        }
        entries.extend(extract_file_refs(stripped));
    }
    entries
}

/// (resolved entries, errors) for footer entries against the graph.
fn resolve_sources(conn: &Connection, entries: &[String]) -> (Vec<String>, Vec<String>) {
    let mut resolved = Vec::new();
    let mut errors = Vec::new();
    for entry in entries {
        let is_file = file_exists(conn, entry).unwrap_or(false);
        if is_file || symbol_exists(conn, entry).unwrap_or(false) {
            resolved.push(entry.clone());
        } else {
            errors.push(format!("Unresolved Sources footer entry: {entry}"));
        }
    }
    (resolved, errors)
}

/// Mark done and run the deterministic critic on the result. On fact-check
/// failure spawns a revise task (up to MAX_REVISE_CYCLES); an exhausted chain
/// persists status failed. A `claimer` that does not own the task refuses.
pub fn complete_task(
    bundle: &Bundle,
    task_id: &str,
    result: &str,
    conn: Option<&Connection>,
    claimer: Option<&str>,
    workspace: Option<&Path>,
) -> Result<CompleteOutcome, ToolError> {
    let Some(task) = read(bundle, task_id)? else {
        return Ok(CompleteOutcome::refused(
            task_id,
            vec!["task not in-progress or not found".into()],
        ));
    };
    if task.status != "in-progress" {
        return Ok(CompleteOutcome::refused(
            task_id,
            vec!["task not in-progress or not found".into()],
        ));
    }
    if let Some(claimer) = claimer
        && !task.assigned_to.is_empty()
        && claimer != task.assigned_to
    {
        return Ok(CompleteOutcome::refused(
            task_id,
            vec![format!(
                "ownership mismatch: task owned by '{}' but complete called by '{claimer}'",
                task.assigned_to
            )],
        ));
    }
    // memory-* results derive from user session content: scrub before persist
    let mut result = result.to_string();
    if task.task_kind.starts_with("memory-") {
        result = strip_private_data(&result);
    }
    if task.task_kind.starts_with("wiki-page") && task.fact_str("repo").is_empty() {
        return Ok(CompleteOutcome::held(
            task_id,
            vec!["wiki-page task is missing the required 'repo' fact".into()],
        ));
    }
    // doc-link completions are gate-checked before anything is written
    let mut doc_link_edges: Vec<(String, String, String)> = Vec::new();
    if task.task_kind == "doc-link" {
        let Some(_conn) = conn else {
            return Ok(CompleteOutcome::held(
                task_id,
                vec!["doc-link completion requires a graph database connection".into()],
            ));
        };
        let (edges, mut errors) = parse_doc_link_result(&result);
        doc_link_edges = edges;
        if errors.is_empty() && doc_link_edges.is_empty() {
            // an empty result must not promote vacuously
            errors.push("no proposed edges found".into());
        }
        if errors.is_empty() {
            let members = match task.fact("members") {
                Some(Yaml::Array(items)) => Some(
                    items
                        .iter()
                        .filter_map(|i| match i {
                            Yaml::String(s) => Some(s.clone()),
                            _ => None,
                        })
                        .collect::<Vec<String>>(),
                ),
                _ => None,
            };
            errors = validate_doc_link_edges(bundle, &doc_link_edges, members.as_deref());
        }
        if !errors.is_empty() {
            return Ok(CompleteOutcome::held(task_id, errors));
        }
    }
    let mut result_concept = Concept {
        type_: "Task-Result".into(),
        title: Some(format!("Result for {task_id}")),
        description: None,
        resource: Some(task.resource.clone()),
        tags: Vec::new(),
        timestamp: Some(iso_utc_now()),
        generated_by: None,
        status: None,
        stale_after: None,
        concept_id: format!("{TASK_DIR}/{task_id}.result"),
        body: result.clone(),
        extensions: Vec::new(),
    };
    bundle.write_concept(&mut result_concept)?;
    // the claim marker goes now: from here the task is finished or left
    // in-progress and re-completable when the critic itself fails
    if let Some(marker) = claim_marker_path(bundle, task_id) {
        let _ = std::fs::remove_file(&marker);
    }
    let Some(conn) = conn else {
        if !finish_terminal(bundle, &task, "done")? {
            return Ok(CompleteOutcome::refused(task_id, vec![RACE_LOST.into()]));
        }
        return Ok(CompleteOutcome::held(task_id, Vec::new()));
    };
    // any failure past this point mirrors Python's except handler: the task
    // stays in-progress and re-completable, nothing is promoted
    Ok(complete_with_critic(
        bundle,
        conn,
        task_id,
        &task,
        &result,
        workspace,
        &doc_link_edges,
    )
    .unwrap_or_else(|_| CompleteOutcome::held(task_id, vec!["critic execution failed".into()])))
}

fn complete_with_critic(
    bundle: &Bundle,
    conn: &Connection,
    task_id: &str,
    task: &Task,
    result: &str,
    workspace: Option<&Path>,
    doc_link_edges: &[(String, String, String)],
) -> Result<CompleteOutcome, ToolError> {
    let mut critic_result = run_critic(conn, task, result)?;
    let wiki_page = task.task_kind.starts_with("wiki-page");
    let mut wiki_sources: Option<Vec<Yaml>> = None;
    if wiki_page {
        let (resolved, source_errors) = resolve_sources(conn, &parse_sources_footer(result));
        let body_unresolved: Vec<String> = extract_file_refs(result)
            .into_iter()
            .filter(|r| !file_exists(conn, r).unwrap_or(false))
            .collect();
        let filtered: Vec<String> = source_errors
            .into_iter()
            .filter(|e| {
                !body_unresolved
                    .iter()
                    .any(|r| e.ends_with(&format!(": {r}")))
            })
            .collect();
        if !filtered.is_empty() {
            critic_result = CriticResult {
                errors: [critic_result.errors, filtered].concat(),
                warnings: critic_result.warnings,
                quality_score: critic_result.quality_score,
                passed: false,
            };
        } else {
            wiki_sources = Some(
                resolved
                    .into_iter()
                    .map(|e| {
                        if file_exists(conn, &e).unwrap_or(false) {
                            Yaml::Hash({
                                let mut h = Hash::new();
                                h.insert(ys("path"), ys(&e));
                                h
                            })
                        } else {
                            Yaml::Hash({
                                let mut h = Hash::new();
                                h.insert(ys("symbol"), ys(&e));
                                h
                            })
                        }
                    })
                    .collect(),
            );
        }
    }
    // the result concept written before the marker removal, re-stamped
    let mut result_concept = bundle.read_concept(&format!("{TASK_DIR}/{task_id}.result"))?;
    set_ext(
        &mut result_concept.extensions,
        "critic_status",
        ys(if critic_result.passed {
            "passed"
        } else {
            "failed"
        }),
    );
    bundle.write_concept(&mut result_concept)?;
    if !critic_result.passed {
        if task.attempt < MAX_REVISE_CYCLES {
            let revise_kind = if let Some(base) = task.task_kind.strip_suffix("-synthesize") {
                format!("{base}-revise")
            } else {
                format!("{}-revise", task.task_kind)
            };
            let mut revise_facts = task.facts.clone();
            revise_facts.insert(
                ys("errors"),
                Yaml::Array(critic_result.errors.iter().map(ys).collect()),
            );
            revise_facts.insert(ys("parent_task_id"), ys(task_id));
            create_task(
                bundle,
                &revise_kind,
                &task.resource,
                revise_facts,
                task.attempt,
            )?;
            if !finish_terminal(bundle, task, "done")? {
                return Ok(CompleteOutcome::refused(task_id, vec![RACE_LOST.into()]));
            }
            return Ok(CompleteOutcome {
                task_id: task_id.to_string(),
                promoted: false,
                revised: true,
                dropped: false,
                refused: false,
                errors: critic_result.errors,
                quality: critic_result.quality_score,
            });
        }
        if !finish_terminal(bundle, task, "failed")? {
            return Ok(CompleteOutcome::refused(task_id, vec![RACE_LOST.into()]));
        }
        return Ok(CompleteOutcome {
            task_id: task_id.to_string(),
            promoted: false,
            revised: false,
            dropped: true,
            refused: false,
            errors: critic_result.errors,
            quality: critic_result.quality_score,
        });
    }
    let mut promoted = false;
    {
        let _lock = bundle.lock()?;
        match read(bundle, task_id)? {
            Some(current) if current.status == "in-progress" => {}
            _ => return Ok(CompleteOutcome::refused(task_id, vec![RACE_LOST.into()])),
        }
        let warning_marker = || -> String {
            if critic_result.warnings.is_empty() {
                result.to_string()
            } else {
                format!(
                    "> [critic-warning] {}\n\n{result}",
                    critic_result.warnings.join("; ")
                )
            }
        };
        if matches!(
            task.task_kind.as_str(),
            "compass-synthesize" | "compass-revise"
        ) {
            let module_path = task.resource.clone();
            let compass_concept = Concept {
                type_: "Compass".into(),
                title: Some(derive_title(&module_path)),
                description: Some(format!("Navigation guide for {module_path}")),
                resource: Some(module_path.clone()),
                tags: module_path
                    .trim_matches('/')
                    .split('/')
                    .filter(|t| !t.is_empty())
                    .take(6)
                    .map(String::from)
                    .collect(),
                timestamp: Some(iso_utc_now()),
                generated_by: None,
                status: None,
                stale_after: None,
                concept_id: format!(
                    "compass/{}",
                    module_path.trim_matches('/').replace('/', "-")
                ),
                body: warning_marker(),
                extensions: Vec::new(),
            };
            let mut compass_concept = compass_concept;
            bundle.write_concept(&mut compass_concept)?;
            promoted = true;
        }
        if matches!(task.task_kind.as_str(), "flow-synthesize" | "flow-revise") {
            let entry = task.resource.clone();
            let safe_id = entry.replace(['/', '.', '#'], "-");
            let flow_concept = Concept {
                type_: "Compass".into(),
                title: Some(format!("Flow: {entry}")),
                description: Some(format!("Execution flow traced from `{entry}`")),
                resource: Some(entry.clone()),
                tags: vec![
                    "flow".into(),
                    entry.split('.').next_back().unwrap_or(&entry).to_string(),
                ],
                timestamp: Some(iso_utc_now()),
                generated_by: None,
                status: None,
                stale_after: None,
                concept_id: format!("compass/flow-{safe_id}"),
                body: warning_marker(),
                extensions: Vec::new(),
            };
            let mut flow_concept = flow_concept;
            bundle.write_concept(&mut flow_concept)?;
            promoted = true;
        }
        if wiki_page {
            let repo = task.fact_str("repo");
            let raw_page_id = match task.resource.split_once('/') {
                Some((_, after)) => after.to_string(),
                None => task.resource.clone(),
            };
            let page_id = raw_page_id.replace(['.', '#'], "-");
            let commit_sha = repo_head(Some(conn), workspace, &repo);
            let mut wiki_concept = if task.task_kind.starts_with("wiki-page-enrich") {
                enriched_article(
                    bundle,
                    task,
                    task_id,
                    &repo,
                    &page_id,
                    result,
                    wiki_sources.as_deref(),
                    commit_sha.as_deref(),
                )?
            } else {
                let mut extensions = vec![
                    ("page_id".into(), ys(&page_id)),
                    ("repo".into(), ys(&repo)),
                    (
                        "input_hash".into(),
                        task.fact("input_hash").cloned().unwrap_or(Yaml::Null),
                    ),
                    ("task_id".into(), ys(task_id)),
                ];
                if let Some(sha) = commit_sha.as_deref() {
                    extensions.push(("commit_sha".into(), ys(sha)));
                }
                if let Some(sources) = wiki_sources.clone() {
                    extensions.push(("sources".into(), Yaml::Array(sources)));
                }
                Concept {
                    type_: "Wiki-Article".into(),
                    title: Some(format!("Wiki: {page_id}")),
                    description: Some(format!("Wiki article for {repo}/{page_id}")),
                    resource: Some(page_id.clone()),
                    tags: vec![repo.clone(), "wiki".into()],
                    timestamp: Some(iso_utc_now()),
                    generated_by: None,
                    status: None,
                    stale_after: None,
                    concept_id: format!("wiki/pages/{repo}/{page_id}"),
                    body: warning_marker(),
                    extensions,
                }
            };
            bundle.write_concept(&mut wiki_concept)?;
            promoted = true;
        }
        if task.task_kind == "doc-link" {
            apply_doc_link_edges(bundle, conn, doc_link_edges)?;
            promoted = true;
        }
        if !finish_terminal(bundle, task, "done")? {
            return Ok(CompleteOutcome::refused(task_id, vec![RACE_LOST.into()]));
        }
    }
    Ok(CompleteOutcome {
        task_id: task_id.to_string(),
        promoted,
        revised: false,
        dropped: false,
        refused: false,
        errors: critic_result.errors,
        quality: critic_result.quality_score,
    })
}

fn run_critic(conn: &Connection, task: &Task, result: &str) -> Result<CriticResult, ToolError> {
    let concept = Concept {
        type_: "Task-Result".into(),
        title: Some(format!("Result for {}", task.id)),
        description: None,
        resource: Some(task.resource.clone()),
        tags: Vec::new(),
        timestamp: Some(iso_utc_now()),
        generated_by: None,
        status: None,
        stale_after: None,
        concept_id: format!("{TASK_DIR}/{}.result", task.id),
        body: result.to_string(),
        extensions: Vec::new(),
    };
    let exempt = matches!(
        task.task_kind.as_str(),
        "wiki-catalog" | "doc-link" | "skill-polish"
    );
    if exempt {
        return Ok(CriticResult {
            errors: Vec::new(),
            warnings: Vec::new(),
            quality_score: 1.0,
            passed: true,
        });
    }
    if task.task_kind.starts_with("wiki-page") {
        return critic_concept_opts(&concept, conn, Some(&["## Sources"]), true);
    }
    critic_concept_opts(&concept, conn, None, true)
}

#[derive(Clone, Debug)]
pub struct DropOutcome {
    pub task_id: String,
    pub dropped: bool,
    pub errors: Vec<String>,
}

/// Mark a pending or in-progress task dropped (terminal, never re-claimable).
pub fn drop_task(bundle: &Bundle, task_id: &str) -> Result<DropOutcome, ToolError> {
    let _lock = bundle.lock()?;
    let Some(mut task) = read(bundle, task_id)? else {
        return Ok(DropOutcome {
            task_id: task_id.to_string(),
            dropped: false,
            errors: vec!["task not found".into()],
        });
    };
    if task.status != "pending" && task.status != "in-progress" {
        return Ok(DropOutcome {
            task_id: task_id.to_string(),
            dropped: false,
            errors: vec![format!(
                "task is {}; only pending or in-progress tasks can be dropped",
                task.status
            )],
        });
    }
    task.status = "dropped".into();
    let mut concept = task_to_concept(&task);
    bundle.write_concept(&mut concept)?;
    if let Some(marker) = claim_marker_path(bundle, task_id) {
        let _ = std::fs::remove_file(&marker);
    }
    Ok(DropOutcome {
        task_id: task_id.to_string(),
        dropped: true,
        errors: Vec::new(),
    })
}

// --- output specs (llm/tasks.py `_output_spec` port) ---------------------------

fn output_spec(task_kind: &str, facts: &Hash) -> String {
    let specs: &[(&str, &str)] = &[
        (
            "compass-synthesize",
            "Write a 25-35 line compass file with exactly these 5 sections:\n# What Does This Module Do? / # Common Modification Patterns / # Build-Failure Patterns / # Cross-Module Dependencies / # Tribal Knowledge\nWhen facts.subsystems is non-empty, also emit `# Subsystems` right after the first section: one line per community (label, size, hub symbol).\nOnly reference files/symbols listed in the facts. Use backticks for code.",
        ),
        (
            "compass-revise",
            "The previous draft had factual errors (listed in facts.errors). Rewrite the compass file fixing ONLY those errors; keep correct content. Same 5-section format. Only reference files/symbols from facts.key_files.",
        ),
        (
            "flow-synthesize",
            "Write a business flow compass file with exactly these 5 sections:\n# What Does This Flow Do? / # Call Sequence / # Failure-Prone Steps / # Modules Spanned / # Tribal Knowledge\nUse the traced call chain in facts.chain as the call sequence. Only reference files/symbols listed in the facts (chain_raw file paths). Use backticks for code. Note branch points and terminal calls from facts.",
        ),
        (
            "flow-revise",
            "The previous flow draft had factual errors (listed in facts.errors). Rewrite fixing ONLY those errors; keep correct content. Same 5-section flow format. Only reference files/symbols from facts.chain_raw.",
        ),
        (
            "wiki",
            "Write an architectural wiki article in markdown. Only reference graph-verified symbols.",
        ),
        (
            "wiki-page",
            "Write an architectural wiki article in markdown for the page described in the facts. Only reference files/symbols from the facts (the seeds); never reference anything outside the graph. Use backticks for code. End the article with a `## Sources` footer listing the files you cited.",
        ),
        (
            "wiki-page-revise",
            "The previous wiki draft had factual errors (listed in facts.errors). Rewrite the article fixing ONLY those errors; keep correct content. Only reference files/symbols from the facts seeds; never reference anything outside the graph. End with a `## Sources` footer.",
        ),
        (
            "wiki-catalog",
            "Refine the deterministic wiki outline provided in the facts: you may reorder, retitle, or merge entries, but every entry must name a module that exists in the graph. Output the refined outline as JSON with the same fields as the input.",
        ),
        (
            "wiki-catalog-revise",
            "The previous catalog refinement had factual errors (listed in facts.errors). Fix ONLY those errors; keep valid entries. Every entry must still name a module that exists in the graph. Output the corrected JSON outline.",
        ),
        (
            "memory-critic",
            "For each draft memory in facts, judge accuracy/usefulness/specificity/non-redundancy. Output one JSON line per memory: {title, keep: bool, score: 0-1, reason}.",
        ),
        (
            "memory-extract",
            "From the session transcript in facts.transcript, extract candidate memories. Output one JSON line per candidate: {type: decision|pattern|mistake|workaround, title, body, confidence}.",
        ),
        ("doc-link", DOC_LINK_OUTPUT_SPEC),
        (
            "doc-link-revise",
            "The previous doc-link proposal was rejected (facts.errors name the invalid references). Rewrite the proposal fixing ONLY those lines; keep valid edges. Same one-edge-per-line format.",
        ),
    ];
    let mut spec = specs
        .iter()
        .find(|(kind, _)| *kind == task_kind)
        .map(|(_, s)| (*s).to_string());
    if spec.is_none() {
        spec = Some(if task_kind.starts_with("wiki-page") {
            specs
                .iter()
                .find(|(kind, _)| *kind == "wiki-page")
                .map(|(_, s)| (*s).to_string())
                .unwrap_or_default()
        } else {
            "Process per the cairn skill.".into()
        });
    }
    let mut spec = spec.unwrap_or_default();
    if task_kind.starts_with("wiki-page")
        && facts
            .get(&ys("diagrams"))
            .is_some_and(|v| !matches!(v, Yaml::Null | Yaml::Boolean(false)))
    {
        spec.push_str("\nInclude Mermaid fenced code blocks for the key structures you describe.");
    }
    spec
}

// --- doc-link results (knowledge/doc_link.py port) ----------------------------

/// Parse one edge per result line; returns (edges, errors).
pub fn parse_doc_link_result(result: &str) -> (Vec<(String, String, String)>, Vec<String>) {
    let mut edges = Vec::new();
    let mut errors = Vec::new();
    for raw in result.lines() {
        let mut line = raw.trim().to_string();
        if line.is_empty() || line.starts_with('#') || line.starts_with("```") {
            continue;
        }
        if let Some(stripped) = line.strip_prefix("- ").or_else(|| line.strip_prefix("* ")) {
            line = stripped.trim().to_string();
        }
        let parts: Vec<String> = line
            .split_whitespace()
            .map(|p| p.trim_matches('`').to_string())
            .collect();
        if parts.len() != 3 {
            errors.push(format!(
                "malformed edge line: '{}' (expected: {DOC_LINK_LINE_SPEC})",
                raw.trim()
            ));
            continue;
        }
        let (src, relation, dst) = (parts[0].clone(), parts[1].clone(), parts[2].clone());
        if !matches!(
            relation.as_str(),
            "relates-to" | "supersedes" | "superseded-by" | "references"
        ) {
            errors.push(format!(
                "invalid relation '{relation}' in line '{}' (expected one of: {RELATION_CHOICES})",
                raw.trim()
            ));
            continue;
        }
        edges.push((src, relation, dst));
    }
    (edges, errors)
}

/// Errors for unknown, self, or optional non-member endpoints.
pub fn validate_doc_link_edges(
    bundle: &Bundle,
    proposed: &[(String, String, String)],
    members: Option<&[String]>,
) -> Vec<String> {
    let known: HashSet<String> = tools_km::knowledge_doc_ids(bundle).into_iter().collect();
    let member_ids: Option<HashSet<String>> =
        members.map(|ms| ms.iter().map(|m| normalize_doc_id(bundle, m)).collect());
    let mut errors = Vec::new();
    for (src, relation, dst) in proposed {
        let norm_src = normalize_doc_id(bundle, src);
        let norm_dst = normalize_doc_id(bundle, dst);
        if !known.contains(&norm_src) {
            errors.push(format!("unknown concept_id: '{src}' (edge source)"));
        }
        if !known.contains(&norm_dst) {
            errors.push(format!("unknown concept_id: '{dst}' (edge target)"));
        }
        if let Some(member_ids) = &member_ids {
            for (raw, norm) in [(src, &norm_src), (dst, &norm_dst)] {
                if !member_ids.contains(norm) {
                    errors.push(format!("edge endpoint outside task members: '{raw}'"));
                }
            }
        }
        if norm_src == norm_dst {
            errors.push(format!("self-referential edge: '{src} {relation} {dst}'"));
        }
    }
    errors
}

/// Add one inferred relates_to entry unless already recorded.
fn append_inferred(
    bundle: &Bundle,
    doc_id: &str,
    other_id: &str,
    relation: &str,
) -> Result<(), ToolError> {
    let mut concept = bundle.read_concept(doc_id)?;
    let mut entries = normalize_relationships(concept.ext("relates_to"));
    if entries.iter().any(|entry| {
        hash_str(entry, "concept_id") == Some(other_id)
            && hash_str(entry, "relation") == Some(relation)
    }) {
        return Ok(());
    }
    let mut entry = Hash::new();
    entry.insert(ys("concept_id"), ys(other_id));
    entry.insert(ys("relation"), ys(relation));
    entry.insert(ys("kind"), ys("inferred"));
    entries.push(Yaml::Hash(entry));
    set_ext(&mut concept.extensions, "relates_to", Yaml::Array(entries));
    bundle.write_concept(&mut concept)
}

/// Apply inferred edges to both docs and rebuild the knowledge index.
pub fn apply_doc_link_edges(
    bundle: &Bundle,
    conn: &Connection,
    proposed: &[(String, String, String)],
) -> Result<(), ToolError> {
    for (src_raw, relation, dst_raw) in proposed {
        let src = normalize_doc_id(bundle, src_raw);
        let dst = normalize_doc_id(bundle, dst_raw);
        append_inferred(bundle, &src, &dst, relation)?;
        append_inferred(bundle, &dst, &src, mirror_relation(relation))?;
    }
    rebuild_knowledge_index(conn, bundle)?;
    Ok(())
}

fn hash_str<'a>(entry: &'a Yaml, key: &str) -> Option<&'a str> {
    match entry {
        Yaml::Hash(h) => match h.get(&ys(key)) {
            Some(Yaml::String(s)) => Some(s),
            _ => None,
        },
        _ => None,
    }
}

/// Normalized relationship entries, deduped first-wins (relationships.py).
fn normalize_relationships(entries: Option<&Yaml>) -> Vec<Yaml> {
    let items: Vec<Yaml> = match entries {
        Some(Yaml::Array(items)) => items.clone(),
        Some(Yaml::Hash(_)) | None => Vec::new(),
        Some(single) => vec![single.clone()],
    };
    let mut out: Vec<Yaml> = Vec::new();
    for item in items {
        normalize_entry(&item, &mut out);
    }
    let mut seen: HashSet<(String, String, String)> = HashSet::new();
    out.into_iter()
        .filter(|entry| {
            let key = (
                hash_str(entry, "concept_id")
                    .unwrap_or_default()
                    .to_string(),
                hash_str(entry, "relation").unwrap_or_default().to_string(),
                hash_str(entry, "kind").unwrap_or_default().to_string(),
            );
            seen.insert(key)
        })
        .collect()
}

fn normalize_entry(item: &Yaml, out: &mut Vec<Yaml>) {
    // a flow-string value ("['a','b']") is recovered by parsing it, leaving
    // bare concept ids intact (relationships.py _unwrap_flow_string)
    let unwrapped;
    let item = match item {
        Yaml::String(s) if s.trim_start().starts_with(['[', '{', '"', '\'']) => {
            match yaml_rust2::YamlLoader::load_from_str(s) {
                Ok(docs) => {
                    unwrapped = docs.into_iter().next().unwrap_or(Yaml::Null);
                    &unwrapped
                }
                Err(_) => item,
            }
        }
        _ => item,
    };
    match item {
        Yaml::Hash(h) => {
            let Some(concept_id) = h.get(&ys("concept_id")) else {
                return;
            };
            if ystr(Some(concept_id)).is_empty() {
                return;
            }
            let mut entry = h.clone();
            if !entry.contains_key(&ys("relation")) {
                entry.insert(ys("relation"), ys("relates-to"));
            }
            let default_kind = match entry.get(&ys("kind")) {
                None => true,
                Some(Yaml::String(s)) => s.is_empty(),
                Some(Yaml::Null) => true,
                Some(Yaml::Boolean(false)) => true,
                _ => false,
            };
            if default_kind {
                entry.insert(ys("kind"), ys("extracted"));
            }
            out.push(Yaml::Hash(entry));
        }
        Yaml::Array(items) => {
            for sub in items {
                normalize_entry(sub, out);
            }
        }
        Yaml::Null => {}
        other => {
            let concept_id = ystr(Some(other)).trim().to_string();
            if concept_id.is_empty() {
                return;
            }
            let mut entry = Hash::new();
            entry.insert(ys("concept_id"), ys(concept_id));
            entry.insert(ys("relation"), ys("relates-to"));
            entry.insert(ys("kind"), ys("extracted"));
            out.push(Yaml::Hash(entry));
        }
    }
}

// --- knowledge index rebuild (knowledge/index.py port) -------------------------

struct DocRec {
    doc_id: String,
    concept: Concept,
}

fn load_docs(bundle: &Bundle) -> Vec<DocRec> {
    bundle
        .list_concepts(Some("knowledge/"))
        .into_iter()
        .filter_map(|cid| {
            bundle.read_concept(&cid).ok().map(|concept| DocRec {
                doc_id: cid,
                concept,
            })
        })
        .collect()
}

struct EdgeRow {
    doc_id: String,
    related_id: String,
    relation: String,
    kind: String,
    provenance: String,
}

/// Rebuild knowledge_edges / knowledge_doc_refs from the bundle's frontmatter.
pub fn rebuild_knowledge_index(conn: &Connection, bundle: &Bundle) -> Result<(), ToolError> {
    let docs = load_docs(bundle);
    let doc_ids: HashSet<&str> = docs.iter().map(|d| d.doc_id.as_str()).collect();
    let resources = resource_index(&docs);
    let mut edges: Vec<EdgeRow> = Vec::new();
    for doc in &docs {
        for entry in normalize_relationships(doc.concept.ext("relates_to")) {
            let pointer = hash_str(&entry, "concept_id").unwrap_or_default().trim();
            let related = resolve_pointer(bundle, pointer, &doc_ids, &resources, &doc.doc_id);
            let Some(related) = related else {
                continue;
            };
            if related == doc.doc_id {
                continue;
            }
            edges.push(EdgeRow {
                doc_id: doc.doc_id.clone(),
                related_id: related.to_string(),
                relation: hash_str(&entry, "relation")
                    .unwrap_or("relates-to")
                    .to_string(),
                kind: hash_str(&entry, "kind").unwrap_or("extracted").to_string(),
                provenance: "frontmatter:relates_to".into(),
            });
        }
    }
    // overlap edges not already declared in either direction
    let declared: HashSet<(String, String)> = edges
        .iter()
        .map(|e| (e.doc_id.clone(), e.related_id.clone()))
        .collect();
    let tags_of = |c: &Concept| -> HashSet<String> { c.tags.iter().cloned().collect() };
    let mods_of = |c: &Concept| -> HashSet<String> {
        match c.ext("affects_modules") {
            Some(Yaml::Array(items)) => items
                .iter()
                .filter_map(|i| match i {
                    Yaml::String(s) => Some(s.clone()),
                    _ => None,
                })
                .collect(),
            _ => HashSet::new(),
        }
    };
    for i in 0..docs.len() {
        for j in (i + 1)..docs.len() {
            let (a, b) = (&docs[i], &docs[j]);
            if declared.contains(&(a.doc_id.clone(), b.doc_id.clone()))
                || declared.contains(&(b.doc_id.clone(), a.doc_id.clone()))
            {
                continue;
            }
            let tag_hit = tags_of(&a.concept)
                .intersection(&tags_of(&b.concept))
                .next()
                .is_some();
            let mod_hit = mods_of(&a.concept)
                .intersection(&mods_of(&b.concept))
                .next()
                .is_some();
            if !tag_hit && !mod_hit {
                continue;
            }
            let provenance = match (tag_hit, mod_hit) {
                (true, true) => "tag+module-overlap",
                (true, false) => "tag-overlap",
                _ => "module-overlap",
            };
            for (src, dst) in [(&a.doc_id, &b.doc_id), (&b.doc_id, &a.doc_id)] {
                edges.push(EdgeRow {
                    doc_id: src.clone(),
                    related_id: dst.clone(),
                    relation: "relates-to".into(),
                    kind: "derived".into(),
                    provenance: provenance.into(),
                });
            }
        }
    }
    write_edges(conn, &edges)?;
    write_refs(conn, &docs)?;
    Ok(())
}

fn resource_index(docs: &[DocRec]) -> HashMap<String, String> {
    let mut index = HashMap::new();
    for doc in docs {
        if let Some(resource) = &doc.concept.resource {
            let trimmed = resource.trim();
            if trimmed.is_empty() {
                continue;
            }
            index
                .entry(trimmed.trim_start_matches('/').to_string())
                .or_insert_with(|| doc.doc_id.clone());
        }
    }
    index
}

fn resolve_pointer<'a>(
    bundle: &Bundle,
    pointer: &str,
    doc_ids: &HashSet<&'a str>,
    resources: &HashMap<String, String>,
    self_id: &str,
) -> Option<&'a str> {
    let mut related = normalize_doc_id(bundle, pointer);
    if !doc_ids.contains(related.as_str()) || related == self_id {
        let bare = pointer.trim_start_matches('/');
        let mut candidate = resources.get(bare).cloned();
        if candidate.is_none() {
            let mut matches: Vec<&String> = resources
                .keys()
                .filter(|r| r.ends_with(&format!("/{bare}")))
                .collect();
            matches.sort_by_key(|r| (r.len(), (*r).to_string()));
            // a unique basename match extends the pointer; two or more is a guess
            if matches.len() == 1 {
                candidate = Some(resources[matches[0]].clone());
            }
        }
        if let Some(found) = candidate {
            related = found;
        }
    }
    doc_ids.get(related.as_str()).copied()
}

fn write_edges(conn: &Connection, edges: &[EdgeRow]) -> Result<(), ToolError> {
    let mut existing: HashMap<(String, String, String, String), f64> = HashMap::new();
    {
        let mut stmt = conn
            .prepare("SELECT doc_id, related_id, relation, kind, created_at FROM knowledge_edges")
            .map_err(|e| ToolError(e.to_string()))?;
        let rows = stmt
            .query_map([], |r| {
                Ok((
                    r.get::<_, String>(0)?,
                    r.get::<_, String>(1)?,
                    r.get::<_, String>(2)?,
                    r.get::<_, String>(3)?,
                    r.get::<_, Option<f64>>(4)?.unwrap_or(0.0),
                ))
            })
            .map_err(|e| ToolError(e.to_string()))?;
        for row in rows {
            let (doc_id, related_id, relation, kind, created_at) =
                row.map_err(|e| ToolError(e.to_string()))?;
            existing.insert((doc_id, related_id, relation, kind), created_at);
        }
    }
    conn.execute("DELETE FROM knowledge_edges", [])
        .map_err(|e| ToolError(e.to_string()))?;
    let now = now_secs();
    let mut seen: HashSet<(String, String, String, String)> = HashSet::new();
    let mut rows: Vec<(String, String, String, String, String, f64)> = Vec::new();
    for e in edges {
        let key = (
            e.doc_id.clone(),
            e.related_id.clone(),
            e.relation.clone(),
            e.kind.clone(),
        );
        if !seen.insert(key.clone()) {
            continue;
        }
        let created_at = existing.get(&key).copied().unwrap_or(now);
        rows.push((
            e.doc_id.clone(),
            e.related_id.clone(),
            e.relation.clone(),
            e.kind.clone(),
            e.provenance.clone(),
            created_at,
        ));
    }
    for row in &rows {
        conn.execute(
            "INSERT INTO knowledge_edges (doc_id, related_id, relation, kind, provenance, created_at) \
             VALUES (?, ?, ?, ?, ?, ?)",
            rusqlite::params![row.0, row.1, row.2, row.3, row.4, row.5],
        )
        .map_err(|e| ToolError(e.to_string()))?;
    }
    Ok(())
}

fn write_refs(conn: &Connection, docs: &[DocRec]) -> Result<(), ToolError> {
    let mut verified_by_key: HashMap<(String, String, String), i64> = HashMap::new();
    for doc in docs {
        for family in ["verified", "sources"] {
            let Some(Yaml::Array(items)) = doc.concept.ext(family) else {
                continue;
            };
            for item in items {
                let Yaml::Hash(entry) = item else {
                    continue;
                };
                let Some(Yaml::String(ref_)) = entry.get(&ys("ref")) else {
                    continue;
                };
                let ref_ = ref_.trim();
                if ref_.is_empty() {
                    continue;
                }
                let kind = match entry.get(&ys("kind")) {
                    Some(Yaml::String(s)) => s.clone(),
                    _ => "file".into(),
                };
                let verified = matches!(entry.get(&ys("verified")), Some(Yaml::Boolean(true)));
                let key = (doc.doc_id.clone(), ref_.to_string(), kind);
                let flag = if verified { 1 } else { 0 };
                let entry = verified_by_key.entry(key).or_insert(0);
                *entry = (*entry).max(flag);
            }
        }
    }
    conn.execute("DELETE FROM knowledge_doc_refs", [])
        .map_err(|e| ToolError(e.to_string()))?;
    let mut unique: Vec<(&(String, String, String), &i64)> = verified_by_key.iter().collect();
    unique.sort_by(|a, b| a.0.cmp(b.0));
    for ((doc_id, ref_, kind), verified) in unique {
        conn.execute(
            "INSERT INTO knowledge_doc_refs (doc_id, ref, ref_kind, verified) VALUES (?, ?, ?, ?)",
            rusqlite::params![doc_id, ref_, kind, verified],
        )
        .map_err(|e| ToolError(e.to_string()))?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch_bundle(tag: &str) -> Bundle {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let dir =
            std::env::temp_dir().join(format!("cairn-tasks-{tag}-{}-{nanos}", std::process::id()));
        std::fs::create_dir_all(&dir).expect("bundle dir");
        Bundle::new(dir)
    }

    fn seeded_conn() -> Connection {
        let conn = Connection::open_in_memory().expect("mem db");
        conn.execute_batch(
            "CREATE TABLE repos (id INTEGER PRIMARY KEY, name TEXT, path TEXT);
             CREATE TABLE files (id INTEGER PRIMARY KEY, repo_id INTEGER, path TEXT, language TEXT);
             CREATE TABLE symbols (id INTEGER PRIMARY KEY, file_id INTEGER, name TEXT, kind TEXT, qualified_name TEXT, line_start INTEGER, line_end INTEGER);
             CREATE TABLE knowledge_edges (doc_id TEXT NOT NULL, related_id TEXT NOT NULL, relation TEXT NOT NULL, kind TEXT NOT NULL, provenance TEXT, created_at REAL, PRIMARY KEY (doc_id, related_id, relation, kind));
             CREATE TABLE knowledge_doc_refs (doc_id TEXT NOT NULL, ref TEXT NOT NULL, ref_kind TEXT NOT NULL, verified INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (doc_id, ref, ref_kind));
             INSERT INTO repos (id, name, path) VALUES (1, 'r1', '/tmp/r1');
             INSERT INTO files (id, repo_id, path, language) VALUES (1, 1, '/tmp/r1/src/graph/queries.py', 'python');
             INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) VALUES (1, 1, 'queries', 'module', 'queries', 1, 100);",
        )
        .expect("schema");
        conn
    }

    fn facts(pairs: &[(&str, Yaml)]) -> Hash {
        let mut h = Hash::new();
        for (k, v) in pairs {
            h.insert(ys(*k), v.clone());
        }
        h
    }

    #[test]
    fn claim_sets_in_progress_and_marker_roundtrip() {
        let bundle = scratch_bundle("claim");
        let task = create_task(
            &bundle,
            "wiki-page",
            "cairn/overview",
            facts(&[("repo", ys("cairn"))]),
            0,
        )
        .expect("create");
        assert_eq!(task.status, "pending");
        let claimed = claim_task(&bundle, &task.id, "agent")
            .expect("claim")
            .expect("claimed");
        assert_eq!(claimed.status, "in-progress");
        assert_eq!(claimed.assigned_to, "agent");
        let reread = get_task(&bundle, &task.id).expect("read").expect("found");
        assert_eq!(reread.status, "in-progress");
        assert!(!reread.claimed_at.is_empty());
        // re-claim of an in-progress task is refused while the claim is fresh
        assert!(
            claim_task(&bundle, &task.id, "other")
                .expect("claim")
                .is_none()
        );
    }

    #[test]
    fn escaping_task_id_is_not_found_and_writes_nowhere() {
        let bundle = scratch_bundle("escape");
        assert!(get_task(&bundle, "../outside").expect("read").is_none());
        assert!(
            claim_task(&bundle, "../../outside", "")
                .expect("claim")
                .is_none()
        );
        let outcome = drop_task(&bundle, "../outside").expect("drop");
        assert!(!outcome.dropped);
        assert_eq!(outcome.errors, vec!["task not found"]);
    }

    #[test]
    fn list_filters_by_status_and_kind_prefix() {
        let bundle = scratch_bundle("list");
        create_task(
            &bundle,
            "wiki-page",
            "cairn/a",
            facts(&[("repo", ys("cairn"))]),
            0,
        )
        .expect("t1");
        let t2 = create_task(&bundle, "wiki-catalog", "cairn", Hash::new(), 0).expect("t2");
        {
            let mut done = task_to_concept(&Task {
                status: "done".into(),
                ..concept_to_task(&bundle.read_concept(&t2.concept_id()).expect("concept"))
            });
            bundle.write_concept(&mut done).expect("write");
        }
        let pending = list_tasks(&bundle, Some("pending"), None, None).expect("list");
        assert_eq!(pending.len(), 1);
        assert_eq!(pending[0].task_kind, "wiki-page");
        let wiki = list_tasks(&bundle, None, None, Some("wiki-")).expect("list");
        assert_eq!(wiki.len(), 2);
        let catalog = list_tasks(&bundle, None, Some("wiki-catalog"), None).expect("list");
        assert_eq!(catalog.len(), 1);
    }

    #[test]
    fn complete_refuses_pending_and_ownership_mismatch() {
        let bundle = scratch_bundle("refuse");
        let conn = seeded_conn();
        let task = create_task(
            &bundle,
            "wiki-page",
            "cairn/a",
            facts(&[("repo", ys("cairn"))]),
            0,
        )
        .expect("create");
        let outcome =
            complete_task(&bundle, &task.id, "body", Some(&conn), None, None).expect("complete");
        assert!(outcome.refused);
        assert_eq!(outcome.errors, vec!["task not in-progress or not found"]);

        claim_task(&bundle, &task.id, "alice")
            .expect("claim")
            .expect("ok");
        let outcome = complete_task(&bundle, &task.id, "body", Some(&conn), Some("bob"), None)
            .expect("complete");
        assert!(outcome.refused);
        assert!(outcome.errors[0].starts_with("ownership mismatch: task owned by 'alice'"));
        let outcome = complete_task(&bundle, &task.id, "body", Some(&conn), Some("alice"), None)
            .expect("complete");
        assert!(!outcome.refused);
    }

    #[test]
    fn complete_compass_pass_promotes_and_fail_spawns_revise() {
        let bundle = scratch_bundle("critic");
        let conn = seeded_conn();
        let task = create_task(
            &bundle,
            "compass-synthesize",
            "src/graph",
            facts(&[("key_files", Yaml::Array(vec![ys("src/graph/queries.py")]))]),
            0,
        )
        .expect("create");
        claim_task(&bundle, &task.id, "")
            .expect("claim")
            .expect("ok");
        let passing = "# What Does This Module Do?\nSee `src/graph/queries.py`.\n\
             # Common Modification Patterns\n...\n# Build-Failure Patterns\n...\n\
             # Cross-Module Dependencies\n...\n# Tribal Knowledge\n...\n";
        let outcome =
            complete_task(&bundle, &task.id, passing, Some(&conn), None, None).expect("c1");
        assert!(
            outcome.promoted,
            "expected promotion, errors: {:?}",
            outcome.errors
        );
        assert!(
            bundle
                .read_concept("compass/src-graph")
                .expect("promoted")
                .body
                .contains(passing.trim())
        );

        let t2 = create_task(
            &bundle,
            "compass-synthesize",
            "src/other",
            facts(&[("key_files", Yaml::Array(vec![ys("src/graph/queries.py")]))]),
            0,
        )
        .expect("create2");
        claim_task(&bundle, &t2.id, "")
            .expect("claim2")
            .expect("ok");
        let outcome = complete_task(
            &bundle,
            &t2.id,
            "See `no/such/file.py`.",
            Some(&conn),
            None,
            None,
        )
        .expect("c2");
        assert!(outcome.revised);
        assert!(outcome.errors.iter().any(|e| e.contains("no/such/file.py")));
        let revise =
            list_tasks(&bundle, Some("pending"), None, Some("compass-revise")).expect("revise");
        assert_eq!(revise.len(), 1);
        assert_eq!(
            concept_to_task(&bundle.read_concept(&revise[0].concept_id()).expect("r"))
                .fact_str("parent_task_id"),
            t2.id
        );
    }

    #[test]
    fn complete_checks_notes_section_of_results() {
        let bundle = scratch_bundle("notes");
        let conn = seeded_conn();
        let task = create_task(
            &bundle,
            "compass-synthesize",
            "src/graph",
            facts(&[("key_files", Yaml::Array(vec![ys("src/graph/queries.py")]))]),
            0,
        )
        .expect("create");
        claim_task(&bundle, &task.id, "")
            .expect("claim")
            .expect("ok");
        let passing = "# What Does This Module Do?\nSee `src/graph/queries.py`.\n\
             # Common Modification Patterns\n...\n# Build-Failure Patterns\n...\n\
             # Cross-Module Dependencies\n...\n# Tribal Knowledge\n...\n";
        let result = format!("{passing}\n## Notes\n- see `no/such/file.py`\n");
        let outcome =
            complete_task(&bundle, &task.id, &result, Some(&conn), None, None).expect("c");
        assert!(outcome.revised, "Notes must be checked: {outcome:?}");
        assert!(!outcome.promoted);
    }

    #[test]
    fn exhausted_chain_persists_failed_status() {
        let bundle = scratch_bundle("exhaust");
        let conn = seeded_conn();
        let task = create_task(
            &bundle,
            "compass-synthesize",
            "src/x",
            Hash::new(),
            MAX_REVISE_CYCLES,
        )
        .expect("create");
        claim_task(&bundle, &task.id, "")
            .expect("claim")
            .expect("ok");
        let outcome = complete_task(
            &bundle,
            &task.id,
            "See `no/such/file.py`.",
            Some(&conn),
            None,
            None,
        )
        .expect("c");
        assert!(outcome.dropped);
        assert!(!outcome.revised);
        assert_eq!(
            get_task(&bundle, &task.id).expect("g").expect("t").status,
            "failed"
        );
    }

    #[test]
    fn drop_is_terminal_and_releases_the_marker() {
        let bundle = scratch_bundle("drop");
        let task = create_task(&bundle, "doc-link", "cairn", Hash::new(), 0).expect("create");
        claim_task(&bundle, &task.id, "")
            .expect("claim")
            .expect("ok");
        let outcome = drop_task(&bundle, &task.id).expect("drop");
        assert!(outcome.dropped);
        assert!(
            !bundle
                .root
                .join(format!("_tasks/{}.claim", task.id))
                .exists()
        );
        assert!(
            claim_task(&bundle, &task.id, "")
                .expect("reclaim")
                .is_none()
        );
        let again = drop_task(&bundle, &task.id).expect("drop2");
        assert!(!again.dropped);
        assert!(again.errors[0].starts_with("task is dropped"));
    }

    #[test]
    fn wiki_page_completion_promotes_article_and_resolves_sources() {
        let bundle = scratch_bundle("wiki");
        let conn = seeded_conn();
        let task = create_task(
            &bundle,
            "wiki-page",
            "cairn/graph-layer",
            facts(&[("repo", ys("r1"))]),
            0,
        )
        .expect("create");
        claim_task(&bundle, &task.id, "")
            .expect("claim")
            .expect("ok");
        let result = "The graph layer.\n\n## Sources\n- `src/graph/queries.py`\n";
        let outcome = complete_task(&bundle, &task.id, result, Some(&conn), None, None).expect("c");
        assert!(outcome.promoted, "errors: {:?}", outcome.errors);
        let page = bundle
            .read_concept("wiki/pages/r1/graph-layer")
            .expect("page");
        assert_eq!(page.type_, "Wiki-Article");
        assert!(page.body.contains("The graph layer."));
        let Yaml::Array(sources) = page.ext("sources").expect("sources") else {
            panic!("sources array");
        };
        assert_eq!(sources.len(), 1);

        // a dead footer entry refuses promotion into a revise task; the
        // footer error is deduped against the body's own citation of the
        // same dead path, so the body error is the one reported
        let t2 = create_task(
            &bundle,
            "wiki-page",
            "cairn/bad",
            facts(&[("repo", ys("r1"))]),
            0,
        )
        .expect("t2");
        claim_task(&bundle, &t2.id, "").expect("c2").expect("ok");
        let result = "Bad page.\n\n## Sources\n- `no/such/file.py`\n";
        let outcome = complete_task(&bundle, &t2.id, result, Some(&conn), None, None).expect("c2");
        assert!(!outcome.promoted);
        assert!(
            outcome
                .errors
                .iter()
                .any(|e| e == "Hallucinated/unresolved file path: no/such/file.py")
        );
    }

    #[test]
    fn doc_link_completion_gates_and_applies_edges() {
        let bundle = scratch_bundle("doclink");
        let conn = seeded_conn();
        // two member docs
        for id in ["knowledge/a", "knowledge/b"] {
            let mut concept = Concept {
                type_: "Knowledge-Doc".into(),
                title: Some(format!("doc {id}")),
                description: None,
                resource: None,
                tags: vec![],
                timestamp: Some(iso_utc_now()),
                generated_by: None,
                status: None,
                stale_after: None,
                concept_id: id.to_string(),
                body: format!("Body of {id}.\n"),
                extensions: vec![("doc_type".into(), ys("spec"))],
            };
            bundle.write_concept(&mut concept).expect("doc");
        }
        let members = Yaml::Array(vec![ys("knowledge/a"), ys("knowledge/b")]);
        let task = create_task(
            &bundle,
            "doc-link",
            "island",
            facts(&[("members", members)]),
            0,
        )
        .expect("t");
        claim_task(&bundle, &task.id, "")
            .expect("claim")
            .expect("ok");

        // invalid endpoints refuse with no writes
        let outcome = complete_task(
            &bundle,
            &task.id,
            "knowledge/a relates-to knowledge/missing",
            Some(&conn),
            None,
            None,
        )
        .expect("c1");
        assert!(!outcome.dropped);
        assert!(!outcome.refused);
        assert!(
            outcome
                .errors
                .iter()
                .any(|e| e.contains("unknown concept_id: 'knowledge/missing'"))
        );
        assert_eq!(
            get_task(&bundle, &task.id).expect("g").expect("t").status,
            "in-progress"
        );

        let outcome = complete_task(
            &bundle,
            &task.id,
            "knowledge/a relates-to knowledge/b",
            Some(&conn),
            None,
            None,
        )
        .expect("c2");
        assert!(outcome.promoted, "errors: {:?}", outcome.errors);
        let a = bundle.read_concept("knowledge/a").expect("a");
        let entries = normalize_relationships(a.ext("relates_to"));
        assert!(
            entries
                .iter()
                .any(|e| hash_str(e, "concept_id") == Some("knowledge/b")
                    && hash_str(e, "kind") == Some("inferred"))
        );
        let count: i64 = conn
            .query_row("SELECT COUNT(*) FROM knowledge_edges", [], |r| r.get(0))
            .expect("edges");
        assert!(count >= 2, "expected the applied edge in the index");
    }

    #[test]
    fn py_display_matches_python_str() {
        assert_eq!(py_display(&ys("plain")), "plain");
        assert_eq!(py_display(&Yaml::Integer(3)), "3");
        assert_eq!(py_display(&Yaml::Boolean(true)), "True");
        assert_eq!(py_display(&Yaml::Null), "None");
        assert_eq!(
            py_display(&Yaml::Array(vec![ys("src/graph/queries.py")])),
            "['src/graph/queries.py']"
        );
    }

    #[test]
    fn output_spec_table_and_fallbacks() {
        assert!(output_spec("compass-synthesize", &Hash::new()).starts_with("Write a 25-35 line"));
        assert_eq!(
            output_spec("unknown-kind", &Hash::new()),
            "Process per the cairn skill."
        );
        let mut f = Hash::new();
        f.insert(ys("diagrams"), Yaml::Boolean(true));
        assert!(
            output_spec("wiki-page-enrich", &f).ends_with(
                "Include Mermaid fenced code blocks for the key structures you describe."
            )
        );
        assert!(
            output_spec("doc-link", &Hash::new()).contains("<concept_id> <relation> <related_id>")
        );
    }

    #[test]
    fn task_body_renders_like_python() {
        let task = Task {
            id: "abc123".into(),
            task_kind: "wiki-page".into(),
            resource: "cairn/overview".into(),
            facts: facts(&[("repo", ys("cairn"))]),
            status: "pending".into(),
            attempt: 1,
            ..Default::default()
        };
        let body = task_to_concept(&task).body;
        let expected = "# Task: wiki-page\n\n\
**Resource:** `cairn/overview`  \n\
**Status:** pending  \n\
**Attempt:** 1/4\n\n\
## Facts (graph-grounded — do not invent beyond these)\n\
**repo:** cairn\n\n\
## Output spec\n\
Write an architectural wiki article in markdown for the page described in the facts. Only reference files/symbols from the facts (the seeds); never reference anything outside the graph. Use backticks for code. End the article with a `## Sources` footer listing the files you cited.\n\
\nWrite your result, then run: `cairn task complete abc123`\n";
        assert_eq!(body, expected);
    }

    #[test]
    fn mid_completion_failure_holds_the_task_re_completable() {
        let bundle = scratch_bundle("held");
        let conn = seeded_conn();
        let task = create_task(
            &bundle,
            "wiki-page-enrich",
            "cairn/ghost",
            facts(&[("repo", ys("r1"))]),
            0,
        )
        .expect("create");
        claim_task(&bundle, &task.id, "")
            .expect("claim")
            .expect("ok");
        let result = "More content.\n\n## Sources\n- `src/graph/queries.py`\n";
        let outcome = complete_task(&bundle, &task.id, result, Some(&conn), None, None)
            .expect("held outcome, not an error");
        assert_eq!(outcome.errors, vec!["critic execution failed"]);
        assert!(!outcome.promoted && !outcome.revised && !outcome.dropped && !outcome.refused);
        assert_eq!(
            get_task(&bundle, &task.id).expect("g").expect("t").status,
            "in-progress"
        );
    }

    #[test]
    fn future_dated_marker_mtime_reads_as_fresh() {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let dir = std::env::temp_dir().join(format!("cairn-stale-{}-{nanos}", std::process::id()));
        std::fs::create_dir_all(&dir).expect("dir");
        let marker = dir.join("t.claim");
        std::fs::write(&marker, "").expect("marker");
        // clock skew: mtime one day in the future must not read as stale
        let ok = std::process::Command::new("touch")
            .arg("-t")
            .arg("203001010000")
            .arg(&marker)
            .status()
            .map(|s| s.success())
            .unwrap_or(false);
        if !ok {
            std::fs::remove_file(&marker).ok();
            return;
        }
        assert!(!try_remove_stale_marker(&marker));
        assert!(marker.exists());
        std::fs::remove_file(&marker).ok();
    }

    #[test]
    fn flow_string_relationship_entries_expand() {
        let raw = ys("['knowledge/a','knowledge/b']");
        let entries = normalize_relationships(Some(&raw));
        let ids: Vec<&str> = entries
            .iter()
            .filter_map(|e| hash_str(e, "concept_id"))
            .collect();
        assert_eq!(ids, vec!["knowledge/a", "knowledge/b"]);
    }
}
