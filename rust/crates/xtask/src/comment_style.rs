//! Shrink-only comment-style gate over the dev-script trees.

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::fmt::Write as _;
use std::fs;
use std::io::Write as _;
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::sync::OnceLock;

use clap::Parser as _;
use regex::Regex;
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};
use tree_sitter::{Node, Parser, Tree};

const MAX_BLOCK_LINES: usize = 3;
const BASELINE_SCHEMA_VERSION: u64 = 1;
const DEFAULT_BASELINE: [&str; 3] = ["scripts", "hooks", "comment-style-baseline.json"];
// tests/ stays out: fixture and scenario prose is test input, not shipped code.
const SOURCE_ROOTS: [&str; 2] = ["scripts", "packaging"];

#[derive(Debug)]
struct CommentStyleError(String);

type Result<T> = std::result::Result<T, CommentStyleError>;

fn fail<T>(message: impl Into<String>) -> Result<T> {
    Err(CommentStyleError(message.into()))
}

#[derive(Debug, Clone, PartialEq)]
struct Violation {
    kind: &'static str,
    path: String,
    line: usize,
    end_line: usize,
    length: usize,
    fingerprint: String,
}

impl Violation {
    fn new(kind: &'static str, path: &str, line: usize, end_line: usize, text: &str) -> Self {
        Self {
            kind,
            path: path.to_string(),
            line,
            end_line,
            length: end_line - line + 1,
            fingerprint: format!("{kind}:{:x}", Sha256::digest(text.as_bytes())),
        }
    }

    fn sort_key(&self) -> (&str, usize, &str) {
        (&self.path, self.line, self.kind)
    }

    fn to_json(&self) -> Value {
        json!({
            "kind": self.kind,
            "path": self.path,
            "line": self.line,
            "end_line": self.end_line,
            "length": self.length,
            "fingerprint": self.fingerprint,
        })
    }
}

// --- Python source model (tree-sitter) ---

/// Parse Python source; any ERROR/MISSING node fails with the first offending line.
pub(crate) fn parse_python(source: &str) -> std::result::Result<Tree, String> {
    let mut parser = Parser::new();
    parser
        .set_language(&tree_sitter_python::LANGUAGE.into())
        .map_err(|err| err.to_string())?;
    let tree = parser
        .parse(source, None)
        .ok_or_else(|| "parser produced no tree".to_string())?;
    if tree.root_node().has_error() {
        let line = first_error_line(tree.root_node()).unwrap_or(1);
        return Err(format!("invalid syntax (line {line})"));
    }
    Ok(tree)
}

fn first_error_line(node: Node) -> Option<usize> {
    if node.is_error() || node.is_missing() {
        return Some(node.start_position().row + 1);
    }
    let mut cursor = node.walk();
    let children: Vec<Node> = node.children(&mut cursor).collect();
    children
        .into_iter()
        .filter(Node::has_error)
        .find_map(first_error_line)
}

/// Decode source bytes the way `tokenize.open` reads them: BOM dropped, universal newlines.
pub(crate) fn decode_source(bytes: &[u8]) -> std::result::Result<String, String> {
    let bytes = bytes.strip_prefix(b"\xef\xbb\xbf").unwrap_or(bytes);
    let text = std::str::from_utf8(bytes).map_err(|err| format!("invalid UTF-8: {err}"))?;
    Ok(text.replace("\r\n", "\n").replace('\r', "\n"))
}

/// A module/class/function whose first statement is a plain `str` literal expression.
pub(crate) struct Docstring<'t> {
    pub(crate) statement: Node<'t>,
    pub(crate) value: String,
}

/// Every docstring statement in the tree, in document order.
pub(crate) fn docstrings<'t>(tree: &'t Tree, source: &str) -> Vec<Docstring<'t>> {
    let mut found = Vec::new();
    for node in preorder(tree.root_node()) {
        let body = match node.kind() {
            "module" => Some(node),
            "class_definition" | "function_definition" => node.child_by_field_name("body"),
            _ => None,
        };
        let Some(statement) = body.and_then(first_statement) else {
            continue;
        };
        if statement.kind() != "expression_statement" {
            continue;
        }
        if let Some(value) = single_child(statement).and_then(|expr| str_value(expr, source)) {
            found.push(Docstring { statement, value });
        }
    }
    found
}

fn preorder(root: Node) -> Vec<Node> {
    let mut nodes = Vec::new();
    let mut stack = vec![root];
    while let Some(node) = stack.pop() {
        nodes.push(node);
        let mut cursor = node.walk();
        let children: Vec<Node> = node.children(&mut cursor).collect();
        stack.extend(children.into_iter().rev());
    }
    nodes
}

