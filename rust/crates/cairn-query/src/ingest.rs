//! Staged knowledge ingestion: source adapters, classification, identity,
//! outbox staging, and the approved-run executor (knowledge/ingest/* port).

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use regex::Regex;
use rusqlite::Connection;
use serde_json::{Value, json};
use yaml_rust2::Yaml;
use yaml_rust2::yaml::Hash;

use crate::tasks::{
    DanglingPointer, KnowledgeIndexReport, StagedPointerRow, dangling_manifest_pointers,
    extract_relationships, normalize_relationships, queue_doc_link_tasks, rebuild_knowledge_index,
};
use crate::tools_km::{
    self, Bundle, Concept, ToolError, check_bundle, doc_type_slug, extract_file_refs,
    extract_symbol_refs, file_exists, list_documents, normalize_doc_id, search_knowledge_docs,
    slugify, strip_private_data, symbol_exists,
};

pub const INGEST_MAX_FILE_SIZE: u64 = 10 * 1024 * 1024;
pub const FED_ORIGIN: &str = "fed";
pub const FED_REPO: &str = "workspace";
pub const CONVERTED_ORIGIN: &str = "converted";
pub const CONVERT_SUFFIXES: [&str; 2] = [".pdf", ".docx"];
pub const MANIFEST_NAME: &str = "manifest.json";
const SLUG_MAX: usize = 60;

fn ys(s: impl Into<String>) -> Yaml {
    Yaml::String(s.into())
}

fn ystr(v: Option<&Yaml>) -> Option<String> {
    match v {
        Some(Yaml::String(s)) => Some(s.clone()),
        Some(Yaml::Real(s)) => Some(s.clone()),
        Some(Yaml::Integer(i)) => Some(i.to_string()),
        Some(Yaml::Boolean(b)) => Some(if *b { "True" } else { "False" }.into()),
        _ => None,
    }
}

// --- ingest config (ingest/config.py port) --------------------------------------

/// Workspace overrides layered over the ingest built-in defaults.
#[derive(Debug, Clone, Default)]
pub struct IngestConfig {
    pub classification: Vec<(String, String)>,
    pub skip_add: Vec<String>,
    pub skip_disable: Vec<String>,
}

/// Python's OSError str(): "[Errno <n>] <message>: '<path>'".
fn py_oserror_text(e: &std::io::Error, path: &str) -> String {
    let message = e
        .to_string()
        .trim_end_matches(&format!(
            " (os error {})",
            e.raw_os_error().unwrap_or_default()
        ))
        .to_string();
    match e.raw_os_error() {
        Some(code) => format!("[Errno {code}] {message}: '{path}'"),
        None => format!("{message}: '{path}'"),
    }
}

fn utf8_lead_len(b: u8) -> Option<usize> {
    match b {
        0x00..=0x7F => Some(1),
        0xC2..=0xDF => Some(2),
        0xE0..=0xEF => Some(3),
        0xF0..=0xF4 => Some(4),
        _ => None,
    }
}

/// Python's UnicodeDecodeError message for non-UTF-8 bytes; None when valid.
fn py_unicode_decode_error(bytes: &[u8]) -> Option<String> {
    let prefix = |start: usize, end: usize, reason: &str| -> String {
        if start == end {
            format!(
                "'utf-8' codec can't decode byte 0x{:02x} in position {start}: {reason}",
                bytes[start]
            )
        } else {
            format!("'utf-8' codec can't decode bytes in position {start}-{end}: {reason}")
        }
    };
    let mut i = 0;
    while i < bytes.len() {
        let lead = bytes[i];
        let Some(len) = utf8_lead_len(lead) else {
            return Some(prefix(i, i, "invalid start byte"));
        };
        if len == 1 {
            i += 1;
            continue;
        }
        let mut consumed_end = i;
        for k in 1..len {
            let Some(&c) = bytes.get(i + k) else {
                return Some(prefix(i, consumed_end, "unexpected end of data"));
            };
            if c & 0xC0 != 0x80 {
                return Some(prefix(i, consumed_end, "invalid continuation byte"));
            }
            consumed_end = i + k;
        }
        // Structurally complete but overlong, surrogate, or past U+10FFFF.
        let b1 = bytes[i + 1];
        if (lead == 0xE0 && b1 < 0xA0)
            || (lead == 0xED && b1 >= 0xA0)
            || (lead == 0xF0 && b1 < 0x90)
            || (lead == 0xF4 && b1 > 0x8F)
        {
            return Some(format!(
                "'utf-8' codec can't decode byte 0x{lead:02x} in position {i}: invalid continuation byte"
            ));
        }
        i += len;
    }
    None
}

/// Python's json.JSONDecodeError text for the shapes serde reports the same
/// position on; other shapes keep serde's own wording.
fn py_json_error_text(text: &str, e: &serde_json::Error) -> String {
    let raw = e.to_string();
    let head = raw.split(" at ").next().unwrap_or("").to_string();
    let mut eof_shaped = false;
    let message = match head.as_str() {
        "expected value" => Some("Expecting value"),
        "trailing characters" => Some("Extra data"),
        "key must be a string" => Some("Expecting property name enclosed in double quotes"),
        "EOF while parsing a value" | "expected ident" => {
            eof_shaped = true;
            Some("Expecting value")
        }
        "EOF while parsing an object" => {
            eof_shaped = true;
            Some("Expecting property name enclosed in double quotes")
        }
        _ => None,
    };
    let Some(message) = message else {
        return raw;
    };
    let at = |line_no: usize, byte_column: usize| -> usize {
        let mut byte = 0usize;
        for (i, line) in text.split('\n').enumerate() {
            if i + 1 == line_no {
                byte += byte_column.saturating_sub(1).min(line.len());
                break;
            }
            byte += line.len() + 1;
        }
        let mut boundary = byte.min(text.len());
        while boundary > 0 && !text.is_char_boundary(boundary) {
            boundary -= 1;
        }
        boundary
    };
    if eof_shaped {
        // EOF is always on the last line; an unfinished identifier is rejected
        // at its start, anything else stops after the last consumed character.
        let last = text.lines().next_back().unwrap_or("");
        let trimmed = last.trim_end_matches([' ', '\t', '\r']);
        let bytes = trimmed.as_bytes();
        let mut run_start = bytes.len();
        while run_start > 0
            && (bytes[run_start - 1].is_ascii_alphanumeric()
                || bytes[run_start - 1] == b'_'
                || bytes[run_start - 1] == b'-')
        {
            run_start -= 1;
        }
        let chars_before_last = text.chars().count() - last.chars().count();
        let line_no = text.lines().count().max(1);
        if run_start < bytes.len() {
            if bytes[run_start].is_ascii_digit() {
                return raw;
            }
            let in_line = trimmed[..run_start].chars().count();
            return format!(
                "{message}: line {line_no} column {} (char {})",
                in_line + 1,
                chars_before_last + in_line
            );
        }
        return format!(
            "{message}: line {line_no} column {} (char {})",
            last.chars().count() + 1,
            text.chars().count()
        );
    }
    let boundary = at(e.line(), e.column());
    let chars = text[..boundary].chars().count();
    format!(
        "{message}: line {} column {} (char {chars})",
        e.line(),
        chars + 1
    )
}

/// Read the ingest section of the workspace's cairn.json; malformed content
/// yields the warning lines Python's load_config prints.
pub fn load_ingest_config(workspace_root: &Path) -> IngestConfig {
    let (config, warnings) = ingest_config_with_warnings(workspace_root);
    for warning in &warnings {
        eprintln!("{warning}");
    }
    config
}

/// The pure loader behind `load_ingest_config` (warnings returned, not printed).
pub fn ingest_config_with_warnings(workspace_root: &Path) -> (IngestConfig, Vec<String>) {
    let mut warnings = Vec::new();
    let mut config = IngestConfig::default();
    let path = workspace_root.join("cairn.json");
    let shown = path.display().to_string();
    let bytes = match std::fs::read(&path) {
        Ok(bytes) => bytes,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return (config, warnings),
        Err(e) => {
            warnings.push(format!(
                "warning: {shown}: could not read ({}); ignoring config",
                py_oserror_text(&e, &shown)
            ));
            return (config, warnings);
        }
    };
    let text = match py_unicode_decode_error(&bytes) {
        Some(err) => {
            warnings.push(format!(
                "warning: {shown}: not valid UTF-8 ({err}); ignoring config"
            ));
            return (config, warnings);
        }
        None => String::from_utf8(bytes).unwrap_or_default(),
    };
    let raw = match serde_json::from_str::<Value>(&text) {
        Ok(raw) => raw,
        Err(e) => {
            warnings.push(format!(
                "warning: {shown}: invalid JSON ({}); ignoring config",
                py_json_error_text(&text, &e)
            ));
            return (config, warnings);
        }
    };
    let Some(obj) = raw.as_object() else {
        warnings.push(format!(
            "warning: {shown}: top-level JSON must be an object; ignoring config"
        ));
        return (config, warnings);
    };
    let Some(ingest) = obj.get("ingest").filter(|v| !v.is_null()) else {
        return (config, warnings);
    };
    let Some(ingest) = ingest.as_object() else {
        warnings.push(format!(
            "warning: {shown}: 'ingest' must be a JSON object; ignoring"
        ));
        return (config, warnings);
    };
    if let Some(rules) = ingest.get("classification").and_then(Value::as_object) {
        for (keyword, doc_type) in rules {
            let (keyword, doc_type) = (keyword.as_str(), doc_type.as_str());
            if let Some(doc_type) = doc_type {
                let (keyword, doc_type) = (keyword.trim(), doc_type.trim());
                if !keyword.is_empty() && !doc_type.is_empty() {
                    config
                        .classification
                        .push((keyword.to_lowercase(), doc_type.to_string()));
                }
            }
        }
    }
    if let Some(skip) = ingest.get("skip") {
        let strings = |v: Option<&Value>| -> Vec<String> {
            v.and_then(Value::as_array)
                .map(|items| {
                    items
                        .iter()
                        .filter_map(Value::as_str)
                        .map(str::to_string)
                        .filter(|s| !s.trim().is_empty())
                        .collect()
                })
                .unwrap_or_default()
        };
        config.skip_add = strings(skip.get("add"));
        config.skip_disable = strings(skip.get("disable"))
            .into_iter()
            .map(|c| c.to_lowercase())
            .collect();
    }
    (config, warnings)
}

// --- source parsing (ingest/parser.py port) -------------------------------------

/// Metadata and frontmatter-stripped body of one source document.
#[derive(Debug, Clone, Default)]
pub struct ParsedDoc {
    pub title: Option<String>,
    pub status: Option<String>,
    pub tags: Vec<String>,
    pub description: Option<String>,
    pub body: String,
    pub extensions: Hash,
}

