//! Review pack engine: the diff blast radius plus review-context sections
//! (`review/engine.py`, `review/spans.py`, `review/capture.py`).

use std::path::Path;

use rusqlite::Connection;
use serde_json::Value;

use crate::blast::{self, BlastResult};
use crate::graph::TaintRegistryTables;
use crate::tools_km::{self, Bundle};

const MEMORY_TYPE_DEFAULT: &str = "decision";
pub const PRE_SUBMIT_MEMORY_TYPES: [&str; 2] = ["mistake", "pattern"];
pub const PRE_SUBMIT_BASE: &str = "main";

pub const PATTERN_MARKER: &str = "cairn:pattern";
pub const CAPTURE_CONFIDENCE: f64 = 0.3;
const TITLE_SUMMARY_MAX_CHARS: usize = 80;

// --- pack model ----------------------------------------------------------------

pub struct MemoryMatch {
    pub kind: String,
    pub title: String,
    pub body: String,
}

pub struct MemoryEntry {
    pub seed: String,
    pub file_path: String,
    pub matches: Vec<MemoryMatch>,
}

pub struct CompassEntry {
    pub module: String,
    pub guide: String,
    pub found: bool,
}

pub struct WikiEntry {
    pub module: String,
    pub pages: String,
    pub found: bool,
}

pub struct Section<T> {
    pub entries: Vec<T>,
    pub error: Option<String>,
}

pub struct ReviewPack {
    pub blast: BlastResult,
    pub memories: Section<MemoryEntry>,
    pub compass: Section<CompassEntry>,
    pub wiki: Section<WikiEntry>,
}

pub struct PreSubmitResult {
    pub memories: Section<MemoryEntry>,
}

// --- pack build ----------------------------------------------------------------

/// Build the review-context pack for the diff against `base`.
pub fn build_pack(
    conn: &mut Connection,
    workspace: &str,
    bundle: &Bundle,
    base: &str,
    fuzzy: bool,
    registry: &TaintRegistryTables,
    refresh: &mut dyn FnMut(&mut Connection) -> Result<String, String>,
) -> Result<ReviewPack, String> {
    let blast = blast::compute_blast(conn, workspace, Some(base), fuzzy, 500, registry, refresh)?;
    Ok(enrich_pack(conn, bundle, blast))
}

/// Attach the memories, compass, and wiki sections to a blast result.
pub fn enrich_pack(conn: &Connection, bundle: &Bundle, blast: BlastResult) -> ReviewPack {
    let mut modules: Vec<String> = blast.seeds.iter().map(|s| stem_of(&s.file_path)).collect();
    modules.sort();
    modules.dedup();
    let memories = memory_section(conn, bundle, &blast.seeds);
    let compass = compass_section(bundle, &modules);
    let wiki = wiki_section(bundle, &modules);
    ReviewPack {
        blast,
        memories,
        compass,
        wiki,
    }
}

/// Match mistake and pattern memories against the diff's seed symbols.
pub fn build_pre_submit(
    conn: &mut Connection,
    workspace: &str,
    bundle: &Bundle,
    fuzzy: bool,
    registry: &TaintRegistryTables,
    refresh: &mut dyn FnMut(&mut Connection) -> Result<String, String>,
) -> Result<PreSubmitResult, String> {
    let blast = blast::compute_blast(
        conn,
        workspace,
        Some(PRE_SUBMIT_BASE),
        fuzzy,
        500,
        registry,
        refresh,
    )?;
    let mut section = memory_section(conn, bundle, &blast.seeds);
    if section.error.is_none() {
        for entry in &mut section.entries {
            entry
                .matches
                .retain(|m| PRE_SUBMIT_MEMORY_TYPES.contains(&m.kind.as_str()));
        }
        section.entries.retain(|e| !e.matches.is_empty());
    }
    Ok(PreSubmitResult { memories: section })
}

/// True when a pre-submit result carries at least one mistake/pattern match;
/// a memory-reader error leaves the match set unverified.
pub fn has_blocking_matches(result: &PreSubmitResult) -> bool {
    if result.memories.error.is_some() {
        return false;
    }
    result.memories.entries.iter().any(|e| {
        e.matches
            .iter()
            .any(|m| PRE_SUBMIT_MEMORY_TYPES.contains(&m.kind.as_str()))
    })
}

