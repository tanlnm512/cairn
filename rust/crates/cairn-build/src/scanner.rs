//! Repo scanner: discover repos and enumerate source files by language with
//! a 4-layer filter (skip dirs, gitignore, config, size).

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};

use sha2::{Digest, Sha256};

use crate::config::load_config;
use crate::gitignore::GitIgnoreSpec;

/// Extension -> language mapping.
pub fn extension_language(suffix: &str) -> Option<&'static str> {
    Some(match suffix {
        ".kt" => "kotlin",
        ".java" => "java",
        ".swift" => "swift",
        ".py" => "python",
        // .tsx picks the TSX grammar internally but routes as "typescript".
        ".ts" | ".tsx" | ".mts" | ".cts" => "typescript",
        ".js" | ".jsx" | ".mjs" | ".cjs" => "javascript",
        ".dart" => "dart",
        ".go" => "go",
        ".php" | ".phtml" | ".php3" | ".php4" | ".php5" => "php",
        ".rb" | ".rbw" => "ruby",
        ".rs" => "rust",
        ".cs" | ".csx" => "csharp",
        ".m" | ".mm" => "objc",
        // Ambiguous across objc/cpp/c; resolved by header sniffing.
        ".h" => "header",
        ".hpp" => "cpp",
        ".c" => "c",
        ".cpp" | ".cc" | ".cxx" => "cpp",
        _ => return None,
    })
}

/// Directories never scanned.
pub const DEFAULT_SKIP_DIRS: &[&str] = &[
    // build output
    "build",
    "out",
    "dist",
    "target",
    "bin",
    "obj",
    // vcs
    ".git",
    ".hg",
    ".svn",
    // package managers / deps
    "node_modules",
    ".venv",
    "venv",
    "env",
    "Pods",
    "vendor",
    "Carthage",
    ".swiftpm",
    ".build",
    // gradle/maven
    ".gradle",
    ".m2",
    // caches / generated
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".next",
    ".nuxt",
    ".turbo",
    ".parcel-cache",
    "coverage",
    ".nyc_output",
    // IDE / editor state
    ".idea",
    ".vscode",
    // cairn's own store (never index the index)
    ".cairn",
];

pub const MAX_FILE_SIZE: u64 = 1_000_000;

pub const REASON_DEFAULT_SKIP: &str = "default_skip";
pub const REASON_GITIGNORED: &str = "gitignored";
pub const REASON_CONFIG_EXCLUDE: &str = "config_exclude";
pub const REASON_SIZE_CAP: &str = "size_cap";
pub const REASON_MINIFIED: &str = "minified_asset";
pub const REASON_PARSER_UNAVAILABLE: &str = "parser_unavailable";
pub const REASON_STAT_ERROR: &str = "stat_error";

/// A source file selected for indexing.
#[derive(Debug, Clone, PartialEq)]
pub struct FileInfo {
    pub repo: String,
    pub repo_path: String,
    pub path: String,
    pub rel_path: String,
    pub language: String,
    pub hash: String,
}

