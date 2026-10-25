//! Diff-seeded reverse dependency radius (`graph/blast.py`).

use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use regex::Regex;
use rusqlite::Connection;
use serde_json::{Value, json};

use crate::graph::{self, TaintPath, TaintRegistryTables, intersect_seeds_registry};

// Large pre-submit diffs (whole-campaign PRs) legitimately exceed tens of
// seconds; the cap exists to fail hung git, not big history.
const GIT_TIMEOUT_S: u64 = 120;

#[derive(Clone, Debug)]
pub struct ParsedHunk {
    pub start: i64,
    pub end: i64,
    pub count: i64,
    pub header: String,
}

#[derive(Clone, Debug)]
pub struct ChangedFile {
    pub repo: String,
    pub path: Option<String>,
    pub old_path: Option<String>,
    pub deleted: bool,
    pub renamed: bool,
    pub hunks: Vec<ParsedHunk>,
    /// `path` falling back to `old_path`; never None for a kept entry.
    pub effective_path: String,
}

#[derive(Clone, Debug)]
pub struct SeedHunk {
    pub start: i64,
    pub end: i64,
}

#[derive(Clone, Debug)]
pub struct BlastSeed {
    pub id: String,
    pub name: String,
    pub qualified_name: Option<String>,
    pub kind: String,
    pub file_path: String,
    pub repo: String,
    pub line_start: i64,
    pub line_end: i64,
    pub hunks: Vec<SeedHunk>,
}

#[derive(Clone, Debug, Default)]
pub struct RadiusRow {
    pub symbol: String,
    pub file: String,
    pub repo: String,
    pub depth: i64,
    pub symbol_id: Option<String>,
    pub depends_on: Vec<String>,
    pub depends_on_ids: Vec<String>,
    pub resolution: String,
}

#[derive(Clone, Debug)]
pub struct BlastCycle {
    pub symbol: String,
    pub depth: i64,
}

pub struct BlastArea {
    pub name: String,
    pub seeds: Vec<BlastSeed>,
    pub radius: Vec<RadiusRow>,
}

pub struct BlastResult {
    pub basis_kind: String,
    pub basis_base: String,
    pub basis_merge_base: Option<String>,
    pub changed_files: Vec<ChangedFile>,
    pub seeds: Vec<BlastSeed>,
    pub areas: Vec<BlastArea>,
    pub radius: Vec<RadiusRow>,
    pub cycles: Vec<BlastCycle>,
    pub unindexed_files: Vec<String>,
    pub deleted_files: Vec<String>,
    pub truncated: bool,
    pub stale_graph: String,
    pub taint_paths: Vec<TaintPath>,
}

impl BlastResult {
    /// The `--format json` payload (`json.dumps(..., sort_keys=True)` shape).
    pub fn to_json(&self) -> Value {
        fn seed_json(s: &BlastSeed) -> Value {
            json!({
                "id": s.id,
                "name": s.name,
                "qualified_name": s.qualified_name,
                "kind": s.kind,
                "file_path": s.file_path,
                "repo": s.repo,
                "line_start": s.line_start,
                "line_end": s.line_end,
                "hunks": s.hunks.iter()
                    .map(|h| json!({"start": h.start, "end": h.end}))
                    .collect::<Vec<_>>(),
            })
        }
        fn radius_json(r: &RadiusRow) -> Value {
            json!({
                "symbol": r.symbol,
                "file": r.file,
                "repo": r.repo,
                "depth": r.depth,
                "symbol_id": r.symbol_id,
                "depends_on": r.depends_on,
                "depends_on_ids": r.depends_on_ids,
                "resolution": r.resolution,
            })
        }
        let mut basis = json!({"kind": self.basis_kind, "base": self.basis_base});
        if let Some(merge_base) = &self.basis_merge_base {
            basis["merge_base"] = json!(merge_base);
        }
        let mut cycles = serde_json::Map::new();
        for cycle in &self.cycles {
            cycles.insert(
                cycle.symbol.clone(),
                json!({"symbol": cycle.symbol, "depth": cycle.depth}),
            );
        }
        json!({
            "basis": basis,
            "changed_files": self.changed_files.iter().map(changed_file_json).collect::<Vec<_>>(),
            "seeds": self.seeds.iter().map(seed_json).collect::<Vec<_>>(),
            "areas": self.areas.iter().map(|a| json!({
                "name": a.name,
                "seeds": a.seeds.iter().map(seed_json).collect::<Vec<_>>(),
                "radius": a.radius.iter().map(radius_json).collect::<Vec<_>>(),
            })).collect::<Vec<_>>(),
            "radius": self.radius.iter().map(radius_json).collect::<Vec<_>>(),
            "cycles": Value::Object(cycles),
            "unindexed_files": self.unindexed_files,
            "deleted_files": self.deleted_files,
            "truncated": self.truncated,
            "stale_graph": self.stale_graph,
            "taint_paths": self.taint_paths.iter().map(|p| json!({
                "hops": p.hops.iter()
                    .map(|h| json!({"file": h.file, "symbol": h.symbol, "resolution": h.resolution}))
                    .collect::<Vec<_>>(),
            })).collect::<Vec<_>>(),
        })
    }
}