fn named_non_comment(node: Node) -> Vec<Node> {
    let mut cursor = node.walk();
    node.named_children(&mut cursor)
        .filter(|child| child.kind() != "comment")
        .collect()
}

fn first_statement(body: Node) -> Option<Node> {
    named_non_comment(body).into_iter().next()
}

fn single_child(node: Node) -> Option<Node> {
    match named_non_comment(node).as_slice() {
        [only] => Some(*only),
        _ => None,
    }
}

fn str_value(expr: Node, source: &str) -> Option<String> {
    match expr.kind() {
        "parenthesized_expression" => single_child(expr).and_then(|inner| str_value(inner, source)),
        "string" => string_literal_value(&source[expr.byte_range()]),
        "concatenated_string" => {
            let mut value = String::new();
            for part in named_non_comment(expr) {
                value.push_str(&string_literal_value(&source[part.byte_range()])?);
            }
            Some(value)
        }
        _ => None,
    }
}

/// Value of a `str` literal token; `None` for bytes, f-strings, and t-strings.
pub(crate) fn string_literal_value(literal: &str) -> Option<String> {
    let prefix_len = literal.find(['"', '\''])?;
    let prefix = literal[..prefix_len].to_ascii_lowercase();
    if prefix.contains(['b', 'f', 't']) {
        return None;
    }
    let body = &literal[prefix_len..];
    let quote_len = if body.starts_with("\"\"\"") || body.starts_with("'''") {
        3
    } else {
        1
    };
    let content = body.get(quote_len..body.len().checked_sub(quote_len)?)?;
    Some(if prefix.contains('r') {
        content.to_string()
    } else {
        unescape(content)
    })
}

fn unescape(content: &str) -> String {
    let mut out = String::with_capacity(content.len());
    let mut chars = content.chars().peekable();
    while let Some(c) = chars.next() {
        if c != '\\' {
            out.push(c);
            continue;
        }
        let Some(escape) = chars.next() else {
            out.push('\\');
            break;
        };
        let simple = match escape {
            '\n' => Some(None),
            '\\' | '\'' | '"' => Some(Some(escape)),
            'a' => Some(Some('\x07')),
            'b' => Some(Some('\x08')),
            'f' => Some(Some('\x0c')),
            'n' => Some(Some('\n')),
            'r' => Some(Some('\r')),
            't' => Some(Some('\t')),
            'v' => Some(Some('\x0b')),
            _ => None,
        };
        if let Some(decoded) = simple {
            out.extend(decoded);
            continue;
        }
        let (radix, width) = match escape {
            '0'..='7' => (8, 3),
            'x' => (16, 2),
            'u' => (16, 4),
            'U' => (16, 8),
            _ => {
                out.push('\\');
                out.push(escape);
                continue;
            }
        };
        let mut digits = String::new();
        if radix == 8 {
            digits.push(escape);
        }
        while digits.len() < width {
            match chars.peek() {
                Some(d) if d.is_digit(radix) => digits.push(chars.next().unwrap_or_default()),
                _ => break,
            }
        }
        match u32::from_str_radix(&digits, radix)
            .ok()
            .and_then(char::from_u32)
        {
            Some(decoded) if radix == 8 || digits.len() == width => out.push(decoded),
            _ => {
                out.push('\\');
                if radix != 8 {
                    out.push(escape);
                }
                out.push_str(&digits);
            }
        }
    }
    out
}

fn expand_tabs(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut column = 0;
    for c in text.chars() {
        match c {
            '\t' => {
                let spaces = 8 - column % 8;
                out.extend(std::iter::repeat_n(' ', spaces));
                column += spaces;
            }
            '\n' | '\r' => {
                out.push(c);
                column = 0;
            }
            _ => {
                out.push(c);
                column += 1;
            }
        }
    }
    out
}

/// `inspect.cleandoc` (space-only margin stripping).
fn cleandoc(doc: &str) -> String {
    let mut lines: Vec<String> = expand_tabs(doc).split('\n').map(str::to_string).collect();
    let margin = lines
        .iter()
        .skip(1)
        .filter_map(|line| {
            let content = line.trim_start_matches(' ').chars().count();
            (content > 0).then(|| line.chars().count() - content)
        })
        .min();
    if let Some(first) = lines.first_mut() {
        *first = first.trim_start_matches(' ').to_string();
    }
    if let Some(margin) = margin {
        for line in lines.iter_mut().skip(1) {
            *line = line.chars().skip(margin).collect();
        }
    }
    while lines.last().is_some_and(String::is_empty) {
        lines.pop();
    }
    let leading = lines.iter().take_while(|line| line.is_empty()).count();
    lines.drain(..leading);
    lines.join("\n")
}