fn stem_of(file_path: &str) -> String {
    Path::new(file_path)
        .file_stem()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_default()
}

fn seed_query(seed_name: &str, file_path: &str) -> String {
    let mut parts: Vec<&str> = Vec::new();
    let stem = stem_of(file_path);
    if !parts.contains(&seed_name) {
        parts.push(seed_name);
    }
    if !parts.contains(&stem.as_str()) {
        parts.push(&stem);
    }
    parts.join(" ")
}

fn memory_section(
    conn: &Connection,
    bundle: &Bundle,
    seeds: &[blast::BlastSeed],
) -> Section<MemoryEntry> {
    let mut section = Section {
        entries: Vec::new(),
        error: None,
    };
    let mut queries: Vec<(&blast::BlastSeed, String)> = Vec::new();
    let mut seen: Vec<String> = Vec::new();
    for seed in seeds {
        let query = seed_query(&seed.name, &seed.file_path);
        if !seen.contains(&query) {
            seen.push(query.clone());
            queries.push((seed, query));
        }
    }
    if queries.is_empty() {
        return section;
    }
    for (seed, query) in &queries {
        let concepts = match tools_km::search_memory(conn, bundle, query, None, None, false, None) {
            Ok(concepts) => concepts,
            Err(e) => {
                section.error = Some(e.0);
                return section;
            }
        };
        section.entries.push(MemoryEntry {
            seed: seed.name.clone(),
            file_path: seed.file_path.clone(),
            matches: concepts
                .into_iter()
                .map(|c| MemoryMatch {
                    kind: c
                        .ext_str("memory_type")
                        .unwrap_or(MEMORY_TYPE_DEFAULT)
                        .to_string(),
                    title: c.title.unwrap_or_default(),
                    body: c.body.trim().to_string(),
                })
                .collect(),
        });
    }
    section
}

fn compass_section(bundle: &Bundle, modules: &[String]) -> Section<CompassEntry> {
    let mut section = Section {
        entries: Vec::new(),
        error: None,
    };
    if modules.is_empty() {
        return section;
    }
    for module in modules {
        let concept = match tools_km::find_compass_concept(bundle, module) {
            Ok(concept) => concept,
            Err(e) => {
                section.error = Some(e.0);
                return section;
            }
        };
        section.entries.push(CompassEntry {
            module: module.clone(),
            guide: concept
                .as_ref()
                .map(|c| format!("# {}\n\n{}", c.title.as_deref().unwrap_or(""), c.body))
                .unwrap_or_default(),
            found: concept.is_some(),
        });
    }
    section
}

fn wiki_section(bundle: &Bundle, modules: &[String]) -> Section<WikiEntry> {
    let mut section = Section {
        entries: Vec::new(),
        error: None,
    };
    if modules.is_empty() {
        return section;
    }
    for module in modules {
        let results = match bundle.search(module, None) {
            Ok(results) => results,
            Err(e) => {
                section.error = Some(e.0);
                return section;
            }
        };
        let results: Vec<_> = results
            .into_iter()
            .filter(|c| c.type_.starts_with("Wiki"))
            .take(10)
            .collect();
        let found = !results.is_empty();
        let pages = results
            .iter()
            .map(|c| format!("# {}\n{}", c.title.as_deref().unwrap_or(""), c.body))
            .collect::<Vec<_>>()
            .join("\n\n---\n\n");
        section.entries.push(WikiEntry {
            module: module.clone(),
            pages,
            found,
        });
    }
    section
}

// --- pack render ----------------------------------------------------------------

#[derive(Clone, Copy, PartialEq, Eq)]
enum PackFormat {
    Text,
    Markdown,
}

/// Render the pack: the blast radius layer plus the enrichment sections.
pub fn render_pack(pack: &ReviewPack, output_format: &str) -> Result<String, String> {
    let format = pack_format(output_format)?;
    let body = blast::render_blast(&pack.blast, output_format)?;
    let sections = render_sections(
        format,
        Some(&pack.memories),
        Some(&pack.compass),
        Some(&pack.wiki),
    );
    if sections.is_empty() {
        return Ok(body);
    }
    Ok(format!(
        "{}\n\n{}\n",
        body.trim_end_matches('\n'),
        sections.trim_end_matches('\n')
    ))
}