fn run_git(repo: &Path, args: &[&str]) -> Result<std::process::Output, String> {
    let mut child = Command::new("git")
        .arg("-c")
        .arg("core.quotePath=false")
        .args(args)
        .current_dir(repo)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("git {args:?}: {e}"))?;
    let deadline = Instant::now() + Duration::from_secs(GIT_TIMEOUT_S);
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                let mut stdout = String::new();
                let mut stderr = String::new();
                if let Some(mut out) = child.stdout.take() {
                    use std::io::Read as _;
                    let _ = out.read_to_string(&mut stdout);
                }
                if let Some(mut err) = child.stderr.take() {
                    use std::io::Read as _;
                    let _ = err.read_to_string(&mut stderr);
                }
                stdout = String::from_utf8_lossy(stdout.as_bytes()).into_owned();
                stderr = String::from_utf8_lossy(stderr.as_bytes()).into_owned();
                return Ok(std::process::Output {
                    status,
                    stdout: stdout.into_bytes(),
                    stderr: stderr.into_bytes(),
                });
            }
            Ok(None) => {
                if Instant::now() >= deadline {
                    let _ = child.kill();
                    return Err(format!("git {} timed out", args.first().unwrap_or(&"")));
                }
                std::thread::sleep(Duration::from_millis(25));
            }
            Err(e) => return Err(format!("git {args:?}: {e}")),
        }
    }
}

fn git_stdout(repo: &Path, args: &[&str], check: bool) -> Result<String, String> {
    let output = run_git(repo, args)?;
    if check && !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
        return Err(if stderr.is_empty() {
            "git command failed".to_string()
        } else {
            stderr
        });
    }
    Ok(String::from_utf8_lossy(&output.stdout).into_owned())
}

/// Decode git's C-style quoted path; octal escapes decode as UTF-8 bytes.
fn unquote_git_path(value: &str) -> String {
    if value.len() < 2 || !value.starts_with('"') || !value.ends_with('"') {
        return value.to_string();
    }
    let body = &value[1..value.len() - 1];
    let escape_re = Regex::new(r"\\(?:(?P<octal>[0-7]{3})|(?P<char>.))").unwrap();
    let mut raw: Vec<u8> = Vec::new();
    let mut pos = 0usize;
    for mat in escape_re.captures_iter(body) {
        let whole = mat.get(0).expect("group 0");
        raw.extend_from_slice(&body.as_bytes()[pos..whole.start()]);
        if let Some(octal) = mat.name("octal") {
            raw.push(u8::from_str_radix(octal.as_str(), 8).unwrap_or(0));
        } else if let Some(ch) = mat.name("char").and_then(|m| m.as_str().chars().next()) {
            let decoded = match ch {
                'n' => "\n".to_string(),
                't' => "\t".to_string(),
                'r' => "\r".to_string(),
                'a' => "\u{7}".to_string(),
                'b' => "\u{8}".to_string(),
                'f' => "\u{c}".to_string(),
                'v' => "\u{b}".to_string(),
                other => other.to_string(),
            };
            raw.extend_from_slice(decoded.as_bytes());
        }
        pos = whole.end();
    }
    raw.extend_from_slice(&body.as_bytes()[pos..]);
    String::from_utf8_lossy(&raw).into_owned()
}

fn diff_path(value: &str) -> Option<String> {
    if value == "/dev/null" {
        return None;
    }
    let value = unquote_git_path(value);
    let value = value
        .strip_prefix("a/")
        .or_else(|| value.strip_prefix("b/"))
        .unwrap_or(&value);
    Some(value.to_string())
}

fn parse_diff(text: &str) -> Vec<(Option<String>, Option<String>, Vec<ParsedHunk>)> {
    let hunk_re = Regex::new(r"^@@ -\d+(?:,\d+)? \+(?P<g1>\d+)(?:,(?P<g2>\d+))? @@").unwrap();
    let mut files: Vec<(Option<String>, Option<String>, Vec<ParsedHunk>)> = Vec::new();
    let mut current: Option<usize> = None;
    let mut in_hunk = false;
    for line in text.lines() {
        if line.starts_with("diff --git ") {
            files.push((None, None, Vec::new()));
            current = files.len().checked_sub(1);
            in_hunk = false;
        } else if current.is_none()
            || (in_hunk
                && matches!(
                    line.as_bytes().first(),
                    Some(b' ') | Some(b'+') | Some(b'-') | Some(b'\\')
                ))
        {
            continue;
        } else if let Some(rest) = line.strip_prefix("--- ") {
            if let Some(idx) = current {
                files[idx].0 = diff_path(rest);
            }
        } else if let Some(rest) = line.strip_prefix("+++ ") {
            if let Some(idx) = current {
                files[idx].1 = diff_path(rest);
            }
        } else if let Some(rest) = line.strip_prefix("rename from ") {
            if let Some(idx) = current {
                files[idx].0 = diff_path(&format!("b/{}", unquote_git_path(rest)));
            }
        } else if let Some(rest) = line.strip_prefix("rename to ") {
            if let Some(idx) = current {
                files[idx].1 = diff_path(&format!("b/{}", unquote_git_path(rest)));
            }
        } else if let Some(idx) = current
            && line.starts_with("@@ ")
            && let Some(captures) = hunk_re.captures(line)
        {
            let start: i64 = captures["g1"].parse().unwrap_or(1);
            let count: i64 = captures
                .name("g2")
                .and_then(|m| m.as_str().parse().ok())
                .unwrap_or(1);
            files[idx].2.push(ParsedHunk {
                start,
                end: start + (count - 1).max(0),
                count,
                header: line.to_string(),
            });
            in_hunk = true;
        }
    }
    files
}

fn repo_path(workspace: &Path, stored_path: &str) -> PathBuf {
    let path = Path::new(stored_path);
    if path.is_absolute() {
        path.to_path_buf()
    } else {
        workspace.join(path)
    }
}

fn resolve_base(repo: &Path, base: &str) -> Result<String, String> {
    let verify = run_git(
        repo,
        &[
            "rev-parse",
            "--verify",
            "--quiet",
            &format!("{base}^{{commit}}"),
        ],
    )?;
    if !verify.status.success() {
        return Err(format!(
            "Unknown base ref '{base}'. Fetch the ref and full history \
             (git fetch <remote> <ref>; in CI avoid shallow clones or use fetch-depth: 0)."
        ));
    }
    let merge_base = git_stdout(repo, &["merge-base", base, "HEAD"], false)
        .map(|s| s.trim().to_string())
        .unwrap_or_default();
    if merge_base.is_empty() {
        return Err(format!(
            "Base ref '{base}' has no common ancestor with HEAD."
        ));
    }
    Ok(merge_base)
}