/// A file the scanner chose not to index, with the reason.
#[derive(Debug, Clone, PartialEq)]
pub struct SkipInfo {
    pub repo: String,
    pub path: String,
    pub rel_path: String,
    pub reason: String,
    pub size_bytes: Option<i64>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct RepositoryRecord {
    pub repo_id: String,
    pub path: PathBuf,
}

fn is_skip_dir_name(name: &str) -> bool {
    let lower = name.to_lowercase();
    DEFAULT_SKIP_DIRS.contains(&name) || DEFAULT_SKIP_DIRS.contains(&lower.as_str())
}

/// Disambiguate .h headers between objc, cpp, and c (first 4096 chars).
pub fn detect_header_language(path: &Path) -> String {
    let content = std::fs::read(path)
        .map(|bytes| String::from_utf8_lossy(&bytes).into_owned())
        .unwrap_or_default();
    let head: String = content.chars().take(4096).collect();
    if head.contains("@interface") || head.contains("@protocol") || head.contains("#import") {
        return "objc".to_string();
    }
    if head.contains("class ")
        || head.contains("namespace ")
        || head.contains("template ")
        || head.contains("std::")
    {
        return "cpp".to_string();
    }
    "c".to_string()
}

/// Map a suffix to its parser language, sniffing ambiguous headers.
pub fn resolve_file_language(suffix: &str, abs_path: &Path) -> String {
    match extension_language(suffix) {
        Some("header") => detect_header_language(abs_path),
        Some(lang) => lang.to_string(),
        None => String::new(),
    }
}

fn top_level_records(root: &Path) -> Vec<RepositoryRecord> {
    let mut records = Vec::new();
    let mut children: Vec<PathBuf> = match std::fs::read_dir(root) {
        Ok(entries) => entries.filter_map(|e| e.ok().map(|e| e.path())).collect(),
        Err(_) => return records,
    };
    children.sort();
    for child in children {
        if !child.is_dir() {
            continue;
        }
        let Some(name) = child.file_name().and_then(|n| n.to_str()) else {
            continue;
        };
        if is_skip_dir_name(name) {
            continue;
        }
        // A child .git FILE is a submodule pointer: part of the parent repo,
        // not a repository of its own to discover.
        if child.join(".git").is_dir() {
            let repo_id = name.to_string();
            records.push(RepositoryRecord {
                repo_id,
                path: child,
            });
        }
    }
    if !records.is_empty() {
        return records;
    }
    if root.join(".git").exists() {
        let repo_id = root
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or_default()
            .to_string();
        return vec![RepositoryRecord {
            repo_id,
            path: root.to_path_buf(),
        }];
    }
    Vec::new()
}

/// Preorder descendant directories under `root`, pruning skip dirs and symlinks.
fn descendant_directories(root: &Path) -> Vec<PathBuf> {
    let mut out = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let mut children: Vec<PathBuf> = match std::fs::read_dir(&dir) {
            Ok(entries) => entries.filter_map(|e| e.ok().map(|e| e.path())).collect(),
            Err(_) => continue,
        };
        children.sort();
        // Reverse-push keeps preorder traversal with a LIFO stack.
        for child in children.into_iter().rev() {
            if !child.is_dir() || child.is_symlink() {
                continue;
            }
            let Some(name) = child.file_name().and_then(|n| n.to_str()) else {
                continue;
            };
            if is_skip_dir_name(name) {
                continue;
            }
            out.push(child.clone());
            stack.push(child);
        }
    }
    out
}

/// Stable ids and roots for every repository selected by config.
pub fn discover_repo_records(workspace: &str) -> Result<Vec<RepositoryRecord>, String> {
    let root = Path::new(workspace);
    if !root.is_dir() {
        return Ok(Vec::new());
    }
    let mut records: Vec<RepositoryRecord> = Vec::new();
    let mut workspace_config: Option<crate::config::CairnConfig> = None;
    for record in top_level_records(root) {
        if workspace_config.is_none() {
            workspace_config = Some(load_config(root)?);
        }
        let ws_nested = workspace_config
            .as_ref()
            .expect("config")
            .include_nested_repos;
        let repo_nested = load_config(&record.path)?.include_nested_repos;
        let include_nested = ws_nested || repo_nested;
        let top_id = record.repo_id.clone();
        let top_path = record.path.clone();
        records.push(record);
        if !include_nested {
            continue;
        }
        for candidate in descendant_directories(&top_path) {
            if !candidate.join(".git").exists() {
                continue;
            }
            let relative = candidate
                .strip_prefix(&top_path)
                .map_err(|e| e.to_string())?
                .to_string_lossy()
                .replace('\\', "/");
            records.push(RepositoryRecord {
                repo_id: format!("{top_id}/{relative}"),
                path: candidate,
            });
        }
    }
    records.sort_by(|a, b| a.path.to_string_lossy().cmp(&b.path.to_string_lossy()));
    Ok(records)
}

/// Load compiled nested gitignore specs for a repository, keyed by owning dir.
fn load_gitignores(repo_root: &Path) -> Vec<(PathBuf, GitIgnoreSpec)> {
    let mut specs = Vec::new();
    for gi in find_gitignore_files(repo_root) {
        let lines = std::fs::read(&gi).map(|bytes| String::from_utf8_lossy(&bytes).into_owned());
        let Ok(lines) = lines else {
            continue;
        };
        let split: Vec<&str> = lines
            .split('\n')
            .map(|l| l.trim_end_matches('\r'))
            .collect();
        let has_content = split.iter().any(|ln| {
            let t = ln.trim();
            !t.is_empty() && !t.starts_with('#')
        });
        if !has_content {
            continue;
        }
        specs.push((
            gi.parent().map(Path::to_path_buf).unwrap_or_default(),
            GitIgnoreSpec::from_lines(&split),
        ));
    }
    specs
}