/// Render pre-submit warnings; zero warnings without a reader failure render
/// as an empty string.
pub fn render_pre_submit(result: &PreSubmitResult, output_format: &str) -> Result<String, String> {
    let format = pack_format(output_format)?;
    if result.memories.entries.is_empty() && result.memories.error.is_none() {
        return Ok(String::new());
    }
    let rendered = render_sections(format, Some(&result.memories), None, None);
    Ok(rendered.trim_start_matches('\n').to_string())
}

fn pack_format(output_format: &str) -> Result<PackFormat, String> {
    match output_format {
        "text" => Ok(PackFormat::Text),
        "markdown" => Ok(PackFormat::Markdown),
        other => Err(format!("unsupported review pack format: {other}")),
    }
}

fn render_sections(
    format: PackFormat,
    memories: Option<&Section<MemoryEntry>>,
    compass: Option<&Section<CompassEntry>>,
    wiki: Option<&Section<WikiEntry>>,
) -> String {
    let mut lines: Vec<String> = Vec::new();
    if let Some(memories) = memories {
        match format {
            PackFormat::Text => {
                lines.push(String::new());
                lines.push("Memories:".into());
                if let Some(error) = &memories.error {
                    lines.push(format!("  (memory enrichment unavailable: {error})"));
                } else if memories.entries.is_empty() {
                    lines.push("  No changed symbols to key memories on.".into());
                }
                for entry in &memories.entries {
                    let header = format!("{} ({})", entry.seed, entry.file_path);
                    if entry.matches.is_empty() {
                        lines.push(format!("  {header}: no matching memories."));
                        continue;
                    }
                    lines.push(format!("  {header}:"));
                    for match_ in &entry.matches {
                        lines.push(
                            format!("    - [{}] {}", match_.kind, match_.title)
                                .trim_end()
                                .into(),
                        );
                        if !match_.body.is_empty() {
                            lines.push(py_indent(&match_.body, "      "));
                        }
                    }
                }
            }
            PackFormat::Markdown => {
                lines.push(String::new());
                lines.push("## Memories".into());
                lines.push(String::new());
                if let Some(error) = &memories.error {
                    lines.push(format!("_Memory enrichment unavailable: {error}_"));
                } else if memories.entries.is_empty() {
                    lines.push("_No changed symbols to key memories on._".into());
                }
                for entry in &memories.entries {
                    lines.push(format!("### `{}` (`{}`)", entry.seed, entry.file_path));
                    if entry.matches.is_empty() {
                        lines.push("- no matching memories".into());
                    }
                    for match_ in &entry.matches {
                        lines.push(
                            format!("- **[{}]** {}", match_.kind, match_.title)
                                .trim_end()
                                .into(),
                        );
                        if !match_.body.is_empty() {
                            lines.push(py_indent(&match_.body, "  "));
                        }
                    }
                    lines.push(String::new());
                }
            }
        }
    }
    if let Some(compass) = compass {
        match format {
            PackFormat::Text => {
                lines.push(String::new());
                lines.push("Module guides:".into());
                if let Some(error) = &compass.error {
                    lines.push(format!("  (compass enrichment unavailable: {error})"));
                } else if compass.entries.is_empty() {
                    lines.push("  No changed modules to look up.".into());
                }
                for entry in &compass.entries {
                    if !entry.found {
                        lines.push(format!("  {}: no compass guide.", entry.module));
                        continue;
                    }
                    lines.push(format!("  {}:", entry.module));
                    lines.push(py_indent(&entry.guide, "    "));
                }
            }
            PackFormat::Markdown => {
                lines.push(String::new());
                lines.push("## Module guides".into());
                lines.push(String::new());
                if let Some(error) = &compass.error {
                    lines.push(format!("_Compass enrichment unavailable: {error}_"));
                } else if compass.entries.is_empty() {
                    lines.push("_No changed modules to look up._".into());
                }
                for entry in &compass.entries {
                    if !entry.found {
                        lines.push(format!("- {}: no compass guide", entry.module));
                        continue;
                    }
                    lines.push(format!("### {}", entry.module));
                    lines.push(String::new());
                    lines.push(entry.guide.clone());
                    lines.push(String::new());
                }
            }
        }
    }
    if let Some(wiki) = wiki {
        match format {
            PackFormat::Text => {
                lines.push(String::new());
                lines.push("Wiki pages:".into());
                if let Some(error) = &wiki.error {
                    lines.push(format!("  (wiki enrichment unavailable: {error})"));
                } else if wiki.entries.is_empty() {
                    lines.push("  No changed modules to look up.".into());
                }
                for entry in &wiki.entries {
                    if !entry.found {
                        lines.push(format!("  {}: no wiki pages.", entry.module));
                        continue;
                    }
                    lines.push(format!("  {}:", entry.module));
                    lines.push(py_indent(&entry.pages, "    "));
                }
            }
            PackFormat::Markdown => {
                lines.push(String::new());
                lines.push("## Wiki".into());
                lines.push(String::new());
                if let Some(error) = &wiki.error {
                    lines.push(format!("_Wiki enrichment unavailable: {error}_"));
                } else if wiki.entries.is_empty() {
                    lines.push("_No changed modules to look up._".into());
                }
                for entry in &wiki.entries {
                    if !entry.found {
                        lines.push(format!("- {}: no wiki pages", entry.module));
                        continue;
                    }
                    lines.push(format!("### {}", entry.module));
                    lines.push(String::new());
                    lines.push(entry.pages.clone());
                    lines.push(String::new());
                }
            }
        }
    }
    lines.join("\n")
}