fn changed_entry(
    repo_id: &str,
    parsed: &(Option<String>, Option<String>, Vec<ParsedHunk>),
) -> Option<ChangedFile> {
    // parse_diff fills .0 from `--- `/`rename from` (old) and .1 from
    // `+++ `/`rename to` (new).
    let (old_path, path, hunks) = parsed;
    let effective_path = path.clone().or_else(|| old_path.clone())?;
    Some(ChangedFile {
        repo: repo_id.to_string(),
        path: path.clone(),
        old_path: old_path.clone(),
        deleted: path.is_none(),
        renamed: old_path.is_some() && path.is_some() && old_path != path,
        hunks: hunks.clone(),
        effective_path,
    })
}

fn changed_files(
    conn: &Connection,
    workspace: &Path,
    base: Option<&str>,
) -> Result<(String, String, Option<String>, Vec<ChangedFile>), String> {
    let mut repos: Vec<(String, String)> = Vec::new();
    {
        let mut stmt = conn
            .prepare("SELECT id, path FROM repos ORDER BY path, id")
            .map_err(|e| e.to_string())?;
        let mapped = stmt
            .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))
            .map_err(|e| e.to_string())?;
        for row in mapped {
            repos.push(row.map_err(|e| e.to_string())?);
        }
    }
    let basis_kind = if base.is_none() {
        "worktree"
    } else {
        "merge-base"
    }
    .to_string();
    let basis_base = base.unwrap_or("HEAD").to_string();
    let mut merge_base: Option<String> = None;
    let mut changed: Vec<ChangedFile> = Vec::new();
    let mut validated = 0usize;
    for (repo_id, stored) in &repos {
        let repo_dir = repo_path(workspace, stored);
        if !repo_dir.join(".git").exists() {
            continue;
        }
        validated += 1;
        let args: Vec<String> = match base {
            None => vec!["diff".into(), "--unified=0".into(), "HEAD".into()],
            Some(base) => {
                let left = resolve_base(&repo_dir, base)?;
                merge_base = Some(left.clone());
                vec!["diff".into(), "--unified=0".into(), format!("{left}..HEAD")]
            }
        };
        let arg_refs: Vec<&str> = args.iter().map(String::as_str).collect();
        let diff = git_stdout(&repo_dir, &arg_refs, true)?;
        for parsed in parse_diff(&diff) {
            if let Some(entry) = changed_entry(repo_id, &parsed) {
                changed.push(entry);
            }
        }
    }
    if base.is_some() && validated == 0 {
        return Err(format!(
            "Base ref '{}' could not be validated: no registered \
             repository with a .git directory exists under the workspace; run from the \
             workspace root or re-index with 'cairn update'.",
            base.unwrap_or_default()
        ));
    }
    Ok((basis_kind, basis_base, merge_base, changed))
}

struct SeedSelection {
    seeds: Vec<BlastSeed>,
    unindexed: Vec<String>,
}

fn seed_record(row: &rusqlite::Row<'_>, file_change: &ChangedFile) -> BlastSeed {
    BlastSeed {
        id: row.get("id").unwrap_or_default(),
        name: row.get("name").unwrap_or_default(),
        qualified_name: row.get("qualified_name").unwrap_or(None),
        kind: row.get("kind").unwrap_or_default(),
        file_path: file_change.effective_path.clone(),
        repo: file_change.repo.clone(),
        line_start: row.get("line_start").unwrap_or_default(),
        line_end: row.get("line_end").unwrap_or_default(),
        hunks: Vec::new(),
    }
}