struct ParserRegexes {
    bold_status: Regex,
    heading_status: Regex,
    heading_status_bare: Regex,
    heading: Regex,
    minimal_pair: Regex,
}

fn parser_regexes() -> &'static ParserRegexes {
    static RES: OnceLock<ParserRegexes> = OnceLock::new();
    RES.get_or_init(|| ParserRegexes {
        bold_status: Regex::new(r"^\s{0,3}\*\*\s*Status\s*:\s*\*\*\s*(.*)$").unwrap(),
        heading_status: Regex::new(r"^\s{0,3}#{1,6}\s*Status\s*:\s*(.+)$").unwrap(),
        heading_status_bare: Regex::new(r"^\s{0,3}#{1,6}\s*Status\s*:?$").unwrap(),
        heading: Regex::new(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$").unwrap(),
        minimal_pair: Regex::new(r"^([A-Za-z][A-Za-z0-9 ._-]*):\s*(.*)$").unwrap(),
    })
}

/// Parse a source document: metadata plus the frontmatter-stripped body.
pub fn parse_source_doc(text: &str) -> ParsedDoc {
    let re = parser_regexes();
    let (block, body) = split_source_frontmatter(text);
    let meta = frontmatter_metadata(block.as_deref(), re);
    let title = as_text(meta.get(&ys("title"))).or_else(|| first_heading(&body, re));
    let status = as_text(meta.get(&ys("status"))).or_else(|| inline_status(&body, re));
    let tags = as_tags(meta.get(&ys("tags")));
    let description = as_text(meta.get(&ys("description")));
    let mut rest = meta;
    for key in ["title", "status", "tags", "description"] {
        rest.remove(&ys(key));
    }
    ParsedDoc {
        title,
        status,
        tags,
        description,
        body,
        extensions: rest,
    }
}

fn split_source_frontmatter(text: &str) -> (Option<String>, String) {
    let lines: Vec<&str> = text.lines().collect();
    if lines.first().is_none_or(|l| l.trim() != "---") {
        return (None, text.to_string());
    }
    for (i, line) in lines.iter().enumerate().skip(1) {
        if line.trim() == "---" {
            let block = lines[1..i].join("\n");
            let body = lines[i + 1..]
                .join("\n")
                .trim_start_matches('\n')
                .to_string();
            return (Some(block), body);
        }
    }
    (None, text.to_string())
}

fn frontmatter_metadata(block: Option<&str>, re: &ParserRegexes) -> Hash {
    let Some(block) = block else {
        return Hash::new();
    };
    match yaml_rust2::YamlLoader::load_from_str(block) {
        Ok(docs) => match docs.into_iter().next() {
            Some(Yaml::Hash(hash)) => hash,
            _ => Hash::new(),
        },
        Err(_) => minimal_frontmatter(block, re),
    }
}

/// Recover clean `key: value` pairs (any key) from malformed YAML.
fn minimal_frontmatter(block: &str, re: &ParserRegexes) -> Hash {
    let mut meta = Hash::new();
    for raw_line in block.lines() {
        let Some(caps) = re.minimal_pair.captures(raw_line.trim()) else {
            continue;
        };
        let key = caps.get(1).unwrap().as_str().to_string();
        let mut value = caps.get(2).unwrap().as_str().trim().to_string();
        if value.is_empty() {
            continue;
        }
        let first = value.chars().next().unwrap_or_default();
        if first == '"' || first == '\'' {
            if value.chars().count() < 2 || !value.ends_with(first) {
                continue;
            }
            value = value[1..value.len() - 1].trim().to_string();
        } else if first == '[' || first == '{' {
            let closer = if first == '[' { ']' } else { '}' };
            if !value.ends_with(closer) {
                continue;
            }
        }
        if value.is_empty() {
            continue;
        }
        meta.insert(ys(key), ys(value));
    }
    meta
}

fn as_text(value: Option<&Yaml>) -> Option<String> {
    let text = ystr(value).unwrap_or_default().trim().to_string();
    (!text.is_empty()).then_some(text)
}

fn as_tags(value: Option<&Yaml>) -> Vec<String> {
    let items: Vec<Yaml> = match value {
        Some(Yaml::String(s)) => {
            let s = s.trim();
            let s = if s.len() >= 2 && s.starts_with('[') && s.ends_with(']') {
                &s[1..s.len() - 1]
            } else {
                s
            };
            return s
                .split(',')
                .map(|i| i.trim().to_string())
                .filter(|i| !i.is_empty())
                .collect();
        }
        Some(Yaml::Array(items)) => items.clone(),
        _ => return Vec::new(),
    };
    items
        .iter()
        .filter_map(|i| ystr(Some(i)))
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .collect()
}

/// Body lines outside ``` / ~~~ fences.
fn unfenced_lines(body: &str) -> Vec<&str> {
    let mut lines = Vec::new();
    let mut fence = "";
    for line in body.lines() {
        let stripped = line.trim();
        if !fence.is_empty() {
            if stripped.starts_with(fence) {
                fence = "";
            }
            continue;
        }
        if stripped.starts_with("```") || stripped.starts_with("~~~") {
            fence = &stripped[..3];
            continue;
        }
        lines.push(line);
    }
    lines
}

fn marker_value(re: &ParserRegexes, line: &str) -> Option<String> {
    if let Some(caps) = re.bold_status.captures(line) {
        return Some(caps.get(1).unwrap().as_str().trim().to_string());
    }
    if let Some(caps) = re.heading_status.captures(line) {
        return Some(caps.get(1).unwrap().as_str().trim().to_string());
    }
    if re.heading_status_bare.is_match(line) {
        return Some(String::new());
    }
    None
}

fn inline_status(body: &str, re: &ParserRegexes) -> Option<String> {
    let lines = unfenced_lines(body);
    for (i, line) in lines.iter().enumerate() {
        let Some(value) = marker_value(re, line) else {
            continue;
        };
        if !value.is_empty() {
            return Some(value);
        }
        // Bare marker: the value may sit on the next line, but a following
        // heading or marker ends the field instead of being captured.
        for follower in &lines[i + 1..] {
            let follower = follower.trim();
            if follower.is_empty() {
                continue;
            }
            if re.heading.is_match(follower) || marker_value(re, follower).is_some() {
                break;
            }
            return Some(follower.to_string());
        }
        return None;
    }
    None
}

fn first_heading(body: &str, re: &ParserRegexes) -> Option<String> {
    for line in unfenced_lines(body) {
        if let Some(caps) = re.heading.captures(line) {
            return Some(caps.get(1).unwrap().as_str().trim().to_string());
        }
    }
    None
}

// --- classification (ingest/classifier.py port) ----------------------------------

const SKIP_STATUSES: [&str; 5] = ["draft", "proposed", "review", "superseded", "deprecated"];
const DECISION_DIR_TOKENS: [&str; 4] = ["decisions", "decision", "adr", "adrs"];

/// doc_type, extra tags, and skip reason for one source document.
#[derive(Debug, Clone)]
pub struct Classification {
    pub doc_type: String,
    pub extra_tags: Vec<String>,
    pub skip_reason: Option<String>,
}

/// fnmatch-style shell pattern match (the `fnmatch` subset SKIP_LIST uses).
fn fnmatch_name(name: &str, pattern: &str) -> bool {
    let mut re = String::from("(?s)^");
    let mut chars = pattern.chars().peekable();
    while let Some(c) = chars.next() {
        match c {
            '*' => re.push_str(".*"),
            '?' => re.push('.'),
            '[' => {
                let mut class = String::from("[");
                let mut first = true;
                for c in chars.by_ref() {
                    if c == ']' && !first {
                        break;
                    }
                    first = false;
                    if c == '!' && class.len() == 1 {
                        class.push('^');
                    } else {
                        class.push(c);
                    }
                }
                class.push(']');
                re.push_str(&class);
            }
            other => re.push_str(&regex::escape(&other.to_string())),
        }
    }
    re.push('$');
    Regex::new(&re).is_ok_and(|r| r.is_match(name))
}

fn tokens(text: &str) -> Vec<String> {
    text.to_lowercase()
        .split(|c: char| !c.is_ascii_lowercase() && !c.is_ascii_digit())
        .filter(|t| !t.is_empty())
        .map(str::to_string)
        .collect()
}

fn normalized(text: &str) -> String {
    tokens(text).join(" ")
}

/// Classify a document and return its draft-gate disposition.
pub fn classify_doc(
    parsed: &ParsedDoc,
    relpath: &str,
    include_drafts: bool,
    rules: &IngestConfig,
) -> Classification {
    // (doc_type, extra_tags, kind_tokens, phrases), checked in order.
    type KindRule = (
        &'static str,
        &'static [&'static str],
        &'static [&'static str],
        &'static [&'static str],
    );
    const KIND_RULES: &[KindRule] = &[
        (
            "decision",
            &[],
            &["adr", "decision", "decisions", "finding", "findings"],
            &[],
        ),
        (
            "spec",
            &[],
            &[
                "feat",
                "feats",
                "spec",
                "specs",
                "specification",
                "specifications",
                "proposal",
                "proposals",
                "design",
                "designs",
                "uc",
                "ucs",
            ],
            &["component spec", "use case"],
        ),
        (
            "workflow",
            &[],
            &["guide", "guides", "runbook", "runbooks", "setup", "setups"],
            &[],
        ),
        (
            "business-rule",
            &[],
            &["convention", "conventions", "standard", "standards"],
            &["code standard", "agent instruction"],
        ),
        (
            "spec",
            &["reference"],
            &["vision", "architecture"],
            &["prior art"],
        ),
    ];

    let mut doc_type = "spec".to_string();
    let mut extra_tags: Vec<String> = Vec::new();
    let matched: Option<(&str, &[&str])> = (|| {
        let title = parsed.title.as_deref()?;
        let title_tokens: HashSet<String> = tokens(title).into_iter().collect();
        let text = format!(" {} ", normalized(title));
        for (keyword, rule_type) in &rules.classification {
            let key = normalized(keyword);
            if !key.is_empty()
                && (title_tokens.contains(&key) || text.contains(&format!(" {key} ")))
            {
                return Some((rule_type.as_str(), &[] as &[&str]));
            }
        }
        for (rule_type, rule_tags, kind_tokens, phrases) in KIND_RULES {
            if kind_tokens.iter().any(|t| title_tokens.contains(*t))
                || phrases.iter().any(|p| normalized(title).contains(p))
            {
                return Some((rule_type, rule_tags));
            }
        }
        None
    })();
    match matched {
        Some((rule_type, rule_tags)) => {
            doc_type = rule_type.to_string();
            extra_tags = rule_tags.iter().map(|t| t.to_string()).collect();
        }
        None => {
            if let Some(relpath_type) = classify_relpath(relpath) {
                doc_type = relpath_type;
            }
        }
    }
    let mut skip_reason = None;
    let status = parsed
        .status
        .as_deref()
        .unwrap_or_default()
        .trim()
        .to_lowercase();
    if SKIP_STATUSES.contains(&status.as_str()) {
        if include_drafts {
            extra_tags.push("draft".to_string());
        } else {
            skip_reason = Some(format!("status: {status}"));
        }
    }
    Classification {
        doc_type,
        extra_tags,
        skip_reason,
    }
}