/// Every `.gitignore` path under root.
fn find_gitignore_files(root: &Path) -> Vec<PathBuf> {
    let mut out = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let mut entries: Vec<PathBuf> = match std::fs::read_dir(&dir) {
            Ok(entries) => entries.filter_map(|e| e.ok().map(|e| e.path())).collect(),
            Err(_) => continue,
        };
        entries.sort();
        for entry in entries {
            if entry.is_dir() {
                if entry.is_symlink() {
                    continue;
                }
                stack.push(entry);
            } else if entry.is_file()
                && entry.file_name().and_then(|n| n.to_str()) == Some(".gitignore")
            {
                out.push(entry);
            }
        }
    }
    out.sort();
    out
}

fn is_gitignored(abs_path: &Path, specs: &[(PathBuf, GitIgnoreSpec)]) -> bool {
    let abs_str = abs_path.to_string_lossy();
    for (gi_dir, spec) in specs {
        let gi_str = gi_dir.to_string_lossy();
        let prefix = format!("{gi_str}{}", std::path::MAIN_SEPARATOR);
        if abs_str == gi_str || !abs_str.starts_with(&prefix) {
            continue;
        }
        let Ok(rel) = abs_path.strip_prefix(gi_dir) else {
            continue;
        };
        if spec.is_ignored(
            &rel.to_string_lossy()
                .replace(std::path::MAIN_SEPARATOR, "/"),
        ) {
            return true;
        }
    }
    false
}

fn match_root_relative(spec: &GitIgnoreSpec, abs_path: &Path, repo_root: &Path) -> bool {
    let Ok(rel) = abs_path.strip_prefix(repo_root) else {
        return false;
    };
    spec.is_ignored(
        &rel.to_string_lossy()
            .replace(std::path::MAIN_SEPARATOR, "/"),
    )
}

fn is_minified(path: &Path) -> bool {
    path.file_name()
        .and_then(|n| n.to_str())
        .is_some_and(|n| n.to_lowercase().contains(".min."))
}

/// The 4-layer filter: indexing eligibility and skip reason for one file.
pub fn classify_file(
    abs_path: &Path,
    repo_root: &Path,
    gitignore_specs: &[(PathBuf, GitIgnoreSpec)],
    exclude_spec: Option<&GitIgnoreSpec>,
    include_spec: Option<&GitIgnoreSpec>,
) -> (bool, String) {
    let rel_parts_ok = abs_path.strip_prefix(repo_root).is_ok();
    if !rel_parts_ok {
        return (false, REASON_DEFAULT_SKIP.to_string());
    }

    // `include` override first; only the Layer D skips still apply.
    if let Some(include) = include_spec
        && match_root_relative(include, abs_path, repo_root)
    {
        if is_minified(abs_path) {
            return (false, REASON_MINIFIED.to_string());
        }
        let Ok(meta) = abs_path.metadata() else {
            return (false, REASON_STAT_ERROR.to_string());
        };
        if meta.len() > MAX_FILE_SIZE {
            return (false, REASON_SIZE_CAP.to_string());
        }
        return (true, String::new());
    }

    // Layer A: default skip dirs.
    let rel_parts = abs_path
        .strip_prefix(repo_root)
        .unwrap_or_else(|_| Path::new(""))
        .components();
    for part in rel_parts {
        if let Some(name) = part.as_os_str().to_str()
            && is_skip_dir_name(name)
        {
            return (false, REASON_DEFAULT_SKIP.to_string());
        }
    }

    // Layer B: gitignore.
    if is_gitignored(abs_path, gitignore_specs) {
        return (false, REASON_GITIGNORED.to_string());
    }

    // Layer C: cairn.json exclude.
    if let Some(exclude) = exclude_spec
        && match_root_relative(exclude, abs_path, repo_root)
    {
        return (false, REASON_CONFIG_EXCLUDE.to_string());
    }

    // Layer D: minified bundles, then the size cap.
    if is_minified(abs_path) {
        return (false, REASON_MINIFIED.to_string());
    }
    let Ok(meta) = abs_path.metadata() else {
        return (false, REASON_STAT_ERROR.to_string());
    };
    if meta.len() > MAX_FILE_SIZE {
        return (false, REASON_SIZE_CAP.to_string());
    }
    (true, String::new())
}