fn is_py_space(c: char) -> bool {
    c.is_whitespace() || ('\x1c'..='\x1f').contains(&c)
}

/// `str.splitlines()` boundaries.
fn py_splitlines(text: &str) -> Vec<&str> {
    let mut lines = Vec::new();
    let mut start = 0;
    let mut iter = text.char_indices().peekable();
    while let Some((index, c)) = iter.next() {
        let is_break = matches!(
            c,
            '\n' | '\r'
                | '\x0b'
                | '\x0c'
                | '\x1c'
                | '\x1d'
                | '\x1e'
                | '\u{85}'
                | '\u{2028}'
                | '\u{2029}'
        );
        if !is_break {
            continue;
        }
        lines.push(&text[start..index]);
        let mut end = index + c.len_utf8();
        if c == '\r' && iter.peek().is_some_and(|&(_, next)| next == '\n') {
            iter.next();
            end += 1;
        }
        start = end;
    }
    if start < text.len() {
        lines.push(&text[start..]);
    }
    lines
}

fn strip_comment(line: &str) -> &str {
    let text = line.trim_start_matches(is_py_space);
    text.strip_prefix('#')
        .map_or(text, |rest| rest.trim_start_matches(is_py_space))
}

// --- Scan ---

fn comment_only_rows(tree: &Tree, lines: &[&str]) -> Vec<usize> {
    preorder(tree.root_node())
        .into_iter()
        .filter(|node| node.kind() == "comment")
        .map(|node| node.start_position().row + 1)
        .filter(|&row| {
            lines
                .get(row - 1)
                .is_some_and(|line| line.trim_start_matches(is_py_space).starts_with('#'))
        })
        .collect()
}

fn runs(rows: &[usize]) -> Vec<(usize, usize)> {
    let mut runs: Vec<(usize, usize)> = Vec::new();
    for &row in rows {
        match runs.last_mut() {
            Some(last) if row == last.1 + 1 => last.1 = row,
            _ => runs.push((row, row)),
        }
    }
    runs
}

fn file_violations(rel: &str, source: &str) -> std::result::Result<Vec<Violation>, String> {
    let tree = parse_python(source)?;
    let lines = py_splitlines(source);
    let mut violations = Vec::new();
    for (start, end) in runs(&comment_only_rows(&tree, &lines)) {
        if end - start < MAX_BLOCK_LINES {
            continue;
        }
        let text: Vec<&str> = (start..=end)
            .map(|row| strip_comment(lines[row - 1]))
            .collect();
        violations.push(Violation::new("comment", rel, start, end, &text.join("\n")));
    }
    for docstring in docstrings(&tree, source) {
        let start = docstring.statement.start_position().row + 1;
        let end = docstring.statement.end_position().row + 1;
        if end - start < MAX_BLOCK_LINES {
            continue;
        }
        let text = cleandoc(&docstring.value);
        violations.push(Violation::new("docstring", rel, start, end, &text));
    }
    Ok(violations)
}

fn py_files(source_root: &Path) -> Vec<PathBuf> {
    let mut files: Vec<PathBuf> = walkdir::WalkDir::new(source_root)
        .into_iter()
        .filter_map(std::result::Result::ok)
        .filter(|entry| !entry.file_type().is_dir())
        .map(walkdir::DirEntry::into_path)
        .filter(|path| path.extension().is_some_and(|ext| ext == "py"))
        .collect();
    files.sort_by_key(|path| posix(path));
    files
}

fn posix(path: &Path) -> String {
    path.to_string_lossy().replace('\\', "/")
}

fn scan_comment_violations(root: &Path) -> Result<Vec<Violation>> {
    let roots: Vec<PathBuf> = SOURCE_ROOTS
        .iter()
        .map(|name| root.join(name))
        .filter(|path| path.is_dir())
        .collect();
    if roots.is_empty() {
        return fail(format!(
            "no source trees found under {}: {}",
            root.display(),
            SOURCE_ROOTS.join("/")
        ));
    }
    let mut violations = Vec::new();
    for source_root in roots {
        for path in py_files(&source_root) {
            let rel = posix(path.strip_prefix(root).unwrap_or(&path));
            let found = fs::read(&path)
                .map_err(|err| err.to_string())
                .and_then(|bytes| decode_source(&bytes))
                .and_then(|source| file_violations(&rel, &source))
                .map_err(|err| CommentStyleError(format!("{rel}: {err}")))?;
            violations.extend(found);
        }
    }
    violations.sort_by(|a, b| a.sort_key().cmp(&b.sort_key()));
    Ok(violations)
}

// --- Baseline ---

#[derive(Debug, Clone)]
struct BaselineEntry {
    fingerprint: String,
    count: u64,
    paths: Vec<String>,
}