fn classify_relpath(relpath: &str) -> Option<String> {
    let normalized_path = relpath.replace('\\', "/");
    let segments: Vec<&str> = normalized_path
        .split('/')
        .filter(|s| !s.is_empty())
        .collect();
    if segments.is_empty() {
        return None;
    }
    let mut dir_tokens: HashSet<String> = HashSet::new();
    for segment in &segments[..segments.len() - 1] {
        dir_tokens.extend(tokens(segment));
    }
    if dir_tokens
        .iter()
        .any(|t| DECISION_DIR_TOKENS.contains(&t.as_str()))
    {
        return Some("decision".into());
    }
    let filename = segments[segments.len() - 1];
    let stem = filename
        .rsplit_once('.')
        .map(|(s, _)| s)
        .unwrap_or(filename);
    let digits: String = stem.chars().take_while(|c| c.is_ascii_digit()).collect();
    if (digits.len() == 3 || digits.len() == 4) && stem[digits.len()..].starts_with('-') {
        return Some("decision".into());
    }
    let name_tokens: HashSet<String> = tokens(stem).into_iter().collect();
    if name_tokens.contains("adr") {
        return Some("decision".into());
    }
    if name_tokens.contains("feat") {
        return Some("spec".into());
    }
    None
}

// --- identity (ingest/identity.py port) ------------------------------------------

/// Derived identity of one source document.
#[derive(Debug, Clone)]
pub struct DocIdentity {
    pub stable_id: String,
    pub title: String,
    pub slug: String,
    pub tags: Vec<String>,
    pub affects_repos: Vec<String>,
    pub affects_modules: Vec<String>,
    pub description: String,
}

/// Derive one document's display identity and optional collision slug.
pub fn build_identity(
    repo: &str,
    relpath: &str,
    parsed: &ParsedDoc,
    seen_slugs: Option<&mut HashSet<String>>,
) -> DocIdentity {
    let stable_id = stable_id(repo, relpath);
    let title = display_title(&stable_id, parsed.title.as_deref());
    let mut slug = slugify(&title);
    if let Some(seen) = seen_slugs {
        if seen.contains(&slug) {
            slug = unique_slug(&slug, repo, seen);
        }
        seen.insert(slug.clone());
    }
    let mut tags = parsed.tags.clone();
    for extra in [stable_id.clone(), repo.to_string()] {
        if !extra.is_empty() && !tags.contains(&extra) {
            tags.push(extra);
        }
    }
    let parent = Path::new(relpath)
        .parent()
        .map(|p| p.to_string_lossy().replace('\\', "/"))
        .unwrap_or_default();
    let affects_modules = if parent.is_empty() || parent == "." {
        Vec::new()
    } else {
        vec![parent]
    };
    DocIdentity {
        stable_id,
        title,
        slug,
        tags,
        affects_repos: vec![repo.to_string()],
        affects_modules,
        description: identity_description(repo, relpath, parsed),
    }
}

/// A path slug, digest-anchored when needed to fit the cap.
fn stable_id(repo: &str, relpath: &str) -> String {
    let full = non_alnum_slug(&format!("{repo}/{relpath}"));
    if full.chars().count() <= SLUG_MAX {
        return full;
    }
    let hash = sha1_hex8(full.as_bytes());
    let room = SLUG_MAX.saturating_sub(hash.chars().count() + 1);
    let trimmed: String = full.chars().take(room).collect();
    let trimmed = trimmed.trim_end_matches('-');
    if trimmed.is_empty() {
        hash
    } else {
        format!("{trimmed}-{hash}")
    }
}

/// Minimal SHA-1 (the identity digest anchor; no sha1 crate in the tree).
fn sha1_hex8(data: &[u8]) -> String {
    let mut h: [u32; 5] = [
        0x6745_2301,
        0xEFCD_AB89,
        0x98BA_DCFE,
        0x1032_5476,
        0xC3D2_E1F0,
    ];
    let ml = (data.len() as u64) * 8;
    let mut msg = data.to_vec();
    msg.push(0x80);
    while msg.len() % 64 != 56 {
        msg.push(0);
    }
    msg.extend_from_slice(&ml.to_be_bytes());
    for block in msg.chunks(64) {
        let mut w = [0u32; 80];
        for (i, word) in block.chunks(4).enumerate() {
            w[i] = u32::from_be_bytes([word[0], word[1], word[2], word[3]]);
        }
        for i in 16..80 {
            w[i] = (w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16]).rotate_left(1);
        }
        let (mut a, mut b, mut c, mut d, mut e) = (h[0], h[1], h[2], h[3], h[4]);
        for (i, wi) in w.iter().enumerate() {
            let (f, k) = match i {
                0..=19 => ((b & c) | ((!b) & d), 0x5A82_7999u32),
                20..=39 => (b ^ c ^ d, 0x6ED9_EBA1),
                40..=59 => ((b & c) | (b & d) | (c & d), 0x8F1B_BCDC),
                _ => (b ^ c ^ d, 0xCA62_C1D6),
            };
            let temp = a
                .rotate_left(5)
                .wrapping_add(f)
                .wrapping_add(e)
                .wrapping_add(k)
                .wrapping_add(*wi);
            e = d;
            d = c;
            c = b.rotate_left(30);
            b = a;
            a = temp;
        }
        h[0] = h[0].wrapping_add(a);
        h[1] = h[1].wrapping_add(b);
        h[2] = h[2].wrapping_add(c);
        h[3] = h[3].wrapping_add(d);
        h[4] = h[4].wrapping_add(e);
    }
    h.iter()
        .flat_map(|x| x.to_be_bytes())
        .map(|b| format!("{b:02x}"))
        .collect::<String>()[..8]
        .to_string()
}

fn non_alnum_slug(text: &str) -> String {
    let lower = text.to_lowercase();
    let mut out = String::new();
    let mut prev_dash = true;
    for c in lower.chars() {
        if c.is_ascii_lowercase() || c.is_ascii_digit() {
            out.push(c);
            prev_dash = false;
        } else if !prev_dash {
            out.push('-');
            prev_dash = true;
        }
    }
    out.trim_matches('-').to_string()
}

fn display_title(stable_id: &str, doc_title: Option<&str>) -> String {
    let Some(doc_title) = doc_title.filter(|t| !t.is_empty()) else {
        return stable_id.to_string();
    };
    let budget = SLUG_MAX as i64 - stable_id.chars().count() as i64 - 1;
    if budget <= 0 {
        return stable_id.to_string();
    }
    format!(
        "{stable_id} — {}",
        doc_title.chars().take(budget as usize).collect::<String>()
    )
}

fn unique_slug(base: &str, repo: &str, seen: &HashSet<String>) -> String {
    let collision = |counter: usize| -> String {
        let mut parts = vec![slugify(repo)];
        if counter > 1 {
            parts.push(counter.to_string());
        }
        let suffix = parts
            .into_iter()
            .filter(|p| !p.is_empty())
            .collect::<Vec<_>>()
            .join("-");
        let room = SLUG_MAX.saturating_sub(suffix.chars().count() + 1);
        if room == 0 {
            return suffix;
        }
        let trimmed: String = base.chars().take(room).collect();
        let trimmed = trimmed.trim_end_matches('-');
        if trimmed.is_empty() {
            suffix
        } else {
            format!("{trimmed}-{suffix}")
        }
    };
    let mut counter = 1;
    let mut candidate = collision(counter);
    while seen.contains(&candidate) {
        counter += 1;
        candidate = collision(counter);
    }
    candidate
}

fn identity_description(repo: &str, relpath: &str, parsed: &ParsedDoc) -> String {
    if let Some(desc) = parsed.description.as_deref().filter(|d| !d.is_empty()) {
        return desc.to_string();
    }
    if let Some(paragraph) = first_meaningful_paragraph(&parsed.body) {
        return paragraph;
    }
    format!("Imported from {repo}/{relpath}")
}

fn first_meaningful_paragraph(body: &str) -> Option<String> {
    let re = parser_regexes();
    let mut lines: Vec<String> = Vec::new();
    let mut fence = "";
    for raw in body.lines() {
        let stripped = raw.trim();
        if !fence.is_empty() {
            if stripped.starts_with(fence) {
                fence = "";
            }
            continue;
        }
        if stripped.starts_with("```") || stripped.starts_with("~~~") {
            fence = &stripped[..3];
            continue;
        }
        if stripped.is_empty() {
            if !lines.is_empty() {
                break;
            }
            continue;
        }
        if lines.is_empty()
            && (re.heading.is_match(stripped) || marker_value(re, stripped).is_some())
        {
            continue;
        }
        lines.push(stripped.to_string());
    }
    (!lines.is_empty()).then(|| lines.join(" "))
}

// --- source adapters (ingest/adapters.py port) -----------------------------------

/// The adapter yield contract: (repo, relpath, text, origin).
type SourcedDoc = (String, String, String, String);
/// Adapter-level skips as (relpath, reason) pairs.
type AdapterSkips = Vec<(String, String)>;

fn size_skip_reason(path: &Path) -> Option<String> {
    match std::fs::metadata(path) {
        Err(e) => Some(format!("unreadable: {e}")),
        Ok(meta) => {
            let size = meta.len();
            (size > INGEST_MAX_FILE_SIZE)
                .then(|| format!("file too large ({size} > {INGEST_MAX_FILE_SIZE} bytes)"))
        }
    }
}

fn read_utf8(path: &Path) -> Result<String, String> {
    std::fs::read_to_string(path).map_err(|e| e.to_string())
}