/// One repo's file walk: sorted names, pruned symlink/nested-repo dirs,
/// symlink files skipped. Skip dirs stay pruned unless `include` patterns
/// must reach files inside them.
fn walk_repo_files(repo_path: &Path, prune_skip_dirs: bool) -> Vec<PathBuf> {
    let mut boundaries: HashSet<String> = HashSet::new();
    for dir in descendant_directories(repo_path) {
        if dir.join(".git").exists()
            && let Ok(resolved) = dir.canonicalize()
        {
            boundaries.insert(resolved.to_string_lossy().into_owned());
        }
    }
    let mut out = Vec::new();
    let mut stack = vec![repo_path.to_path_buf()];
    while let Some(directory) = stack.pop() {
        let mut names: Vec<PathBuf> = match std::fs::read_dir(&directory) {
            Ok(entries) => entries.filter_map(|e| e.ok().map(|e| e.path())).collect(),
            Err(_) => continue,
        };
        names.sort();

        let mut kept_dirs: Vec<PathBuf> = Vec::new();
        for child in &names {
            if !child.is_dir() {
                continue;
            }
            if child.is_symlink() {
                continue;
            }
            let Some(name) = child.file_name().and_then(|n| n.to_str()) else {
                continue;
            };
            if prune_skip_dirs && is_skip_dir_name(name) {
                continue;
            }
            if let Ok(resolved) = child.canonicalize()
                && boundaries.contains(&resolved.to_string_lossy().into_owned())
            {
                continue;
            }
            kept_dirs.push(child.clone());
        }
        // Reverse-push keeps lexicographic preorder like os.walk's top-down order.
        for child in kept_dirs.into_iter().rev() {
            stack.push(child);
        }

        for child in &names {
            if !child.is_file() {
                continue;
            }
            if child.is_symlink() {
                continue;
            }
            out.push(child.clone());
        }
    }
    out
}

/// Repository files to index plus records for skipped files, in walk order.
pub fn iter_files_and_skips(
    repo_id: &str,
    repo_path: &Path,
) -> Result<(Vec<FileInfo>, Vec<SkipInfo>), String> {
    let specs = load_gitignores(repo_path);
    let config = load_config(repo_path)?;
    let exclude_spec = if config.exclude.is_empty() {
        None
    } else {
        Some(GitIgnoreSpec::from_lines(
            &config
                .exclude
                .iter()
                .map(String::as_str)
                .collect::<Vec<_>>(),
        ))
    };
    let include_spec = if config.include.is_empty() {
        None
    } else {
        Some(GitIgnoreSpec::from_lines(
            &config
                .include
                .iter()
                .map(String::as_str)
                .collect::<Vec<_>>(),
        ))
    };

    let mut files = Vec::new();
    let mut skips = Vec::new();
    for path in walk_repo_files(repo_path, include_spec.is_none()) {
        let Some(suffix) = path
            .extension()
            .and_then(|e| e.to_str())
            .map(|e| format!(".{e}"))
        else {
            continue;
        };
        if extension_language(&suffix).is_none() {
            continue; // not a source file at all; neither indexed nor skipped
        }
        let (should_index, reason) = classify_file(
            &path,
            repo_path,
            &specs,
            exclude_spec.as_ref(),
            include_spec.as_ref(),
        );
        let rel = path
            .strip_prefix(repo_path)
            .map_err(|e| e.to_string())?
            .to_string_lossy()
            .replace(std::path::MAIN_SEPARATOR, "/");
        if should_index {
            let language = resolve_file_language(&suffix, &path);
            if !crate::parse_dispatch::is_language_available(&language) {
                skips.push(SkipInfo {
                    repo: repo_id.to_string(),
                    path: path.to_string_lossy().into_owned(),
                    rel_path: rel,
                    reason: REASON_PARSER_UNAVAILABLE.to_string(),
                    size_bytes: None,
                });
                continue;
            }
            // classify_file stat'd the file moments earlier; a read failure
            // in between is the same transient class, not a failed build.
            let hash = match file_sha256(&path) {
                Ok(hash) => hash,
                Err(_) => {
                    skips.push(SkipInfo {
                        repo: repo_id.to_string(),
                        path: path.to_string_lossy().into_owned(),
                        rel_path: rel,
                        reason: REASON_STAT_ERROR.to_string(),
                        size_bytes: None,
                    });
                    continue;
                }
            };
            files.push(FileInfo {
                repo: repo_id.to_string(),
                repo_path: repo_path.to_string_lossy().into_owned(),
                path: path.to_string_lossy().into_owned(),
                rel_path: rel,
                language,
                hash,
            });
        } else {
            let size_bytes = std::fs::metadata(&path).ok().map(|m| m.len() as i64);
            skips.push(SkipInfo {
                repo: repo_id.to_string(),
                path: path.to_string_lossy().into_owned(),
                rel_path: rel,
                reason,
                size_bytes,
            });
        }
    }
    Ok((files, skips))
}