fn entries(violations: &[Violation]) -> Vec<BaselineEntry> {
    let mut grouped: BTreeMap<&str, (u64, BTreeSet<&str>)> = BTreeMap::new();
    for violation in violations {
        let entry = grouped.entry(&violation.fingerprint).or_default();
        entry.0 += 1;
        entry.1.insert(&violation.path);
    }
    grouped
        .into_iter()
        .map(|(fingerprint, (count, paths))| BaselineEntry {
            fingerprint: fingerprint.to_string(),
            count,
            paths: paths.into_iter().map(str::to_string).collect(),
        })
        .collect()
}

fn baseline_document(violations: &[Violation]) -> Value {
    let rows: Vec<Value> = entries(violations)
        .into_iter()
        .map(|entry| {
            json!({"fingerprint": entry.fingerprint, "count": entry.count, "paths": entry.paths})
        })
        .collect();
    json!({"schema_version": BASELINE_SCHEMA_VERSION, "violations": rows})
}

fn write_baseline(path: &Path, violations: &[Violation]) -> Result<()> {
    let text = py_json(&baseline_document(violations), Some(2)) + "\n";
    let io_error = |err: std::io::Error| CommentStyleError(format!("{}: {err}", path.display()));
    let parent = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    fs::create_dir_all(parent).map_err(io_error)?;
    let name = path.file_name().unwrap_or_default().to_string_lossy();
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.subsec_nanos());
    let tmp = parent.join(format!(".{name}.{}{nanos}.tmp", std::process::id()));
    let written = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&tmp)
        .and_then(|mut file| {
            file.write_all(text.as_bytes())?;
            file.sync_all()
        })
        .and_then(|()| fs::rename(&tmp, path));
    if let Err(err) = written {
        let _ = fs::remove_file(&tmp);
        return Err(io_error(err));
    }
    Ok(())
}

fn fingerprint_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\A(?:comment|docstring):[0-9a-f]{64}\z").expect("valid regex"))
}

fn load_baseline(path: &Path) -> Result<Vec<BaselineEntry>> {
    if !path.is_file() {
        return fail(format!("baseline not found: {}", path.display()));
    }
    let document: Value = fs::read(path)
        .map_err(|err| err.to_string())
        .and_then(|bytes| serde_json::from_slice(&bytes).map_err(|err| err.to_string()))
        .map_err(|err| {
            CommentStyleError(format!("invalid baseline JSON: {}: {err}", path.display()))
        })?;
    validate_baseline(path, &document)
}

fn has_exact_keys(map: &Map<String, Value>, keys: &[&str]) -> bool {
    map.len() == keys.len() && keys.iter().all(|key| map.contains_key(*key))
}

fn validate_baseline(path: &Path, document: &Value) -> Result<Vec<BaselineEntry>> {
    let invalid = || CommentStyleError(format!("invalid baseline: {}", path.display()));
    let document = document
        .as_object()
        .filter(|map| has_exact_keys(map, &["schema_version", "violations"]))
        .ok_or_else(invalid)?;
    // Python compares with `==`, so `1.0` and `true` also equal the schema version.
    let schema_ok = match &document["schema_version"] {
        Value::Bool(flag) => *flag,
        Value::Number(number) => number.as_f64() == Some(1.0),
        _ => false,
    };
    if !schema_ok {
        return fail(format!(
            "unsupported baseline schema_version: {}",
            path.display()
        ));
    }
    let rows = document["violations"].as_array().ok_or_else(invalid)?;
    let mut entries: Vec<BaselineEntry> = Vec::new();
    for row in rows {
        let row = row
            .as_object()
            .filter(|map| has_exact_keys(map, &["fingerprint", "count", "paths"]))
            .ok_or_else(invalid)?;
        let fingerprint = row["fingerprint"]
            .as_str()
            .filter(|fp| fingerprint_re().is_match(fp))
            .ok_or_else(invalid)?;
        let count = row["count"]
            .as_u64()
            .filter(|count| *count >= 1)
            .ok_or_else(invalid)?;
        let paths: Vec<String> = row["paths"]
            .as_array()
            .and_then(|items| {
                items
                    .iter()
                    .map(|item| item.as_str().filter(|s| !s.is_empty()).map(str::to_string))
                    .collect()
            })
            .ok_or_else(invalid)?;
        let strictly_sorted = paths.windows(2).all(|pair| pair[0] < pair[1]);
        if paths.is_empty() || !strictly_sorted {
            return Err(invalid());
        }
        if entries.iter().any(|entry| entry.fingerprint == fingerprint) {
            return fail(format!(
                "duplicate baseline entry {fingerprint}: {}",
                path.display()
            ));
        }
        entries.push(BaselineEntry {
            fingerprint: fingerprint.to_string(),
            count,
            paths,
        });
    }
    if !entries
        .windows(2)
        .all(|pair| pair[0].fingerprint <= pair[1].fingerprint)
    {
        return fail(format!("unsorted baseline entries: {}", path.display()));
    }
    Ok(entries)
}