/// Fed markdown and directory documents; skips are returned alongside.
fn fed_markdown_docs(paths: &[PathBuf]) -> Result<(Vec<SourcedDoc>, AdapterSkips), String> {
    let mut docs = Vec::new();
    let mut skipped = Vec::new();
    for path in paths {
        if !path.exists() {
            return Err(format!("Fed path does not exist: {}", path.display()));
        }
        if path.is_dir() {
            let mut md_files: Vec<PathBuf> = walk_md_files(path);
            md_files.sort();
            for md_file in md_files {
                let relpath = md_file
                    .strip_prefix(path)
                    .map(|p| p.to_string_lossy().replace('\\', "/"))
                    .unwrap_or_else(|_| md_file.to_string_lossy().to_string());
                if let Some(doc) = yield_document(&md_file, &relpath, &mut skipped) {
                    docs.push(doc);
                }
            }
        } else if path
            .extension()
            .is_some_and(|e| e.eq_ignore_ascii_case("md"))
        {
            let relpath = path.to_string_lossy().replace('\\', "/");
            if let Some(doc) = yield_document(path, &relpath, &mut skipped) {
                docs.push(doc);
            }
        } else {
            let suffix = path
                .extension()
                .map(|e| format!(".{}", e.to_string_lossy()))
                .unwrap_or_default();
            let reason = if suffix.is_empty() {
                "unsupported type: no suffix".to_string()
            } else {
                format!("unsupported type: {suffix}")
            };
            skipped.push((path.to_string_lossy().to_string(), reason));
        }
    }
    Ok((docs, skipped))
}

fn yield_document(
    path: &Path,
    relpath: &str,
    skipped: &mut Vec<(String, String)>,
) -> Option<SourcedDoc> {
    if let Some(reason) = size_skip_reason(path) {
        skipped.push((relpath.to_string(), reason));
        return None;
    }
    match read_utf8(path) {
        Ok(text) => Some((
            FED_REPO.to_string(),
            relpath.to_string(),
            text,
            FED_ORIGIN.to_string(),
        )),
        Err(e) => {
            skipped.push((relpath.to_string(), format!("unreadable: {e}")));
            None
        }
    }
}

fn walk_md_files(root: &Path) -> Vec<PathBuf> {
    let mut out = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let Ok(entries) = std::fs::read_dir(&dir) else {
            continue;
        };
        for entry in entries.flatten() {
            let p = entry.path();
            if p.is_dir() {
                stack.push(p);
            } else if p.extension().is_some_and(|e| e.eq_ignore_ascii_case("md")) {
                out.push(p);
            }
        }
    }
    out
}

/// Binary documents convert natively; PDFs prefer the optional
/// `cairn_intel_ingest` sidecar when its interpreter is importable.
fn fed_binary_docs(paths: &[PathBuf]) -> Result<(Vec<SourcedDoc>, AdapterSkips), String> {
    if paths.is_empty() {
        return Ok((Vec::new(), Vec::new()));
    }
    let needs_sidecar = paths.iter().any(|p| convert_suffix(p) == ".pdf");
    let sidecar = needs_sidecar
        .then(|| crate::sidecar::python_with("cairn_intel_ingest"))
        .flatten();
    fed_binary_docs_with(paths, sidecar.as_deref())
}

/// The lowercased ".ext" suffix of `path`, or "".
fn convert_suffix(path: &Path) -> String {
    path.extension()
        .map(|e| format!(".{}", e.to_string_lossy().to_lowercase()))
        .unwrap_or_default()
}

fn fed_binary_docs_with(
    paths: &[PathBuf],
    sidecar: Option<&Path>,
) -> Result<(Vec<SourcedDoc>, AdapterSkips), String> {
    let mut docs = Vec::new();
    let mut skipped = Vec::new();
    for path in paths {
        if !path.exists() {
            return Err(format!("Fed path does not exist: {}", path.display()));
        }
        let suffix = convert_suffix(path);
        if !CONVERT_SUFFIXES.contains(&suffix.as_str()) {
            continue;
        }
        let relpath = path.to_string_lossy().replace('\\', "/");
        let outcome = match (suffix.as_str(), sidecar) {
            (".pdf", Some(python)) => convert_with_sidecar(python, path),
            _ => convert_natively(path, &suffix),
        };
        match outcome {
            Ok(markdown) => docs.push((
                FED_REPO.to_string(),
                relpath,
                markdown,
                CONVERTED_ORIGIN.to_string(),
            )),
            Err(reason) => skipped.push((relpath, reason)),
        }
    }
    Ok((docs, skipped))
}

/// Native docx/PDF conversion under the ingest size bound.
fn convert_natively(path: &Path, suffix: &str) -> Result<String, String> {
    if let Some(reason) = size_skip_reason(path) {
        return Err(reason);
    }
    let bytes = std::fs::read(path).map_err(|e| format!("conversion failed: {e}"))?;
    crate::convert::convert_document(suffix, &bytes)
}

/// `python -m cairn_intel_ingest <path>`: markdown on stdout, or the skip
/// reason on stderr with a non-zero exit.
fn convert_with_sidecar(python: &Path, path: &Path) -> Result<String, String> {
    let output = std::process::Command::new(python)
        .args(["-m", "cairn_intel_ingest"])
        .arg(path)
        .output()
        .map_err(|e| format!("conversion failed: {e}"))?;
    if !output.status.success() {
        let reason = String::from_utf8_lossy(&output.stderr).trim().to_string();
        return Err(if reason.is_empty() {
            format!(
                "conversion failed: exit {}",
                output.status.code().unwrap_or(-1)
            )
        } else {
            reason
        });
    }
    let markdown = String::from_utf8_lossy(&output.stdout).into_owned();
    if markdown.trim().is_empty() {
        return Err("conversion returned no markdown".to_string());
    }
    Ok(markdown)
}

const DEFAULT_DOC_DIRS: [&str; 4] = ["docs", "decisions", "adr", "adrs"];

#[derive(Debug, Clone)]
struct SkipRule {
    kind: &'static str,
    pattern: String,
    reason: String,
}

fn skip_rules(add: &[String], disable: &[String]) -> Vec<SkipRule> {
    const SKIP_LIST: &[(&str, &str, &str, &str)] = &[
        ("dir", "drafts", "skip-list: drafts directory", "drafts"),
        (
            "dir",
            "meetings",
            "skip-list: meetings directory",
            "meetings",
        ),
        (
            "dir",
            "*_generated",
            "skip-list: generated mirror directory",
            "generated",
        ),
        (
            "dir",
            "changelogs",
            "skip-list: changelogs directory",
            "changelogs",
        ),
        (
            "dir",
            "templates",
            "skip-list: templates directory",
            "templates",
        ),
        (
            "file",
            "changelog*",
            "skip-list: changelog file",
            "changelogs",
        ),
        ("file", "template*", "skip-list: template file", "templates"),
        (
            "file",
            "*-template.md",
            "skip-list: template file",
            "templates",
        ),
        (
            "file",
            "meeting-notes*",
            "skip-list: meeting-notes file",
            "meetings",
        ),
    ];
    let disabled: HashSet<&str> = disable.iter().map(String::as_str).collect();
    let mut rules: Vec<SkipRule> = SKIP_LIST
        .iter()
        .filter(|(_, _, _, category)| !disabled.contains(category))
        .map(|(kind, pattern, reason, _category)| SkipRule {
            kind,
            pattern: pattern.to_string(),
            reason: reason.to_string(),
        })
        .collect();
    for pattern in add {
        let kind = if pattern.ends_with('/') {
            "dir"
        } else {
            "file"
        };
        rules.push(SkipRule {
            kind,
            pattern: pattern.trim_end_matches('/').to_lowercase(),
            reason: format!("skip-list: {pattern} (workspace)"),
        });
    }
    rules
}

fn skip_reason(rel: &str, rules: &[SkipRule]) -> Option<String> {
    let parts: Vec<&str> = rel.split('/').collect();
    let filename = parts.last().copied().unwrap_or_default().to_lowercase();
    for rule in rules {
        if rule.kind == "dir" {
            if dir_rule_matches(&parts, &rule.pattern) {
                return Some(rule.reason.clone());
            }
        } else if fnmatch_name(&filename, &rule.pattern) {
            return Some(rule.reason.clone());
        }
    }
    None
}

fn dir_rule_matches(parts: &[&str], pattern: &str) -> bool {
    if parts.len() < 2 {
        return false;
    }
    let segments: Vec<String> = parts[..parts.len() - 1]
        .iter()
        .map(|p| p.to_lowercase())
        .collect();
    if segments.iter().any(|s| fnmatch_name(s, pattern)) {
        return true;
    }
    (2..=segments.len()).any(|i| fnmatch_name(&segments[..i].join("/"), pattern))
}

fn repo_scan_docs(
    repo_root: &Path,
    add: &[String],
    disable: &[String],
) -> Result<(Vec<SourcedDoc>, AdapterSkips), String> {
    if !repo_root.is_dir() {
        return Err(format!("Repo root does not exist: {}", repo_root.display()));
    }
    let rules = skip_rules(add, disable);
    let repo = repo_root
        .file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_default();
    let mut docs = Vec::new();
    let mut skipped = Vec::new();
    for doc_dir in DEFAULT_DOC_DIRS {
        let root = repo_root.join(doc_dir);
        if !root.is_dir() {
            continue;
        }
        let mut md_files = walk_md_files(&root);
        md_files.sort();
        for md_file in md_files {
            let rel = md_file
                .strip_prefix(repo_root)
                .map(|p| p.to_string_lossy().replace('\\', "/"))
                .unwrap_or_default();
            let mut reason = skip_reason(&rel, &rules);
            if reason.is_none() {
                reason = size_skip_reason(&md_file);
            }
            if let Some(reason) = reason {
                skipped.push((rel.clone(), reason));
                continue;
            }
            match read_utf8(&md_file) {
                Ok(text) => docs.push((repo.clone(), rel, text, repo.clone())),
                Err(e) => skipped.push((rel, format!("unreadable: {e}"))),
            }
        }
    }
    Ok((docs, skipped))
}

// --- ADR supersede detection (ingest/adr.py port) --------------------------------

fn adr_regexes() -> &'static (Regex, Regex, Regex) {
    static RES: OnceLock<(Regex, Regex, Regex)> = OnceLock::new();
    RES.get_or_init(|| {
        (
            Regex::new(r"(?i)\bsupersedes?\b[\s:]+(?:adr[\s.-]*)?#?(\d{3,4})\b").unwrap(),
            Regex::new(r"(?i)\bsuperseded[\s-]+by\b[\s:]+(?:adr[\s.-]*)?#?(\d{3,4})\b").unwrap(),
            Regex::new(r"^(\d{3,4})-").unwrap(),
        )
    })
}

fn dir_of(relpath: &str) -> String {
    let normalized_path = relpath.replace('\\', "/");
    let segments: Vec<&str> = normalized_path
        .split('/')
        .filter(|s| !s.is_empty())
        .collect();
    segments[..segments.len().saturating_sub(1)].join("/")
}

fn staged_concept_id(entry: &StagedEntry) -> String {
    format!(
        "knowledge/{}/{}",
        doc_type_slug(&entry.classification.doc_type),
        entry.identity.slug
    )
}