/// `textwrap.indent`: prefix every non-blank line.
fn py_indent(text: &str, prefix: &str) -> String {
    text.split('\n')
        .map(|line| {
            if line.trim().is_empty() {
                line.to_string()
            } else {
                format!("{prefix}{line}")
            }
        })
        .collect::<Vec<_>>()
        .join("\n")
}

// --- comment spans (`review/spans.py`) -------------------------------------------

pub struct SymbolSpan {
    pub name: String,
    pub qualified_name: Option<String>,
    pub kind: String,
    pub line_start: i64,
    pub line_end: i64,
}

pub struct CommentSymbols {
    pub file_path: String,
    pub symbols: Vec<SymbolSpan>,
}

impl CommentSymbols {
    pub fn file_level(&self) -> bool {
        self.symbols.is_empty()
    }
}

/// Resolve a comment location to the symbols whose inclusive spans enclose
/// `line`; `None` is file-level. Symbols with NULL spans never match.
pub fn resolve_comment_symbols(
    conn: &Connection,
    file_path: &str,
    line: Option<i64>,
    repo: Option<&str>,
) -> Result<CommentSymbols, String> {
    if file_path.trim().is_empty() {
        return Err("file_path must be a non-empty string".into());
    }
    if let Some(line) = line
        && line < 1
    {
        return Err(format!(
            "line must be a positive integer or None, got {line}"
        ));
    }
    let path = file_path.replace('\\', "/");
    let path = path.strip_prefix("./").unwrap_or(&path).to_string();
    let Some(line) = line else {
        return Ok(CommentSymbols {
            file_path: path,
            symbols: Vec::new(),
        });
    };
    let base_sql = "SELECT s.name, s.qualified_name, s.kind, s.line_start, s.line_end \
                    FROM symbols s JOIN files f ON f.id = s.file_id \
                    WHERE f.path = ? ";
    let sql = match repo {
        Some(_) => format!(
            "{base_sql}AND f.repo_id = ? AND s.line_start IS NOT NULL \
             AND s.line_end IS NOT NULL AND s.line_start <= ? AND s.line_end >= ? \
             ORDER BY (s.line_end - s.line_start), s.name"
        ),
        None => format!(
            "{base_sql}AND s.line_start IS NOT NULL \
             AND s.line_end IS NOT NULL AND s.line_start <= ? AND s.line_end >= ? \
             ORDER BY (s.line_end - s.line_start), s.name"
        ),
    };
    let symbols = conn
        .prepare(&sql)
        .and_then(|mut stmt| {
            let map_row = |row: &rusqlite::Row<'_>| {
                Ok(SymbolSpan {
                    name: row.get(0)?,
                    qualified_name: row.get(1)?,
                    kind: row.get(2)?,
                    line_start: row.get(3)?,
                    line_end: row.get(4)?,
                })
            };
            let rows = match repo {
                Some(repo) => stmt.query_map(rusqlite::params![path, repo, line, line], map_row),
                None => stmt.query_map(rusqlite::params![path, line, line], map_row),
            }?;
            rows.collect::<Result<Vec<_>, _>>()
        })
        .map_err(|e| e.to_string())?;
    Ok(CommentSymbols {
        file_path: path,
        symbols,
    })
}