/// Lowercase hex SHA-256 of a file's bytes.
pub fn file_sha256(path: &Path) -> Result<String, String> {
    let bytes = std::fs::read(path).map_err(|e| format!("{}: {e}", path.display()))?;
    let mut hasher = Sha256::new();
    hasher.update(&bytes);
    Ok(hex(&hasher.finalize()))
}

pub(crate) fn hex(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        out.push_str(&format!("{b:02x}"));
    }
    out
}

/// All source files selected across the workspace (or one filtered repo).
pub fn scan_workspace(
    workspace: &str,
    repo_filter: Option<&str>,
) -> Result<(Vec<FileInfo>, Vec<SkipInfo>), String> {
    let mut all_files = Vec::new();
    let mut all_skips = Vec::new();
    if let Some(repo_filter) = repo_filter {
        let repo_path = resolve_repo_path(workspace, repo_filter)?;
        if repo_path.join(".git").exists() {
            let repo_id = discover_repo_records(workspace)?
                .into_iter()
                .find(|r| r.path == repo_path)
                .map(|r| r.repo_id)
                .unwrap_or_else(|| {
                    repo_path
                        .file_name()
                        .and_then(|n| n.to_str())
                        .unwrap_or_default()
                        .to_string()
                });
            let (files, skips) = iter_files_and_skips(&repo_id, &repo_path)?;
            all_files.extend(files);
            all_skips.extend(skips);
        }
    } else {
        for record in discover_repo_records(workspace)? {
            let (files, skips) = iter_files_and_skips(&record.repo_id, &record.path)?;
            all_files.extend(files);
            all_skips.extend(skips);
        }
    }
    Ok((all_files, all_skips))
}

/// Map a repository name to its workspace path.
pub fn resolve_repo_path(workspace: &str, repo_name: &str) -> Result<PathBuf, String> {
    for record in discover_repo_records(workspace)? {
        if record.repo_id == repo_name {
            return Ok(record.path);
        }
    }
    let ws = Path::new(workspace);
    if is_single_repo_workspace(workspace) {
        return Ok(ws.to_path_buf());
    }
    Ok(ws.join(repo_name))
}

/// Resolve a stored graph path to an absolute disk path.
pub fn resolve_file_path(workspace: &str, repo_id: &str, stored_path: &str) -> PathBuf {
    let stored = Path::new(stored_path);
    if stored.is_absolute() {
        return stored.to_path_buf();
    }
    resolve_repo_path(workspace, repo_id)
        .unwrap_or_else(|_| PathBuf::from(workspace))
        .join(stored)
}

/// The stable repository id carried by a discovered repo path (dir-name fallback).
pub fn repository_id_for_path(workspace: &str, repo_path: &Path) -> String {
    discover_repo_records(workspace)
        .ok()
        .unwrap_or_default()
        .into_iter()
        .find(|r| r.path == repo_path)
        .map(|r| r.repo_id)
        .unwrap_or_else(|| {
            repo_path
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or_default()
                .to_string()
        })
}