fn marker_text(parsed: &ParsedDoc) -> String {
    let mut lines: Vec<String> = Vec::new();
    let mut fence = "";
    for line in parsed.body.lines() {
        let stripped = line.trim();
        if !fence.is_empty() {
            if stripped.starts_with(fence) {
                fence = "";
            }
            continue;
        }
        if stripped.starts_with("```") || stripped.starts_with("~~~") {
            fence = &stripped[..3];
            continue;
        }
        lines.push(line.to_string());
    }
    if let Some(status) = &parsed.status {
        lines.push(status.clone());
    }
    lines.join("\n")
}

fn adr_number(entry: &StagedEntry) -> Option<String> {
    let normalized_path = entry.relpath.replace('\\', "/");
    let segments: Vec<&str> = normalized_path
        .split('/')
        .filter(|s| !s.is_empty())
        .collect();
    let filename = segments.last()?;
    let stem = filename
        .rsplit_once('.')
        .map(|(s, _)| s)
        .unwrap_or(filename);
    let number = adr_regexes().2.captures(stem)?.get(1)?.as_str().to_string();
    let parent = &segments[..segments.len() - 1];
    if !parent.is_empty() {
        let mut dir_tokens: HashSet<String> = HashSet::new();
        for segment in parent {
            dir_tokens.extend(tokens(segment));
        }
        if !dir_tokens
            .iter()
            .any(|t| DECISION_DIR_TOKENS.contains(&t.as_str()))
        {
            return None;
        }
    }
    Some(number)
}

/// Accepted bidirectional supersede entries keyed by (repo, relpath).
fn detect_supersede_relationships(entries: &[StagedEntry]) -> HashMap<(String, String), Vec<Yaml>> {
    let (supersedes_re, superseded_by_re, _) = adr_regexes();
    let accepted: Vec<&StagedEntry> = entries
        .iter()
        .filter(|e| e.classification.skip_reason.is_none())
        .collect();
    let mut numbers: HashMap<(String, String, String), String> = HashMap::new();
    let mut sorted_accepted = accepted.clone();
    sorted_accepted.sort_by(|a, b| (&a.repo, &a.relpath).cmp(&(&b.repo, &b.relpath)));
    for entry in &sorted_accepted {
        if let Some(number) = adr_number(entry) {
            numbers
                .entry((entry.repo.clone(), dir_of(&entry.relpath), number))
                .or_insert_with(|| staged_concept_id(entry));
        }
    }
    let ids: HashMap<(String, String), String> = accepted
        .iter()
        .map(|e| ((e.repo.clone(), e.relpath.clone()), staged_concept_id(e)))
        .collect();
    let mut keys_by_id: HashMap<String, (String, String)> = HashMap::new();
    let mut sorted_keys: Vec<&(String, String)> = ids.keys().collect();
    sorted_keys.sort();
    for key in sorted_keys {
        keys_by_id
            .entry(ids.get(key).cloned().unwrap_or_default())
            .or_insert_with(|| key.clone());
    }
    let mut edges: HashSet<(String, String)> = HashSet::new();
    for entry in &accepted {
        let Some(doc_id) = ids.get(&(entry.repo.clone(), entry.relpath.clone())) else {
            continue;
        };
        let family = (entry.repo.clone(), dir_of(&entry.relpath));
        let text = marker_text(&entry.parsed);
        for (pattern, forward) in [(supersedes_re, true), (superseded_by_re, false)] {
            for caps in pattern.captures_iter(&text) {
                let target = caps.get(1).unwrap().as_str();
                let Some(target_id) =
                    numbers.get(&(family.0.clone(), family.1.clone(), target.to_string()))
                else {
                    continue;
                };
                if target_id == doc_id {
                    continue;
                }
                edges.insert(if forward {
                    (doc_id.clone(), target_id.clone())
                } else {
                    (target_id.clone(), doc_id.clone())
                });
            }
        }
    }
    let mut sorted_edges: Vec<(String, String)> = edges.into_iter().collect();
    sorted_edges.sort();
    let mut detected: HashMap<(String, String), Vec<Yaml>> = HashMap::new();
    let entry_for = |_id: &str, other: &str, relation: &str| -> Yaml {
        let mut h = Hash::new();
        h.insert(ys("concept_id"), ys(other));
        h.insert(ys("relation"), ys(relation));
        h.insert(ys("kind"), ys("extracted"));
        Yaml::Hash(h)
    };
    for (new_id, old_id) in sorted_edges {
        if let Some(key) = keys_by_id.get(&new_id) {
            detected.entry(key.clone()).or_default().push(entry_for(
                &new_id,
                &old_id,
                "supersedes",
            ));
        }
        if let Some(key) = keys_by_id.get(&old_id) {
            detected.entry(key.clone()).or_default().push(entry_for(
                &old_id,
                &new_id,
                "superseded-by",
            ));
        }
    }
    detected
}

// --- staging (ingest/staging.py port) ---------------------------------------------

/// One source document ready to stage.
pub struct StagedEntry {
    pub repo: String,
    pub relpath: String,
    pub origin: String,
    pub parsed: ParsedDoc,
    pub classification: Classification,
    pub identity: DocIdentity,
}

/// One staged manifest row.
#[derive(Debug, Clone)]
pub struct StagedRow {
    pub concept_id: String,
    pub title: String,
    pub doc_type: String,
    pub tags: Vec<String>,
    pub description: String,
    pub resource: String,
    pub affects_repos: Vec<String>,
    pub affects_modules: Vec<String>,
    pub origin: String,
    pub repo: String,
    pub source_path: String,
    pub body: String,
    pub staged_path: String,
    pub relationships: Vec<Yaml>,
}

/// One skipped manifest row.
#[derive(Debug, Clone)]
pub struct SkippedRow {
    pub source_path: String,
    pub skip: String,
}

/// The parsed staging manifest.
#[derive(Debug, Clone)]
pub struct IngestManifest {
    pub workspace: PathBuf,
    pub accepted: usize,
    pub skipped: usize,
    pub rows: Vec<ManifestRow>,
}

#[derive(Debug, Clone)]
pub enum ManifestRow {
    Skipped(SkippedRow),
    Staged(Box<StagedRow>),
}

fn bump_count(counts: &mut Vec<(String, usize)>, key: &str) {
    match counts.iter_mut().find(|(k, _)| k == key) {
        Some((_, n)) => *n += 1,
        None => counts.push((key.to_string(), 1)),
    }
}

fn source_path_of(entry: &StagedEntry) -> String {
    format!("{}/{}", entry.repo, entry.relpath)
}

fn merged_tags(base: &[String], extra: &[String]) -> Vec<String> {
    let mut merged = base.to_vec();
    for tag in extra {
        if !merged.contains(tag) {
            merged.push(tag.clone());
        }
    }
    merged
}

/// Stage a deterministic OKF outbox and return its manifest.
pub fn stage_outbox(
    entries: Vec<StagedEntry>,
    outbox_dir: &Path,
) -> Result<IngestManifest, String> {
    std::fs::create_dir_all(outbox_dir).map_err(|e| e.to_string())?;
    let detected = detect_supersede_relationships(&entries);
    let mut sorted = entries;
    sorted.sort_by(|a, b| (&a.repo, &a.relpath).cmp(&(&b.repo, &b.relpath)));

    let mut rows: Vec<ManifestRow> = Vec::new();
    let mut by_type: Vec<(String, usize)> = Vec::new();
    let mut by_repo: Vec<(String, usize)> = Vec::new();
    let mut accepted = 0usize;
    let mut skipped = 0usize;
    for entry in &sorted {
        let source_path = source_path_of(entry);
        if entry.classification.skip_reason.is_some() {
            rows.push(ManifestRow::Skipped(SkippedRow {
                source_path,
                skip: entry.classification.skip_reason.clone().unwrap_or_default(),
            }));
            skipped += 1;
            continue;
        }
        let detected_rels = detected
            .get(&(entry.repo.clone(), entry.relpath.clone()))
            .cloned()
            .unwrap_or_default();
        let staged = stage_document(entry, &source_path, outbox_dir, &detected_rels)?;
        bump_count(&mut by_type, &staged.doc_type);
        bump_count(&mut by_repo, &staged.repo);
        rows.push(ManifestRow::Staged(Box::new(staged)));
        accepted += 1;
    }

    let count_object = |counts: &[(String, usize)]| -> serde_json::Map<String, Value> {
        counts.iter().map(|(k, v)| (k.clone(), json!(v))).collect()
    };
    let manifest = json!({
        "version": 1,
        "generated_at": tools_km::iso_utc_micros(),
        "workspace": outbox_dir.to_string_lossy(),
        "counts": {
            "accepted": accepted,
            "skipped": skipped,
            "by_type": Value::Object(count_object(&by_type)),
            "by_repo": Value::Object(count_object(&by_repo)),
        },
        "rows": rows.iter().map(row_json).collect::<Vec<_>>(),
    });
    std::fs::write(
        outbox_dir.join(MANIFEST_NAME),
        format!(
            "{}\n",
            serde_json::to_string_pretty(&manifest).map_err(|e| e.to_string())?
        ),
    )
    .map_err(|e| e.to_string())?;
    Ok(IngestManifest {
        workspace: outbox_dir.to_path_buf(),
        accepted,
        skipped,
        rows,
    })
}

fn row_json(row: &ManifestRow) -> Value {
    match row {
        ManifestRow::Skipped(s) => json!({
            "source_path": s.source_path,
            "skip": s.skip,
        }),
        ManifestRow::Staged(s) => {
            let mut obj = serde_json::Map::new();
            obj.insert("concept_id".into(), json!(s.concept_id));
            obj.insert("title".into(), json!(s.title));
            obj.insert("doc_type".into(), json!(s.doc_type));
            obj.insert("tags".into(), json!(s.tags));
            obj.insert("description".into(), json!(s.description));
            obj.insert("resource".into(), json!(s.resource));
            obj.insert("affects_repos".into(), json!(s.affects_repos));
            obj.insert("affects_modules".into(), json!(s.affects_modules));
            obj.insert("origin".into(), json!(s.origin));
            obj.insert("repo".into(), json!(s.repo));
            obj.insert("source_path".into(), json!(s.source_path));
            obj.insert("body".into(), json!(s.body));
            obj.insert("staged_path".into(), json!(s.staged_path));
            if !s.relationships.is_empty() {
                obj.insert("relationships".into(), relationships_json(&s.relationships));
            }
            Value::Object(obj)
        }
    }
}