fn seed_symbols(conn: &Connection, changed: &[ChangedFile]) -> Result<SeedSelection, String> {
    let mut order: Vec<String> = Vec::new();
    let mut seeds: BTreeMap<String, BlastSeed> = BTreeMap::new();
    let mut unindexed: BTreeSet<String> = BTreeSet::new();
    for file_change in changed {
        let rel = format!("{}:{}", file_change.repo, file_change.effective_path);
        let file_row: Option<String> = conn
            .query_row(
                "SELECT id FROM files WHERE repo_id = ?1 AND path = ?2",
                rusqlite::params![file_change.repo, file_change.effective_path],
                |r| r.get(0),
            )
            .map(Some)
            .or_else(|e| match e {
                rusqlite::Error::QueryReturnedNoRows => Ok(None),
                other => Err(other),
            })
            .map_err(|e| e.to_string())?;
        let Some(file_id) = file_row else {
            unindexed.insert(rel);
            continue;
        };
        if file_change.deleted {
            let mut stmt = conn
                .prepare(
                    "SELECT id, name, qualified_name, kind, line_start, line_end \
                     FROM symbols WHERE file_id = ?1 AND kind != 'module' \
                     AND line_start IS NOT NULL AND line_end IS NOT NULL \
                     ORDER BY line_start, name",
                )
                .map_err(|e| e.to_string())?;
            let mapped = stmt
                .query_map(rusqlite::params![file_id], |r| {
                    Ok((
                        seed_record(r, file_change),
                        r.get::<_, i64>("line_start")?,
                        r.get::<_, i64>("line_end")?,
                    ))
                })
                .map_err(|e| e.to_string())?;
            for row in mapped {
                let (seed, line_start, line_end) = row.map_err(|e| e.to_string())?;
                let seed_id = seed.id.clone();
                if !seeds.contains_key(&seed_id) {
                    order.push(seed_id.clone());
                    seeds.insert(seed_id.clone(), seed);
                }
                seeds
                    .get_mut(&seed_id)
                    .expect("seed inserted above")
                    .hunks
                    .push(SeedHunk {
                        start: line_start,
                        end: line_end,
                    });
            }
            continue;
        }
        for hunk in &file_change.hunks {
            let mut stmt = conn
                .prepare(
                    "SELECT id, name, qualified_name, kind, line_start, line_end \
                     FROM symbols WHERE file_id = ?1 AND kind != 'module' \
                     AND line_start IS NOT NULL AND line_end IS NOT NULL \
                     AND line_start <= ?2 AND line_end >= ?3",
                )
                .map_err(|e| e.to_string())?;
            let mapped = stmt
                .query_map(rusqlite::params![file_id, hunk.end, hunk.start], |r| {
                    Ok(seed_record(r, file_change))
                })
                .map_err(|e| e.to_string())?;
            let mut candidates: Vec<BlastSeed> = mapped
                .collect::<Result<_, _>>()
                .map_err(|e| e.to_string())?;
            if candidates.is_empty() {
                continue;
            }
            candidates.sort_by(|a, b| {
                (a.line_end - a.line_start)
                    .cmp(&(b.line_end - b.line_start))
                    .then_with(|| b.line_start.cmp(&a.line_start))
                    .then_with(|| a.name.cmp(&b.name))
            });
            let selected = candidates.swap_remove(0);
            let hunk = SeedHunk {
                start: hunk.start,
                end: hunk.end,
            };
            match seeds.get_mut(&selected.id) {
                Some(seed) => seed.hunks.push(hunk),
                None => {
                    order.push(selected.id.clone());
                    let mut seed = selected;
                    seed.hunks.push(hunk);
                    seeds.insert(seed.id.clone(), seed);
                }
            }
        }
    }
    let mut ordered: Vec<BlastSeed> = order
        .into_iter()
        .map(|id| seeds.get(&id).expect("ordered ids exist").clone())
        .collect();
    ordered.sort_by(|a, b| {
        a.repo
            .cmp(&b.repo)
            .then_with(|| a.file_path.cmp(&b.file_path))
            .then_with(|| a.line_start.cmp(&b.line_start))
            .then_with(|| a.name.cmp(&b.name))
    });
    Ok(SeedSelection {
        seeds: ordered,
        unindexed: unindexed.into_iter().collect(),
    })
}

struct RadiusOutcome {
    radius: Vec<RadiusRow>,
    cycles: Vec<BlastCycle>,
    truncated: bool,
}

fn radius(
    conn: &Connection,
    seeds: &[BlastSeed],
    fuzzy: bool,
    limit: usize,
) -> Result<RadiusOutcome, String> {
    let mut by_key: BTreeMap<(String, String, String), RadiusRow> = BTreeMap::new();
    let mut cycles: BTreeMap<String, BlastCycle> = BTreeMap::new();
    let mut truncated = false;
    for seed in seeds {
        let result = graph::impact_analysis_seeded(conn, &seed.name, &seed.id, 10, limit, fuzzy)
            .map_err(|e| e.to_string())?;
        truncated = truncated || result.truncated;
        for (symbol, depth) in &result.cycles {
            cycles.entry(symbol.clone()).or_insert(BlastCycle {
                symbol: symbol.clone(),
                depth: *depth,
            });
        }
        for row in &result.impacted {
            let key = (row.symbol.clone(), row.file.clone(), row.repo.clone());
            match by_key.get(&key) {
                None => {
                    by_key.insert(
                        key,
                        RadiusRow {
                            symbol: row.symbol.clone(),
                            file: row.file.clone(),
                            repo: row.repo.clone(),
                            depth: row.depth,
                            ..Default::default()
                        },
                    );
                }
                Some(previous) if row.depth < previous.depth => {
                    by_key.insert(
                        key,
                        RadiusRow {
                            symbol: row.symbol.clone(),
                            file: row.file.clone(),
                            repo: row.repo.clone(),
                            depth: row.depth,
                            ..Default::default()
                        },
                    );
                }
                _ => {}
            }
        }
    }
    let mut rows: Vec<RadiusRow> = by_key.into_values().collect();
    rows.sort_by(|a, b| {
        a.depth
            .cmp(&b.depth)
            .then_with(|| a.repo.cmp(&b.repo))
            .then_with(|| a.file.cmp(&b.file))
            .then_with(|| a.symbol.cmp(&b.symbol))
    });
    Ok(RadiusOutcome {
        radius: rows,
        cycles: cycles.into_values().collect(),
        truncated,
    })
}

struct AnnotateMatch {
    source_id: String,
    target_id: Option<String>,
    target: String,
    target_file: Option<String>,
    target_repo: Option<String>,
    resolution: String,
}