// --- Check ---

struct Stale {
    fingerprint: String,
    expected: u64,
    found: u64,
    paths: Vec<String>,
}

struct CheckResult {
    remaining: usize,
    new: Vec<Violation>,
    stale: Vec<Stale>,
}

impl CheckResult {
    fn ok(&self) -> bool {
        self.new.is_empty() && self.stale.is_empty()
    }

    fn to_json(&self) -> Value {
        let stale: Vec<Value> = self
            .stale
            .iter()
            .map(|entry| {
                json!({
                    "fingerprint": entry.fingerprint,
                    "expected": entry.expected,
                    "found": entry.found,
                    "paths": entry.paths,
                })
            })
            .collect();
        json!({
            "ok": self.ok(),
            "remaining": self.remaining,
            "new": self.new.iter().map(Violation::to_json).collect::<Vec<_>>(),
            "stale": stale,
        })
    }
}

fn check_comment_baseline(root: &Path, baseline_path: &Path) -> Result<CheckResult> {
    let violations = scan_comment_violations(root)?;
    let entries = load_baseline(baseline_path)?;
    let mut order: Vec<&str> = Vec::new();
    let mut current: HashMap<&str, Vec<&Violation>> = HashMap::new();
    for violation in &violations {
        let occurrences = current.entry(&violation.fingerprint).or_default();
        if occurrences.is_empty() {
            order.push(&violation.fingerprint);
        }
        occurrences.push(violation);
    }
    let allowed = |fingerprint: &str| {
        entries
            .iter()
            .find(|entry| entry.fingerprint == fingerprint)
            .map_or(0, |entry| entry.count)
    };

    let mut new: Vec<Violation> = Vec::new();
    for fingerprint in order {
        let occurrences = &current[fingerprint];
        let excess = (occurrences.len() as u64).saturating_sub(allowed(fingerprint)) as usize;
        new.extend(
            occurrences[occurrences.len() - excess..]
                .iter()
                .map(|v| (*v).clone()),
        );
    }
    new.sort_by(|a, b| a.sort_key().cmp(&b.sort_key()));

    let stale = entries
        .iter()
        .filter_map(|entry| {
            let found = current.get(entry.fingerprint.as_str()).map_or(0, Vec::len) as u64;
            (found < entry.count).then(|| Stale {
                fingerprint: entry.fingerprint.clone(),
                expected: entry.count,
                found,
                paths: entry.paths.clone(),
            })
        })
        .collect();

    Ok(CheckResult {
        remaining: violations.len(),
        new,
        stale,
    })
}

// --- Python-compatible JSON (`json.dumps(..., sort_keys=True)`) ---

fn py_json(value: &Value, indent: Option<usize>) -> String {
    let mut out = String::new();
    write_py_json(&mut out, value, indent, 0);
    out
}

fn write_py_json(out: &mut String, value: &Value, indent: Option<usize>, depth: usize) {
    let newline = |out: &mut String, depth: usize| {
        if let Some(width) = indent {
            out.push('\n');
            out.extend(std::iter::repeat_n(' ', width * depth));
        }
    };
    let separator = if indent.is_some() { "," } else { ", " };
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(flag) => out.push_str(if *flag { "true" } else { "false" }),
        Value::Number(number) => out.push_str(&number.to_string()),
        Value::String(text) => write_py_string(out, text),
        Value::Array(items) if items.is_empty() => out.push_str("[]"),
        Value::Object(map) if map.is_empty() => out.push_str("{}"),
        Value::Array(items) => {
            out.push('[');
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    out.push_str(separator);
                }
                newline(out, depth + 1);
                write_py_json(out, item, indent, depth + 1);
            }
            newline(out, depth);
            out.push(']');
        }
        Value::Object(map) => {
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            out.push('{');
            for (index, key) in keys.into_iter().enumerate() {
                if index > 0 {
                    out.push_str(separator);
                }
                newline(out, depth + 1);
                write_py_string(out, key);
                out.push_str(": ");
                write_py_json(out, &map[key], indent, depth + 1);
            }
            newline(out, depth);
            out.push('}');
        }
    }
}

fn write_py_string(out: &mut String, text: &str) {
    out.push('"');
    for c in text.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\x08' => out.push_str("\\b"),
            '\x0c' => out.push_str("\\f"),
            ' '..='~' => out.push(c),
            _ => {
                let mut units = [0u16; 2];
                for unit in c.encode_utf16(&mut units) {
                    let _ = write!(out, "\\u{unit:04x}");
                }
            }
        }
    }
    out.push('"');
}