// --- event capture (`review/capture.py`) -----------------------------------------

#[derive(Debug)]
pub struct ReviewEvent {
    pub resolved: bool,
    pub file_path: String,
    pub line: Option<i64>,
    pub body: String,
    pub thread_url: String,
    pub repo: Option<String>,
}

pub struct CaptureOutcome {
    pub recorded: bool,
    pub type_: Option<String>,
    pub path: Option<String>,
    pub tier: Option<String>,
    pub file_level: bool,
    pub symbols: Vec<String>,
    pub title: Option<String>,
}

/// Validate one event payload against the pinned shape; unknown fields are
/// ignored.
pub fn parse_event(payload: &Value) -> Result<ReviewEvent, String> {
    let Some(object) = payload.as_object() else {
        return Err("event payload must be a JSON object".into());
    };
    for field in ["resolved", "file", "line", "body", "thread_url"] {
        if !object.contains_key(field) {
            return Err(format!("event payload field '{field}' is required"));
        }
    }
    let resolved = object["resolved"]
        .as_bool()
        .ok_or("event payload field 'resolved' must be a boolean")?;
    let file_path = object["file"]
        .as_str()
        .ok_or("event payload field 'file' must be a non-empty string")?;
    if file_path.trim().is_empty() {
        return Err("event payload field 'file' must be a non-empty string".into());
    }
    let line = match &object["line"] {
        Value::Null => None,
        Value::Number(number) if number.is_i64() || number.is_u64() => {
            let value = number
                .as_i64()
                .or_else(|| number.as_u64().map(|u| u as i64))
                .unwrap_or_default();
            if value < 1 {
                return Err("event payload field 'line' must be a positive integer or null".into());
            }
            Some(value)
        }
        _ => return Err("event payload field 'line' must be a positive integer or null".into()),
    };
    let body = object["body"]
        .as_str()
        .ok_or("event payload field 'body' must be a string")?;
    let thread_url = object["thread_url"]
        .as_str()
        .ok_or("event payload field 'thread_url' must be a string")?;
    let repo = match object.get("repo") {
        None | Some(Value::Null) => None,
        Some(Value::String(value)) if !value.trim().is_empty() => Some(value.clone()),
        Some(_) => {
            return Err("event payload field 'repo' must be a non-empty string or null".into());
        }
    };
    Ok(ReviewEvent {
        resolved,
        file_path: file_path.to_string(),
        line,
        body: body.to_string(),
        thread_url: thread_url.to_string(),
        repo,
    })
}

/// Deterministic capture type: the marker thread label means pattern, any
/// other resolved comment records as a mistake.
pub fn classify_event(event: &ReviewEvent) -> &'static str {
    if event.body.contains(PATTERN_MARKER) {
        "pattern"
    } else {
        "mistake"
    }
}

/// Record one adapted comment event; an open (unresolved) comment records
/// nothing (`recorded: false`).
pub fn capture_review_event(
    conn: &Connection,
    bundle: &Bundle,
    payload: &Value,
) -> Result<CaptureOutcome, String> {
    let event = parse_event(payload)?;
    if !event.resolved {
        return Ok(CaptureOutcome {
            recorded: false,
            type_: None,
            path: None,
            tier: None,
            file_level: true,
            symbols: Vec::new(),
            title: None,
        });
    }
    let spans = resolve_comment_symbols(conn, &event.file_path, event.line, event.repo.as_deref())?;
    let memory_type = classify_event(&event);
    let title = build_title(&spans, &event);
    let stored = tools_km::capture_memory(
        conn,
        bundle,
        memory_type,
        &title,
        &build_body(&spans, &event),
        Some(&spans.file_path),
        CAPTURE_CONFIDENCE,
        None,
        None,
    )
    .map_err(|e| e.0)?;
    Ok(CaptureOutcome {
        recorded: true,
        type_: Some(memory_type.to_string()),
        path: Some(stored.path),
        tier: Some(stored.tier),
        file_level: spans.file_level(),
        symbols: spans
            .symbols
            .iter()
            .map(|s| s.qualified_name.clone().unwrap_or(s.name.clone()))
            .collect(),
        title: Some(title),
    })
}