fn annotate_radius(
    conn: &Connection,
    seeds: &[BlastSeed],
    radius: &mut [RadiusRow],
) -> Result<(), String> {
    let seed_ids: HashSet<String> = seeds.iter().map(|s| s.id.clone()).collect();
    let seed_names: HashSet<String> = seeds.iter().map(|s| s.name.clone()).collect();
    let mut by_depth: Vec<(i64, String, String, String)> = radius
        .iter()
        .map(|r| (r.depth, r.symbol.clone(), r.file.clone(), r.repo.clone()))
        .collect();
    by_depth.sort_by_key(|entry| entry.0);
    for row in radius.iter_mut() {
        let earlier_names: HashSet<String> = by_depth
            .iter()
            .filter(|other| other.0 < row.depth)
            .map(|other| other.1.clone())
            .collect();
        let earlier_keys: HashSet<(String, String, String)> = by_depth
            .iter()
            .filter(|other| other.0 < row.depth)
            .map(|other| (other.1.clone(), other.2.clone(), other.3.clone()))
            .collect();
        let mut stmt = conn
            .prepare(
                "SELECT source.id AS source_id, e.target_id, \
                 COALESCE(t.name, e.target_name) AS target, \
                 target_file.path AS target_file, \
                 target_file.repo_id AS target_repo, e.resolution \
                 FROM edges e \
                 JOIN symbols source ON e.source_id = source.id \
                 JOIN files source_file ON source.file_id = source_file.id \
                 LEFT JOIN symbols t ON e.target_id = t.id \
                 LEFT JOIN files target_file ON t.file_id = target_file.id \
                 WHERE source.name = ?1 AND source_file.path = ?2 \
                 AND source_file.repo_id = ?3",
            )
            .map_err(|e| e.to_string())?;
        let mapped = stmt
            .query_map(rusqlite::params![row.symbol, row.file, row.repo], |r| {
                Ok(AnnotateMatch {
                    source_id: r.get(0)?,
                    target_id: r.get(1)?,
                    target: r.get(2)?,
                    target_file: r.get(3)?,
                    target_repo: r.get(4)?,
                    resolution: r.get::<_, Option<String>>(5)?.unwrap_or_default(),
                })
            })
            .map_err(|e| e.to_string())?;
        let matches: Vec<AnnotateMatch> = mapped
            .collect::<Result<_, _>>()
            .map_err(|e| e.to_string())?;

        let identity_in = |m: &AnnotateMatch, set: &HashSet<(String, String, String)>| match (
            &m.target_file,
            &m.target_repo,
        ) {
            (Some(file), Some(repo)) => {
                set.contains(&(m.target.clone(), file.clone(), repo.clone()))
            }
            _ => false,
        };
        let is_applicable = |m: &AnnotateMatch| match &m.target_id {
            None => seed_names.contains(&m.target) || earlier_names.contains(&m.target),
            Some(target_id) => seed_ids.contains(target_id) || identity_in(m, &earlier_keys),
        };
        let mut applicable: Vec<&AnnotateMatch> =
            matches.iter().filter(|m| is_applicable(m)).collect();
        row.symbol_id = matches.first().map(|m| m.source_id.clone());
        if !applicable.is_empty() {
            applicable.sort_by_key(|m| m.resolution != "exact");
            let mut depends_on: BTreeSet<String> = BTreeSet::new();
            for m in &applicable {
                depends_on.insert(m.target.clone());
            }
            row.depends_on = depends_on.into_iter().collect();
            let mut depends_on_ids: BTreeSet<String> = BTreeSet::new();
            for m in &applicable {
                if let Some(target_id) = &m.target_id
                    && (seed_ids.contains(target_id) || identity_in(m, &earlier_keys))
                {
                    depends_on_ids.insert(target_id.clone());
                }
            }
            row.depends_on_ids = depends_on_ids.into_iter().collect();
            row.resolution = if applicable[0].resolution.is_empty() {
                "unresolved".to_string()
            } else {
                applicable[0].resolution.clone()
            };
        } else {
            row.depends_on = seed_names
                .iter()
                .cloned()
                .collect::<BTreeSet<_>>()
                .into_iter()
                .collect();
            row.depends_on_ids = seed_ids
                .iter()
                .cloned()
                .collect::<BTreeSet<_>>()
                .into_iter()
                .collect();
            row.resolution = "exact".to_string();
        }
    }
    Ok(())
}

fn areas(seeds: &[BlastSeed], radius: &[RadiusRow]) -> Vec<BlastArea> {
    let mut names: BTreeSet<String> = BTreeSet::new();
    names.extend(seeds.iter().map(|s| s.repo.clone()));
    names.extend(radius.iter().map(|r| r.repo.clone()));
    names
        .into_iter()
        .map(|name| BlastArea {
            seeds: seeds.iter().filter(|s| s.repo == name).cloned().collect(),
            radius: radius.iter().filter(|r| r.repo == name).cloned().collect(),
            name,
        })
        .collect()
}

fn result_notes(result: &BlastResult) -> Vec<String> {
    let mut notes = Vec::new();
    if !result.stale_graph.is_empty() {
        notes.push(result.stale_graph.clone());
    }
    if result.truncated {
        notes.push("Truncated: results hit the radius cap; some dependents are missing.".into());
    }
    notes
}

fn mermaid_esc(text: &str) -> String {
    text.replace('\\', "\\\\")
        .replace('"', "\\\"")
        .replace('[', "\\[")
        .replace(']', "\\]")
        .replace('|', "\\|")
        .replace('\n', " ")
}

fn mermaid_id(name: &str, used: &mut HashSet<String>) -> String {
    let base: String = name
        .chars()
        .map(|c| if c.is_alphanumeric() { c } else { '_' })
        .take(80)
        .collect();
    let mut candidate = if base.is_empty() {
        "node".to_string()
    } else {
        base.clone()
    };
    let mut suffix = 2;
    while used.contains(&candidate) {
        candidate = format!("{base}_{suffix}");
        suffix += 1;
    }
    used.insert(candidate.clone());
    candidate
}

fn render_text(result: &BlastResult) -> String {
    let mut lines = vec![
        "Blast radius".to_string(),
        format!("Basis: {} ({})", result.basis_kind, result.basis_base),
    ];
    for area in &result.areas {
        lines.push(String::new());
        lines.push(format!("Area: {}", area.name));
        lines.push("Changed symbols:".to_string());
        if area.seeds.is_empty() {
            lines.push("  (none)".to_string());
        } else {
            for seed in &area.seeds {
                lines.push(format!(
                    "  {} ({}:{}-{})",
                    seed.name, seed.file_path, seed.line_start, seed.line_end
                ));
            }
        }
        lines.push("Dependents:".to_string());
        if area.radius.is_empty() {
            lines.push("  No dependents".to_string());
        } else {
            for row in &area.radius {
                lines.push(format!(
                    "  {} (depth {}) {} [{}]",
                    row.symbol, row.depth, row.file, row.resolution
                ));
            }
        }
    }
    if result.areas.is_empty() {
        lines.push(String::new());
        lines.push("Dependents:".to_string());
        lines.push("  No dependents".to_string());
    }
    let notes = result_notes(result);
    if !notes.is_empty() {
        lines.push(String::new());
        lines.extend(notes);
    }
    format!("{}\n", lines.join("\n"))
}