// --- CLI ---

#[derive(clap::Parser, Debug)]
#[command(
    name = "comment-style",
    about = "Enforce the shrink-only comment-style baseline over the dev-script trees."
)]
struct Args {
    /// Emit machine-readable JSON.
    #[arg(long = "json")]
    as_json: bool,
    /// Repository root (default: discovered from cwd).
    #[arg(long)]
    root: Option<PathBuf>,
    /// Baseline path (default: <root>/scripts/hooks/comment-style-baseline.json).
    #[arg(long)]
    baseline: Option<PathBuf>,
    /// Create the baseline; refuses an existing file.
    #[arg(long, conflicts_with = "update")]
    init: bool,
    /// Rewrite the baseline after shrinkage; refuses growth.
    #[arg(long)]
    update: bool,
}

fn find_root() -> PathBuf {
    let cwd = std::env::current_dir()
        .and_then(fs::canonicalize)
        .unwrap_or_default();
    cwd.ancestors()
        .find(|candidate| candidate.join("pyproject.toml").is_file())
        .map_or_else(|| cwd.clone(), Path::to_path_buf)
}

fn resolve(path: &Path) -> PathBuf {
    fs::canonicalize(path).unwrap_or_else(|_| {
        std::env::current_dir()
            .map(|cwd| cwd.join(path))
            .unwrap_or_else(|_| path.to_path_buf())
    })
}

fn display(path: &Path, root: &Path) -> String {
    path.strip_prefix(root)
        .map_or_else(|_| path.display().to_string(), posix)
}

fn writer_payload(flag: &str, remaining: usize, baseline: &str) -> String {
    py_json(
        &json!({"ok": true, flag: true, "remaining": remaining, "baseline": baseline}),
        None,
    )
}

fn run_init(root: &Path, baseline: &Path, as_json: bool) -> Result<u8> {
    if baseline.exists() {
        return fail(format!("baseline already exists: {}", baseline.display()));
    }
    let violations = scan_comment_violations(root)?;
    write_baseline(baseline, &violations)?;
    let shown = display(baseline, root);
    if as_json {
        println!(
            "{}",
            writer_payload("initialized", violations.len(), &shown)
        );
    } else {
        println!(
            "comment-style: initialized {shown} with {} grandfathered violations",
            violations.len()
        );
    }
    Ok(0)
}

fn run_update(root: &Path, baseline: &Path, as_json: bool) -> Result<u8> {
    let previous = load_baseline(baseline)?;
    let violations = scan_comment_violations(root)?;
    let growth: Vec<String> = entries(&violations)
        .into_iter()
        .filter_map(|entry| {
            let old = previous
                .iter()
                .find(|prev| prev.fingerprint == entry.fingerprint)
                .map_or(0, |prev| prev.count);
            (entry.count > old).then(|| format!("{} ({old} -> {})", entry.fingerprint, entry.count))
        })
        .collect();
    if !growth.is_empty() {
        return fail(format!(
            "baseline update refused (growth): {}: {}",
            growth.join(", "),
            baseline.display()
        ));
    }
    write_baseline(baseline, &violations)?;
    let shown = display(baseline, root);
    if as_json {
        println!("{}", writer_payload("updated", violations.len(), &shown));
    } else {
        println!(
            "comment-style: updated {shown}, {} grandfathered violations remaining",
            violations.len()
        );
    }
    Ok(0)
}

fn failure_lines(result: &CheckResult) -> Vec<String> {
    let new = result.new.iter().map(|v| {
        let label = if v.kind == "comment" {
            "comment block"
        } else {
            "docstring"
        };
        format!(
            "new {label}: {}:{}-{} ({} lines)",
            v.path, v.line, v.end_line, v.length
        )
    });
    let stale = result.stale.iter().map(|entry| {
        format!(
            "stale baseline entry {}: expected {}, found {} ({})",
            entry.fingerprint,
            entry.expected,
            entry.found,
            entry.paths.join(", ")
        )
    });
    new.chain(stale).collect()
}

fn run_check(root: &Path, baseline: &Path, as_json: bool) -> Result<u8> {
    let result = check_comment_baseline(root, baseline)?;
    if as_json {
        println!("{}", py_json(&result.to_json(), None));
        return Ok(u8::from(!result.ok()));
    }
    if !result.ok() {
        for line in failure_lines(&result) {
            eprintln!("{line}");
        }
        eprintln!(
            "comment-style: {} new, {} stale",
            result.new.len(),
            result.stale.len()
        );
        return Ok(1);
    }
    println!(
        "comment-style: {} grandfathered violations remaining",
        result.remaining
    );
    Ok(0)
}