/// Symlink-resolving path normalization that tolerates nonexistent leaves
/// (non-strict `Path.resolve()`), so deletion events keep resolving.
pub fn resolve_lenient(path: &Path) -> PathBuf {
    let mut tail: Vec<std::ffi::OsString> = Vec::new();
    let mut cur = path.to_path_buf();
    loop {
        if cur.symlink_metadata().is_ok() {
            break;
        }
        match (cur.parent(), cur.file_name()) {
            (Some(parent), Some(name)) if parent != cur => {
                tail.push(name.to_os_string());
                cur = parent.to_path_buf();
            }
            _ => break,
        }
    }
    let base = std::fs::canonicalize(&cur).unwrap_or(cur);
    tail.into_iter()
        .rev()
        .fold(base, |acc, part| acc.join(part))
}

/// Longest owning repository name for an absolute path under the workspace.
pub fn infer_repo_for_path(workspace: &str, abs_path: &Path) -> Option<String> {
    let resolved = resolve_lenient(abs_path);
    let records = discover_repo_records(workspace).ok()?;
    let mut best: Option<(usize, String)> = None;
    for record in records {
        let root = std::fs::canonicalize(&record.path).ok()?;
        if resolved != root
            && resolved.starts_with(&root)
            && best
                .as_ref()
                .is_none_or(|(depth, _)| root.components().count() > *depth)
        {
            best = Some((root.components().count(), record.repo_id));
        }
    }
    best.map(|(_, repo)| repo)
}

/// Layer A over a repo-relative path: any component is a default skip dir.
pub fn path_under_skip_dir(rel: &Path) -> bool {
    rel.components()
        .any(|c| c.as_os_str().to_str().is_some_and(is_skip_dir_name))
}

/// Whether the full scan filter skips a path (the watcher's drift gate).
pub fn is_skipped(path: &Path, repo_root: &Path) -> Result<bool, String> {
    let is_source = path
        .extension()
        .and_then(|e| e.to_str())
        .map(|e| format!(".{e}"))
        .and_then(|s| extension_language(&s))
        .is_some();
    if !is_source {
        return Ok(true);
    }
    let specs = load_gitignores(repo_root);
    let config = load_config(repo_root)?;
    let exclude_spec = if config.exclude.is_empty() {
        None
    } else {
        Some(GitIgnoreSpec::from_lines(
            &config
                .exclude
                .iter()
                .map(String::as_str)
                .collect::<Vec<_>>(),
        ))
    };
    let include_spec = if config.include.is_empty() {
        None
    } else {
        Some(GitIgnoreSpec::from_lines(
            &config
                .include
                .iter()
                .map(String::as_str)
                .collect::<Vec<_>>(),
        ))
    };
    let (should_index, _) = classify_file(
        path,
        repo_root,
        &specs,
        exclude_spec.as_ref(),
        include_spec.as_ref(),
    );
    Ok(!should_index)
}

/// Whether the workspace root itself is the repository.
pub fn is_single_repo_workspace(workspace: &str) -> bool {
    let root = Path::new(workspace);
    if !root.is_dir() || !root.join(".git").exists() {
        return false;
    }
    let Ok(entries) = std::fs::read_dir(root) else {
        return false;
    };
    for entry in entries.filter_map(|e| e.ok()) {
        let child = entry.path();
        // A .git file marks a submodule of the root, not a separate repo.
        if child.is_dir() && child.join(".git").is_dir() {
            return false;
        }
    }
    true
}

/// Dominant language for a repo (by file count; first-seen wins ties).
pub fn infer_repo_language(files: &[FileInfo]) -> Option<String> {
    let mut order: Vec<String> = Vec::new();
    let mut counts: HashMap<String, usize> = HashMap::new();
    for f in files {
        if !counts.contains_key(&f.language) {
            order.push(f.language.clone());
        }
        *counts.entry(f.language.clone()).or_insert(0) += 1;
    }
    // First maximum in iteration (first-seen) order wins.
    let mut best: Option<(String, usize)> = None;
    for lang in order {
        let count = counts[&lang];
        if best.as_ref().is_none_or(|(_, c)| count > *c) {
            best = Some((lang, count));
        }
    }
    best.map(|(lang, _)| lang)
}