fn render_markdown(result: &BlastResult) -> String {
    let mut lines = vec!["# Blast radius".to_string(), String::new()];
    for area in &result.areas {
        lines.push(format!("## Area: {}", area.name));
        lines.push(String::new());
        lines.push("### Changed symbols".to_string());
        if area.seeds.is_empty() {
            lines.push("- none".to_string());
        } else {
            for seed in &area.seeds {
                lines.push(format!("- `{}` (`{}`)", seed.name, seed.file_path));
            }
        }
        lines.push(String::new());
        lines.push("### Dependents".to_string());
        if area.radius.is_empty() {
            lines.push("- No dependents".to_string());
        } else {
            for row in &area.radius {
                lines.push(format!(
                    "- `{}` — depth {} (`{}`, {})",
                    row.symbol, row.depth, row.file, row.resolution
                ));
            }
        }
        lines.push(String::new());
    }
    if result.areas.is_empty() {
        lines.push("## Dependents".to_string());
        lines.push(String::new());
        lines.push("- No dependents".to_string());
        lines.push(String::new());
    }
    let notes = result_notes(result);
    if !notes.is_empty() {
        lines.push(String::new());
        for note in notes {
            lines.push(format!("_{note}_"));
        }
    }
    let joined = lines.join("\n");
    format!("{}\n", joined.trim_end())
}

fn render_mermaid(result: &BlastResult) -> String {
    let mut lines = vec!["flowchart TD".to_string()];
    for note in result_notes(result) {
        lines.push(format!("    %% {note}"));
    }
    if result.radius.is_empty() {
        lines.push("    %% No dependents".to_string());
        return format!("{}\n", lines.join("\n"));
    }
    struct NodeTable {
        ids: HashMap<String, String>,
        by_name: HashMap<String, Vec<String>>,
        by_symbol_id: HashMap<String, String>,
        used: HashSet<String>,
    }
    impl NodeTable {
        fn add(
            &mut self,
            key: &str,
            symbol: &str,
            label: &str,
            symbol_id: Option<&str>,
            lines: &mut Vec<String>,
        ) {
            let node_id = mermaid_id(symbol, &mut self.used);
            self.by_name
                .entry(symbol.to_string())
                .or_default()
                .push(node_id.clone());
            if let Some(symbol_id) = symbol_id {
                self.by_symbol_id
                    .insert(symbol_id.to_string(), node_id.clone());
            }
            self.ids.insert(key.to_string(), node_id);
            lines.push(format!("    {}[\"{}\"]", self.ids[key], mermaid_esc(label)));
        }
    }
    let mut nodes = NodeTable {
        ids: HashMap::new(),
        by_name: HashMap::new(),
        by_symbol_id: HashMap::new(),
        used: HashSet::new(),
    };

    let radius_key = |row: &RadiusRow| {
        row.symbol_id
            .clone()
            .unwrap_or_else(|| format!("radius:{}:{}:{}", row.repo, row.file, row.symbol))
    };
    for seed in &result.seeds {
        nodes.add(
            &format!("seed:{}", seed.id),
            &seed.name,
            &format!("{} ({})", seed.name, seed.file_path),
            Some(&seed.id),
            &mut lines,
        );
    }
    for row in &result.radius {
        let key = radius_key(row);
        if !nodes.ids.contains_key(&key) {
            nodes.add(
                &key,
                &row.symbol,
                &format!("{} ({})", row.symbol, row.file),
                row.symbol_id.as_deref(),
                &mut lines,
            );
        }
    }
    for row in &result.radius {
        let target_key = radius_key(row);
        let target = nodes.ids[&target_key].clone();
        let mut sources: BTreeSet<String> = BTreeSet::new();
        for symbol_id in &row.depends_on_ids {
            if let Some(source) = nodes.by_symbol_id.get(symbol_id) {
                sources.insert(source.clone());
            }
        }
        if sources.is_empty() {
            for dependency in &row.depends_on {
                if let Some(list) = nodes.by_name.get(dependency) {
                    sources.extend(list.iter().cloned());
                }
            }
        }
        for source in sources {
            lines.push(format!("    {source} --> {target}"));
        }
    }
    format!("{}\n", lines.join("\n"))
}

/// Render a computed blast result without another graph traversal.
pub fn render_blast(result: &BlastResult, output_format: &str) -> Result<String, String> {
    match output_format {
        "text" => Ok(render_text(result)),
        "markdown" => Ok(render_markdown(result)),
        "mermaid" => Ok(render_mermaid(result)),
        other => Err(format!("unsupported blast format: {other}")),
    }
}