fn execute(args: &Args) -> u8 {
    let root = resolve(&args.root.clone().unwrap_or_else(find_root));
    let baseline = args
        .baseline
        .clone()
        .unwrap_or_else(|| DEFAULT_BASELINE.iter().fold(root.clone(), |p, s| p.join(s)));
    let outcome = if args.init {
        run_init(&root, &baseline, args.as_json)
    } else if args.update {
        run_update(&root, &baseline, args.as_json)
    } else {
        run_check(&root, &baseline, args.as_json)
    };
    outcome.unwrap_or_else(|CommentStyleError(message)| {
        if args.as_json {
            println!("{}", py_json(&json!({"ok": false, "error": message}), None));
        } else {
            eprintln!("comment-style: error: {message}");
        }
        1
    })
}

/// Run the comment-style gate; writer modes never grow the baseline.
pub fn run(args: &[String]) -> ExitCode {
    let argv = std::iter::once("comment-style".to_string()).chain(args.iter().cloned());
    match Args::try_parse_from(argv) {
        Ok(parsed) => ExitCode::from(execute(&parsed)),
        Err(err) => {
            let _ = err.print();
            ExitCode::from(u8::try_from(err.exit_code()).unwrap_or(2))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const LETTER_BLOCK: &str = "# alpha\n# beta\n# gamma\n# delta\n";
    const WORD_BLOCK: &str = "# north\n# south\n# east\n# west\n";

    // Pinned hashes: the baseline file format depends on these exact fingerprints.
    const PARITY_FIXTURE: &str = concat!(
        r#""""Module summary.

"#,
        "\tTabbed line with \\x41 escape and \\101 octal\n",
        r#"    indented\tline
"""


class Owner:
    r"""Raw \n docstring
        kept
          as-is
    """

    def method(self):
        ("implicit "
         'concatenation'
         """ spans
         lines""")


def not_docstrings():
    f"""f-string
    is
    not
    a docstring"""


# café comment
#   indented
#
# end
"#
    );

    fn repo(name: &str, files: &[(&str, &str)]) -> PathBuf {
        let root =
            std::env::temp_dir().join(format!("xtask-comment-style-{}-{name}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(root.join("scripts")).unwrap();
        for (rel, text) in files {
            write_script(&root, rel, text);
        }
        root
    }

    fn write_script(root: &Path, rel: &str, text: &str) {
        fs::write(root.join("scripts").join(rel), text).unwrap();
    }

    fn spans(violations: &[Violation]) -> Vec<(&str, &str, usize, usize)> {
        violations
            .iter()
            .map(|v| (v.kind, v.path.as_str(), v.line, v.end_line))
            .collect()
    }

    fn error_of<T>(result: Result<T>) -> String {
        match result {
            Ok(_) => panic!("expected a CommentStyleError"),
            Err(CommentStyleError(message)) => message,
        }
    }

    #[test]
    fn only_standalone_comment_runs_longer_than_three_lines_are_flagged() {
        let root = repo(
            "blocks",
            &[
                ("a.py", &format!("{LETTER_BLOCK}\n# one\n# two\n# three\n")),
                ("blank.py", "# one\n# two\n\n# three\n# four\n"),
                (
                    "code.py",
                    "# one\n# two\n# three\nX = 1\n# four\n# five\n# six\n",
                ),
                (
                    "trailing.py",
                    "a = 1  # one\nb = 2  # two\nc = 3  # three\nd = 4  # four\n",
                ),
                ("mixed.py", "# one\n# two\n# three\nX = 1  # four\n"),
            ],
        );

        let violations = scan_comment_violations(&root).unwrap();

        assert_eq!(spans(&violations), [("comment", "scripts/a.py", 1, 4)]);
        assert_eq!(violations[0].length, 4);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn fingerprints_match_the_python_gate_byte_for_byte() {
        let root = repo("parity", &[("doc.py", PARITY_FIXTURE)]);

        let found: Vec<(usize, usize, String)> = scan_comment_violations(&root)
            .unwrap()
            .into_iter()
            .map(|v| (v.line, v.end_line, v.fingerprint))
            .collect();

        let expected = [
            (
                1,
                5,
                "docstring:536ef99445a37cba23d1092038e0ea923f4f97de291441ad13693e1b6ac3887c",
            ),
            (
                9,
                12,
                "docstring:c2aa7da03c1be3beb782b0d2d6785e610359fb8ad789e8d833cce0f622fe6bec",
            ),
            (
                15,
                18,
                "docstring:227f63dc9f7bca9533a12e4f909909973dd3ebf69d517ac42e8c39d78a3cdaab",
            ),
            (
                28,
                31,
                "comment:9c6eaa680293d5d19e6a5d9cc580125a27fa0044bdaa28010c21ee9c7d2c9f66",
            ),
        ]
        .map(|(line, end, fp)| (line, end, fp.to_string()));
        assert_eq!(found, expected);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn scan_fails_closed_on_broken_syntax_and_missing_trees() {
        let root = repo("broken", &[("broken.py", "def broken(:\n    pass\n")]);
        assert!(error_of(scan_comment_violations(&root)).contains("scripts/broken.py"));

        fs::remove_dir_all(root.join("scripts")).unwrap();
        assert!(error_of(scan_comment_violations(&root)).contains("no source trees found"));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn check_requires_exact_occurrence_counts() {
        let root = repo("counts", &[("a.py", LETTER_BLOCK), ("b.py", LETTER_BLOCK)]);
        let baseline = root.join("baseline.json");
        run_init(&root, &baseline, true).unwrap();
        assert!(check_comment_baseline(&root, &baseline).unwrap().ok());

        write_script(&root, "c.py", LETTER_BLOCK);
        let grown = check_comment_baseline(&root, &baseline).unwrap();
        assert_eq!(spans(&grown.new), [("comment", "scripts/c.py", 1, 4)]);
        assert!(grown.stale.is_empty());
        assert_eq!(run_check(&root, &baseline, true).unwrap(), 1);

        fs::remove_file(root.join("scripts/c.py")).unwrap();
        write_script(&root, "b.py", "X = 1\n");
        let shrunk = check_comment_baseline(&root, &baseline).unwrap();
        assert!(shrunk.new.is_empty());
        let stale = &shrunk.stale[0];
        assert_eq!((stale.expected, stale.found), (2, 1));
        assert_eq!(stale.paths, ["scripts/a.py", "scripts/b.py"]);
        assert_eq!(run_check(&root, &baseline, false).unwrap(), 1);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn untrusted_baselines_fail_naming_the_file() {
        let root = repo("malformed", &[("a.py", "X = 1\n")]);
        let baseline = root.join("baseline.json");
        let fp = format!("comment:{}", "a".repeat(64));
        let cases = [
            None,
            Some("{not-json".to_string()),
            Some(r#"{"schema_version": 2, "violations": []}"#.to_string()),
            Some(format!(
                r#"{{"schema_version": 1, "violations": [{{"fingerprint": "{fp}", "count": 0, "paths": ["x"]}}]}}"#
            )),
            Some(format!(
                r#"{{"schema_version": 1, "violations": [{{"fingerprint": "{fp}", "count": 1, "paths": ["b", "a"]}}]}}"#
            )),
        ];
        for document in cases {
            if let Some(text) = &document {
                fs::write(&baseline, text).unwrap();
            }
            let message = error_of(check_comment_baseline(&root, &baseline));
            assert!(message.contains("baseline.json"), "{document:?}: {message}");
        }
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn writers_never_overwrite_or_grow_the_baseline() {
        let root = repo("writers", &[("a.py", LETTER_BLOCK), ("b.py", WORD_BLOCK)]);
        let baseline = root.join("baseline.json");
        run_init(&root, &baseline, false).unwrap();
        let initial = fs::read(&baseline).unwrap();
        assert!(error_of(run_init(&root, &baseline, false)).contains("baseline.json"));
        assert_eq!(fs::read(&baseline).unwrap(), initial);

        write_script(&root, "a.py", "X = 1\n");
        run_update(&root, &baseline, false).unwrap();
        let shrunk = fs::read(&baseline).unwrap();
        assert_eq!(run_check(&root, &baseline, false).unwrap(), 0);

        write_script(&root, "c.py", LETTER_BLOCK);
        assert!(error_of(run_update(&root, &baseline, false)).contains("growth"));
        assert_eq!(fs::read(&baseline).unwrap(), shrunk);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn json_output_matches_python_dumps_formatting() {
        let value = json!({
            "b": ["é", "x\"\\\n\t\u{1}"],
            "a": {},
            "c": [],
            "d": [{"z": 1, "y": true, "x": null}],
        });
        let indented = "{\n  \"a\": {},\n  \"b\": [\n    \"\\u00e9\",\n    \"x\\\"\\\\\\n\\t\\u0001\"\n  ],\n  \"c\": [],\n  \"d\": [\n    {\n      \"x\": null,\n      \"y\": true,\n      \"z\": 1\n    }\n  ]\n}";
        assert_eq!(py_json(&value, Some(2)), indented);
        assert_eq!(
            py_json(&json!({"b": ["é😀"], "a": 1}), None),
            r#"{"a": 1, "b": ["\u00e9\ud83d\ude00"]}"#
        );
    }
}