fn relationships_json(entries: &[Yaml]) -> Value {
    Value::Array(
        entries
            .iter()
            .map(|e| match e {
                Yaml::Hash(h) => {
                    let mut obj = serde_json::Map::new();
                    for (k, v) in h {
                        let key = ystr(Some(k)).unwrap_or_default();
                        obj.insert(key, yaml_to_json(v));
                    }
                    Value::Object(obj)
                }
                other => yaml_to_json(other),
            })
            .collect(),
    )
}

fn yaml_to_json(v: &Yaml) -> Value {
    match v {
        Yaml::String(s) => Value::String(s.clone()),
        Yaml::Real(s) => serde_json::from_str(s).unwrap_or(Value::Null),
        Yaml::Integer(i) => Value::from(*i),
        Yaml::Boolean(b) => Value::from(*b),
        Yaml::Array(items) => Value::Array(items.iter().map(yaml_to_json).collect()),
        Yaml::Hash(h) => {
            let mut obj = serde_json::Map::new();
            for (k, v) in h {
                obj.insert(ystr(Some(k)).unwrap_or_default(), yaml_to_json(v));
            }
            Value::Object(obj)
        }
        _ => Value::Null,
    }
}

fn stage_document(
    entry: &StagedEntry,
    source_path: &str,
    outbox_dir: &Path,
    detected: &[Yaml],
) -> Result<StagedRow, String> {
    let doc_type = doc_type_slug(&entry.classification.doc_type);
    let identity = &entry.identity;
    let concept_id = format!("knowledge/{}/{}", doc_type, identity.slug);
    let staged_path = format!("{concept_id}.md");
    let title = strip_private_data(&identity.title);
    let description = strip_private_data(&identity.description);
    let stripped_body = entry.parsed.body.trim_end();
    let body = if stripped_body.is_empty() {
        format!("Source: {source_path}\n")
    } else {
        format!("{stripped_body}\n\nSource: {source_path}\n")
    };
    let body = strip_private_data(&body);
    let tags = merged_tags(&identity.tags, &entry.classification.extra_tags);
    let relationships = normalize_relationships(Some(&Yaml::Array(
        extract_relationships(&entry.parsed.extensions)
            .into_iter()
            .chain(detected.iter().cloned())
            .collect(),
    )));
    let mut extensions: Vec<(String, Yaml)> = vec![
        ("tier".into(), ys("asserted")),
        ("doc_status".into(), ys("active")),
        ("doc_source".into(), ys("imported")),
        (
            "affects_modules".into(),
            str_list_yaml(&identity.affects_modules),
        ),
        (
            "affects_repos".into(),
            str_list_yaml(&identity.affects_repos),
        ),
    ];
    if !relationships.is_empty() {
        extensions.push(("relates_to".into(), Yaml::Array(relationships.clone())));
    }
    let concept = Concept {
        type_: format!("Knowledge-{doc_type}"),
        title: Some(title.clone()),
        description: Some(description.clone()),
        resource: Some(source_path.to_string()),
        tags: tags.clone(),
        concept_id: concept_id.clone(),
        body: body.clone(),
        extensions,
        ..Default::default()
    };
    let staged_file = outbox_dir.join(&staged_path);
    concept.to_file(&staged_file).map_err(|e| e.to_string())?;
    let staged_back = Concept::from_file(&staged_file).map_err(|e| e.to_string())?;
    let expected_type = format!("Knowledge-{doc_type}");
    if staged_back.title != Some(title.clone()) || staged_back.type_ != expected_type {
        return Err(format!(
            "staging cross-check failed for {staged_path}: expected title={} type={}, staged \
             title={} type={}",
            py_repr(&title),
            py_repr(&expected_type),
            py_repr(staged_back.title.as_deref().unwrap_or("None")),
            py_repr(&staged_back.type_)
        ));
    }
    Ok(StagedRow {
        concept_id,
        title,
        doc_type,
        tags,
        description,
        resource: source_path.to_string(),
        affects_repos: identity.affects_repos.clone(),
        affects_modules: identity.affects_modules.clone(),
        origin: entry.origin.clone(),
        repo: entry.repo.clone(),
        source_path: source_path.to_string(),
        body,
        staged_path,
        relationships,
    })
}

fn py_repr(s: &str) -> String {
    format!("'{}'", s.replace('\\', "\\\\").replace('\'', "\\'"))
}

fn str_list_yaml(items: &[String]) -> Yaml {
    Yaml::Array(items.iter().map(|i| ys(i.clone())).collect())
}

// --- run_ingest (ingest/__init__.py port) ----------------------------------------

/// Stage fed and scanned documents without touching the knowledge store.
pub fn run_ingest(
    workspace: &Path,
    files: &[PathBuf],
    dirs: &[PathBuf],
    repos: &[PathBuf],
    outbox: Option<&Path>,
    include_drafts: bool,
) -> Result<IngestManifest, String> {
    let overrides = load_ingest_config(workspace);
    let outbox_dir = outbox
        .map(Path::to_path_buf)
        .unwrap_or_else(|| workspace.join(".cairn").join("ingest-outbox"));
    let (docs, scan_skips) = sourced(files, dirs, repos, &overrides)?;
    let mut seen_slugs: HashSet<String> = HashSet::new();
    let mut entries: Vec<StagedEntry> = Vec::new();
    let mut sorted_docs = docs;
    sorted_docs.sort_by(|a, b| (&a.0, &a.1).cmp(&(&b.0, &b.1)));
    for (repo, relpath, text, origin) in sorted_docs {
        let parsed = parse_source_doc(&text);
        let mut classification = classify_doc(&parsed, &relpath, include_drafts, &overrides);
        apply_fed_tag(&origin, &parsed, &mut classification);
        let identity = build_identity(&repo, &relpath, &parsed, Some(&mut seen_slugs));
        entries.push(StagedEntry {
            repo,
            relpath,
            origin,
            parsed,
            classification,
            identity,
        });
    }
    for (repo, relpath, reason) in scan_skips {
        let identity = build_identity(&repo, &relpath, &ParsedDoc::default(), None);
        entries.push(StagedEntry {
            origin: repo.clone(),
            relpath,
            parsed: ParsedDoc::default(),
            classification: Classification {
                doc_type: "spec".to_string(),
                extra_tags: Vec::new(),
                skip_reason: Some(reason),
            },
            identity,
            repo,
        });
    }
    stage_outbox(entries, &outbox_dir)
}

/// Scan-level skips as (repo, relpath, reason) triples.
type ScanSkip = (String, String, String);

fn sourced(
    files: &[PathBuf],
    dirs: &[PathBuf],
    repos: &[PathBuf],
    overrides: &IngestConfig,
) -> Result<(Vec<SourcedDoc>, Vec<ScanSkip>), String> {
    let suffix_of = |p: &Path| {
        p.extension()
            .map(|e| format!(".{}", e.to_string_lossy().to_lowercase()))
    };
    let binary: Vec<PathBuf> = files
        .iter()
        .filter(|p| suffix_of(p).is_some_and(|s| CONVERT_SUFFIXES.contains(&s.as_str())))
        .cloned()
        .collect();
    let markdown: Vec<PathBuf> = files
        .iter()
        .filter(|p| !binary.contains(p))
        .cloned()
        .collect();

    let (mut docs, skips) = fed_markdown_docs(&[markdown, dirs.to_vec()].concat())?;
    let mut scan_skips: Vec<ScanSkip> = skips
        .into_iter()
        .map(|(relpath, reason)| (FED_REPO.to_string(), relpath, reason))
        .collect();
    if !binary.is_empty() {
        let (bin_docs, bin_skips) = fed_binary_docs(&binary)?;
        docs.extend(bin_docs);
        scan_skips.extend(
            bin_skips
                .into_iter()
                .map(|(relpath, reason)| (FED_REPO.to_string(), relpath, reason)),
        );
    }
    for repo_root in repos {
        let (repo_docs, repo_skips) =
            repo_scan_docs(repo_root, &overrides.skip_add, &overrides.skip_disable)?;
        docs.extend(repo_docs);
        let repo_name = repo_root
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default();
        scan_skips.extend(
            repo_skips
                .into_iter()
                .map(|(relpath, reason)| (repo_name.clone(), relpath, reason)),
        );
    }
    Ok((docs, scan_skips))
}

/// Origin tags: status-less fed docs get `fed`; converted get `converted`.
fn apply_fed_tag(origin: &str, parsed: &ParsedDoc, classification: &mut Classification) {
    if origin == CONVERTED_ORIGIN {
        if !classification
            .extra_tags
            .iter()
            .any(|t| t == CONVERTED_ORIGIN)
        {
            classification.extra_tags.push(CONVERTED_ORIGIN.to_string());
        }
        return;
    }
    if origin != FED_ORIGIN || parsed.status.is_some() {
        return;
    }
    if !classification.extra_tags.iter().any(|t| t == FED_ORIGIN) {
        classification.extra_tags.push(FED_ORIGIN.to_string());
    }
}

// --- doc refs (ingest/refs.py port) ----------------------------------------------

/// Refs verified against the graph: files before symbols.
pub fn resolve_doc_refs(conn: &Connection, body: &str) -> Vec<(String, String)> {
    let mut entries = Vec::new();
    for ref_ in extract_file_refs(body) {
        if file_exists(conn, &ref_).unwrap_or(false) {
            entries.push((ref_, "file".to_string()));
        }
    }
    let mut seen: HashSet<String> = HashSet::new();
    for ref_ in extract_symbol_refs(body) {
        if seen.insert(ref_.clone()) && symbol_exists(conn, &ref_).unwrap_or(false) {
            entries.push((ref_, "symbol".to_string()));
        }
    }
    entries
}

// --- executor (ingest/executor.py port) -------------------------------------------

/// The approved-run report; each verify leg is a named boolean/count.
#[derive(Debug, Clone)]
pub struct ExecutorReport {
    pub written: Vec<String>,
    pub embedded: Option<usize>,
    pub accepted: usize,
    pub skipped: usize,
    pub index_edges: usize,
    pub index_doc_refs: usize,
    pub verified_refs: usize,
    pub doc_link_tasks: usize,
    pub dangling_pointers: Vec<DanglingPointer>,
    pub embed_error: Option<String>,
    pub store_count: usize,
    pub expected_count: usize,
    pub count_ok: bool,
    pub smoke_search_hit: bool,
    pub validate_ok: bool,
    pub validate_errors: usize,
    pub validate_message: String,
}

/// One knowledge-embed pass over the bundle (CLI-supplied backend).
pub type EmbedPass = dyn Fn(&Connection, &Bundle) -> Result<usize, String>;