/// Refresh stored spans and return the reverse radius of a git diff.
/// `refresh` runs between the deleted and live seed legs and returns the
/// stale-graph banner ("" when the graph is current).
pub fn compute_blast(
    conn: &mut Connection,
    workspace: &str,
    base: Option<&str>,
    fuzzy: bool,
    limit: usize,
    registry: &TaintRegistryTables,
    refresh: &mut dyn FnMut(&mut Connection) -> Result<String, String>,
) -> Result<BlastResult, String> {
    let workspace_path = Path::new(workspace)
        .canonicalize()
        .unwrap_or_else(|_| PathBuf::from(workspace));
    let (basis_kind, basis_base, basis_merge_base, changed) =
        changed_files(conn, &workspace_path, base)?;

    let deleted_changed: Vec<ChangedFile> = changed.iter().filter(|c| c.deleted).cloned().collect();
    let deleted_selection = seed_symbols(conn, &deleted_changed)?;
    let deleted_outcome = radius(conn, &deleted_selection.seeds, fuzzy, limit)?;

    let stale_graph = refresh(conn)?;

    let live_changed: Vec<ChangedFile> = changed.iter().filter(|c| !c.deleted).cloned().collect();
    let live_selection = seed_symbols(conn, &live_changed)?;
    let live_outcome = radius(conn, &live_selection.seeds, fuzzy, limit)?;

    let mut seeds: Vec<BlastSeed> = deleted_selection
        .seeds
        .iter()
        .chain(live_selection.seeds.iter())
        .cloned()
        .collect();
    seeds.sort_by(|a, b| {
        a.repo
            .cmp(&b.repo)
            .then_with(|| a.file_path.cmp(&b.file_path))
            .then_with(|| a.line_start.cmp(&b.line_start))
            .then_with(|| a.name.cmp(&b.name))
    });
    let mut unindexed: BTreeSet<String> = BTreeSet::new();
    unindexed.extend(deleted_selection.unindexed);
    unindexed.extend(live_selection.unindexed);

    let mut radius_by_key: HashMap<(String, String, String), RadiusRow> = HashMap::new();
    for row in deleted_outcome
        .radius
        .iter()
        .chain(live_outcome.radius.iter())
    {
        let key = (row.symbol.clone(), row.file.clone(), row.repo.clone());
        let replace = match radius_by_key.get(&key) {
            None => true,
            Some(previous) => row.depth < previous.depth,
        };
        if replace {
            radius_by_key.insert(key, row.clone());
        }
    }
    let mut radius_rows: Vec<RadiusRow> = radius_by_key.into_values().collect();
    radius_rows.sort_by(|a, b| {
        a.depth
            .cmp(&b.depth)
            .then_with(|| a.repo.cmp(&b.repo))
            .then_with(|| a.file.cmp(&b.file))
            .then_with(|| a.symbol.cmp(&b.symbol))
    });
    let mut cycles: BTreeMap<String, BlastCycle> = BTreeMap::new();
    for cycle in deleted_outcome
        .cycles
        .iter()
        .chain(live_outcome.cycles.iter())
    {
        cycles
            .entry(cycle.symbol.clone())
            .or_insert_with(|| cycle.clone());
    }
    let truncated = deleted_outcome.truncated || live_outcome.truncated;
    annotate_radius(conn, &seeds, &mut radius_rows)?;
    let taint_paths = intersect_seeds_registry(
        conn,
        registry,
        &seeds.iter().map(|s| s.name.clone()).collect::<HashSet<_>>(),
        fuzzy,
    )
    .map_err(|e| e.to_string())?;
    let mut deleted: BTreeSet<String> = BTreeSet::new();
    for item in &changed {
        if item.deleted {
            deleted.insert(format!("{}:{}", item.repo, item.effective_path));
        }
    }
    let areas = areas(&seeds, &radius_rows);
    Ok(BlastResult {
        basis_kind,
        basis_base,
        basis_merge_base,
        changed_files: changed,
        seeds,
        areas,
        radius: radius_rows,
        cycles: cycles.into_values().collect(),
        unindexed_files: unindexed.into_iter().collect(),
        deleted_files: deleted.into_iter().collect(),
        truncated,
        stale_graph,
        taint_paths,
    })
}

/// The taint-path warning the text/markdown renderers append.
pub fn taint_warning(paths: &[TaintPath]) -> String {
    graph::format_taint_warning(paths)
}