fn pull_number(thread_url: &str) -> Option<&str> {
    let start = thread_url.find("/pull/")? + "/pull/".len();
    let rest = &thread_url[start..];
    let end = rest
        .find(|c: char| !c.is_ascii_digit())
        .unwrap_or(rest.len());
    if end == 0 {
        return None;
    }
    Some(&rest[..end])
}

/// Title carrying the keying and thread: `<file> [pull/N]: <summary>`.
fn build_title(spans: &CommentSymbols, event: &ReviewEvent) -> String {
    let mut summary = event
        .body
        .lines()
        .map(str::trim)
        .find(|line| !line.is_empty())
        .unwrap_or("")
        .to_string();
    if summary.is_empty() {
        summary = "review comment".into();
    }
    if summary.chars().count() > TITLE_SUMMARY_MAX_CHARS {
        let cut: String = summary.chars().take(TITLE_SUMMARY_MAX_CHARS).collect();
        summary = format!("{}...", cut.trim_end());
    }
    let prefix = match pull_number(&event.thread_url) {
        Some(pull) => format!("{} pull/{pull}: ", spans.file_path),
        None => format!("{}: ", spans.file_path),
    };
    format!("{prefix}{summary}")
}

/// Body carrying the comment text, the backtick-ref keying, and the thread link.
fn build_body(spans: &CommentSymbols, event: &ReviewEvent) -> String {
    let mut parts: Vec<String> = Vec::new();
    let body = event.body.trim();
    if !body.is_empty() {
        parts.push(body.to_string());
    }
    parts.push(format!("File: `{}`", spans.file_path));
    if !spans.symbols.is_empty() {
        let names = spans
            .symbols
            .iter()
            .map(|s| format!("`{}`", s.qualified_name.clone().unwrap_or(s.name.clone())))
            .collect::<Vec<_>>()
            .join(", ");
        parts.push(format!("Symbols: {names}"));
    }
    let thread_url = event.thread_url.trim();
    if !thread_url.is_empty() {
        parts.push(format!("Thread: {thread_url}"));
    }
    parts.join("\n")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn memory_entry(seed: &str, matches: Vec<MemoryMatch>) -> MemoryEntry {
        MemoryEntry {
            seed: seed.into(),
            file_path: format!("src/{seed}.py"),
            matches,
        }
    }

    #[test]
    fn pre_submit_renders_only_warning_matches() {
        let section = Section {
            entries: vec![memory_entry(
                "alpha",
                vec![MemoryMatch {
                    kind: "mistake".into(),
                    title: "warned".into(),
                    body: "body line".into(),
                }],
            )],
            error: None,
        };
        let result = PreSubmitResult { memories: section };
        assert!(has_blocking_matches(&result));
        let text = render_pre_submit(&result, "text").unwrap();
        assert_eq!(
            text,
            "Memories:\n  alpha (src/alpha.py):\n    - [mistake] warned\n      body line"
        );
        let md = render_pre_submit(&result, "markdown").unwrap();
        assert!(md.contains("- **[mistake]** warned"));
        assert!(md.contains("  body line"));
        assert!(!has_blocking_matches(&PreSubmitResult {
            memories: Section {
                entries: vec![memory_entry(
                    "beta",
                    vec![MemoryMatch {
                        kind: "decision".into(),
                        title: "not a warning".into(),
                        body: String::new(),
                    }],
                )],
                error: None,
            }
        }));
    }

    #[test]
    fn pre_submit_empty_renders_blank_and_gates_nothing() {
        let result = PreSubmitResult {
            memories: Section {
                entries: vec![],
                error: None,
            },
        };
        assert!(!has_blocking_matches(&result));
        assert_eq!(render_pre_submit(&result, "text").unwrap(), "");
        let errored = PreSubmitResult {
            memories: Section {
                entries: vec![memory_entry(
                    "alpha",
                    vec![MemoryMatch {
                        kind: "mistake".into(),
                        title: "unverified".into(),
                        body: String::new(),
                    }],
                )],
                error: Some("reader down".into()),
            },
        };
        assert!(!has_blocking_matches(&errored));
        assert!(
            render_pre_submit(&errored, "text")
                .unwrap()
                .contains("(memory enrichment unavailable: reader down)")
        );
    }

    #[test]
    fn render_pack_rejects_non_pack_formats() {
        let pack = ReviewPack {
            blast: BlastResult {
                basis_kind: "worktree".into(),
                basis_base: "HEAD".into(),
                basis_merge_base: None,
                changed_files: vec![],
                seeds: vec![],
                areas: vec![],
                radius: vec![],
                cycles: vec![],
                unindexed_files: vec![],
                deleted_files: vec![],
                truncated: false,
                stale_graph: String::new(),
                taint_paths: vec![],
            },
            memories: Section {
                entries: vec![],
                error: None,
            },
            compass: Section {
                entries: vec![],
                error: None,
            },
            wiki: Section {
                entries: vec![],
                error: None,
            },
        };
        assert_eq!(
            render_pack(&pack, "mermaid").unwrap_err(),
            "unsupported review pack format: mermaid"
        );
    }

    #[test]
    fn parse_event_validates_fields_in_order() {
        assert_eq!(
            parse_event(&serde_json::json!({})).unwrap_err(),
            "event payload field 'resolved' is required"
        );
        assert_eq!(
            parse_event(&serde_json::json!({
                "resolved": "yes", "file": "a.py", "line": null,
                "body": "b", "thread_url": "u",
            }))
            .unwrap_err(),
            "event payload field 'resolved' must be a boolean"
        );
        assert_eq!(
            parse_event(&serde_json::json!({
                "resolved": true, "file": "  ", "line": null,
                "body": "b", "thread_url": "u",
            }))
            .unwrap_err(),
            "event payload field 'file' must be a non-empty string"
        );
        assert_eq!(
            parse_event(&serde_json::json!({
                "resolved": true, "file": "a.py", "line": 0,
                "body": "b", "thread_url": "u",
            }))
            .unwrap_err(),
            "event payload field 'line' must be a positive integer or null"
        );
        assert_eq!(
            parse_event(&serde_json::json!({
                "resolved": true, "file": "a.py", "line": 3, "body": "b",
                "thread_url": "u", "repo": " ",
            }))
            .unwrap_err(),
            "event payload field 'repo' must be a non-empty string or null"
        );
        let event = parse_event(&serde_json::json!({
            "resolved": true, "file": "./a.py", "line": null, "body": "b",
            "thread_url": "u", "extra": 1,
        }))
        .unwrap();
        assert_eq!(event.file_path, "./a.py");
        assert!(event.repo.is_none());
    }

    #[test]
    fn capture_title_carries_pull_and_truncates() {
        let spans = CommentSymbols {
            file_path: "src/a.py".into(),
            symbols: Vec::new(),
        };
        let event = ReviewEvent {
            resolved: true,
            file_path: "src/a.py".into(),
            line: None,
            body: format!("{}  ", "x".repeat(90)),
            thread_url: "https://github.com/o/r/pull/1234".into(),
            repo: None,
        };
        let title = build_title(&spans, &event);
        assert_eq!(title, format!("src/a.py pull/1234: {}...", "x".repeat(80)));
        let no_pull = ReviewEvent {
            thread_url: "https://example.com/no/pulls".into(),
            body: "single line\nsecond".into(),
            ..event
        };
        assert_eq!(build_title(&spans, &no_pull), "src/a.py: single line");
        assert_eq!(classify_event(&no_pull), "mistake");
        let marked = ReviewEvent {
            body: "note cairn:pattern here".into(),
            ..no_pull
        };
        assert_eq!(classify_event(&marked), "pattern");
    }

    #[test]
    fn capture_body_lists_keying_and_thread() {
        let spans = CommentSymbols {
            file_path: "src/a.py".into(),
            symbols: vec![SymbolSpan {
                name: "alpha".into(),
                qualified_name: Some("mod.alpha".into()),
                kind: "function".into(),
                line_start: 1,
                line_end: 9,
            }],
        };
        let event = ReviewEvent {
            resolved: true,
            file_path: "src/a.py".into(),
            line: Some(5),
            body: "  fix this  ".into(),
            thread_url: " https://t/pull/9 ".into(),
            repo: None,
        };
        assert_eq!(
            build_body(&spans, &event),
            "fix this\nFile: `src/a.py`\nSymbols: `mod.alpha`\nThread: https://t/pull/9"
        );
    }

    #[test]
    fn seed_query_dedups_name_and_stem() {
        assert_eq!(seed_query("alpha", "src/alpha.py"), "alpha");
        assert_eq!(seed_query("alpha", "src/mod.py"), "alpha mod");
    }
}