/// Write approved rows, embed when available, and refresh derived knowledge.
pub fn execute_manifest(
    manifest: &IngestManifest,
    conn: &Connection,
    bundle: &Bundle,
    embed: Option<&EmbedPass>,
) -> Result<ExecutorReport, ToolError> {
    let accepted: Vec<&StagedRow> = manifest
        .rows
        .iter()
        .filter_map(|r| match r {
            ManifestRow::Staged(row) => Some(row.as_ref()),
            ManifestRow::Skipped(_) => None,
        })
        .collect();
    let pre_ids = pre_existing_docs(bundle);
    let mut written: Vec<String> = Vec::new();
    let mut overwritten = 0usize;
    let mut verified_refs_total = 0usize;
    let mut sorted: Vec<&StagedRow> = accepted.clone();
    sorted.sort_by(|a, b| (&a.repo, &a.source_path).cmp(&(&b.repo, &b.source_path)));
    for row in sorted {
        if pre_ids.contains(&row.concept_id) {
            overwritten += 1;
        }
        let verified_refs = resolve_doc_refs(conn, &row.body);
        verified_refs_total += verified_refs.len();
        let relationships = merge_existing_inferred(bundle, &row.concept_id, &row.relationships);
        let description = if row.description.is_empty() {
            None
        } else {
            Some(row.description.as_str())
        };
        written.push(tools_km::add_document_with_slug(
            bundle,
            &row.title,
            &row.body,
            &row.doc_type,
            row.tags.clone(),
            row.affects_modules.clone(),
            row.affects_repos.clone(),
            Some(&row.resource),
            None,
            None,
            None,
            description,
            "imported",
            None,
            Some(&relationships),
            Some(&verified_refs),
        )?);
    }

    let mut embedded: Option<usize> = None;
    let mut embed_error: Option<String> = None;
    if let Some(embed) = embed {
        match embed(conn, bundle) {
            Ok(count) => embedded = Some(count),
            Err(e) => embed_error = Some(e),
        }
    }

    let index: KnowledgeIndexReport = rebuild_knowledge_index(conn, bundle)?;
    let doc_link_tasks = queue_doc_link_tasks(conn, bundle);

    let expected = pre_ids.len() + accepted.len() - overwritten;
    let store_count = if bundle.root.exists() {
        list_documents(bundle, None, None, None).len()
    } else {
        0
    };
    let mut smoke_search_hit = false;
    if let Some(first) = accepted.first() {
        let probe = first
            .title
            .rsplit(" — ")
            .next()
            .unwrap_or(&first.title)
            .to_string();
        smoke_search_hit = search_knowledge_docs(conn, bundle, &probe, 5)
            .map(|rows| !rows.is_empty())
            .unwrap_or(false);
    }
    let validate_errors = check_bundle(&bundle.root);
    let validate_message = validate_errors.first().cloned().unwrap_or_default();

    Ok(ExecutorReport {
        written,
        embedded,
        accepted: accepted.len(),
        skipped: manifest.skipped,
        index_edges: index.edges,
        index_doc_refs: index.doc_refs,
        verified_refs: verified_refs_total,
        doc_link_tasks,
        dangling_pointers: index.dangling,
        embed_error,
        // Strict equality against the whole-store expectation.
        count_ok: store_count == expected,
        smoke_search_hit,
        validate_ok: validate_errors.is_empty(),
        validate_errors: validate_errors.len(),
        validate_message,
        expected_count: expected,
        store_count,
    })
}

fn pre_existing_docs(bundle: &Bundle) -> HashSet<String> {
    if !bundle.root.exists() {
        return HashSet::new();
    }
    bundle
        .list_concepts(Some("knowledge/"))
        .into_iter()
        .map(|cid| normalize_doc_id(bundle, &cid))
        .collect()
}

/// Merge declared relationships with an existing concept's inferred ones.
fn merge_existing_inferred(bundle: &Bundle, concept_id: &str, declared: &[Yaml]) -> Vec<Yaml> {
    let merged = normalize_relationships(Some(&Yaml::Array(declared.to_vec())));
    if !bundle.root.exists() {
        return merged;
    }
    let Ok(existing) = bundle.read_concept(concept_id) else {
        return merged;
    };
    let inferred: Vec<Yaml> = normalize_relationships(existing.ext("relates_to"))
        .into_iter()
        .filter(
            |entry| matches!(entry, Yaml::Hash(h) if h.get(&ys("kind")) == Some(&ys("inferred"))),
        )
        .collect();
    normalize_relationships(Some(&Yaml::Array(
        merged.into_iter().chain(inferred).collect(),
    )))
}