/// One changed-file JSON record; `path` is the effective (new-or-old) path.
fn changed_file_json(c: &ChangedFile) -> Value {
    json!({
        "repo": c.repo,
        "path": c.effective_path,
        "old_path": c.old_path,
        "deleted": c.deleted,
        "renamed": c.renamed,
        "hunks": c.hunks.iter()
            .map(|h| json!({"start": h.start, "end": h.end, "count": h.count, "header": h.header}))
            .collect::<Vec<_>>(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_diff_extracts_paths_renames_and_hunks() {
        let text = "diff --git a/src/a.py b/src/a.py\nindex 1..2 100644\n--- a/src/a.py\n+++ b/src/a.py\n@@ -10,2 +11,3 @@ def x():\n context\n+added\n@@ -0,0 +1 @@\n+new file line\ndiff --git a/old.rs b/new.rs\nsimilarity index 90%\nrename from old.rs\nrename to new.rs\n@@ -3,1 +3,1 @@\n-old\n+new\n";
        let files = parse_diff(text);
        assert_eq!(files.len(), 2);
        assert_eq!(files[0].0.as_deref(), Some("src/a.py"));
        assert_eq!(files[0].1.as_deref(), Some("src/a.py"));
        assert_eq!(files[0].2.len(), 2);
        assert_eq!((files[0].2[0].start, files[0].2[0].end), (11, 13));
        assert_eq!((files[0].2[1].start, files[0].2[1].end), (1, 1));
        assert_eq!(files[1].0.as_deref(), Some("old.rs"));
        assert_eq!(files[1].1.as_deref(), Some("new.rs"));
    }

    #[test]
    fn hunks_inside_a_hunk_body_are_not_headers() {
        let text = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1,2 +1,2 @@\n--- not a header\n+++ not a header\n@@ -9,1 +9,1 @@\n-real\n+real\n";
        let files = parse_diff(text);
        assert_eq!(files.len(), 1);
        assert_eq!(files[0].2.len(), 2);
    }

    #[test]
    fn changed_entry_classifies_deletion_addition_and_rename() {
        // parse_diff fills .0 from `--- ` (old) and .1 from `+++ ` (new).
        let deletion = parse_diff(
            "diff --git a/gone.py b/gone.py\n--- a/gone.py\n+++ /dev/null\n@@ -1,5 +0,0 @@\n-def f():\n    pass\n",
        );
        let entry = changed_entry("r", &deletion[0]).expect("entry");
        assert!(entry.deleted, "+++ /dev/null is a deletion");
        assert_eq!(entry.effective_path, "gone.py");
        assert_eq!(entry.old_path.as_deref(), Some("gone.py"));
        assert!(entry.path.is_none());

        let addition = parse_diff(
            "diff --git a/new.py b/new.py\nnew file mode 100644\n--- /dev/null\n+++ b/new.py\n@@ -0,0 +1,2 @@\n+def g():\n+    pass\n",
        );
        let entry = changed_entry("r", &addition[0]).expect("entry");
        assert!(!entry.deleted, "--- /dev/null is not a deletion");
        assert!(entry.old_path.is_none());
        assert_eq!(entry.path.as_deref(), Some("new.py"));

        let rename = parse_diff(
            "diff --git a/old.py b/new.py\nsimilarity index 90%\nrename from old.py\nrename to new.py\n",
        );
        let entry = changed_entry("r", &rename[0]).expect("entry");
        assert!(entry.renamed);
        assert_eq!(entry.old_path.as_deref(), Some("old.py"));
        assert_eq!(entry.path.as_deref(), Some("new.py"));
        assert_eq!(entry.effective_path, "new.py");
        assert_eq!(changed_file_json(&entry)["path"], json!("new.py"));
    }

    #[test]
    fn unquote_decodes_octal_and_simple_escapes() {
        assert_eq!(unquote_git_path("plain.rs"), "plain.rs");
        assert_eq!(unquote_git_path("\"a\\303\\251b\""), "a\u{e9}b");
        assert_eq!(unquote_git_path("\"a\\tb\""), "a\tb");
    }

    #[test]
    fn mermaid_id_sanitizes_and_dedupes() {
        let mut used = HashSet::new();
        let first = mermaid_id("src/cairn:run(1)", &mut used);
        assert_eq!(first, "src_cairn_run_1_");
        let second = mermaid_id("src/cairn:run(1)", &mut used);
        assert_eq!(second, "src_cairn_run_1__2");
    }

    #[test]
    fn compute_blast_seeds_from_a_worktree_diff_and_annotates_the_radius() {
        let dir = std::env::temp_dir().join(format!(
            "cairn-blast-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("clock")
                .as_nanos()
        ));
        std::fs::create_dir_all(&dir).expect("scratch");
        let git = |args: &[&str]| {
            let ok = std::process::Command::new("git")
                .args(args)
                .current_dir(&dir)
                .status()
                .expect("git binary");
            assert!(ok.success(), "git {args:?} failed");
        };
        git(&["init", "-q"]);
        git(&["config", "user.email", "t@t"]);
        git(&["config", "user.name", "t"]);
        let baseline = "def bottom():\n    return 1\n\n\ndef mid():\n    return bottom()\n\n\ndef top():\n    return mid()\n";
        std::fs::write(dir.join("a.py"), baseline).expect("source");
        git(&["add", "."]);
        git(&["commit", "-qm", "base"]);

        let db = dir.join("store.kg");
        let mut conn = cairn_store::schema::open_initialized(&db).expect("store");
        conn.execute_batch(
            "INSERT INTO repos (id, name, path, language) VALUES ('r', 'r', '.', 'python');
             INSERT INTO files (id, repo_id, path, language) VALUES ('f1', 'r', 'a.py', 'python');
             INSERT INTO symbols (id, file_id, name, qualified_name, kind, line_start, line_end)
               VALUES ('s-bottom', 'f1', 'bottom', 'bottom', 'function', 1, 2),
                      ('s-mid', 'f1', 'mid', 'mid', 'function', 5, 6),
                      ('s-top', 'f1', 'top', 'top', 'function', 9, 10);
             INSERT INTO edges (id, source_id, target_id, kind, resolution)
               VALUES ('e1', 's-mid', 's-bottom', 'call', 'exact'),
                      ('e2', 's-top', 's-mid', 'call', 'exact');",
        )
        .expect("seed rows");

        // Edit inside mid's span: `git diff HEAD` seeds mid, never bottom/top.
        std::fs::write(
            dir.join("a.py"),
            baseline.replace("return bottom()", "x = 1\n    return bottom()"),
        )
        .expect("edit");

        let registry = crate::graph::merged_registry(&[], &[]);
        let mut no_refresh = |_conn: &mut Connection| Ok::<String, String>(String::new());
        let result = compute_blast(
            &mut conn,
            &dir.to_string_lossy(),
            None,
            false,
            500,
            &registry,
            &mut no_refresh,
        )
        .expect("blast");
        assert_eq!(result.seeds.len(), 1);
        assert_eq!(result.seeds[0].name, "mid");
        assert_eq!(result.seeds[0].hunks.len(), 1);
        assert_eq!(result.radius.len(), 1);
        assert_eq!(result.radius[0].symbol, "top");
        assert_eq!(result.radius[0].depth, 0);
        assert_eq!(result.radius[0].resolution, "exact");
        assert!(result.radius[0].depends_on.contains(&"mid".to_string()));
        let text = render_blast(&result, "text").expect("text");
        assert!(text.contains("Changed symbols:"));
        assert!(text.contains("mid (a.py:5-6)"));
        assert!(text.contains("top (depth 0) a.py [exact]"));
        let _ = std::fs::remove_dir_all(&dir);
    }
}