/// Staged relates_to pointers that would not index (dry-run prediction).
pub fn predict_dangling(bundle: &Bundle, manifest: &IngestManifest) -> Vec<DanglingPointer> {
    let rows: Vec<StagedPointerRow<'_>> = manifest
        .rows
        .iter()
        .filter_map(|r| match r {
            ManifestRow::Staged(row) => Some(StagedPointerRow {
                source_path: Some(&row.source_path),
                concept_id: Some(&row.concept_id),
                relationships: &row.relationships,
            }),
            ManifestRow::Skipped(_) => None,
        })
        .collect();
    dangling_manifest_pointers(bundle, &rows)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(tag: &str) -> PathBuf {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let dir = std::env::temp_dir().join(format!("cairn-{tag}-{}-{nanos}", std::process::id()));
        std::fs::create_dir_all(&dir).expect("scratch dir");
        dir
    }

    /// A stand-in for `python -m cairn_intel_ingest <path>`: markdown on
    /// stdout, or `reason` on stderr with exit 1.
    #[cfg(unix)]
    fn fake_sidecar(dir: &Path, reason: Option<&str>) -> PathBuf {
        use std::os::unix::fs::PermissionsExt;
        let body = match reason {
            None => "#!/bin/sh\nprintf '# converted %s' \"$(basename \"$3\")\"\n",
            Some(reason) => &format!("#!/bin/sh\necho '{reason}' >&2\nexit 1\n"),
        };
        let script = dir.join("python3");
        std::fs::write(&script, body).expect("script");
        std::fs::set_permissions(&script, std::fs::Permissions::from_mode(0o755)).expect("chmod");
        script
    }

    fn convert_fixture(name: &str) -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/convert")
            .join(name)
    }

    #[test]
    fn docx_converts_natively_without_a_sidecar() {
        let docx = convert_fixture("sample.docx");
        let golden = std::fs::read_to_string(convert_fixture("sample.docx.md")).expect("golden");
        let (docs, skipped) =
            fed_binary_docs_with(std::slice::from_ref(&docx), None).expect("adapter");
        assert!(skipped.is_empty(), "{skipped:?}");
        assert_eq!(docs.len(), 1);
        assert_eq!(docs[0].2, golden.trim_end());
        assert_eq!(docs[0].3, CONVERTED_ORIGIN);
    }

    #[test]
    fn pdf_extracts_text_natively_without_a_sidecar() {
        let pdf = convert_fixture("gateway.pdf");
        let (docs, skipped) =
            fed_binary_docs_with(std::slice::from_ref(&pdf), None).expect("adapter");
        assert!(skipped.is_empty(), "{skipped:?}");
        assert_eq!(docs.len(), 1);
        assert!(docs[0].2.contains("Deploy the gateway"));
        assert!(docs[0].2.contains("Route traffic through the edge proxy."));
    }

    #[test]
    #[cfg(unix)]
    fn docx_stays_native_and_pdf_prefers_the_sidecar_when_present() {
        let dir = scratch("ingest-sidecar");
        let docx = convert_fixture("sample.docx");
        let pdf = dir.join("spec.pdf");
        std::fs::write(&pdf, b"%PDF-1.4").expect("pdf");
        let note = dir.join("note.md");
        std::fs::write(&note, b"# md").expect("md");
        let python = fake_sidecar(&dir, None);

        let (docs, skipped) =
            fed_binary_docs_with(&[pdf.clone(), docx, note], Some(&python)).expect("adapter");
        assert!(skipped.is_empty(), "{skipped:?}");
        let by_rel = |docs: &[(String, String, String, String)], rel: &str| -> String {
            docs.iter()
                .find(|d| d.1.ends_with(rel))
                .map(|d| d.2.clone())
                .unwrap_or_default()
        };
        assert_eq!(by_rel(&docs, "spec.pdf"), "# converted spec.pdf");
        assert!(by_rel(&docs, "sample.docx").contains("# Deployment Guide"));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn corrupt_binary_docs_skip_with_the_conversion_failed_reason() {
        let dir = scratch("ingest-sidecar-skip");
        let docx = dir.join("spec.docx");
        std::fs::write(&docx, b"PK").expect("docx");
        let pdf = dir.join("spec.pdf");
        std::fs::write(&pdf, b"%PDF-1.4 not a real document").expect("pdf");
        let blank = convert_fixture("blank.pdf");

        let (docs, skipped) =
            fed_binary_docs_with(&[docx, pdf.clone(), blank], None).expect("adapter");
        assert!(docs.is_empty());
        let reasons: Vec<&str> = skipped.iter().map(|(_, r)| r.as_str()).collect();
        assert_eq!(reasons.len(), 3, "{reasons:?}");
        assert!(reasons[0].starts_with("conversion failed:"), "{reasons:?}");
        assert!(reasons[1].starts_with("conversion failed:"), "{reasons:?}");
        assert_eq!(reasons[2], "empty extraction");

        #[cfg(unix)]
        {
            let python = fake_sidecar(&dir, Some("conversion failed: not a pdf"));
            let (docs, skipped) =
                fed_binary_docs_with(std::slice::from_ref(&pdf), Some(&python)).expect("adapter");
            assert!(docs.is_empty());
            assert_eq!(
                skipped,
                vec![(
                    pdf.to_string_lossy().to_string(),
                    "conversion failed: not a pdf".to_string()
                )]
            );
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn sha1_hex8_matches_the_reference_vector() {
        // echo -n "abc" | shasum
        assert_eq!(sha1_hex8(b"abc"), "a9993e36");
        assert_eq!(
            sha1_hex8(b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"),
            "84983e44"
        );
    }

    #[test]
    fn fnmatch_covers_the_skip_list_shapes() {
        assert!(fnmatch_name("changelog.md", "changelog*"));
        assert!(fnmatch_name("meeting-notes-2.md", "meeting-notes*"));
        assert!(fnmatch_name("release-template.md", "*-template.md"));
        assert!(!fnmatch_name("intro.md", "template*"));
        assert!(fnmatch_name("notes", "note?"));
        assert!(!fnmatch_name("notex", "note[!x]s"));
    }

    #[test]
    fn parse_reads_frontmatter_status_and_heading_title() {
        let doc = parse_source_doc(
            "---\ntitle: Fed title\ntags: [a, b]\nstatus: proposed\nextra: keep\n---\n\n# Body\n\nText.\n",
        );
        assert_eq!(doc.title.as_deref(), Some("Fed title"));
        assert_eq!(doc.status.as_deref(), Some("proposed"));
        assert_eq!(doc.tags, vec!["a", "b"]);
        assert!(doc.extensions.contains_key(&ys("extra")));

        let doc = parse_source_doc("# Just a heading\n\n**Status:** accepted\n");
        assert_eq!(doc.title.as_deref(), Some("Just a heading"));
        assert_eq!(doc.status.as_deref(), Some("accepted"));

        // malformed YAML falls back to minimal pairs; unclosed brackets drop
        let doc = parse_source_doc("---\nbroken: [unclosed\nplain: hello\n---\nbody\n");
        assert_eq!(doc.body, "body");
        assert!(!doc.extensions.contains_key(&ys("broken")));
        assert_eq!(
            doc.extensions.get(&ys("plain")).and_then(|v| ystr(Some(v))),
            Some("hello".to_string())
        );
    }

    #[test]
    fn classify_routes_titles_paths_and_draft_status() {
        let rules = IngestConfig::default();
        let spec = ParsedDoc {
            title: Some("Auth token design".into()),
            status: Some("accepted".into()),
            ..Default::default()
        };
        assert_eq!(
            classify_doc(&spec, "001.md", false, &rules).doc_type,
            "spec"
        );

        let adr = ParsedDoc {
            title: Some("Rotate keys".into()),
            status: None,
            ..Default::default()
        };
        assert_eq!(
            classify_doc(&adr, "docs/adr/0012-rotate-keys.md", false, &rules).doc_type,
            "decision"
        );

        let draft = ParsedDoc {
            title: Some("Design draft".into()),
            status: Some("draft".into()),
            ..Default::default()
        };
        let classified = classify_doc(&draft, "note.md", false, &rules);
        assert_eq!(classified.skip_reason.as_deref(), Some("status: draft"));
        let readmitted = classify_doc(&draft, "note.md", true, &rules);
        assert!(readmitted.extra_tags.contains(&"draft".to_string()));
    }

    #[test]
    fn build_identity_carries_stable_id_tags_and_modules() {
        let parsed = ParsedDoc {
            title: Some("Auth token design".into()),
            status: Some("accepted".into()),
            ..Default::default()
        };
        let identity = build_identity("workspace", "001-auth-design.md", &parsed, None);
        assert_eq!(identity.stable_id, "workspace-001-auth-design-md");
        assert_eq!(
            identity.title,
            "workspace-001-auth-design-md — Auth token design"
        );
        assert_eq!(
            identity.slug,
            "workspace-001-auth-design-md-auth-token-design"
        );
        assert_eq!(
            identity.tags,
            vec!["workspace-001-auth-design-md", "workspace"]
        );
        assert!(identity.affects_modules.is_empty());

        let nested = build_identity("repo", "docs/sub/page.md", &parsed, None);
        assert_eq!(nested.affects_modules, vec!["docs/sub"]);
    }

    #[test]
    fn long_paths_digest_anchor_the_stable_id() {
        let long_rel = format!("{}.md", "x".repeat(80));
        let id = stable_id("workspace", &long_rel);
        assert!(id.chars().count() <= SLUG_MAX);
        assert!(id.contains('-'));
        assert_eq!(id.split('-').next_back().unwrap().len(), 8);
    }

    #[test]
    fn ingest_config_warnings_match_python_shapes() {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let dir = std::env::temp_dir().join(format!("cairn-cfg-{}-{nanos}", std::process::id()));
        std::fs::create_dir_all(&dir).expect("dir");

        // missing file: no warnings, defaults
        let (config, warnings) = ingest_config_with_warnings(&dir);
        assert!(warnings.is_empty(), "{warnings:?}");
        assert!(config.classification.is_empty());

        // invalid JSON
        std::fs::write(dir.join("cairn.json"), "{not json").expect("write");
        let (_, warnings) = ingest_config_with_warnings(&dir);
        let shown = dir.join("cairn.json").display().to_string();
        assert_eq!(
            warnings,
            vec![format!(
                "warning: {shown}: invalid JSON (Expecting property name enclosed \
                 in double quotes: line 1 column 2 (char 1)); ignoring config"
            )],
            "{warnings:?}"
        );

        // top-level must be an object
        std::fs::write(dir.join("cairn.json"), "[1, 2]").expect("write");
        let (_, warnings) = ingest_config_with_warnings(&dir);
        assert_eq!(
            warnings,
            vec![format!(
                "warning: {}: top-level JSON must be an object; ignoring config",
                dir.join("cairn.json").display()
            )],
            "{warnings:?}"
        );

        // non-object ingest section
        std::fs::write(dir.join("cairn.json"), "{\"ingest\": []}").expect("write");
        let (_, warnings) = ingest_config_with_warnings(&dir);
        assert_eq!(
            warnings,
            vec![format!(
                "warning: {}: 'ingest' must be a JSON object; ignoring",
                dir.join("cairn.json").display()
            )],
            "{warnings:?}"
        );

        // invalid UTF-8: Python's UnicodeDecodeError text
        std::fs::write(dir.join("cairn.json"), b"{\"a\": 1}\n\xe2\x80").expect("write");
        let (_, warnings) = ingest_config_with_warnings(&dir);
        assert_eq!(
            warnings,
            vec![format!(
                "warning: {}: not valid UTF-8 ('utf-8' codec can't decode bytes in position 9-10: unexpected end of data); ignoring config",
                dir.join("cairn.json").display()
            )],
            "{warnings:?}"
        );
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn py_json_error_text_matches_the_reference_vectors() {
        let mapped = |raw: &str| -> String {
            let e = serde_json::from_str::<Value>(raw).expect_err("must fail");
            py_json_error_text(raw, &e)
        };
        assert_eq!(mapped(""), "Expecting value: line 1 column 1 (char 0)");
        assert_eq!(mapped("x"), "Expecting value: line 1 column 1 (char 0)");
        assert_eq!(
            mapped("{not json"),
            "Expecting property name enclosed in double quotes: line 1 column 2 (char 1)"
        );
        assert_eq!(
            mapped("{"),
            "Expecting property name enclosed in double quotes: line 1 column 2 (char 1)"
        );
        assert_eq!(mapped("{} {}"), "Extra data: line 1 column 4 (char 3)");
        assert_eq!(mapped("nul"), "Expecting value: line 1 column 1 (char 0)");
        assert_eq!(mapped("nul "), "Expecting value: line 1 column 1 (char 0)");
        assert_eq!(
            mapped("{\"a\":tru"),
            "Expecting value: line 1 column 6 (char 5)"
        );
        assert_eq!(
            mapped("{\"a\": "),
            "Expecting value: line 1 column 7 (char 6)"
        );
        // shapes without a verified mapping keep serde's own wording
        assert_eq!(
            mapped("\"abc"),
            "EOF while parsing a string at line 1 column 4"
        );
        assert_eq!(mapped("[1,]"), "trailing comma at line 1 column 4");
    }

    #[test]
    fn py_unicode_decode_error_matches_the_reference_vectors() {
        let cases: &[(&[u8], &str)] = &[
            (
                b"a\x80",
                "'utf-8' codec can't decode byte 0x80 in position 1: invalid start byte",
            ),
            (
                b"\xe2(",
                "'utf-8' codec can't decode byte 0xe2 in position 0: invalid continuation byte",
            ),
            (
                b"\xe2\x80",
                "'utf-8' codec can't decode bytes in position 0-1: unexpected end of data",
            ),
            (
                b"\xf8",
                "'utf-8' codec can't decode byte 0xf8 in position 0: invalid start byte",
            ),
            (
                b"\xe2\x80(",
                "'utf-8' codec can't decode bytes in position 0-1: invalid continuation byte",
            ),
            (
                b"\xc3",
                "'utf-8' codec can't decode byte 0xc3 in position 0: unexpected end of data",
            ),
            (
                b"\xc0\x80",
                "'utf-8' codec can't decode byte 0xc0 in position 0: invalid start byte",
            ),
            (
                b"\xed\xa0\x80",
                "'utf-8' codec can't decode byte 0xed in position 0: invalid continuation byte",
            ),
            (
                b"\xf4\x90\x80\x80",
                "'utf-8' codec can't decode byte 0xf4 in position 0: invalid continuation byte",
            ),
            (
                b"\xe0\x80\x80",
                "'utf-8' codec can't decode byte 0xe0 in position 0: invalid continuation byte",
            ),
        ];
        for (raw, expected) in cases {
            assert_eq!(
                py_unicode_decode_error(raw).as_deref(),
                Some(*expected),
                "input {raw:?}"
            );
        }
        assert_eq!(py_unicode_decode_error(b"valid \xe2\x82\xac text"), None);
    }

    #[test]
    fn manifest_carries_isoformat_timestamp_and_first_seen_counts() {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let dir =
            std::env::temp_dir().join(format!("cairn-manifest-{}-{nanos}", std::process::id()));
        let fed = dir.join("fed");
        std::fs::create_dir_all(&fed).expect("fed dir");
        // 001 classifies by title ("guide" -> workflow) before 002's numbered
        // filename (-> decision), so first-seen order is workflow, decision.
        std::fs::write(
            fed.join("001-setup-guide.md"),
            "# Setup guide\n\nHow to set up.\n\n**Status:** accepted\n",
        )
        .expect("001");
        std::fs::write(
            fed.join("002-rotate-keys.md"),
            "# Rotate keys\n\nSupersedes nothing.\n\n**Status:** accepted\n",
        )
        .expect("002");

        let workspace = dir.join("ws");
        std::fs::create_dir_all(&workspace).expect("ws");
        let manifest = run_ingest(
            &workspace,
            &[],
            std::slice::from_ref(&fed),
            &[],
            None,
            false,
        )
        .expect("ingest");
        assert_eq!(manifest.accepted, 2);

        let text = std::fs::read_to_string(workspace.join(".cairn/ingest-outbox/manifest.json"))
            .expect("manifest");
        let parsed: serde_json::Value = serde_json::from_str(&text).expect("json");
        let stamp = parsed["generated_at"].as_str().expect("generated_at");
        assert!(
            stamp.ends_with("+00:00") && stamp.contains('.'),
            "datetime.isoformat() shape expected, got {stamp}"
        );
        let by_type: Vec<&str> = parsed["counts"]["by_type"]
            .as_object()
            .expect("by_type")
            .keys()
            .map(String::as_str)
            .collect();
        assert_eq!(
            by_type,
            vec!["workflow", "decision"],
            "counts keep first-seen order, not sorted order"
        );
        std::fs::remove_dir_all(&dir).ok();
    }
}
