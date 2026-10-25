//! Knowledge/memory-family tools: recall/record, memory lifecycle, knowledge docs, compass router.
//!
//! Ports `src/cairn/mcp_server/tools_memory.py`, `tools_knowledge.py`,
//! `tools_compass.py`, and the `cairn.memory` / `cairn.knowledge` /
//! `cairn.okf` / `cairn.refs` modules they call. Responses are byte-parity
//! with the Python tools (see `rust/golden/mcp/*.json`).

use std::collections::HashMap;
use std::collections::HashSet;
use std::fmt;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use std::time::{SystemTime, UNIX_EPOCH};

use regex::Regex;
use rusqlite::Connection;
use yaml_rust2::Yaml;
use yaml_rust2::yaml::Hash;

use crate::tools_search::{cosine_scan, embed_query, rrf_fuse, semantic_env, semantic_provenance};

// --- errors -----------------------------------------------------------------

/// Tool failure: the message a Python tool would have raised with.
#[derive(Debug, Clone)]
pub struct ToolError(pub String);

impl fmt::Display for ToolError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for ToolError {}

fn err<T>(msg: impl Into<String>) -> Result<T, ToolError> {
    Err(ToolError(msg.into()))
}

/// String-valued YAML node (never auto-typed; `Yaml::from_str` would coerce
/// number/bool-looking titles into scalars).
pub fn ys(s: impl Into<String>) -> Yaml {
    Yaml::String(s.into())
}

// --- time helpers (no chrono dep) -------------------------------------------

/// Current UTC time as `YYYY-MM-DDTHH:MM:SSZ` (Python's `%Y-%m-%dT%H:%M:%SZ`).
pub fn iso_utc_now() -> String {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock before epoch");
    format_iso_utc(now.as_secs() as i64)
}

/// Python `datetime.now(timezone.utc).isoformat()` (microseconds + offset).
pub fn iso_utc_micros() -> String {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock before epoch");
    let days = now.as_secs() as i64 / 86_400;
    let rem = now.as_secs() as i64 % 86_400;
    let (y, m, d) = civil_from_days(days);
    format!(
        "{y:04}-{m:02}-{d:02}T{h:02}:{mi:02}:{s:02}.{micros:06}+00:00",
        h = rem / 3600,
        mi = (rem % 3600) / 60,
        s = rem % 60,
        micros = now.subsec_micros()
    )
}

/// UTC date as `YYYY-MM-DD` (Python's `%Y-%m-%d`).
pub fn iso_utc_date_now() -> String {
    iso_utc_now()[..10].to_string()
}

/// Format epoch seconds as `YYYY-MM-DDTHH:MM:SSZ`.
pub fn format_iso_utc(secs: i64) -> String {
    let days = secs.div_euclid(86_400);
    let rem = secs.rem_euclid(86_400);
    let (y, m, d) = civil_from_days(days);
    format!(
        "{y:04}-{m:02}-{d:02}T{h:02}:{mi:02}:{s:02}Z",
        h = rem / 3600,
        mi = (rem % 3600) / 60,
        s = rem % 60,
    )
}

/// Days-since-epoch to (year, month, day); Howard Hinnant's civil_from_days.
fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (if m <= 2 { y + 1 } else { y }, m, d)
}

/// (year, month, day) to days-since-epoch; Hinnant's days_from_civil.
fn days_from_civil(y: i64, m: u32, d: u32) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = y.div_euclid(400);
    let yoe = y.rem_euclid(400);
    let mp = if m > 2 { m - 3 } else { m + 9 } as i64;
    let doy = (153 * mp + 2) / 5 + d as i64 - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

/// Parse an ISO-8601 date/datetime to epoch seconds. `naive_as_utc` mirrors
/// Python's split: `parse_memory_timestamp` rejects naive values while
/// `_parse_instant` accepts them as UTC.
pub fn parse_iso_ts(value: &str, naive_as_utc: bool) -> Option<i64> {
    let v = value.trim();
    let re = ts_re();
    let caps = re.captures(v)?;
    let y: i64 = caps["y"].parse().ok()?;
    let mo: u32 = caps["mo"].parse().ok()?;
    let d: u32 = caps["d"].parse().ok()?;
    let h: i64 = caps
        .name("h")
        .and_then(|m| m.as_str().parse().ok())
        .unwrap_or(0);
    let mi: i64 = caps
        .name("mi")
        .and_then(|m| m.as_str().parse().ok())
        .unwrap_or(0);
    let s: i64 = caps
        .name("s")
        .and_then(|m| m.as_str().parse().ok())
        .unwrap_or(0);
    let mut secs = days_from_civil(y, mo, d) * 86_400 + h * 3600 + mi * 60 + s;
    match caps.name("off") {
        Some(off) => {
            let off = off.as_str();
            if off != "Z" && off != "z" {
                let sign: i64 = if off.starts_with('-') { -1 } else { 1 };
                let digits: String = off.chars().filter(|c| c.is_ascii_digit()).collect();
                if digits.len() >= 2 {
                    let oh: i64 = digits[..2].parse().ok()?;
                    let om: i64 = digits.get(2..4).and_then(|x| x.parse().ok()).unwrap_or(0);
                    secs -= sign * (oh * 3600 + om * 60);
                }
            }
        }
        None => {
            if !naive_as_utc {
                return None;
            }
        }
    }
    Some(secs)
}

fn ts_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"^(?<y>\d{4})-(?<mo>\d{2})-(?<d>\d{2})(?:[T ](?<h>\d{2}):(?<mi>\d{2})(?::(?<s>\d{2}))?(?:\.\d+)?\s*(?<off>Z|z|[+-]\d{2}:?\d{2})?)?$",
        )
        .expect("ts regex")
    })
}

/// Fixed-width UTC chronological sort key (`_instant_key`).
fn instant_key(secs: i64) -> String {
    let mut s = format_iso_utc(secs);
    s.truncate(19);
    s.push_str(".000000");
    s
}

// --- unique suffixes (Python uuid4().hex[:6] role) --------------------------

fn unique_suffix() -> String {
    use std::sync::atomic::{AtomicU64, Ordering};
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let n = COUNTER.fetch_add(1, Ordering::Relaxed);
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos() as u64)
        .unwrap_or(0);
    let mut h = (nanos
        ^ n.wrapping_mul(0x9E37_79B9_7F4A_7C15)
        ^ ((std::process::id() as u64) << 32)) as u64;
    h ^= h << 13;
    h ^= h >> 7;
    h ^= h << 17;
    format!("{:06x}", h & 0xFFFFFF)
}

fn unique_ref_id() -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    format!("{nanos:032x}{:04x}", unique_suffix().len())
}

// --- tokenization (graph/tokenize.py port) ----------------------------------

pub const BASE_STOP_WORDS: &[&str] = &[
    "the", "how", "does", "what", "is", "where", "when", "why", "this", "that", "are", "can",
    "work", "for", "with", "from", "into", "about", "which", "their", "there", "will", "would",
    "should", "could", "have", "been", "some", "just", "only", "also", "did", "not", "but", "and",
    "or", "all", "any", "use", "used", "using", "our", "my", "we", "do", "if", "has", "had", "was",
    "were", "get", "got", "make", "made", "way",
];

/// Split on non-alphanumeric boundaries, lowercase, filter stop words.
pub fn simple_tokenize(text: &str, extra_stop: Option<&HashSet<String>>) -> Vec<String> {
    let stop: HashSet<&str> = BASE_STOP_WORDS.iter().copied().collect();
    let mut tokens = Vec::new();
    let mut seen = HashSet::new();
    for tok in text.split(|c: char| !c.is_ascii_alphanumeric()) {
        let tok = tok.trim().to_ascii_lowercase();
        if tok.len() >= 3 && !stop.contains(tok.as_str()) && !seen.contains(&tok) {
            if extra_stop.is_some_and(|extra| extra.contains(&tok)) {
                continue;
            }
            seen.insert(tok.clone());
            tokens.push(tok);
        }
    }
    tokens
}

// --- reference extraction + graph existence (refs.py port) ------------------

pub const FILE_EXTENSIONS: &[&str] = &[
    ".kt", ".java", ".swift", ".py", ".ts", ".tsx", ".js", ".jsx", ".dart", ".m", ".mm",
];

fn is_file_like(token: &str) -> bool {
    token.contains('/') || FILE_EXTENSIONS.iter().any(|e| token.ends_with(e))
}

/// Backtick-quoted file-path refs, order-preserving deduped.
pub fn extract_file_refs(body: &str) -> Vec<String> {
    let mut refs = Vec::new();
    let mut seen = HashSet::new();
    for m in backtick_tokens(body) {
        if !is_file_like(&m) {
            continue;
        }
        if m.starts_with("./") || m.starts_with("cairn ") {
            continue;
        }
        if seen.insert(m.clone()) {
            refs.push(m);
        }
    }
    refs
}

/// Backtick-quoted symbol/qualified-name refs (not deduped, like Python).
pub fn extract_symbol_refs(body: &str) -> Vec<String> {
    static SYMBOL_RE: OnceLock<Regex> = OnceLock::new();
    let re = SYMBOL_RE.get_or_init(|| {
        Regex::new(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*(?:\(\))?$")
            .expect("symbol regex")
    });
    backtick_tokens(body)
        .into_iter()
        .filter(|m| !is_file_like(m) && re.is_match(m))
        .collect()
}

fn backtick_tokens(body: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut rest = body;
    while let Some(start) = rest.find('`') {
        let after = &rest[start + 1..];
        match after.find('`') {
            Some(end) => {
                out.push(after[..end].to_string());
                rest = &after[end + 1..];
            }
            None => break,
        }
    }
    out
}

fn escape_like(value: &str) -> String {
    value
        .replace('\\', "\\\\")
        .replace('%', "\\%")
        .replace('_', "\\_")
}

/// True when `ref` resolves to a stored file path or directory prefix.
pub fn file_exists(conn: &Connection, ref_: &str) -> Result<bool, rusqlite::Error> {
    let ref_ = ref_.trim_matches('/');
    if ref_.is_empty() {
        return Ok(false);
    }
    let (a, b, c, d) = path_match_params(ref_);
    let hit = conn.query_row(
        "SELECT 1 FROM files WHERE (path = ? OR path LIKE ? ESCAPE '\\' OR path LIKE ? ESCAPE '\\' OR path LIKE ? ESCAPE '\\') LIMIT 1",
        rusqlite::params![a, b, c, d],
        |_| Ok(()),
    );
    if hit.is_ok() {
        return Ok(true);
    }
    let Some((rid, rest)) = ref_.split_once('/') else {
        return Ok(false);
    };
    let (a, b, c, d) = path_match_params(rest);
    let hit = conn.query_row(
        "SELECT 1 FROM files WHERE repo_id = ? AND (path = ? OR path LIKE ? ESCAPE '\\' OR path LIKE ? ESCAPE '\\' OR path LIKE ? ESCAPE '\\') LIMIT 1",
        rusqlite::params![rid, a, b, c, d],
        |_| Ok(()),
    );
    Ok(hit.is_ok())
}

/// The 4 LIKE params matching a path ref (exact, suffix, root prefix, mid prefix).
pub fn path_match_params(ref_: &str) -> (String, String, String, String) {
    let e = escape_like(ref_);
    (
        ref_.to_string(),
        format!("%/{e}"),
        format!("{e}/%"),
        format!("%/{e}/%"),
    )
}

/// True when `name` matches a symbol name or qualified-name reference.
pub fn symbol_exists(conn: &Connection, name: &str) -> Result<bool, rusqlite::Error> {
    let bare = name.strip_suffix("()").unwrap_or(name);
    let hit = conn.query_row(
        "SELECT 1 FROM symbols WHERE name = ? OR qualified_name = ? OR qualified_name LIKE ? ESCAPE '\\' LIMIT 1",
        rusqlite::params![bare, bare, format!("%.{}", escape_like(bare))],
        |_| Ok(()),
    );
    if hit.is_ok() {
        return Ok(true);
    }
    if bare.contains('.') {
        let last = bare.rsplit('.').next().unwrap_or(bare);
        let hit = conn.query_row(
            "SELECT 1 FROM symbols WHERE name = ? LIMIT 1",
            rusqlite::params![last],
            |_| Ok(()),
        );
        return Ok(hit.is_ok());
    }
    Ok(false)
}

// --- privacy filter (memory/privacy.py port) ---------------------------------

/// Strip `<private>` tags, URI credentials, and secret-looking tokens.
pub fn strip_private_data(text: &str) -> String {
    static PRIVACY: OnceLock<Privacy> = OnceLock::new();
    let p = PRIVACY.get_or_init(Privacy::new);
    let out = p.private_tag.replace_all(text, "[REDACTED]");
    let out = p.uri_cred.replace(&out, "${1}://[REDACTED_SECRET]@");
    let mut out = out.to_string();
    for re in &p.secrets {
        out = re.replace_all(&out, "[REDACTED_SECRET]").to_string();
    }
    out
}

struct Privacy {
    private_tag: Regex,
    uri_cred: Regex,
    secrets: Vec<Regex>,
}

impl Privacy {
    fn new() -> Self {
        let secret_sources = [
            r#"(?:api[_-]?key|secret|token|password|credential|auth)[\s]*[=:]\s*["']?[A-Za-z0-9_\-/.+]{20,}["']?"#,
            r"Bearer\s+[A-Za-z0-9._\-+/=]{20,}",
            r"sk-proj-[A-Za-z0-9\-_]{20,}",
            r"(?:sk|pk|rk|ak)-[A-Za-z0-9][A-Za-z0-9\-_]{19,}",
            r"sk-ant-[A-Za-z0-9\-_]{20,}",
            r"gh[pus]_[A-Za-z0-9]{36,}",
            r"github_pat_[A-Za-z0-9_]{22,}",
            r"xoxb-[A-Za-z0-9\-]+",
            r"AKIA[0-9A-Z]{16}",
            r"AIza[A-Za-z0-9\-_]{35}",
            r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
            r"npm_[A-Za-z0-9]{36}",
            r"glpat-[A-Za-z0-9\-_]{20,}",
            r"dop_v1_[A-Za-z0-9]{64}",
        ];
        Self {
            private_tag: Regex::new(r"(?is)<private>[\s\S]*?</private>").expect("private tag regex"),
            uri_cred: Regex::new(
                r"(?i)\b(postgres(?:ql)?|mysql|mariadb|rediss?|mongodb(?:\+srv)?|amqps?|https?|ftps?)://[^/@\s:]*:[^/@\s]+@",
            )
            .expect("uri cred regex"),
            secrets: secret_sources
                .iter()
                .map(|s| Regex::new(&format!("(?i){s}")).expect("secret regex"))
                .collect(),
        }
    }
}

// --- failure signature (memory/recurrence.py port) ----------------------------

/// Stable 16-hex key for the normalized shape of one tool failure.
pub fn failure_signature(tool_name: &str, error: &str) -> String {
    use sha2::{Digest, Sha256};

    struct Shape {
        ws: Regex,
        uuid: Regex,
        path: Regex,
        hex: Regex,
        digits: Regex,
    }
    static SHAPE: OnceLock<Shape> = OnceLock::new();
    let s = SHAPE.get_or_init(|| Shape {
        ws: Regex::new(r"\s+").expect("ws regex"),
        uuid: Regex::new(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
            .expect("uuid regex"),
        path: Regex::new(r"(?:/[A-Za-z0-9._-]+)+").expect("path regex"),
        hex: Regex::new(r"\b[0-9a-f]{8,}\b").expect("hex regex"),
        digits: Regex::new(r"\d+").expect("digits regex"),
    });
    let text = s.ws.replace_all(error, " ").trim().to_lowercase();
    let text = s.uuid.replace_all(&text, " ");
    let text = s.path.replace_all(&text, " ");
    let text = s.hex.replace_all(&text, " ");
    let text = s.digits.replace_all(&text, "0");
    let text: String = text.chars().take(200).collect();
    let digest = Sha256::digest(format!("{tool_name}\n{text}").as_bytes());
    digest
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect::<String>()[..16]
        .to_string()
}

// --- slugify (okf/utils.py port) ---------------------------------------------

/// URL-safe slug: lowercase, non-alnum runs to hyphens, trimmed, max 60 chars.
pub fn slugify(text: &str) -> String {
    let mut out = String::new();
    let mut prev_dash = true; // swallows leading runs
    for c in text.chars() {
        if c.is_ascii_alphanumeric() {
            out.push(c.to_ascii_lowercase());
            prev_dash = false;
        } else if !prev_dash {
            out.push('-');
            prev_dash = true;
        }
    }
    let trimmed = out.trim_matches('-');
    trimmed.chars().take(60).collect()
}

/// Python str.title(): uppercase each alphabetic run's first char, lowercase the rest.
pub fn py_title(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut prev_alpha = false;
    for c in s.chars() {
        if c.is_alphabetic() {
            if prev_alpha {
                out.extend(c.to_lowercase());
            } else {
                out.extend(c.to_uppercase());
            }
        } else {
            out.push(c);
        }
        prev_alpha = c.is_alphabetic();
    }
    out
}

// --- YAML frontmatter (okf/concept.py port) ----------------------------------

/// An OKF concept: frontmatter fields + producer extensions + markdown body.
#[derive(Debug, Clone, Default)]
pub struct Concept {
    pub type_: String,
    pub title: Option<String>,
    pub description: Option<String>,
    pub resource: Option<String>,
    pub tags: Vec<String>,
    pub timestamp: Option<String>,
    pub generated_by: Option<String>,
    pub status: Option<String>,
    pub stale_after: Option<String>,
    pub concept_id: String,
    pub body: String,
    pub extensions: Vec<(String, Yaml)>,
}

impl Concept {
    /// Read a concept from `path`; `concept_id` is the absolute path sans .md.
    pub fn from_file(path: &Path) -> std::io::Result<Concept> {
        let text = std::fs::read_to_string(path)
            .map_err(|e| std::io::Error::new(e.kind(), format!("{}: {e}", path.display())))?;
        let concept_id = path.with_extension("").to_string_lossy().to_string();
        Ok(Self::from_text(&text, concept_id))
    }

    /// Parse concept text; unparsable/absent frontmatter leaves the whole text as body.
    pub fn from_text(text: &str, concept_id: String) -> Concept {
        let Some((fm, body)) = split_frontmatter(text) else {
            return Concept {
                concept_id,
                body: text.to_string(),
                ..Default::default()
            };
        };
        let mut ext_map = fm;
        let take = |m: &mut Vec<(String, Yaml)>, k: &str| {
            m.iter()
                .position(|(key, _)| key == k)
                .map(|i| m.remove(i).1)
        };
        let type_ = str_of(take(&mut ext_map, "type").as_ref()).unwrap_or_default();
        let title = str_of(take(&mut ext_map, "title").as_ref());
        let description = str_of(take(&mut ext_map, "description").as_ref());
        let resource = str_of(take(&mut ext_map, "resource").as_ref());
        let tags = take(&mut ext_map, "tags")
            .map(|v| str_vec(&v))
            .unwrap_or_default();
        let mut generated_by = None;
        let mut timestamp = None;
        if let Some(Yaml::Hash(generated)) = take(&mut ext_map, "generated") {
            generated_by = str_of(generated.get(&ys("by")));
            timestamp = str_of(generated.get(&ys("at")));
        }
        if timestamp.is_none() {
            timestamp = str_of(take(&mut ext_map, "timestamp").as_ref());
        }
        let _ = take(&mut ext_map, "okf_version");
        let status = str_of(take(&mut ext_map, "status").as_ref());
        let stale_after = str_of(take(&mut ext_map, "stale_after").as_ref());
        Concept {
            type_,
            title,
            description,
            resource,
            tags,
            timestamp,
            generated_by,
            status,
            stale_after,
            concept_id,
            body,
            extensions: ext_map,
        }
    }

    pub fn ext(&self, key: &str) -> Option<&Yaml> {
        self.extensions
            .iter()
            .find(|(k, _)| k == key)
            .map(|(_, v)| v)
    }

    /// Extension value as a string exactly as stored (Python `.get(key)`).
    pub fn ext_str(&self, key: &str) -> Option<&str> {
        match self.ext(key) {
            Some(Yaml::String(s)) | Some(Yaml::Real(s)) => Some(s.as_str()),
            _ => None,
        }
    }

    pub fn ext_f64(&self, key: &str) -> Option<f64> {
        match self.ext(key) {
            Some(Yaml::Real(s)) => s.parse().ok(),
            Some(Yaml::Integer(i)) => Some(*i as f64),
            _ => None,
        }
    }

    pub fn ext_bool(&self, key: &str) -> Option<bool> {
        match self.ext(key) {
            Some(Yaml::Boolean(b)) => Some(*b),
            _ => None,
        }
    }

    /// Render the concept as markdown with YAML frontmatter (`to_markdown`).
    pub fn to_markdown(&self) -> String {
        let mut fm = Hash::new();
        fm.insert(ys("type"), ys(&self.type_));
        if let Some(t) = &self.title {
            fm.insert(ys("title"), ys(t));
        }
        if let Some(d) = &self.description {
            fm.insert(ys("description"), ys(d));
        }
        if let Some(r) = &self.resource {
            fm.insert(ys("resource"), ys(r));
        }
        if !self.tags.is_empty() {
            fm.insert(ys("tags"), str_list_yaml(&self.tags));
        }
        let ts = self.timestamp.clone().unwrap_or_else(iso_utc_now);
        let mut generated = Hash::new();
        generated.insert(
            ys("by"),
            ys(self
                .generated_by
                .clone()
                .unwrap_or_else(|| format!("cairn/{}", env!("CARGO_PKG_VERSION")))),
        );
        generated.insert(ys("at"), ys(&ts));
        fm.insert(ys("generated"), Yaml::Hash(generated));
        if let Some(s) = &self.status {
            fm.insert(ys("status"), ys(s));
        }
        if let Some(s) = &self.stale_after {
            fm.insert(ys("stale_after"), ys(s));
        }
        fm.insert(ys("okf_version"), ys("0.2"));
        for (k, v) in &self.extensions {
            fm.insert(ys(k), v.clone());
        }
        format!("---\n{}\n---\n\n{}", emit_yaml(&Yaml::Hash(fm)), self.body)
    }

    /// Write the concept atomically (temp file + rename).
    pub fn to_file(&self, path: &Path) -> std::io::Result<()> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let tmp = path.with_extension(format!("md.{}.{}.tmp", std::process::id(), unique_suffix()));
        std::fs::write(&tmp, self.to_markdown())?;
        match std::fs::rename(&tmp, path) {
            Ok(()) => Ok(()),
            Err(e) => {
                let _ = std::fs::remove_file(&tmp);
                Err(e)
            }
        }
    }
}

fn str_of(v: Option<&Yaml>) -> Option<String> {
    match v {
        Some(Yaml::String(s)) => Some(s.clone()),
        Some(Yaml::Null) | None => None,
        Some(other) => Some(scalar_repr(other)),
    }
}

fn str_vec(v: &Yaml) -> Vec<String> {
    match v {
        Yaml::Array(items) => items
            .iter()
            .map(|i| str_of(Some(i)).unwrap_or_default())
            .collect(),
        Yaml::String(s) if s.is_empty() => Vec::new(),
        other => vec![str_of(Some(other)).unwrap_or_default()],
    }
}

fn str_list_yaml(items: &[String]) -> Yaml {
    Yaml::Array(items.iter().map(ys).collect())
}

/// Python-str() rendering of a non-string YAML scalar.
fn scalar_repr(v: &Yaml) -> String {
    match v {
        Yaml::Boolean(true) => "True".into(),
        Yaml::Boolean(false) => "False".into(),
        Yaml::Integer(i) => i.to_string(),
        Yaml::Real(s) => s.clone(),
        Yaml::Null => "None".into(),
        Yaml::Array(items) => {
            let inner: Vec<String> = items.iter().map(scalar_repr).collect();
            format!("[{}]", inner.join(", "))
        }
        Yaml::Hash(h) => {
            let inner: Vec<String> = h
                .iter()
                .map(|(k, v)| format!("{}: {}", scalar_repr(k), scalar_repr(v)))
                .collect();
            format!("{{{}}}", inner.join(", "))
        }
        Yaml::String(s) => s.clone(),
        other => format!("{other:?}"),
    }
}

/// Split `---` frontmatter per `OKFConcept._split_frontmatter`.
fn split_frontmatter(text: &str) -> Option<(Vec<(String, Yaml)>, String)> {
    if !text.starts_with("---") {
        return None;
    }
    let close = frontmatter_close(text)?;
    let line_end = text[close..].find('\n').map(|i| close + i);
    let body_start = line_end.map_or(text.len(), |i| i + 1);
    let yaml_text = &text[3..close];
    let body = text[body_start..].trim_start_matches('\n').to_string();
    match yaml_rust2::YamlLoader::load_from_str(yaml_text) {
        Ok(docs) => match docs.into_iter().next() {
            Some(Yaml::Hash(h)) => Some((
                h.into_iter().map(|(k, v)| (scalar_repr(&k), v)).collect(),
                body,
            )),
            _ => None,
        },
        Err(_) => None,
    }
}

/// Offset of the first line exactly `---` past the delimiter, else None.
fn frontmatter_close(text: &str) -> Option<usize> {
    let mut offset = 3;
    while offset < text.len() {
        let found = text[offset..].find('\n').map(|i| offset + i);
        let line_end = found.unwrap_or(text.len());
        if text[offset..line_end].trim_end_matches([' ', '\t', '\r']) == "---" {
            return Some(offset);
        }
        offset = found? + 1;
    }
    None
}

// --- YAML emitter (PyYAML block-style compatible) -----------------------------

/// Emit a Yaml value in PyYAML `safe_dump(sort_keys=False)` block style.
pub fn emit_yaml(value: &Yaml) -> String {
    let mut out = String::new();
    emit_node(value, 0, &mut out);
    out.trim_end_matches('\n').to_string()
}

fn emit_node(value: &Yaml, indent: usize, out: &mut String) {
    match value {
        Yaml::Hash(h) => {
            if h.is_empty() {
                out.push_str("{}\n");
                return;
            }
            for (k, v) in h {
                let key = emit_inline_scalar(k);
                match v {
                    Yaml::Hash(inner) if !inner.is_empty() => {
                        out.push_str(&" ".repeat(indent));
                        out.push_str(&format!("{key}:\n"));
                        emit_node(v, indent + 2, out);
                    }
                    Yaml::Array(items)
                        if !items.is_empty()
                            && items
                                .iter()
                                .all(|i| !matches!(i, Yaml::Hash(_) | Yaml::Array(_))) =>
                    {
                        out.push_str(&" ".repeat(indent));
                        out.push_str(&format!("{key}:\n"));
                        for item in items {
                            out.push_str(&" ".repeat(indent));
                            out.push_str(&format!("- {}\n", emit_inline_scalar(item)));
                        }
                    }
                    Yaml::Array(_) => {
                        out.push_str(&" ".repeat(indent));
                        out.push_str(&format!("{key}:\n"));
                        emit_node(v, indent, out);
                    }
                    other => {
                        out.push_str(&" ".repeat(indent));
                        out.push_str(&format!("{key}: {}\n", emit_inline_scalar(other)));
                    }
                }
            }
        }
        Yaml::Array(items) => {
            for item in items {
                match item {
                    Yaml::Hash(h) if !h.is_empty() => {
                        out.push_str(&" ".repeat(indent));
                        out.push_str("- ");
                        let mut inner = String::new();
                        emit_node(item, indent + 2, &mut inner);
                        out.push_str(inner.trim_start());
                    }
                    Yaml::Hash(_) => {
                        out.push_str(&" ".repeat(indent));
                        out.push_str("- {}\n");
                    }
                    other => {
                        out.push_str(&" ".repeat(indent));
                        out.push_str(&format!("- {}\n", emit_inline_scalar(other)));
                    }
                }
            }
        }
        other => {
            out.push_str(&emit_inline_scalar(other));
            out.push('\n');
        }
    }
}

fn emit_inline_scalar(v: &Yaml) -> String {
    match v {
        Yaml::String(s) => quote_yaml(s),
        Yaml::Integer(i) => i.to_string(),
        Yaml::Real(s) => {
            // Keep the value a float on re-parse ("1" would read back as int).
            if s.contains('.') || s.contains(['e', 'E']) {
                s.clone()
            } else {
                format!("{s}.0")
            }
        }
        Yaml::Boolean(b) => b.to_string(),
        Yaml::Null => "null".into(),
        Yaml::Hash(h) if h.is_empty() => "{}".into(),
        Yaml::Array(a) if a.is_empty() => "[]".into(),
        other => scalar_repr(other),
    }
}

fn quote_yaml(s: &str) -> String {
    if plain_safe(s) {
        s.to_string()
    } else {
        format!("'{}'", s.replace('\'', "''"))
    }
}

fn plain_safe(s: &str) -> bool {
    static NUMERIC: OnceLock<Regex> = OnceLock::new();
    static TS: OnceLock<Regex> = OnceLock::new();
    if s.is_empty()
        || s.starts_with(' ')
        || s.ends_with(' ')
        || s.contains(": ")
        || s.contains(" #")
        || s.contains('\n')
        || s.contains('\t')
        || s.ends_with(':')
    {
        return false;
    }
    if NUMERIC
        .get_or_init(|| Regex::new(r"^-?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$").expect("num regex"))
        .is_match(s)
    {
        return false;
    }
    if matches!(
        s.to_ascii_lowercase().as_str(),
        "null" | "~" | "true" | "false" | "yes" | "no" | "on" | "off"
    ) {
        return false;
    }
    if TS
        .get_or_init(|| Regex::new(r"^\d{4}-\d{2}-\d{2}([T ].*)?$").expect("ts regex"))
        .is_match(s)
    {
        return false;
    }
    !s.starts_with([
        '-', '?', ':', ',', '[', ']', '{', '}', '#', '&', '*', '!', '|', '>', '\'', '"', '%', '@',
        '`',
    ])
}

// --- bundle (okf/bundle.py port) ----------------------------------------------

/// Manages the `.knowledge/` directory tree of OKF concepts.
pub struct Bundle {
    pub root: PathBuf,
}

/// Resolve a path as Python's non-strict `Path.resolve()`: canonicalize the
/// deepest existing ancestor and re-apply the remaining components.
pub(crate) fn resolve_lenient(p: &Path) -> PathBuf {
    if let Ok(c) = p.canonicalize() {
        return c;
    }
    let mut ancestor = p.parent();
    while let Some(a) = ancestor {
        if let Ok(base) = a.canonicalize() {
            let mut resolved = base;
            if let Ok(tail) = p.strip_prefix(a) {
                for comp in tail.components() {
                    match comp {
                        std::path::Component::Normal(seg) => resolved.push(seg),
                        std::path::Component::ParentDir => {
                            resolved.pop();
                        }
                        _ => {}
                    }
                }
            }
            return resolved;
        }
        ancestor = a.parent();
    }
    p.to_path_buf()
}

impl Bundle {
    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self { root: root.into() }
    }

    fn validate_concept_path(&self, concept_id: &str) -> Result<PathBuf, ToolError> {
        let root_abs = resolve_lenient(&self.root);
        let path = self.root.join(format!("{concept_id}.md"));
        let resolved = resolve_lenient(&path);
        if !resolved.starts_with(&root_abs) {
            return err(format!("concept_id escapes bundle root: {concept_id:?}"));
        }
        Ok(resolved)
    }

    /// Read a concept by its ID (path without .md, relative to root).
    pub fn read_concept(&self, concept_id: &str) -> Result<Concept, ToolError> {
        let path = self.validate_concept_path(concept_id)?;
        Concept::from_file(&path).map_err(|e| ToolError(e.to_string()))
    }

    /// The bundle-relative concept file path (escape-checked).
    pub fn concept_file(&self, concept_id: &str) -> Result<PathBuf, ToolError> {
        self.validate_concept_path(concept_id)
    }

    /// Write a concept at `<concept_id>.md`, invalidating the search index.
    pub fn write_concept(&self, concept: &mut Concept) -> Result<(), ToolError> {
        if concept.concept_id.is_empty() {
            return err("concept_id is required to write");
        }
        let path = self.validate_concept_path(&concept.concept_id)?;
        concept
            .to_file(&path)
            .map_err(|e| ToolError(format!("concept write failed: {e}")))?;
        self.update_log("write", &concept.concept_id);
        Ok(())
    }

    /// List concept IDs (relative, no .md), sorted; `index.md`/`log.md` skipped.
    pub fn list_concepts(&self, prefix: Option<&str>) -> Vec<String> {
        let mut ids = Vec::new();
        let Ok(root) = self.root.canonicalize() else {
            return ids;
        };
        let mut stack = vec![root.clone()];
        while let Some(dir) = stack.pop() {
            let Ok(entries) = std::fs::read_dir(&dir) else {
                continue;
            };
            for entry in entries.flatten() {
                let p = entry.path();
                if p.is_dir() {
                    stack.push(p);
                } else if p.extension().is_some_and(|e| e == "md") {
                    let name = p
                        .file_name()
                        .map(|n| n.to_string_lossy().to_string())
                        .unwrap_or_default();
                    if name == "index.md" || name == "log.md" {
                        continue;
                    }
                    let rel = p
                        .strip_prefix(&root)
                        .expect("under root")
                        .with_extension("")
                        .to_string_lossy()
                        .replace('\\', "/");
                    if prefix.is_none_or(|pre| rel.starts_with(pre)) {
                        ids.push(rel);
                    }
                }
            }
        }
        ids.sort();
        ids
    }

    /// Concept id relative to the bundle root; already-relative ids pass through.
    pub fn relativize_id(&self, concept_id: &str) -> String {
        let p = Path::new(concept_id);
        if !p.is_absolute() {
            return concept_id.to_string();
        }
        match resolve_lenient(p).strip_prefix(resolve_lenient(&self.root)) {
            Ok(rel) => rel.to_string_lossy().replace('\\', "/"),
            Err(_) => concept_id.to_string(),
        }
    }

    /// Scored substring search over title/description/tags/body (lowercased counts).
    pub fn search(&self, query: &str, limit: Option<usize>) -> Result<Vec<Concept>, ToolError> {
        let query_lower = query.to_lowercase();
        let mut results: Vec<(usize, Concept)> = Vec::new();
        for cid in self.list_concepts(None) {
            let concept = match self.read_concept(&cid) {
                Ok(c) => c,
                Err(_) => continue,
            };
            let tags_str = concept.tags.join(" ").to_lowercase();
            let score = count_sub(
                &concept.title.as_deref().unwrap_or_default().to_lowercase(),
                &query_lower,
            ) + count_sub(
                &concept
                    .description
                    .as_deref()
                    .unwrap_or_default()
                    .to_lowercase(),
                &query_lower,
            ) + count_sub(&concept.body.to_lowercase(), &query_lower)
                + count_sub(&tags_str, &query_lower);
            if score > 0 {
                results.push((score, concept));
            }
        }
        results.sort_by_key(|(score, _)| std::cmp::Reverse(*score));
        Ok(results
            .into_iter()
            .take(limit.unwrap_or(usize::MAX))
            .map(|(_, c)| c)
            .collect())
    }

    fn update_log(&self, action: &str, concept_id: &str) {
        let log_path = self.root.join("log.md");
        let entry = format!("- {} | {action} | {concept_id}\n", iso_utc_now());
        use std::io::Write;
        match std::fs::OpenOptions::new().append(true).open(&log_path) {
            Ok(mut f) => {
                let _ = f.write_all(entry.as_bytes());
            }
            Err(_) => {
                let _ = std::fs::create_dir_all(&self.root);
                let _ = std::fs::write(&log_path, format!("# OKF Bundle Log\n\n{entry}"));
            }
        }
    }

    /// Advisory cross-process lock over bundle mutations (okf/bundle.py port).
    pub fn lock(&self) -> Result<BundleLockGuard<'_>, ToolError> {
        BundleLockGuard::acquire(self)
    }
}

const BUNDLE_LOCK_TIMEOUT_S: f64 = 5.0;
// Python renders the default timeout as str(5.0)
const BUNDLE_LOCK_TIMEOUT_TEXT: &str = "5.0";

thread_local! {
    static LOCK_DEPTH: std::cell::RefCell<std::collections::HashMap<String, u32>> =
        std::cell::RefCell::new(std::collections::HashMap::new());
}

/// RAII guard for the bundle mutation lock: flock on `.okf.lock`, reentrant
/// per thread (a nested acquire is a no-op, matching the Python lock).
pub struct BundleLockGuard<'a> {
    bundle: &'a Bundle,
    key: String,
    file: Option<std::fs::File>,
}

#[cfg(unix)]
fn flock_ex_nb(file: &std::fs::File) -> bool {
    use std::os::unix::io::AsRawFd;
    // flock(2) via the platform libc std already links; no libc crate needed.
    unsafe extern "C" {
        fn flock(fd: i32, operation: i32) -> i32;
    }
    const LOCK_EX: i32 = 2;
    const LOCK_NB: i32 = 4;
    // Any errno retries until the deadline; only contention is expected.
    unsafe { flock(file.as_raw_fd(), LOCK_EX | LOCK_NB) == 0 }
}

impl<'a> BundleLockGuard<'a> {
    fn acquire(bundle: &'a Bundle) -> Result<BundleLockGuard<'a>, ToolError> {
        let _ = std::fs::create_dir_all(&bundle.root);
        let key = resolve_lenient(&bundle.root).to_string_lossy().to_string();
        let mut nested = false;
        LOCK_DEPTH.with(|depth| {
            let mut depth = depth.borrow_mut();
            let entry = depth.entry(key.clone()).or_insert(0);
            if *entry > 0 {
                *entry += 1;
                nested = true;
            }
        });
        if nested {
            return Ok(BundleLockGuard {
                bundle,
                key,
                file: None,
            });
        }
        let lock_path = bundle.root.join(".okf.lock");
        let file = std::fs::OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(false)
            .open(&lock_path)
            .map_err(|e| ToolError(format!("{}: {e}", lock_path.display())))?;
        let deadline =
            std::time::Instant::now() + std::time::Duration::from_secs_f64(BUNDLE_LOCK_TIMEOUT_S);
        loop {
            #[cfg(unix)]
            let held = flock_ex_nb(&file);
            #[cfg(not(unix))]
            let held = true;
            if held {
                break;
            }
            if std::time::Instant::now() >= deadline {
                return err(format!(
                    "Timed out after {BUNDLE_LOCK_TIMEOUT_TEXT}s waiting for the OKF \
                     bundle lock at {} -- another process is mutating {key}.",
                    lock_path.display()
                ));
            }
            std::thread::sleep(std::time::Duration::from_millis(50));
        }
        // set only once the flock is held: a failed acquire must not read as
        // nested later
        LOCK_DEPTH.with(|depth| depth.borrow_mut().insert(key.clone(), 1));
        Ok(BundleLockGuard {
            bundle,
            key,
            file: Some(file),
        })
    }
}

impl Drop for BundleLockGuard<'_> {
    fn drop(&mut self) {
        if let Some(file) = self.file.take() {
            #[cfg(unix)]
            {
                use std::os::unix::io::AsRawFd;
                unsafe extern "C" {
                    fn flock(fd: i32, operation: i32) -> i32;
                }
                const LOCK_UN: i32 = 8;
                unsafe { flock(file.as_raw_fd(), LOCK_UN) };
            }
            LOCK_DEPTH.with(|depth| depth.borrow_mut().insert(self.key.clone(), 0));
        } else {
            LOCK_DEPTH.with(|depth| {
                if let Some(entry) = depth.borrow_mut().get_mut(&self.key) {
                    *entry = entry.saturating_sub(1);
                }
            });
        }
        let _ = self.bundle;
    }
}

fn count_sub(haystack: &str, needle: &str) -> usize {
    if needle.is_empty() {
        return 0;
    }
    haystack.matches(needle).count()
}

// --- memory store (memory/store.py port) --------------------------------------

pub const TIERS: &[&str] = &["raw", "drafts", "tribal", "archived"];
pub const STANCES: &[&str] = &["preferred", "tentative", "contested"];
pub const TIER_ORDER: &[&str] = &["tribal", "drafts", "raw", "archived"];

fn tier_dir(tier: &str) -> &'static str {
    match tier {
        "raw" => "memory/raw",
        "drafts" => "memory/drafts",
        "tribal" => "memory/tribal",
        _ => "memory/archived",
    }
}

fn tier_type_prefix(tier: &str) -> &'static str {
    match tier {
        "raw" => "Raw",
        "drafts" => "Draft",
        _ => "Tribal",
    }
}

/// Map a memory score to a tier.
pub fn tier_for_score(score: f64) -> &'static str {
    if score < 0.3 {
        "raw"
    } else if score < 0.5 {
        "drafts"
    } else {
        "tribal"
    }
}

/// Python round(x, 3).
pub fn py_round3(x: f64) -> f64 {
    (x * 1000.0).round() / 1000.0
}

/// Validate a bounded agent id or return the error.
pub fn validate_agent_id(agent_id: &str) -> Result<String, ToolError> {
    static RE: OnceLock<Regex> = OnceLock::new();
    if agent_id.is_empty() {
        return err("agent id must be a non-empty string");
    }
    if agent_id.len() > 64 {
        return err("agent id exceeds 64 characters");
    }
    let re = RE.get_or_init(|| Regex::new(r"^[A-Za-z0-9._-]+$").expect("agent id regex"));
    if !re.is_match(agent_id) {
        return err("agent id allows only [A-Za-z0-9._-]");
    }
    Ok(agent_id.to_string())
}

/// Relative-to-root form of a concept id (`_rel_id`/`_norm_cid`).
pub fn rel_cid(bundle: &Bundle, concept_id: &str) -> String {
    let p = Path::new(concept_id);
    if !p.is_absolute() {
        return concept_id.to_string();
    }
    bundle.relativize_id(concept_id)
}

fn in_memory_namespace(bundle: &Bundle, concept: &Concept) -> bool {
    let resolved = rel_cid(bundle, &concept.concept_id);
    resolved == "memory/" || resolved.starts_with("memory/")
}

/// Parse a memory timestamp; naive or malformed input is rejected.
pub fn parse_memory_timestamp(ts: &str) -> Option<i64> {
    parse_iso_ts(ts, false)
}

/// Build a memory concept with lifecycle, stance, and supersession metadata.
#[allow(clippy::too_many_arguments)]
pub fn create_memory(
    type_: &str,
    title: &str,
    body: &str,
    resource: Option<&str>,
    confidence: f64,
    session_origin: Option<&str>,
    tags: Option<Vec<String>>,
    score: Option<f64>,
    supersedes: Option<Vec<String>>,
    stance: Option<&str>,
) -> Result<Concept, ToolError> {
    if let Some(s) = stance.filter(|s| !STANCES.contains(s)) {
        return err(format!(
            "stance must be one of preferred|tentative|contested, got {s:?}"
        ));
    }
    let confidence = confidence.clamp(0.0, 1.0);
    let tier = tier_for_score(score.unwrap_or(confidence));
    let ts = iso_utc_now();
    let mut extensions: Vec<(String, Yaml)> = vec![
        (
            "memory_status".into(),
            ys(if tier == "drafts" { "draft" } else { tier }),
        ),
        (
            "memory_score".into(),
            Yaml::Real(fmt_score(score.unwrap_or(confidence))),
        ),
        (
            "memory_signals".into(),
            Yaml::Hash({
                let mut h = Hash::new();
                h.insert(ys("agent_confidence"), Yaml::Real(fmt_score(confidence)));
                h.insert(ys("freshness"), Yaml::Real("1.0".into()));
                h.insert(ys("staleness_days"), Yaml::Integer(0));
                h
            }),
        ),
        ("memory_tier".into(), ys(tier)),
        ("tier".into(), ys("asserted")),
        ("memory_type".into(), ys(type_)),
        ("session_origin".into(), ys(session_origin.unwrap_or(""))),
        (
            "promotion_history".into(),
            Yaml::Array(vec![Yaml::Hash({
                let mut h = Hash::new();
                h.insert(ys("date"), ys(&ts));
                h.insert(ys("action"), ys("captured"));
                h.insert(ys("score"), Yaml::Real(fmt_score(confidence)));
                h.insert(ys("tier"), ys(tier));
                h
            })]),
        ),
        ("memory_is_latest".into(), Yaml::Boolean(true)),
        (
            "memory_supersedes".into(),
            Yaml::Array(supersedes.unwrap_or_default().into_iter().map(ys).collect()),
        ),
        ("memory_superseded_by".into(), Yaml::Null),
    ];
    if let Some(s) = stance {
        extensions.push(("memory_stance".into(), ys(s)));
    }
    Ok(Concept {
        type_: format!("{}-{type_}", tier_type_prefix(tier)),
        title: Some(title.to_string()),
        description: Some(title.to_string()),
        resource: resource.map(str::to_string),
        tags: tags.unwrap_or_else(|| vec![type_.to_string()]),
        timestamp: Some(ts),
        body: body.to_string(),
        extensions,
        ..Default::default()
    })
}

/// Python-str-style float text ("0.578", "1.0").
pub fn fmt_score(x: f64) -> String {
    let r = py_round3(x);
    let s = format!("{r}");
    if s.contains('.') { s } else { format!("{s}.0") }
}

/// Write validity extensions + the indexed projection row.
pub fn write_validity(
    concept: &mut Concept,
    valid_from: &str,
    valid_until: Option<&str>,
    successor_symbol: Option<&str>,
    bundle: Option<&Bundle>,
    conn: Option<&Connection>,
) -> Result<(), ToolError> {
    if valid_from.is_empty() {
        return err("valid_from is required");
    }
    if concept.concept_id.is_empty() {
        return err("concept_id is required");
    }
    set_ext(&mut concept.extensions, "valid_from", ys(valid_from));
    set_or_remove(&mut concept.extensions, "valid_until", valid_until);
    set_or_remove(
        &mut concept.extensions,
        "successor_symbol",
        successor_symbol,
    );
    if let Some(bundle) = bundle {
        concept.concept_id = rel_cid(bundle, &concept.concept_id);
        bundle.write_concept(concept)?;
    }
    if let Some(conn) = conn {
        conn.execute(
            "INSERT INTO memory_validity (concept_id, valid_from, valid_until, successor_symbol) \
             VALUES (?, ?, ?, ?) ON CONFLICT(concept_id) DO UPDATE SET \
             valid_from = excluded.valid_from, valid_until = excluded.valid_until, \
             successor_symbol = excluded.successor_symbol",
            rusqlite::params![
                concept.concept_id,
                valid_from,
                valid_until,
                successor_symbol
            ],
        )
        .map_err(|e| ToolError(e.to_string()))?;
    }
    Ok(())
}

fn set_ext(ext: &mut Vec<(String, Yaml)>, key: &str, value: Yaml) {
    match ext.iter_mut().find(|(k, _)| k == key) {
        Some(slot) => slot.1 = value,
        None => ext.push((key.to_string(), value)),
    }
}

fn set_or_remove(ext: &mut Vec<(String, Yaml)>, key: &str, value: Option<&str>) {
    match value {
        Some(v) => set_ext(ext, key, ys(v)),
        None => ext.retain(|(k, _)| k != key),
    }
}

/// Write a memory to its tier and move validity and old-file cleanup with it.
pub fn store_memory(
    concept: &mut Concept,
    bundle: &Bundle,
    tier: &str,
    old_id: Option<&str>,
    conn: Option<&Connection>,
) -> Result<String, ToolError> {
    let old_rel = old_id.map(|o| rel_cid(bundle, o));
    let keep_id = old_rel
        .as_ref()
        .is_some_and(|o| o.starts_with(&format!("{}/", tier_dir(tier))));
    if keep_id {
        // Same-tier re-store keeps the id so refs stay attached.
        concept.concept_id = old_rel.as_ref().expect("keep implies old").clone();
    } else {
        let slug = slugify(concept.title.as_deref().unwrap_or(""));
        let slug = if slug.is_empty() {
            "memory".to_string()
        } else {
            slug
        };
        concept.concept_id = if tier == "raw" {
            format!(
                "memory/raw/{}-{slug}-{}",
                iso_utc_date_now(),
                unique_suffix()
            )
        } else {
            format!("{}/{slug}-{}", tier_dir(tier), unique_suffix())
        };
    }
    set_ext(&mut concept.extensions, "memory_tier", ys(tier));
    let valid_from = concept
        .ext_str("valid_from")
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .or_else(|| concept.timestamp.clone())
        .unwrap_or_else(iso_utc_now);
    let valid_until = concept
        .ext_str("valid_until")
        .filter(|s| !s.is_empty())
        .map(str::to_string);
    let successor = concept
        .ext_str("successor_symbol")
        .filter(|s| !s.is_empty())
        .map(str::to_string);
    let moved = old_rel.as_deref().is_some_and(|o| o != concept.concept_id);
    if let Err(e) = write_validity(
        concept,
        &valid_from,
        valid_until.as_deref(),
        successor.as_deref(),
        Some(bundle),
        conn,
    ) {
        // A failed move must not leave the new-tier file beside the old one.
        if moved {
            let new_file = bundle.root.join(format!("{}.md", concept.concept_id));
            if new_file.exists() {
                let _ = std::fs::remove_file(&new_file);
            }
        }
        return Err(e);
    }
    if moved {
        let old_id = old_rel.expect("moved implies old");
        let old_file = bundle.root.join(format!("{old_id}.md"));
        if old_file.exists() {
            let _ = std::fs::remove_file(&old_file);
        }
        if let Some(conn) = conn {
            conn.execute(
                "DELETE FROM memory_validity WHERE concept_id = ?",
                rusqlite::params![old_id],
            )
            .map_err(|e| ToolError(e.to_string()))?;
        }
    }
    Ok(concept.concept_id.clone())
}

/// Read a memory-namespaced concept by full concept id or path.
pub fn get_memory(bundle: &Bundle, path: &str) -> Result<Option<Concept>, ToolError> {
    let bare = path.strip_suffix(".md").unwrap_or(path);
    let mut cid = bare.to_string();
    if !cid.starts_with("memory/") && !cid.contains("/memory/") {
        cid = format!("memory/{cid}");
    }
    let concept = match bundle.read_concept(&cid) {
        Ok(c) => Some(c),
        Err(_) => bundle.read_concept(bare).ok(),
    };
    Ok(concept.filter(|c| in_memory_namespace(bundle, c)))
}

/// List memory concepts, optionally filtered by tier or tag.
pub fn list_memories(bundle: &Bundle, tier: Option<&str>, tag: Option<&str>) -> Vec<Concept> {
    let prefix = tier.map_or_else(|| "memory/".to_string(), |t| format!("{}/", tier_dir(t)));
    let mut out = Vec::new();
    for cid in bundle.list_concepts(Some(&prefix)) {
        let Ok(c) = bundle.read_concept(&cid) else {
            continue;
        };
        if tag.is_some_and(|tag| !c.tags.iter().any(|t| t == tag)) {
            continue;
        }
        out.push(c);
    }
    out
}

/// Delete only a memory-namespaced concept and its exact reference rows.
pub fn delete_memory(
    bundle: &Bundle,
    memory_path: &str,
    conn: Option<&Connection>,
) -> Result<bool, ToolError> {
    let bare = memory_path.strip_suffix(".md").unwrap_or(memory_path);
    let mut cid = bare.to_string();
    if !cid.starts_with("memory/") {
        cid = format!("memory/{cid}");
    }
    let concept = get_memory(bundle, memory_path)?;
    let (abs_cid, rel) = match concept {
        Some(c) => {
            if !in_memory_namespace(bundle, &c) {
                return Ok(false);
            }
            (c.concept_id.clone(), rel_cid(bundle, &c.concept_id))
        }
        None => (cid.clone(), cid.clone()),
    };
    let file_path = match bundle.validate_concept_path(&rel) {
        Ok(p) => p,
        Err(_) => return Ok(false),
    };
    if !file_path.exists() {
        return Ok(false);
    }
    std::fs::remove_file(&file_path).map_err(|e| ToolError(e.to_string()))?;
    if let Some(conn) = conn {
        delete_memory_rows(bundle, &abs_cid, conn)?;
    }
    Ok(true)
}

fn delete_memory_rows(
    bundle: &Bundle,
    concept_id: &str,
    conn: &Connection,
) -> Result<(), ToolError> {
    let rel = rel_cid(bundle, concept_id);
    let mut ids = vec![rel];
    if ids[0] != concept_id {
        ids.push(concept_id.to_string());
    }
    let placeholders = vec!["?"; ids.len()].join(", ");
    let params: Vec<&str> = ids.iter().map(String::as_str).collect();
    for table in [
        "memory_refs(meadow_path)",
        "memory_validity(concept_id)",
        "memory_embeddings(doc_id)",
    ] {
        let (table, column) = table.split_once('(').expect("table spec");
        let column = column.trim_end_matches(')');
        conn.execute(
            &format!("DELETE FROM {table} WHERE {column} IN ({placeholders})"),
            rusqlite::params_from_iter(params.iter()),
        )
        .map_err(|e| ToolError(e.to_string()))?;
    }
    Ok(())
}

/// Move a memory to a strictly lower tier and return its new path.
pub fn demote_memory(
    bundle: &Bundle,
    memory_path: &str,
    target_tier: &str,
    conn: Option<&Connection>,
) -> Result<Option<String>, ToolError> {
    let Some(mut concept) = get_memory(bundle, memory_path)? else {
        return Ok(None);
    };
    let current = concept
        .ext_str("memory_tier")
        .unwrap_or("drafts")
        .to_string();
    let (Some(ti), Some(ci)) = (
        TIER_ORDER.iter().position(|t| *t == target_tier),
        TIER_ORDER.iter().position(|t| *t == current),
    ) else {
        return Ok(None);
    };
    if ti <= ci {
        return Ok(None);
    }
    let old_id = rel_cid(bundle, &concept.concept_id);
    set_ext(&mut concept.extensions, "memory_tier", ys(target_tier));
    let new_id = store_memory(&mut concept, bundle, target_tier, Some(&old_id), conn)?;
    Ok(Some(new_id))
}

/// Promote a memory to compass or wiki and return its new concept id.
pub fn promote_memory(
    bundle: &Bundle,
    memory_path: &str,
    conn: Option<&Connection>,
) -> Result<Option<String>, ToolError> {
    let mut concept = match get_memory(bundle, memory_path)? {
        Some(c) => c,
        None => return Ok(None),
    };
    let mtype = concept
        .ext_str("memory_type")
        .unwrap_or("decision")
        .to_string();
    let compass_bound = matches!(
        mtype.as_str(),
        "decision" | "pattern" | "mistake" | "workaround"
    );
    let unique = unique_suffix();
    let title_slug = slugify(concept.title.as_deref().unwrap_or(""));
    let new_id = if compass_bound {
        format!("compass/promoted-{title_slug}-{unique}")
    } else {
        format!("wiki/features/promoted-{title_slug}-{unique}")
    };
    concept.type_ = if compass_bound {
        "Compass".to_string()
    } else {
        "Wiki-Feature".to_string()
    };
    set_ext(&mut concept.extensions, "memory_status", ys("canonical"));
    concept.extensions.retain(|(k, _)| k != "memory_tier");
    let old_id = rel_cid(bundle, &concept.concept_id);
    concept.concept_id = new_id.clone();
    let score = concept.ext_f64("memory_score").unwrap_or(0.0);
    append_promotion(&mut concept, "force_promote", score);
    bundle.write_concept(&mut concept)?;
    if let Some(conn) = conn {
        rename_memory_embedding(conn, &old_id, &new_id)?;
    }
    let old_file = bundle.root.join(format!("{old_id}.md"));
    if old_file.exists() {
        let _ = std::fs::remove_file(&old_file);
    }
    Ok(Some(new_id))
}

fn rename_memory_embedding(conn: &Connection, old_id: &str, new_id: &str) -> Result<(), ToolError> {
    conn.execute(
        "UPDATE memory_embeddings SET doc_id = ? WHERE doc_id = ?",
        rusqlite::params![new_id, old_id],
    )
    .map_err(|e| ToolError(e.to_string()))?;
    Ok(())
}

/// The deterministic critic's fixed score when no LLM critic is wired.
pub const DEFAULT_CRITIC_SCORE: f64 = 0.5;

fn append_promotion(concept: &mut Concept, action: &str, score: f64) {
    let entry = Yaml::Hash({
        let mut h = Hash::new();
        h.insert(ys("date"), ys(iso_utc_now()));
        h.insert(ys("action"), ys(action));
        h.insert(ys("score"), Yaml::Real(fmt_score(score)));
        h
    });
    match concept
        .extensions
        .iter_mut()
        .find(|(k, _)| k == "promotion_history")
    {
        Some((_, Yaml::Array(items))) => items.push(entry),
        Some(slot) => slot.1 = Yaml::Array(vec![entry]),
        None => concept
            .extensions
            .push(("promotion_history".into(), Yaml::Array(vec![entry]))),
    }
}

fn age_days(ts: &str) -> f64 {
    let Some(secs) = parse_memory_timestamp(ts) else {
        return 0.0;
    };
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    (now - secs) as f64 / 86_400.0
}

/// Expire raw and stale tribal memories into the archived tier
/// (embedding reap rides the embeddings workstream).
pub fn decay(
    bundle: &Bundle,
    raw_max_days: i64,
    tribal_max_stale: i64,
    conn: Option<&Connection>,
) -> Result<(usize, usize), ToolError> {
    let mut expired = 0;
    let mut archived = 0;
    for concept in list_memories(bundle, Some("raw"), None) {
        let expire = concept
            .timestamp
            .as_deref()
            .is_some_and(|ts| age_days(ts) > raw_max_days as f64);
        if expire {
            let mut c = concept;
            let old_id = c.concept_id.clone();
            store_memory(&mut c, bundle, "archived", Some(&old_id), conn)?;
            expired += 1;
        }
    }
    for concept in list_memories(bundle, Some("tribal"), None) {
        let stale = concept
            .timestamp
            .as_deref()
            .is_some_and(|ts| age_days(ts) > tribal_max_stale as f64);
        if stale {
            let mut c = concept;
            let old_id = c.concept_id.clone();
            store_memory(&mut c, bundle, "archived", Some(&old_id), conn)?;
            archived += 1;
        }
    }
    Ok((expired, archived))
}

// --- stance reflect (memory/stance.py port) -------------------------------------

/// Reflect outcome counts (`reflect_store` summary).
pub struct ReflectSummary {
    pub changed: usize,
    pub unchanged: usize,
    pub contested: usize,
}

/// Recompute stance keys from supersession and citation evidence; idempotent.
pub fn reflect_store(bundle: &Bundle, conn: &Connection) -> Result<ReflectSummary, ToolError> {
    let mut summary = ReflectSummary {
        changed: 0,
        unchanged: 0,
        contested: 0,
    };
    let mut memo: HashMap<String, bool> = HashMap::new();
    let _lock = bundle.lock()?;
    for cid in bundle.list_concepts(Some("memory/")) {
        let Ok(mut concept) = bundle.read_concept(&cid) else {
            continue;
        };
        let (changed, final_stance) = reflect_one(bundle, conn, &mut concept, &mut memo)?;
        if changed {
            concept.concept_id = cid;
            bundle.write_concept(&mut concept)?;
            summary.changed += 1;
        } else {
            summary.unchanged += 1;
        }
        if final_stance.as_deref() == Some("contested") {
            summary.contested += 1;
        }
    }
    Ok(summary)
}

fn has_refs(concept: &Concept) -> bool {
    !extract_file_refs(&concept.body).is_empty() || !extract_symbol_refs(&concept.body).is_empty()
}

fn read_peer(bundle: &Bundle, edge: &str) -> Option<Concept> {
    let cid = edge.strip_suffix(".md").unwrap_or(edge);
    bundle.read_concept(cid).ok()
}

fn shares_verified_symbol_ref(
    conn: &Connection,
    a: &Concept,
    b: &Concept,
    memo: &mut HashMap<String, bool>,
) -> Result<bool, ToolError> {
    let a_refs: HashSet<String> = extract_symbol_refs(&a.body).into_iter().collect();
    let b_refs: HashSet<String> = extract_symbol_refs(&b.body).into_iter().collect();
    let mut shared: Vec<String> = a_refs.intersection(&b_refs).cloned().collect();
    shared.sort();
    for ref_ in shared {
        if !memo.contains_key(&ref_) {
            let verified = symbol_exists(conn, &ref_).map_err(|e| ToolError(e.to_string()))?;
            memo.insert(ref_.clone(), verified);
        }
        if memo[&ref_] {
            return Ok(true);
        }
    }
    Ok(false)
}

/// One reflect pass over a concept; mutates stance/baseline keys only.
fn reflect_one(
    bundle: &Bundle,
    conn: &Connection,
    concept: &mut Concept,
    memo: &mut HashMap<String, bool>,
) -> Result<(bool, Option<String>), ToolError> {
    let peer = concept
        .ext_str("memory_superseded_by")
        .and_then(|edge| read_peer(bundle, edge));
    let has_refs = has_refs(concept);
    let fraction =
        py_round3(graph_verification(concept, conn).map_err(|e| ToolError(e.to_string()))?);
    let recorded = concept.ext_f64("memory_refs_baseline");
    let verdict = if let Some(peer) = &peer
        && shares_verified_symbol_ref(conn, concept, peer, memo)?
    {
        Some(("contested", Some(peer.concept_id.clone())))
    } else if has_refs {
        if let Some(baseline) = recorded
            && fraction < baseline
        {
            Some(("tentative", None))
        } else if fraction >= 1.0 {
            Some(("preferred", None))
        } else {
            None
        }
    } else {
        None
    };

    let mut changed = false;
    if has_refs {
        // Seeding cannot fire the drop rule; a present baseline only rises.
        let updated = recorded.map_or(fraction, |r| r.max(fraction));
        if Some(updated) != recorded {
            set_ext(
                &mut concept.extensions,
                "memory_refs_baseline",
                Yaml::Real(fmt_score(updated)),
            );
            changed = true;
        }
    }
    if let Some((verdict, _)) = &verdict
        && concept.ext_str("memory_stance") != Some(verdict)
    {
        set_ext(&mut concept.extensions, "memory_stance", ys(*verdict));
        changed = true;
    }
    match verdict {
        Some(("contested", Some(ref peer_id))) => {
            let peer_rel = rel_cid(bundle, peer_id);
            if concept.ext_str("memory_stance_peer") != Some(peer_rel.as_str()) {
                set_ext(&mut concept.extensions, "memory_stance_peer", ys(&peer_rel));
                changed = true;
            }
        }
        _ => {
            // The peer key is reflect-owned: drop it whenever no contested
            // verdict holds, even when no verdict fired at all.
            if concept
                .extensions
                .iter()
                .any(|(k, _)| k == "memory_stance_peer")
            {
                concept
                    .extensions
                    .retain(|(k, _)| k != "memory_stance_peer");
                changed = true;
            }
        }
    }
    let final_stance = match verdict {
        Some((verdict, _)) => Some(verdict.to_string()),
        None => concept.ext_str("memory_stance").map(str::to_string),
    };
    Ok((changed, final_stance))
}

// --- evolve + batch critic (memory/promotion.py port) ---------------------------

/// Evolve outcome: stored path, tier, score, and the superseded id.
pub struct EvolveResult {
    pub path: String,
    pub tier: String,
    pub score: f64,
    pub superseded: String,
}

/// Create a redacted revision that supersedes `memory_path` (`evolve_memory`).
pub fn evolve_memory(
    conn: &Connection,
    bundle: &Bundle,
    memory_path: &str,
    new_title: Option<&str>,
    new_body: Option<&str>,
) -> Result<Option<EvolveResult>, ToolError> {
    let new_body = new_body.map(strip_private_data);
    let new_title = new_title.map(strip_private_data);
    let _lock = bundle.lock()?;
    let Some(old) = get_memory(bundle, memory_path)? else {
        return Ok(None);
    };
    let mtype = old.ext_str("memory_type").unwrap_or("decision").to_string();
    let norm_old = rel_cid(bundle, &old.concept_id);
    let old_chain: Vec<String> = match old.ext("memory_supersedes") {
        Some(Yaml::Array(items)) => items
            .iter()
            .map(|i| rel_cid(bundle, &str_of(Some(i)).unwrap_or_default()))
            .collect(),
        _ => Vec::new(),
    };
    let mut chain = vec![norm_old];
    chain.extend(old_chain);
    let confidence = old
        .ext("memory_signals")
        .and_then(|v| match v {
            Yaml::Hash(h) => h.get(&ys("agent_confidence")).and_then(|x| match x {
                Yaml::Real(s) => s.parse().ok(),
                Yaml::Integer(i) => Some(*i as f64),
                _ => None,
            }),
            _ => None,
        })
        .unwrap_or(0.7);
    let title = new_title
        .filter(|t| !t.is_empty())
        .or_else(|| old.title.clone())
        .filter(|t| !t.is_empty())
        .unwrap_or_else(|| "memory".to_string());
    let body = new_body
        .filter(|b| !b.is_empty())
        .or_else(|| Some(old.body.clone()))
        .filter(|b| !b.is_empty())
        .unwrap_or_default();
    let mut concept = create_memory(
        &mtype,
        &title,
        &body,
        old.resource.as_deref(),
        confidence,
        None,
        Some(old.tags.clone()),
        None,
        Some(chain),
        None,
    )?;
    let signals = score_memory(&concept, conn)?;
    apply_score(&mut concept, &signals);
    seed_refs_baseline(&mut concept, &signals);
    let tier = tier_for_score(signals["score"]).to_string();
    let new_path = store_memory(&mut concept, bundle, &tier, None, Some(conn))?;
    mark_superseded(bundle, &old.concept_id, &new_path)?;
    append_promotion(&mut concept, "evolve", signals["score"]);
    Ok(Some(EvolveResult {
        path: new_path,
        tier,
        score: signals["score"],
        superseded: old.concept_id,
    }))
}

/// Batch-critic outcome counts.
pub struct BatchCriticResult {
    pub processed: usize,
    pub tribal: usize,
    pub dropped: usize,
    pub remaining_drafts: usize,
}

/// Run the deterministic critic over queued drafts; promote, drop, or keep.
pub fn batch_critic(conn: &Connection, bundle: &Bundle) -> Result<BatchCriticResult, ToolError> {
    let drafts = list_memories(bundle, Some("drafts"), None);
    let mut out = BatchCriticResult {
        processed: drafts.len(),
        tribal: 0,
        dropped: 0,
        remaining_drafts: 0,
    };
    for mut concept in drafts {
        let mut signals = score_memory(&concept, conn)?;
        signals.insert("critic_score".to_string(), py_round3(DEFAULT_CRITIC_SCORE));
        let score = py_round3(compute_score(&signals));
        signals.insert("score".to_string(), score);
        apply_score(&mut concept, &signals);
        let new_tier = tier_for_score(score);
        let old_id = concept.concept_id.clone();
        let old_rel = rel_cid(bundle, &old_id);
        let decision = if score < 0.3 {
            "archive"
        } else if new_tier == "tribal" {
            "promote"
        } else {
            "keep_draft"
        };
        append_promotion(&mut concept, decision, score);
        let tier = match decision {
            "archive" => {
                out.dropped += 1;
                "archived"
            }
            "promote" => {
                out.tribal += 1;
                "tribal"
            }
            _ => {
                out.remaining_drafts += 1;
                "drafts"
            }
        };
        let new_id = store_memory(&mut concept, bundle, tier, Some(&old_id), Some(conn))?;
        if new_id != old_rel {
            rename_memory_embedding(conn, &old_rel, &new_id)?;
        }
    }
    Ok(out)
}

// --- consolidate (memory/store.py port) -----------------------------------------

/// Merge same-title raw memories into unified tribal knowledge, archiving
/// their sources; returns the archived source count.
pub fn consolidate_memories(
    bundle: &Bundle,
    conn: Option<&Connection>,
) -> Result<usize, ToolError> {
    let _lock = bundle.lock()?;
    let raw_ids = bundle.list_concepts(Some("memory/raw"));
    if raw_ids.len() < 2 {
        return Ok(0);
    }
    let mut concepts = Vec::new();
    for cid in &raw_ids {
        if let Ok(c) = bundle.read_concept(cid) {
            concepts.push(c);
        }
    }
    // Insertion-ordered groups keyed by the lowercased title (Python dict order).
    let mut groups: Vec<(String, Vec<Concept>)> = Vec::new();
    for c in concepts {
        let key = c.title.as_deref().unwrap_or("").trim().to_lowercase();
        if key.is_empty() {
            continue;
        }
        match groups.iter_mut().find(|(k, _)| *k == key) {
            Some((_, group)) => group.push(c),
            None => groups.push((key, vec![c])),
        }
    }
    let mut consolidated = 0usize;
    for (title_key, group) in groups {
        if group.len() < 2 {
            continue;
        }
        let primary = &group[0];
        let mut merged_body_lines = vec![primary.body.clone()];
        for c in &group[1..] {
            if !c.body.is_empty() && !merged_body_lines.contains(&c.body) {
                merged_body_lines.push(c.body.clone());
            }
        }
        let new_title = primary
            .title
            .clone()
            .filter(|t| !t.is_empty())
            .unwrap_or_else(|| py_title(&title_key));
        let mut tags: Vec<String> = Vec::new();
        for c in &group {
            for t in &c.tags {
                if !tags.contains(t) {
                    tags.push(t.clone());
                }
            }
        }
        let mut unified = Concept {
            type_: "TribalMemory".to_string(),
            title: Some(new_title.clone()),
            description: Some(
                primary
                    .description
                    .clone()
                    .filter(|d| !d.is_empty())
                    .unwrap_or_else(|| format!("Consolidated memory for {new_title}")),
            ),
            body: merged_body_lines.join("\n\n---\n\n"),
            tags,
            concept_id: format!("memory/tribal/{}-{}", slugify(&new_title), unique_suffix()),
            extensions: vec![
                ("memory_tier".to_string(), ys("tribal")),
                (
                    "consolidated_from".to_string(),
                    Yaml::Array(
                        group
                            .iter()
                            .map(|c| ys(rel_cid(bundle, &c.concept_id)))
                            .collect(),
                    ),
                ),
            ],
            ..Default::default()
        };
        bundle.write_concept(&mut unified)?;
        if let Some(conn) = conn {
            let valid_from = unified
                .ext_str("valid_from")
                .filter(|s| !s.is_empty())
                .map(str::to_string)
                .or_else(|| unified.timestamp.clone())
                .unwrap_or_else(iso_utc_now);
            write_validity(&mut unified, &valid_from, None, None, None, Some(conn))?;
        }
        for c in group {
            if c.concept_id.is_empty() || c.concept_id == unified.concept_id {
                continue;
            }
            let source_id = rel_cid(bundle, &c.concept_id);
            let mut c = c;
            let old_file = bundle.root.join(format!("{}.md", c.concept_id));
            set_ext(&mut c.extensions, "memory_tier", ys("archived"));
            let slug = slugify(c.title.as_deref().unwrap_or(""));
            let slug = if slug.is_empty() {
                "memory".to_string()
            } else {
                slug
            };
            c.concept_id = format!("memory/archived/{slug}-{}", unique_suffix());
            if bundle.write_concept(&mut c).is_err() {
                continue;
            }
            if old_file.exists() {
                let _ = std::fs::remove_file(&old_file);
            }
            if let Some(conn) = conn {
                // A failed move keeps the already-archived file but skips the count.
                let moved = conn
                    .execute(
                        "UPDATE memory_validity SET concept_id = ?1 WHERE concept_id = ?2",
                        rusqlite::params![c.concept_id, source_id],
                    )
                    .is_ok();
                let renamed = rename_memory_embedding(conn, &source_id, &c.concept_id).is_ok();
                if !moved || !renamed {
                    continue;
                }
            }
            consolidated += 1;
        }
    }
    Ok(consolidated)
}

// --- scoring (memory/scoring.py port) ------------------------------------------

/// Fraction of backtick-quoted file/symbol refs that exist in the graph.
pub fn graph_verification(concept: &Concept, conn: &Connection) -> Result<f64, rusqlite::Error> {
    let body = &concept.body;
    let file_refs = extract_file_refs(body);
    let symbol_refs = extract_symbol_refs(body);
    let total = file_refs.len() + symbol_refs.len();
    if total == 0 {
        return Ok(1.0);
    }
    let mut verified = 0;
    for r in &file_refs {
        if file_exists(conn, r)? {
            verified += 1;
        }
    }
    for r in &symbol_refs {
        if symbol_exists(conn, r)? {
            verified += 1;
        }
    }
    Ok(verified as f64 / total as f64)
}

fn cross_session_refs(concept: &Concept, conn: &Connection) -> Result<i64, rusqlite::Error> {
    if concept.concept_id.is_empty() {
        return Ok(0);
    }
    conn.query_row(
        "SELECT COUNT(DISTINCT session_id) FROM memory_refs WHERE memory_path = ?",
        rusqlite::params![concept.concept_id],
        |r| r.get(0),
    )
}

fn freshness(concept: &Concept) -> f64 {
    let Some(ts) = concept.timestamp.as_deref().or_else(|| {
        concept.ext("timestamp").and_then(|v| match v {
            Yaml::String(s) => Some(s.as_str()),
            _ => None,
        })
    }) else {
        return 0.5;
    };
    if concept.ext_str("doc_source") == Some("manual") {
        return 1.0;
    }
    let Some(secs) = parse_memory_timestamp(ts) else {
        return 0.5;
    };
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    let days = (now - secs) as f64 / 86_400.0;
    let mtype = concept.ext_str("memory_type").unwrap_or("decision");
    let half_life: f64 = match mtype {
        "pattern" | "mistake" | "workaround" => 270.0,
        _ => 90.0,
    };
    let lam = std::f64::consts::LN_2 / half_life;
    (-lam * days).exp()
}

fn reinforcement(concept: &Concept, conn: &Connection) -> Result<f64, rusqlite::Error> {
    if concept.concept_id.is_empty() {
        return Ok(0.0);
    }
    let mut cids = vec![concept.concept_id.clone()];
    let cid = &concept.concept_id;
    if Path::new(cid).is_absolute() && cid.contains("memory/") {
        let pos = cid.find("memory/").expect("checked");
        if &cid[pos..] != cid {
            cids.push(format!("memory/{}", &cid[pos + "memory/".len()..]));
        }
    }
    let placeholders = vec!["?"; cids.len()].join(", ");
    let params: Vec<&str> = cids.iter().map(String::as_str).collect();
    let rows: Vec<String> = {
        let mut stmt = conn.prepare(&format!(
            "SELECT referenced_at FROM memory_refs WHERE memory_path IN ({placeholders})"
        ))?;
        let mapped = stmt.query_map(rusqlite::params_from_iter(params.iter()), |r| {
            r.get::<_, String>(0)
        })?;
        mapped.collect::<Result<Vec<_>, _>>()?
    };
    if rows.is_empty() {
        return Ok(0.0);
    }
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    let sigma = 0.3;
    let mut boost = 0.0;
    for ts in rows {
        if let Some(secs) = parse_iso_ts(&ts, true) {
            let days_since = (now - secs) as f64 / 86_400.0;
            if days_since > 0.0 {
                boost += 1.0 / days_since;
            }
        }
    }
    Ok((boost * sigma).min(1.0))
}

fn authority(concept: &Concept) -> f64 {
    if concept.ext_str("doc_source") == Some("manual") {
        1.0
    } else {
        0.5
    }
}

/// Scoring signals + weighted score (`score_memory`).
pub fn score_memory(
    concept: &Concept,
    conn: &Connection,
) -> Result<HashMap<String, f64>, ToolError> {
    let signal_of = |key: &str| -> Option<f64> {
        match concept.ext("memory_signals") {
            Some(Yaml::Hash(h)) => h.get(&ys(key)).and_then(|x| match x {
                Yaml::Real(s) => s.parse().ok(),
                Yaml::Integer(i) => Some(*i as f64),
                _ => None,
            }),
            _ => None,
        }
    };
    let agent_confidence = signal_of("agent_confidence")
        .unwrap_or_else(|| concept.ext_f64("memory_score").unwrap_or(0.5));
    let graph_v = graph_verification(concept, conn).map_err(|e| ToolError(e.to_string()))?;
    let refs = cross_session_refs(concept, conn).map_err(|e| ToolError(e.to_string()))?;
    let refs_signal = (refs as f64 / 5.0).min(1.0);
    let critic = signal_of("critic_score").unwrap_or(0.5);
    let fresh = freshness(concept);
    let reinforce = reinforcement(concept, conn).map_err(|e| ToolError(e.to_string()))?;
    let mut signals = HashMap::new();
    signals.insert("graph_verification".to_string(), py_round3(graph_v));
    signals.insert("cross_session_refs".to_string(), refs as f64);
    signals.insert(
        "cross_session_refs_signal".to_string(),
        py_round3(refs_signal),
    );
    signals.insert("agent_confidence".to_string(), py_round3(agent_confidence));
    signals.insert("critic_score".to_string(), py_round3(critic));
    signals.insert("freshness".to_string(), py_round3(fresh));
    signals.insert("reinforcement".to_string(), py_round3(reinforce));
    signals.insert("authority".to_string(), py_round3(authority(concept)));
    signals.insert("score".to_string(), py_round3(compute_score(&signals)));
    Ok(signals)
}

/// Apply the weighted formula to an already-computed signals dict.
pub fn compute_score(signals: &HashMap<String, f64>) -> f64 {
    0.357 * signals["graph_verification"]
        + 0.286 * signals["cross_session_refs_signal"]
        + 0.214 * signals["agent_confidence"]
        + 0.0715 * signals["freshness"]
        + 0.0715 * signals.get("reinforcement").copied().unwrap_or(0.0)
}

fn apply_score(concept: &mut Concept, signals: &HashMap<String, f64>) {
    let mut sig = Hash::new();
    for key in [
        "graph_verification",
        "cross_session_refs",
        "agent_confidence",
        "critic_score",
        "freshness",
        "reinforcement",
        "authority",
    ] {
        sig.insert(ys(key), Yaml::Real(fmt_score(signals[key])));
    }
    set_ext(&mut concept.extensions, "memory_signals", Yaml::Hash(sig));
    // The raw signals["score"] (score_memory rounds, batch_critic's rescore does not).
    let score_text = format!("{}", signals["score"]);
    let score_text = if score_text.contains('.') {
        score_text
    } else {
        format!("{score_text}.0")
    };
    set_ext(
        &mut concept.extensions,
        "memory_score",
        Yaml::Real(score_text),
    );
}

fn seed_refs_baseline(concept: &mut Concept, signals: &HashMap<String, f64>) {
    let body_refs =
        extract_file_refs(&concept.body).len() + extract_symbol_refs(&concept.body).len();
    if body_refs > 0 {
        set_ext(
            &mut concept.extensions,
            "memory_refs_baseline",
            Yaml::Real(fmt_score(signals["graph_verification"])),
        );
    }
}

// --- search_memory (memory/promotion.py port) ----------------------------------

fn parse_instant(value: &str) -> Option<i64> {
    parse_iso_ts(value, true)
}

fn bound_key(value: &Yaml) -> Option<String> {
    match value {
        Yaml::String(_) | Yaml::Real(_) | Yaml::Integer(_) => {
            parse_instant(&scalar_repr(value)).map(instant_key)
        }
        _ => None,
    }
}

fn valid_at(concept: &Concept, query_key: &str) -> bool {
    if concept
        .ext("valid_from")
        .and_then(bound_key)
        .is_some_and(|vf| vf.as_str() > query_key)
    {
        return false;
    }
    if concept
        .ext("valid_until")
        .and_then(bound_key)
        .is_some_and(|vu| vu.as_str() <= query_key)
    {
        return false;
    }
    true
}

fn visible(
    concept: &Concept,
    tier: Option<&str>,
    include_superseded: bool,
    query_key: &str,
) -> bool {
    if tier.is_some_and(|t| !concept.ext_str("memory_tier").unwrap_or("").starts_with(t)) {
        return false;
    }
    if !include_superseded && !concept.ext_bool("memory_is_latest").unwrap_or(true) {
        return false;
    }
    valid_at(concept, query_key)
}

/// Record sanitized memory references in one best-effort transaction.
/// Credit refs for memories surfaced in a rendered response (analytics only).
pub fn record_references_batch(conn: &Connection, refs: &[(String, String)], session_id: &str) {
    if refs.is_empty() {
        return;
    }
    let now = iso_utc_now();
    for (memory_path, context) in refs {
        let context = strip_private_data(context);
        let context: String = context.chars().take(200).collect();
        let _ = conn.execute(
            "INSERT INTO memory_refs (id, memory_path, session_id, referenced_at, context) VALUES (?, ?, ?, ?, ?)",
            rusqlite::params![unique_ref_id(), memory_path, session_id, now, context],
        );
    }
}

fn lexical_memory_match(concepts: Vec<Concept>, query: &str) -> Vec<Concept> {
    let stop: HashSet<&str> = BASE_STOP_WORDS.iter().copied().collect();
    let tokens: Vec<String> = simple_tokenize(query, None)
        .into_iter()
        .filter(|t| !stop.contains(t.as_str()))
        .collect();
    if tokens.is_empty() {
        return Vec::new();
    }
    let mut scored: Vec<(usize, Concept)> = Vec::new();
    for c in concepts {
        let hay = format!(
            "{} {} {}",
            c.title.as_deref().unwrap_or_default(),
            c.description.as_deref().unwrap_or_default(),
            c.body
        )
        .to_lowercase();
        let hits = tokens.iter().filter(|t| hay.contains(t.as_str())).count();
        if hits > 0 {
            scored.push((hits, c));
        }
    }
    scored.sort_by_key(|(hits, _concept)| std::cmp::Reverse(*hits));
    scored.into_iter().map(|(_, c)| c).collect()
}

/// Search memories ranked by fused lexical + semantic matches (semantic gated
/// on the memory embedding table).
#[allow(clippy::too_many_arguments)]
pub fn search_memory(
    conn: &Connection,
    bundle: &Bundle,
    query: &str,
    tier: Option<&str>,
    session_id: Option<&str>,
    include_superseded: bool,
    as_of: Option<&str>,
) -> Result<Vec<Concept>, ToolError> {
    let instant = match as_of {
        Some(a) => parse_instant(a)
            .ok_or_else(|| ToolError(format!("as_of must be an ISO-8601 date, got {a:?}")))?,
        None => SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs() as i64)
            .unwrap_or(0),
    };
    let query_key = instant_key(instant);

    let mut lexical_hits: Vec<Concept> = bundle
        .search(query, Some(20))?
        .into_iter()
        .filter(|c| c.concept_id.starts_with("memory/") || c.ext("memory_tier").is_some())
        .filter(|c| visible(c, tier, include_superseded, &query_key))
        .collect();

    let mut resolved: HashMap<String, Concept> = HashMap::new();
    for c in &lexical_hits {
        resolved.insert(c.concept_id.clone(), c.clone());
    }

    // Lexical broaden when substring search alone came up thin.
    if lexical_hits.len() <= 2 {
        let mut all_mem = Vec::new();
        for cid in bundle.list_concepts(Some("memory/")) {
            let c = match bundle.read_concept(&cid) {
                Ok(c) if visible(&c, tier, include_superseded, &query_key) => c,
                _ => continue,
            };
            all_mem.push(c);
        }
        for c in &all_mem {
            resolved
                .entry(c.concept_id.clone())
                .or_insert_with(|| c.clone());
        }
        for c in lexical_memory_match(all_mem, query) {
            if !lexical_hits.iter().any(|h| h.concept_id == c.concept_id) {
                resolved
                    .entry(c.concept_id.clone())
                    .or_insert_with(|| c.clone());
                lexical_hits.push(c);
            }
        }
    }

    let mut semantic_hits =
        semantic_memory_search(conn, bundle, query, tier, include_superseded, &query_key);
    let lexical_ids: Vec<String> = lexical_hits.iter().map(|c| c.concept_id.clone()).collect();

    let mut results = Vec::new();
    if semantic_hits.is_empty() {
        // Without semantic hits the fused order is the lexical order.
        for c in &lexical_hits {
            if let Some(rc) = resolved.get(&c.concept_id) {
                results.push(rc.clone());
            }
        }
    } else {
        // The semantic object carries the provenance stamp that must survive
        // into the result, so it wins for any id found both ways; dual hits
        // upgrade to "fused".
        let semantic_ids: Vec<String> =
            semantic_hits.iter().map(|c| c.concept_id.clone()).collect();
        let lexical_id_set: HashSet<&str> = lexical_ids.iter().map(String::as_str).collect();
        for c in &mut semantic_hits {
            if lexical_id_set.contains(c.concept_id.as_str()) {
                let prov = c
                    .ext_str("provenance")
                    .unwrap_or_default()
                    .replace("semantic", "fused");
                set_ext(&mut c.extensions, "provenance", ys(prov.as_str()));
            }
        }
        for c in semantic_hits {
            resolved.insert(c.concept_id.clone(), c);
        }
        for (cid, _score) in rrf_fuse(&[lexical_ids, semantic_ids], 60) {
            if let Some(rc) = resolved.get(&cid) {
                results.push(rc.clone());
            }
        }
    }

    if let Some(sid) = session_id {
        let refs: Vec<(String, String)> = results
            .iter()
            .filter(|c| c.ext_str("memory_tier") == Some("tribal"))
            .map(|c| (c.concept_id.clone(), query.to_string()))
            .collect();
        record_references_batch(conn, &refs, sid);
    }
    Ok(results)
}

/// Each embedded memory once at its best-matching chunk, cosine-ranked above
/// 0.1; best-effort — any failure yields an empty list, never an error.
fn semantic_memory_search(
    conn: &Connection,
    bundle: &Bundle,
    query: &str,
    tier: Option<&str>,
    include_superseded: bool,
    query_key: &str,
) -> Vec<Concept> {
    const THRESHOLD: f64 = 0.1;
    const LIMIT: usize = 20;
    let env = semantic_env(None);
    let rows: Vec<(String, Vec<u8>, i64)> = (|| {
        let mut stmt = conn
            .prepare("SELECT doc_id, vec, dim FROM memory_embeddings WHERE model = ?")
            .ok()?;
        stmt.query_map([&env.model], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, Vec<u8>>(1)?,
                r.get::<_, i64>(2)?,
            ))
        })
        .ok()?
        .collect::<Result<Vec<_>, _>>()
        .ok()
    })()
    .unwrap_or_default();
    if rows.is_empty() {
        return Vec::new();
    }
    let Ok((q_blob, q_dim)) = embed_query(&env, query) else {
        return Vec::new();
    };
    let vectors: Vec<(Vec<u8>, i64)> = rows
        .iter()
        .map(|(_, vec, dim)| (vec.clone(), *dim))
        .collect();
    let prov = semantic_provenance(&env);
    let mut out: Vec<Concept> = Vec::new();
    let mut seen: HashSet<String> = HashSet::new();
    for (_score, index) in cosine_scan(&q_blob, q_dim, &vectors, THRESHOLD) {
        if !seen.insert(rows[index].0.clone()) {
            continue; // keep only the best-scoring chunk per concept
        }
        let Ok(mut concept) = bundle.read_concept(&rows[index].0) else {
            continue; // row orphaned by a since-moved/deleted memory
        };
        if !visible(&concept, tier, include_superseded, query_key) {
            continue;
        }
        set_ext(&mut concept.extensions, "provenance", ys(prov.as_str()));
        out.push(concept);
        if out.len() >= LIMIT {
            break;
        }
    }
    out
}

fn embed_memory_count(conn: &Connection) -> Result<i64, rusqlite::Error> {
    let model = current_model("memory");
    conn.query_row(
        "SELECT COUNT(*) FROM memory_embeddings WHERE model = ? AND chunk_index = 0",
        rusqlite::params![model],
        |r| r.get(0),
    )
}

fn current_model(corpus: &str) -> String {
    match std::env::var("CAIRN_EMBED_BACKEND").as_deref() {
        Ok("hash") => "hash-256-v1".to_string(),
        Ok("openai") => std::env::var("CAIRN_EMBED_OPENAI_MODEL")
            .unwrap_or_else(|_| "text-embedding-3-small".to_string()),
        _ => {
            let _ = corpus;
            "BAAI/bge-m3".to_string()
        }
    }
}

pub fn unembedded_memory_hint(conn: &Connection, bundle: &Bundle) -> Result<String, ToolError> {
    let total = bundle.list_concepts(Some("memory/")).len();
    if total == 0 {
        return Ok(String::new());
    }
    let embedded = embed_memory_count(conn).map_err(|e| ToolError(e.to_string()))?;
    if embedded < total as i64 {
        return Ok(format!(
            "({} of {total} memories not yet embedded -- run `cairn memory embed` for semantic recall)",
            total as i64 - embedded
        ));
    }
    Ok(String::new())
}

// --- capture (memory/promotion.py port) -----------------------------------------

fn is_session_bookkeeping(title: &str, body: &str) -> bool {
    struct Bookkeeping {
        task_id: Regex,
        branch: Regex,
        date: Regex,
        count: Regex,
        progress_count: Regex,
    }
    static RES: OnceLock<Bookkeeping> = OnceLock::new();
    let b = RES.get_or_init(|| Bookkeeping {
        task_id: Regex::new(r"\bT\d{3}\b").expect("task id regex"),
        branch: Regex::new(
            r"(?:^|[^\w/])(?:feature|feat|fix|bugfix|hotfix|chore|release|docs|refactor|spec)/[A-Za-z0-9._-]+",
        )
        .expect("branch regex"),
        date: Regex::new(r"\b\d{4}-\d{2}-\d{2}\b").expect("date regex"),
        count: Regex::new(
            r"\b\d+\s+(?:commits?|files?|prs?|tasks?|modules?|specs?|tests?)\b",
        )
        .expect("count regex"),
        progress_count: Regex::new(
            r"\b\d+\s+(?:[\w'-]+\s+){0,3}(?:done|left|remaining|pending|complete|completed|to go)\b",
        )
        .expect("progress regex"),
    });
    let combined = format!("{title}\n{body}");
    if b.task_id.is_match(&combined) || b.branch.is_match(&combined) {
        return true;
    }
    // Python expressed "dated count" with same-line lookaheads: a line carrying
    // both a date and a unit count.
    if combined
        .lines()
        .any(|line| b.date.is_match(line) && b.count.is_match(line))
    {
        return true;
    }
    b.progress_count.is_match(title)
}

fn find_supersession_candidate(
    conn: &Connection,
    bundle: &Bundle,
    type_: &str,
    title: &str,
) -> Result<Option<String>, ToolError> {
    let mut candidates: Vec<Concept> = Vec::new();
    for cid in bundle.list_concepts(Some("memory/")) {
        let Ok(c) = bundle.read_concept(&cid) else {
            continue;
        };
        if c.ext_str("memory_type") != Some(type_) {
            continue;
        }
        if !c.ext_bool("memory_is_latest").unwrap_or(true) {
            continue;
        }
        candidates.push(c);
    }
    if candidates.is_empty() {
        return Ok(None);
    }
    // Tier 1: exact title match (case-insensitive).
    let title_lower = title.trim().to_lowercase();
    if !title_lower.is_empty() {
        for c in &candidates {
            if c.title.as_deref().unwrap_or("").trim().to_lowercase() == title_lower {
                return Ok(Some(c.concept_id.clone()));
            }
        }
    }
    // Tier 2 (cosine over embeddings) is gated on the embeddings workstream.
    let _ = conn;
    Ok(None)
}

fn mark_superseded(bundle: &Bundle, old_id: &str, new_id: &str) -> Result<(), ToolError> {
    let Some(mut old) = get_memory(bundle, old_id)? else {
        return Ok(());
    };
    set_ext(
        &mut old.extensions,
        "memory_is_latest",
        Yaml::Boolean(false),
    );
    set_ext(&mut old.extensions, "memory_superseded_by", ys(new_id));
    bundle.write_concept(&mut old)
}

/// Create, score, redact, and store a memory, superseding a close latest match.
#[allow(clippy::too_many_arguments)]
pub fn capture_memory(
    conn: &Connection,
    bundle: &Bundle,
    type_: &str,
    title: &str,
    body: &str,
    resource: Option<&str>,
    confidence: f64,
    session_origin: Option<&str>,
    stance: Option<&str>,
) -> Result<CaptureResult, ToolError> {
    let body = strip_private_data(body);
    let title = strip_private_data(title);
    let _lock = bundle.lock()?;
    let superseded_id = find_supersession_candidate(conn, bundle, type_, &title)?;

    let mut supersedes_chain: Vec<String> = Vec::new();
    if let Some(old_id) = &superseded_id
        && let Ok(Some(old)) = get_memory(bundle, old_id)
    {
        let norm_id = rel_cid(bundle, old_id);
        let old_chain: Vec<String> = match old.ext("memory_supersedes") {
            Some(Yaml::Array(items)) => items
                .iter()
                .map(|i| rel_cid(bundle, &str_of(Some(i)).unwrap_or_default()))
                .collect(),
            _ => Vec::new(),
        };
        supersedes_chain = vec![norm_id];
        supersedes_chain.extend(old_chain);
    }

    let mut concept = create_memory(
        type_,
        &title,
        &body,
        resource,
        confidence,
        session_origin,
        None,
        None,
        if supersedes_chain.is_empty() {
            None
        } else {
            Some(supersedes_chain)
        },
        stance,
    )?;
    let signals = score_memory(&concept, conn)?;
    apply_score(&mut concept, &signals);
    seed_refs_baseline(&mut concept, &signals);
    let mut tier = tier_for_score(signals["score"]).to_string();
    if is_session_bookkeeping(&title, &body) {
        tier = "raw".to_string();
        set_ext(
            &mut concept.extensions,
            "memory_triage",
            ys("session-bookkeeping"),
        );
    }
    let path = store_memory(&mut concept, bundle, &tier, None, Some(conn))?;

    if let Some(old_id) = &superseded_id {
        mark_superseded(bundle, old_id, &path)?;
        append_promotion(&mut concept, "supersede", signals["score"]);
    }

    Ok(CaptureResult {
        path,
        tier,
        score: signals["score"],
        superseded: superseded_id,
    })
}

/// Capture outcome: stored path, tier, score, and the superseded id if any.
pub struct CaptureResult {
    pub path: String,
    pub tier: String,
    pub score: f64,
    pub superseded: Option<String>,
}

// --- knowledge store (knowledge/store.py port) ----------------------------------

/// The path-safe doc_type for knowledge ids: slugified, "general" fallback.
pub fn doc_type_slug(doc_type: &str) -> String {
    let s = slugify(doc_type);
    if s.is_empty() {
        "general".to_string()
    } else {
        s
    }
}

/// The concept id add_document assigns a title/doc_type (or explicit slug).
pub fn doc_concept_id(title: &str, doc_type: &str, slug: Option<&str>) -> String {
    format!(
        "knowledge/{}/{}",
        doc_type_slug(doc_type),
        slug.map_or_else(|| slugify(&strip_private_data(title)), str::to_string)
    )
}

/// Normalize a doc id to its bare bundle-relative concept id.
pub fn normalize_doc_id(bundle: &Bundle, concept_id: &str) -> String {
    let cid = concept_id.strip_suffix(".md").unwrap_or(concept_id);
    let resolved = resolve_lenient(&bundle.root.join(format!("{cid}.md")));
    match resolved.strip_prefix(resolve_lenient(&bundle.root)) {
        Ok(rel) => {
            let rel = rel.to_string_lossy().replace('\\', "/");
            rel.strip_suffix(".md").unwrap_or(&rel).to_string()
        }
        Err(_) => cid.to_string(),
    }
}

fn refuse_out_of_namespace(
    bundle: &Bundle,
    doc_id: &str,
    concept: Option<&Concept>,
) -> Result<(), ToolError> {
    let resolved = match concept {
        Some(c) => normalize_doc_id(bundle, &c.concept_id),
        None => {
            let resolved = normalize_doc_id(bundle, doc_id);
            match bundle.validate_concept_path(&resolved) {
                Err(_) => return Ok(()),
                Ok(p) if !p.exists() => return Ok(()),
                Ok(_) => resolved,
            }
        }
    };
    if resolved == "knowledge" || resolved.starts_with("knowledge/") {
        return Ok(());
    }
    err(format!(
        "Refused: '{doc_id}' resolves to '{resolved}', outside the knowledge/ namespace. \
         The knowledge store only manages knowledge/ concepts (compass/wiki/memory documents \
         cannot be modified or deleted through it)."
    ))
}

/// One structured workflow step.
#[derive(Debug, Clone, Default)]
pub struct WorkflowStep {
    pub name: Option<String>,
    pub description: Option<String>,
    pub symbol: Option<String>,
    pub file: Option<String>,
}

fn steps_yaml(steps: &[WorkflowStep]) -> Yaml {
    Yaml::Array(
        steps
            .iter()
            .map(|s| {
                let mut h = Hash::new();
                if let Some(n) = &s.name {
                    h.insert(ys("name"), ys(n));
                }
                if let Some(d) = &s.description {
                    h.insert(ys("description"), ys(d));
                }
                if let Some(sym) = &s.symbol {
                    h.insert(ys("symbol"), ys(sym));
                }
                if let Some(f) = &s.file {
                    h.insert(ys("file"), ys(f));
                }
                Yaml::Hash(h)
            })
            .collect(),
    )
}

fn steps_of(concept: &Concept) -> Vec<WorkflowStep> {
    match concept.ext("steps") {
        Some(Yaml::Array(items)) => items
            .iter()
            .map(|item| match item {
                Yaml::Hash(h) => WorkflowStep {
                    name: str_of(h.get(&ys("name"))),
                    description: str_of(h.get(&ys("description"))),
                    symbol: str_of(h.get(&ys("symbol"))),
                    file: str_of(h.get(&ys("file"))),
                },
                _ => WorkflowStep::default(),
            })
            .collect(),
        _ => Vec::new(),
    }
}

/// Redact, normalize, and store one document; return its concept id.
#[allow(clippy::too_many_arguments)]
pub fn add_document(
    bundle: &Bundle,
    title: &str,
    body: &str,
    doc_type: &str,
    tags: Vec<String>,
    affects_modules: Vec<String>,
    affects_repos: Vec<String>,
    resource: Option<&str>,
    owner: Option<&str>,
    epic_link: Option<&str>,
    steps: Option<Vec<WorkflowStep>>,
    description: Option<&str>,
    doc_source: &str,
) -> Result<String, ToolError> {
    add_document_with_slug(
        bundle,
        title,
        body,
        doc_type,
        tags,
        affects_modules,
        affects_repos,
        resource,
        owner,
        epic_link,
        steps,
        description,
        doc_source,
        None,
        None,
        None,
    )
}

/// `add_document` with an explicit slug override (import dedupe path).
#[allow(clippy::too_many_arguments)]
pub fn add_document_with_slug(
    bundle: &Bundle,
    title: &str,
    body: &str,
    doc_type: &str,
    tags: Vec<String>,
    affects_modules: Vec<String>,
    affects_repos: Vec<String>,
    resource: Option<&str>,
    owner: Option<&str>,
    epic_link: Option<&str>,
    steps: Option<Vec<WorkflowStep>>,
    description: Option<&str>,
    doc_source: &str,
    slug: Option<&str>,
    relationships: Option<&[Yaml]>,
    verified_refs: Option<&[(String, String)]>,
) -> Result<String, ToolError> {
    let title = strip_private_data(title);
    let body = strip_private_data(body);
    let description = description.map(strip_private_data);
    let steps = steps.map(|steps| -> Vec<WorkflowStep> {
        steps
            .into_iter()
            .map(|mut step| {
                if let Some(desc) = &step.description {
                    let redacted = strip_private_data(desc);
                    step.description = Some(redacted);
                }
                step
            })
            .collect()
    });
    let concept_id = doc_concept_id(&title, doc_type, slug);

    let mut extensions: Vec<(String, Yaml)> = vec![
        ("tier".into(), ys("asserted")),
        ("doc_status".into(), ys("active")),
        ("doc_owner".into(), ys(owner.unwrap_or(""))),
        ("doc_source".into(), ys(doc_source)),
        ("epic_link".into(), ys(epic_link.unwrap_or(""))),
        ("affects_modules".into(), str_list_yaml(&affects_modules)),
        ("affects_repos".into(), str_list_yaml(&affects_repos)),
    ];
    if let Some(steps) = &steps {
        extensions.push(("steps".into(), steps_yaml(steps)));
    }
    if let Some(relationships) = relationships
        && !relationships.is_empty()
    {
        extensions.push((
            "relates_to".into(),
            Yaml::Array(crate::tasks::normalize_relationships(Some(&Yaml::Array(
                relationships.to_vec(),
            )))),
        ));
    }
    if let Some(verified) = verified_refs.filter(|refs| !refs.is_empty()) {
        extensions.push(("verified".into(), verified_yaml(verified)));
    }

    let mut concept = Concept {
        type_: format!("Knowledge-{doc_type}"),
        title: Some(title.clone()),
        description: Some(description.unwrap_or_else(|| title.clone())),
        resource: resource.map(str::to_string),
        tags,
        concept_id: concept_id.clone(),
        body,
        extensions,
        ..Default::default()
    };
    let _lock = bundle.lock()?;
    if get_document(bundle, &concept_id)?.is_some() {
        // Same title+type replaces the prior document in place.
    }
    bundle.write_concept(&mut concept)?;
    Ok(concept_id)
}

/// The OKF `verified` family rows for doc->code refs.
fn verified_yaml(refs: &[(String, String)]) -> Yaml {
    Yaml::Array(
        refs.iter()
            .map(|(ref_, kind)| {
                let mut h = Hash::new();
                h.insert(ys("ref"), ys(ref_.clone()));
                h.insert(ys("kind"), ys(kind.clone()));
                h.insert(ys("verified"), Yaml::Boolean(true));
                Yaml::Hash(h)
            })
            .collect(),
    )
}

/// List knowledge documents, optionally filtered by type/status/tag.
pub fn list_documents(
    bundle: &Bundle,
    doc_type: Option<&str>,
    status: Option<&str>,
    tag: Option<&str>,
) -> Vec<Concept> {
    let prefix = doc_type.map_or_else(|| "knowledge/".to_string(), |t| format!("knowledge/{t}/"));
    let mut out = Vec::new();
    for cid in bundle.list_concepts(Some(&prefix)) {
        let Ok(concept) = bundle.read_concept(&cid) else {
            continue;
        };
        if status.is_some_and(|status| concept.ext_str("doc_status") != Some(status)) {
            continue;
        }
        if tag.is_some_and(|tag| !concept.tags.iter().any(|t| t == tag)) {
            continue;
        }
        out.push(concept);
    }
    out
}

/// Read a knowledge document by concept_id.
pub fn get_document(bundle: &Bundle, doc_id: &str) -> Result<Option<Concept>, ToolError> {
    Ok(bundle.read_concept(doc_id).ok())
}

pub const DOC_STATUSES: &[&str] = &["active", "superseded", "archived"];

/// Apply a forward-only status transition and return success.
pub fn update_status(bundle: &Bundle, doc_id: &str, new_status: &str) -> Result<bool, ToolError> {
    if !DOC_STATUSES.contains(&new_status) {
        return Ok(false);
    }
    if !bundle.root.exists() {
        return Ok(false);
    }
    let _lock = bundle.lock()?;
    let concept = get_document(bundle, doc_id)?;
    refuse_out_of_namespace(bundle, doc_id, concept.as_ref())?;
    let Some(mut concept) = concept else {
        return Ok(false);
    };
    let current = concept
        .ext_str("doc_status")
        .unwrap_or("active")
        .to_string();
    let (Some(ni), Some(ci)) = (
        DOC_STATUSES.iter().position(|s| *s == new_status),
        DOC_STATUSES.iter().position(|s| *s == current),
    ) else {
        return Ok(false);
    };
    if ni < ci {
        return Ok(false);
    }
    set_ext(&mut concept.extensions, "doc_status", ys(new_status));
    bundle.write_concept(&mut concept)?;
    Ok(true)
}

/// Delete a document file and/or embeddings without committing.
pub fn delete_document(
    bundle: &Bundle,
    doc_id: &str,
    conn: Option<&Connection>,
    unlink: bool,
) -> Result<bool, ToolError> {
    let _lock = bundle.lock()?;
    let concept = get_document(bundle, doc_id)?;
    refuse_out_of_namespace(bundle, doc_id, concept.as_ref())?;
    let effective = concept
        .map(|c| c.concept_id)
        .unwrap_or_else(|| doc_id.to_string());
    let file_path = match bundle.validate_concept_path(&effective) {
        Ok(p) => p,
        Err(_) => return Ok(false),
    };
    if !file_path.exists() {
        return Ok(false);
    }
    if unlink {
        std::fs::remove_file(&file_path).map_err(|e| ToolError(e.to_string()))?;
    }
    if let Some(conn) = conn {
        let rel_id = normalize_doc_id(bundle, &effective);
        conn.execute(
            "DELETE FROM knowledge_embeddings WHERE doc_id = ?",
            rusqlite::params![rel_id],
        )
        .map_err(|e| ToolError(e.to_string()))?;
    }
    Ok(true)
}

// --- knowledge search (knowledge/search.py port) ---------------------------------

const WEIGHT_TITLE: usize = 10;
const WEIGHT_DESCRIPTION: usize = 5;
const WEIGHT_TAGS: usize = 5;
const WEIGHT_BODY: usize = 1;

fn doc_visible(concept: &Concept, include_archived: bool) -> bool {
    if include_archived {
        return true;
    }
    concept.ext_str("doc_status").unwrap_or("active") != "archived"
}

/// One scored knowledge-search hit.
#[derive(Debug, Clone)]
pub struct KnowledgeHit {
    pub concept_id: String,
    pub title: String,
    pub doc_type: String,
    pub score: f64,
    pub provenance: String,
    pub affects_modules: Vec<String>,
    pub affects_repos: Vec<String>,
    pub chunk: String,
    pub graph_deps: Vec<(String, Yaml)>,
}

/// Scored lexical (or semantic-fallback) knowledge search with graph bridging.
pub fn search_knowledge_docs(
    conn: &Connection,
    bundle: &Bundle,
    query: &str,
    limit: usize,
) -> Result<Vec<KnowledgeHit>, ToolError> {
    let mut results = lexical_search(conn, bundle, query, limit, false)?;
    if results.is_empty() {
        results = semantic_knowledge_search(conn, query, limit)?;
    }
    for r in &mut results {
        if !r.affects_repos.is_empty() {
            r.graph_deps = graph_bridge(conn, &r.affects_repos);
        }
    }
    Ok(results)
}

fn ext_str_list(concept: &Concept, key: &str) -> Vec<String> {
    match concept.ext(key) {
        Some(v) => str_vec(v).into_iter().filter(|s| !s.is_empty()).collect(),
        None => Vec::new(),
    }
}

fn chunk_of(concept: &Concept) -> String {
    format!(
        "{}\n{}",
        concept.description.as_deref().unwrap_or_default(),
        concept.body.chars().take(200).collect::<String>()
    )
}

fn lexical_search(
    conn: &Connection,
    bundle: &Bundle,
    query: &str,
    limit: usize,
    include_archived: bool,
) -> Result<Vec<KnowledgeHit>, ToolError> {
    let tokens = simple_tokenize(query, None);
    if tokens.is_empty() {
        return lexical_search_substring(bundle, query, limit, include_archived);
    }
    if tokens.len() == 1 {
        return lexical_search_substring(bundle, &tokens[0], limit, include_archived);
    }
    let knowledge_cids = bundle.list_concepts(Some("knowledge/"));
    let mut ranked: Vec<(usize, String, Concept)> = Vec::new();
    for cid in &knowledge_cids {
        let Ok(concept) = bundle.read_concept(cid) else {
            continue;
        };
        if !doc_visible(&concept, include_archived) {
            continue;
        }
        let title_lower = concept.title.as_deref().unwrap_or_default().to_lowercase();
        let desc_lower = concept
            .description
            .as_deref()
            .unwrap_or_default()
            .to_lowercase();
        let body_lower = concept.body.to_lowercase();
        let tags_joined = concept.tags.join(" ").to_lowercase();
        let mut score = 0;
        for tok in &tokens {
            if title_lower.contains(tok.as_str()) {
                score += WEIGHT_TITLE;
            }
            if desc_lower.contains(tok.as_str()) {
                score += WEIGHT_DESCRIPTION;
            }
            if tags_joined.contains(tok.as_str()) {
                score += WEIGHT_TAGS;
            }
            if body_lower.contains(tok.as_str()) {
                score += WEIGHT_BODY;
            }
        }
        if score > 0 {
            ranked.push((score, cid.clone(), concept));
        }
    }
    ranked.sort_by_key(|(score, _cid, _concept)| std::cmp::Reverse(*score));

    let mut out: Vec<KnowledgeHit> = Vec::new();
    let mut expansion_budget = limit / 2;
    let mut expanded_ids: HashSet<String> = HashSet::new();
    for (score, cid, concept) in ranked.iter().take(limit) {
        out.push(KnowledgeHit {
            concept_id: cid.clone(),
            title: concept.title.clone().unwrap_or_default(),
            doc_type: cid.split('/').nth(1).unwrap_or("unknown").to_string(),
            score: *score as f64,
            provenance: "lexical_knowledge".into(),
            affects_modules: ext_str_list(concept, "affects_modules"),
            affects_repos: ext_str_list(concept, "affects_repos"),
            chunk: chunk_of(concept),
            graph_deps: Vec::new(),
        });
        if expansion_budget > 0 {
            let related = find_related(
                conn,
                bundle,
                cid,
                *score as f64,
                &ranked,
                &expanded_ids,
                include_archived,
            )?;
            for (rel_cid, rel_score, rel_concept) in related.into_iter().take(expansion_budget) {
                out.push(KnowledgeHit {
                    doc_type: rel_cid.split('/').nth(1).unwrap_or("unknown").to_string(),
                    concept_id: rel_cid.clone(),
                    title: rel_concept.title.clone().unwrap_or_default(),
                    score: rel_score,
                    provenance: "lexical_knowledge_expanded".into(),
                    affects_modules: ext_str_list(&rel_concept, "affects_modules"),
                    affects_repos: ext_str_list(&rel_concept, "affects_repos"),
                    chunk: chunk_of(&rel_concept),
                    graph_deps: Vec::new(),
                });
                expanded_ids.insert(rel_cid);
                expansion_budget -= 1;
            }
        }
    }
    out.truncate(limit);
    Ok(out)
}

fn lexical_search_substring(
    bundle: &Bundle,
    query: &str,
    limit: usize,
    include_archived: bool,
) -> Result<Vec<KnowledgeHit>, ToolError> {
    let knowledge_cids = bundle.list_concepts(Some("knowledge/"));
    let hits = bundle.search(query, Some(limit * 2))?;
    let mut out = Vec::new();
    for c in hits {
        let mut rel_id = c.concept_id.clone();
        let mut matched = false;
        for kcid in &knowledge_cids {
            if rel_id.ends_with(kcid) || rel_id == *kcid {
                rel_id = kcid.clone();
                matched = true;
                break;
            }
        }
        if !matched || !doc_visible(&c, include_archived) {
            continue;
        }
        out.push(KnowledgeHit {
            doc_type: rel_id.split('/').nth(1).unwrap_or("unknown").to_string(),
            concept_id: rel_id,
            title: c.title.clone().unwrap_or_default(),
            score: 1.0,
            provenance: "lexical_knowledge".into(),
            affects_modules: ext_str_list(&c, "affects_modules"),
            affects_repos: ext_str_list(&c, "affects_repos"),
            chunk: chunk_of(&c),
            graph_deps: Vec::new(),
        });
    }
    out.truncate(limit);
    Ok(out)
}

fn find_related(
    conn: &Connection,
    bundle: &Bundle,
    parent_id: &str,
    parent_score: f64,
    scored: &[(usize, String, Concept)],
    already_in_results: &HashSet<String>,
    include_archived: bool,
) -> Result<Vec<(String, f64, Concept)>, ToolError> {
    let mut related = Vec::new();
    let mut seen = HashSet::new();
    for (cid, kind) in neighbors_or_empty(conn, bundle, parent_id) {
        if kind != "extracted" && kind != "derived" {
            continue;
        }
        if seen.contains(&cid)
            || already_in_results.contains(&cid)
            || scored.iter().any(|(_, c, _)| *c == cid)
        {
            continue;
        }
        seen.insert(cid.clone());
        let Ok(concept) = bundle.read_concept(&cid) else {
            continue;
        };
        if !doc_visible(&concept, include_archived) {
            continue;
        }
        related.push((cid, (parent_score * 0.5).max(0.5), concept));
    }
    // f64 scores have no Ord; scores are finite so partial_cmp is total here.
    #[allow(clippy::unnecessary_sort_by)]
    related.sort_by(|a, b| {
        b.1.partial_cmp(&a.1)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then(a.0.cmp(&b.0))
    });
    Ok(related)
}

fn neighbors_or_empty(
    conn: &Connection,
    bundle: &Bundle,
    parent_id: &str,
) -> Vec<(String, String)> {
    let normalized = normalize_doc_id(bundle, parent_id);
    let mut out = Vec::new();
    for (column, other) in [("doc_id", "related_id"), ("related_id", "doc_id")] {
        let sql = format!(
            "SELECT {other}, kind FROM knowledge_edges WHERE {column} = ? ORDER BY {other}"
        );
        let rows: Vec<(String, String)> = match conn.prepare(&sql).and_then(|mut stmt| {
            stmt.query_map(rusqlite::params![normalized], |r| {
                Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?))
            })?
            .collect()
        }) {
            Ok(rows) => rows,
            Err(_) => return out,
        };
        for (cid, kind) in rows {
            if bundle.read_concept(&cid).is_err() {
                continue; // deleted between rebuilds: never a phantom neighbor
            }
            out.push((cid, kind));
        }
    }
    out
}

fn semantic_knowledge_search(
    conn: &Connection,
    _query: &str,
    _limit: usize,
) -> Result<Vec<KnowledgeHit>, ToolError> {
    let count: i64 = conn
        .query_row("SELECT COUNT(*) FROM knowledge_embeddings", [], |r| {
            r.get(0)
        })
        .unwrap_or(0);
    if count == 0 {
        return Ok(Vec::new());
    }
    // Vector ranking lands with the embeddings workstream; degrade to no hits.
    Ok(Vec::new())
}

fn graph_bridge(conn: &Connection, repos: &[String]) -> Vec<(String, Yaml)> {
    let mut out = Vec::new();
    for repo in repos {
        match cross_repo_deps(conn, repo) {
            Ok(deps) => out.push((repo.clone(), deps)),
            Err(_) => out.push((
                repo.clone(),
                Yaml::Hash({
                    let mut h = Hash::new();
                    h.insert(ys("error"), ys("repo not found in graph"));
                    h
                }),
            )),
        }
    }
    out
}

fn cross_repo_deps(conn: &Connection, repo: &str) -> Result<Yaml, ToolError> {
    let namespaces = load_namespaces();
    // Dependencies: imports in `repo` resolving into another repo's namespace.
    let mut deps: HashMap<String, (String, i64)> = HashMap::new();
    let rows: Vec<String> = {
        let mut stmt = conn
            .prepare(
                "SELECT imported_path FROM imports WHERE file_id IN \
                 (SELECT id FROM files WHERE repo_id = ?)",
            )
            .map_err(|e| ToolError(e.to_string()))?;
        stmt.query_map(rusqlite::params![repo], |r| r.get::<_, String>(0))
            .map_err(|e| ToolError(e.to_string()))?
            .collect::<Result<Vec<_>, _>>()
            .map_err(|e| ToolError(e.to_string()))?
    };
    for path in rows {
        for (ns, owner) in &namespaces {
            if (path == *ns || path.starts_with(&format!("{ns}."))) && owner != repo {
                let entry = deps.entry(owner.clone()).or_insert_with(|| (ns.clone(), 0));
                entry.1 += 1;
            }
        }
    }
    let mut dependencies: Vec<(i64, Yaml)> = deps
        .into_iter()
        .map(|(owner, (evidence, count))| {
            let yaml = Yaml::Hash({
                let mut h = Hash::new();
                h.insert(ys("repo"), ys(&owner));
                h.insert(ys("type"), ys("import"));
                h.insert(ys("evidence"), ys(&evidence));
                h.insert(ys("count"), Yaml::Integer(count));
                h
            });
            (count, yaml)
        })
        .collect();
    dependencies.sort_by_key(|(count, _)| std::cmp::Reverse(*count));

    // Dependents: other repos importing this repo's namespaces.
    let my_namespaces: Vec<&String> = namespaces
        .iter()
        .filter(|(_, owner)| owner == repo)
        .map(|(ns, _)| ns)
        .collect();
    let mut dependents: Vec<(i64, Yaml)> = Vec::new();
    if !my_namespaces.is_empty() {
        let where_clause: Vec<String> = my_namespaces
            .iter()
            .map(|_| "(imported_path = ? OR substr(imported_path, 1, ?) = ?)".to_string())
            .collect();
        let mut params: Vec<String> = vec![repo.to_string()];
        for ns in &my_namespaces {
            params.push((*ns).clone());
            params.push((ns.len() + 1).to_string());
            params.push(format!("{ns}."));
        }
        let sql = format!(
            "SELECT imported_path, repo_id FROM imports JOIN files ON imports.file_id = files.id \
             WHERE files.repo_id != ? AND ({})",
            where_clause.join(" OR ")
        );
        if let Ok(mut stmt) = conn.prepare(&sql) {
            let mapped = stmt.query_map(rusqlite::params_from_iter(params.iter()), |r| {
                Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?))
            });
            if let Ok(mapped) = mapped {
                let rows: Vec<(String, String)> =
                    mapped.collect::<Result<Vec<_>, _>>().unwrap_or_default();
                let mut dep_map: HashMap<String, i64> = HashMap::new();
                for (_path, other_repo) in rows {
                    *dep_map.entry(other_repo).or_insert(0) += 1;
                }
                dependents = dep_map
                    .into_iter()
                    .map(|(r, count)| {
                        let yaml = Yaml::Hash({
                            let mut h = Hash::new();
                            h.insert(ys("repo"), ys(&r));
                            h.insert(ys("type"), ys("import"));
                            h.insert(ys("count"), Yaml::Integer(count));
                            h
                        });
                        (count, yaml)
                    })
                    .collect();
                dependents.sort_by_key(|(count, _)| std::cmp::Reverse(*count));
            }
        }
    }
    Ok(Yaml::Hash({
        let mut h = Hash::new();
        h.insert(
            ys("dependencies"),
            Yaml::Array(dependencies.into_iter().map(|(_, y)| y).collect()),
        );
        h.insert(
            ys("dependents"),
            Yaml::Array(dependents.into_iter().map(|(_, y)| y).collect()),
        );
        h
    }))
}

fn load_namespaces() -> Vec<(String, String)> {
    let parsed = std::env::var("CAIRN_REPO_NAMESPACES")
        .ok()
        .and_then(|raw| serde_json::from_str::<serde_json::Value>(&raw).ok());
    if let Some(serde_json::Value::Object(map)) = parsed {
        let clean: Vec<(String, String)> = map
            .into_iter()
            .filter(|(k, _)| !k.trim().is_empty())
            .filter_map(|(k, v)| v.as_str().map(|s| (k, s.to_string())))
            .filter(|(_, v)| !v.trim().is_empty())
            .collect();
        if !clean.is_empty() {
            return clean;
        }
    }
    [
        ("xyz.be.utils", "be-sdk"),
        ("xyz.be.customer.networking", "be-sdk"),
        ("xyz.be.customer.common", "be-sdk"),
        ("xyz.be.partner.common", "be-sdk"),
        ("xyz.be.common", "be-sdk"),
        ("xyz.be.networking", "be-sdk"),
        ("xyz.be.coreui", "be-core-ui"),
        ("xyz.be.newcoreui", "be-core-ui"),
        ("xyz.be.core_ui_v4", "be-core-ui"),
        ("xyz.be.core.ui", "be-core-ui"),
    ]
    .into_iter()
    .map(|(k, v)| (k.to_string(), v.to_string()))
    .collect()
}

// --- workflow (knowledge/workflow.py port) ---------------------------------------

pub fn resolve_workflow(bundle: &Bundle, ref_: &str) -> Result<Option<Concept>, ToolError> {
    if ref_.starts_with("knowledge/workflow/") {
        return get_document(bundle, ref_);
    }
    let by_slug_id = format!("knowledge/workflow/{}", slugify(ref_));
    if let Some(c) = get_document(bundle, &by_slug_id)? {
        return Ok(Some(c));
    }
    for c in list_documents(bundle, Some("workflow"), None, None) {
        if c.title.as_deref().unwrap_or("").trim().to_lowercase() == ref_.trim().to_lowercase() {
            return Ok(Some(c));
        }
    }
    Ok(None)
}

/// Resolved workflow projection.
pub struct WorkflowTrace {
    pub title: String,
    pub concept_id: String,
    pub doc_status: String,
    pub steps: Vec<WorkflowStep>,
}

/// Resolve a workflow by title/slug/concept id into its ordered projection.
pub fn trace_workflow(bundle: &Bundle, ref_: &str) -> Result<Option<WorkflowTrace>, ToolError> {
    let Some(concept) = resolve_workflow(bundle, ref_)? else {
        return Ok(None);
    };
    Ok(Some(WorkflowTrace {
        title: concept.title.clone().unwrap_or_default(),
        concept_id: normalize_doc_id(bundle, &concept.concept_id),
        doc_status: concept
            .ext_str("doc_status")
            .unwrap_or("active")
            .to_string(),
        steps: steps_of(&concept),
    }))
}

/// Render the authoritative structured steps as a markdown body.
pub fn render_steps_body(title: &str, steps: &[WorkflowStep]) -> String {
    let mut lines = vec![format!("# {title}\n")];
    for (i, step) in steps.iter().enumerate() {
        let n = i + 1;
        let name = match &step.name {
            Some(name) if !name.is_empty() => name.clone(),
            _ => format!("Step {n}"),
        };
        lines.push(format!("{n}. **{name}**"));
        if let Some(desc) = &step.description
            && !desc.is_empty()
        {
            lines.push(format!("   {desc}"));
        }
        if let Some(sym) = &step.symbol
            && !sym.is_empty()
        {
            lines.push(format!("   - symbol: `{sym}`"));
        }
        if let Some(file) = &step.file
            && !file.is_empty()
        {
            lines.push(format!("   - file: `{file}`"));
        }
    }
    format!("{}\n", lines.join("\n"))
}

/// Store one nonempty workflow and return its concept id.
pub fn add_workflow(
    bundle: &Bundle,
    title: &str,
    steps: Vec<WorkflowStep>,
    tags: Vec<String>,
    affects_modules: Vec<String>,
    affects_repos: Vec<String>,
    resource: Option<&str>,
) -> Result<String, ToolError> {
    if steps.is_empty() {
        return err("add_workflow requires at least one step");
    }
    let body = render_steps_body(title, &steps);
    add_document(
        bundle,
        title,
        &body,
        "workflow",
        tags,
        affects_modules,
        affects_repos,
        resource,
        None,
        None,
        Some(steps),
        None,
        "manual",
    )
}

// --- workflow staleness + sync (knowledge/workflow.py port) ---------------------

/// One stale step anchor.
pub struct StaleStep {
    pub step: String,
    pub symbol: String,
    pub file: String,
    pub symbol_ok: bool,
    pub file_ok: bool,
}

/// Staleness report for a workflow's graph anchors.
pub struct WorkflowStalenessReport {
    pub concept_id: String,
    pub title: String,
    pub resource: String,
    pub total_steps: usize,
    pub stale_details: Vec<StaleStep>,
}

impl WorkflowStalenessReport {
    pub fn stale_count(&self) -> usize {
        self.stale_details.len()
    }
}

/// Staleness report for one workflow, resolved by ref; None when unresolved.
pub fn check_workflow_staleness(
    conn: &Connection,
    bundle: &Bundle,
    ref_: &str,
) -> Result<Option<WorkflowStalenessReport>, ToolError> {
    let Some(concept) = resolve_workflow(bundle, ref_)? else {
        return Ok(None);
    };
    let steps = steps_of(&concept);
    let mut stale_details = Vec::new();
    for step in &steps {
        let sym = step.symbol.clone().unwrap_or_default();
        let file = step.file.clone().unwrap_or_default();
        // Skip the truncation-notice pseudo-step.
        if sym.is_empty() || sym.starts_with('(') {
            continue;
        }
        let symbol_ok = if sym.is_empty() {
            true
        } else {
            symbol_exists(conn, &sym).map_err(|e| ToolError(e.to_string()))?
        };
        let file_ok = if file.is_empty() {
            true
        } else {
            file_exists(conn, &file).map_err(|e| ToolError(e.to_string()))?
        };
        if !symbol_ok || !file_ok {
            stale_details.push(StaleStep {
                step: step.name.clone().unwrap_or_else(|| "?".to_string()),
                symbol: sym,
                file,
                symbol_ok,
                file_ok,
            });
        }
    }
    Ok(Some(WorkflowStalenessReport {
        concept_id: normalize_doc_id(bundle, &concept.concept_id),
        title: concept.title.clone().unwrap_or_default(),
        resource: concept.resource.clone().unwrap_or_default(),
        total_steps: steps.len(),
        stale_details,
    }))
}

/// Staleness reports for every workflow with stale steps, most stale first.
pub fn check_all_workflows(
    conn: &Connection,
    bundle: &Bundle,
) -> Result<Vec<WorkflowStalenessReport>, ToolError> {
    let mut reports = Vec::new();
    for concept in list_documents(bundle, Some("workflow"), None, None) {
        // Resolve each doc by its own concept id: title resolution lets
        // same-title docs shadow each other.
        let cid = normalize_doc_id(bundle, &concept.concept_id);
        if let Some(report) = check_workflow_staleness(conn, bundle, &cid)?
            && report.stale_count() > 0
        {
            reports.push(report);
        }
    }
    reports.sort_by_key(|r| std::cmp::Reverse(r.stale_count()));
    Ok(reports)
}

/// Sync outcome: step delta, or the reason the workflow was left unchanged.
pub struct WorkflowSyncResult {
    pub concept_id: String,
    pub title: String,
    pub resource: String,
    pub old_step_count: usize,
    pub new_step_count: usize,
    pub added: Vec<String>,
    pub removed: Vec<String>,
    pub error: Option<String>,
}

/// Re-trace a workflow and replace only its steps and rendered body.
pub fn sync_workflow(
    conn: &Connection,
    bundle: &Bundle,
    ref_: &str,
    max_steps: usize,
) -> Result<Option<WorkflowSyncResult>, ToolError> {
    let Some(concept) = resolve_workflow(bundle, ref_)? else {
        return Ok(None);
    };
    let resource = concept.resource.clone().unwrap_or_default();
    let old_steps = steps_of(&concept);
    let old_names: HashSet<String> = old_steps
        .iter()
        .map(|s| s.name.clone().unwrap_or_default())
        .collect();

    let error_result = |error: String, removed: Vec<String>| WorkflowSyncResult {
        concept_id: normalize_doc_id(bundle, &concept.concept_id),
        title: concept.title.clone().unwrap_or_default(),
        resource: resource.clone(),
        old_step_count: old_steps.len(),
        new_step_count: 0,
        added: Vec::new(),
        removed,
        error: Some(error),
    };

    if resource.is_empty() {
        return Ok(Some(error_result(
            "Workflow has no resource (entry symbol) recorded — nothing to \
             re-trace. Workflow left unchanged."
                .to_string(),
            Vec::new(),
        )));
    }
    let facts = gather_flow_facts(conn, &resource, None).map_err(|e| ToolError(e.to_string()))?;
    if facts.total_steps <= 1 {
        return Ok(Some(error_result(
            format!(
                "Entry symbol '{resource}' no longer traces — it may have been \
                 renamed or removed. Workflow left unchanged."
            ),
            sorted_set(&old_names),
        )));
    }
    let new_steps = flow_to_workflow(&facts, max_steps);
    let new_names: HashSet<String> = new_steps
        .iter()
        .map(|s| s.name.clone().unwrap_or_default())
        .collect();
    let added = sorted_diff(&new_names, &old_names);
    let removed = sorted_diff(&old_names, &new_names);

    let redacted: Vec<WorkflowStep> = new_steps
        .iter()
        .map(|s| {
            let mut s = s.clone();
            if let Some(desc) = &s.description
                && !desc.is_empty()
            {
                s.description = Some(strip_private_data(desc));
            }
            s
        })
        .collect();
    let mut ext = concept.extensions.clone();
    set_ext(&mut ext, "steps", flow_steps_yaml(&redacted));
    let title = concept
        .title
        .as_deref()
        .filter(|t| !t.is_empty())
        .unwrap_or(&resource);
    let new_body = strip_private_data(&render_steps_body(title, &redacted));
    // Python's sync constructs a fresh concept: the generated stamp is renewed
    // and first-class status/stale_after are dropped.
    let mut updated = Concept {
        type_: concept.type_.clone(),
        title: concept.title.clone(),
        description: concept.description.clone(),
        resource: concept.resource.clone(),
        tags: concept.tags.clone(),
        concept_id: concept.concept_id.clone(),
        body: new_body,
        extensions: ext,
        ..Default::default()
    };
    bundle.write_concept(&mut updated)?;
    Ok(Some(WorkflowSyncResult {
        concept_id: normalize_doc_id(bundle, &concept.concept_id),
        title: concept.title.clone().unwrap_or_default(),
        resource,
        old_step_count: old_steps.len(),
        new_step_count: redacted.len(),
        added,
        removed,
        error: None,
    }))
}

fn sorted_set(names: &HashSet<String>) -> Vec<String> {
    let mut out: Vec<String> = names.iter().cloned().collect();
    out.sort();
    out
}

fn sorted_diff(from: &HashSet<String>, without: &HashSet<String>) -> Vec<String> {
    let mut out: Vec<String> = from.difference(without).cloned().collect();
    out.sort();
    out
}

/// Steps yaml in flow_to_workflow's dict order (name, symbol, file, description).
fn flow_steps_yaml(steps: &[WorkflowStep]) -> Yaml {
    Yaml::Array(
        steps
            .iter()
            .map(|s| {
                let mut h = Hash::new();
                if let Some(n) = &s.name {
                    h.insert(ys("name"), ys(n));
                }
                if let Some(sym) = &s.symbol {
                    h.insert(ys("symbol"), ys(sym));
                }
                if let Some(f) = &s.file {
                    h.insert(ys("file"), ys(f));
                }
                if let Some(d) = &s.description {
                    h.insert(ys("description"), ys(d));
                }
                Yaml::Hash(h)
            })
            .collect(),
    )
}

// --- compass tools (tools_compass.py + compass/router.py port) --------------------

/// First compass concept matching `module` by resource or concept id.
pub fn find_compass_concept(bundle: &Bundle, module: &str) -> Result<Option<Concept>, ToolError> {
    for cid in bundle.list_concepts(Some("compass/")) {
        let Ok(concept) = bundle.read_concept(&cid) else {
            continue;
        };
        if concept
            .resource
            .as_deref()
            .is_some_and(|r| r.contains(module))
            || cid.contains(module)
        {
            return Ok(Some(concept));
        }
    }
    Ok(None)
}

fn search_knowledge_data(
    bundle: &Bundle,
    query: &str,
    type_filter: &str,
    limit: usize,
) -> Result<Vec<Concept>, ToolError> {
    let mut results = bundle.search(query, None)?;
    if !type_filter.is_empty() {
        results.retain(|c| c.type_.starts_with(type_filter));
    }
    results.truncate(limit);
    Ok(results)
}

// --- flow compass (compass/generator.py + compass/critic.py port) ------------

/// Default step cap for flow-to-workflow conversion (the MCP tool default).
pub const DEFAULT_FLOW_STEP_LIMIT: usize = 20;

/// Graph-grounded facts for one flow trace.
pub struct FlowFacts {
    pub entry: String,
    pub total_steps: usize,
    pub truncated: bool,
    pub chain_lines: Vec<String>,
    pub chain_raw: Vec<crate::graph::FlowNode>,
    pub branches: Vec<(String, Vec<String>)>,
    pub leaves: Vec<String>,
    pub modules: Vec<String>,
    pub cycles: Vec<(String, i64)>,
}

fn repo_roots(conn: &Connection) -> Result<HashMap<String, String>, rusqlite::Error> {
    let mut stmt = conn.prepare("SELECT id, path FROM repos")?;
    let rows = stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))?;
    rows.collect()
}

fn rel_path(path: &str, repo: &str, roots: &HashMap<String, String>) -> String {
    if let Some(root) = roots.get(repo)
        && let Some(stripped) = path.strip_prefix(&format!("{root}/"))
    {
        return stripped.to_string();
    }
    path.to_string()
}

pub fn gather_flow_facts(
    conn: &Connection,
    entry: &str,
    entry_id: Option<&str>,
) -> Result<FlowFacts, rusqlite::Error> {
    let flow = crate::graph::trace_flow_opts(conn, entry, 8, entry_id);
    let roots = repo_roots(conn)?;
    let mut chain_lines = Vec::new();
    for node in &flow.chain {
        let indent = "  ".repeat(node.depth.max(0) as usize);
        let fname = if node.file.is_empty() {
            "?".to_string()
        } else {
            node.file
                .rsplit('/')
                .next()
                .unwrap_or(&node.file)
                .to_string()
        };
        chain_lines.push(format!(
            "{indent}- `{}` ({}, {}:{})",
            node.symbol, node.kind, fname, node.repo
        ));
    }
    Ok(FlowFacts {
        entry: entry.to_string(),
        total_steps: flow.total,
        truncated: flow.truncated,
        chain_lines,
        chain_raw: flow
            .chain
            .iter()
            .map(|node| {
                let mut node = node.clone();
                node.file = rel_path(&node.file, &node.repo, &roots);
                node
            })
            .collect(),
        branches: flow.branches,
        leaves: flow.leaves,
        modules: flow.modules,
        cycles: flow.cycles,
    })
}

/// Deterministic 5-section body for a flow compass.
fn flow_template_body(facts: &FlowFacts) -> String {
    let mut lines: Vec<String> = vec![
        "# What Does This Flow Do?".to_string(),
        format!("- Entry point: `{}`", facts.entry),
        format!(
            "- Traces {} call step(s) across {} module(s).",
            facts.total_steps,
            facts.modules.len()
        ),
    ];
    if facts.truncated {
        lines.push("- (trace truncated at the node limit; some branches omitted)".to_string());
    }
    lines.push(String::new());

    lines.push("# Call Sequence".to_string());
    if facts.chain_lines.is_empty() {
        lines.push("- (no outgoing calls traced from the entry point)".to_string());
    } else {
        lines.extend(facts.chain_lines.iter().cloned());
    }
    lines.push(String::new());

    lines.push("# Failure-Prone Steps".to_string());
    if !facts.branches.is_empty() {
        for (symbol, callees) in facts.branches.iter().take(5) {
            let shown: Vec<String> = callees.iter().take(4).map(|c| format!("`{c}`")).collect();
            lines.push(format!(
                "- `{symbol}` branches to {} -- a change here ripples to \
                 multiple downstream paths.",
                shown.join(", ")
            ));
        }
    }
    if !facts.leaves.is_empty() {
        let sample: Vec<String> = facts
            .leaves
            .iter()
            .take(5)
            .map(|l| format!("`{l}`"))
            .collect();
        lines.push(format!(
            "- Terminal calls (side effects): {}.",
            sample.join(", ")
        ));
    }
    if facts.branches.is_empty() && facts.leaves.is_empty() {
        lines.push("- (no branch points or terminal calls detected)".to_string());
    }
    lines.push(String::new());

    lines.push("# Modules Spanned".to_string());
    let mut seen_files: Vec<String> = Vec::new();
    for node in &facts.chain_raw {
        if !node.file.is_empty() && !seen_files.contains(&node.file) {
            seen_files.push(node.file.clone());
        }
    }
    if !seen_files.is_empty() {
        seen_files.sort();
        for f in seen_files.iter().take(8) {
            let fname = f.rsplit('/').next().unwrap_or(f);
            lines.push(format!("- `{fname}` (`{f}`)"));
        }
    } else if !facts.modules.is_empty() {
        for module in facts.modules.iter().take(6) {
            lines.push(module.clone());
        }
    } else {
        lines.push("- (single module)".to_string());
    }
    lines.push(String::new());

    lines.push("# Tribal Knowledge".to_string());
    if !facts.cycles.is_empty() {
        let cyc: Vec<String> = facts
            .cycles
            .iter()
            .take(3)
            .map(|(s, _)| format!("`{s}`"))
            .collect();
        lines.push(format!(
            "- Recursive/cyclic calls detected at: {}.",
            cyc.join(", ")
        ));
    }
    lines.push("- (populated from code comments and TODOs when an LLM is available)".to_string());
    lines.join("\n") + "\n"
}

fn flow_name_files(conn: &Connection, name: &str) -> Result<Vec<String>, rusqlite::Error> {
    let mut stmt = conn.prepare(
        "SELECT DISTINCT f.path FROM symbols s JOIN files f ON s.file_id = f.id \
         WHERE s.name = ?",
    )?;
    let rows = stmt.query_map([name], |r| r.get::<_, String>(0))?;
    rows.collect()
}

fn flow_same_file_ids(
    conn: &Connection,
    name: &str,
    file: &str,
) -> Result<Vec<String>, rusqlite::Error> {
    let mut stmt = conn.prepare(
        "SELECT s.id FROM symbols s JOIN files f ON s.file_id = f.id \
         WHERE s.name = ? AND f.path = ?",
    )?;
    let rows = stmt.query_map(rusqlite::params![name, file], |r| r.get::<_, String>(0))?;
    rows.collect()
}

/// The collision-safe coverage key shared by detection and generation.
fn coverage_resource(
    name: &str,
    file: &str,
    files: &[String],
    local_ids: &[String],
    symbol_id: &str,
) -> String {
    if files.len() <= 1 && local_ids.len() <= 1 {
        return name.to_string();
    }
    let base = file.rsplit('/').next().unwrap_or("?");
    let mut bcounts: HashMap<&str, i64> = HashMap::new();
    for f in files {
        let b = f.rsplit('/').next().unwrap_or("?");
        *bcounts.entry(b).or_insert(0) += 1;
    }
    let suffix = if bcounts.get(base).copied().unwrap_or(0) > 1 {
        let parts: Vec<&str> = file.split('/').filter(|p| !p.is_empty()).collect();
        if parts.len() >= 2 {
            parts[parts.len() - 2..].join("/")
        } else {
            base.to_string()
        }
    } else {
        base.to_string()
    };
    let mut suffix = suffix;
    if local_ids.len() > 1 && !symbol_id.is_empty() {
        suffix = format!("{suffix}-{symbol_id}");
    }
    format!("{name}#{suffix}")
}

pub fn flow_coverage_key(
    conn: &Connection,
    entry: &str,
    entry_id: Option<&str>,
    facts: &FlowFacts,
) -> Result<String, rusqlite::Error> {
    let row = match entry_id {
        Some(id) => crate::graph::find_definition_by_id(conn, id),
        None => crate::graph::find_definition(conn, entry, 1)?
            .into_iter()
            .next(),
    };
    let entry_file = match &row {
        Some(r) => r.file_path.clone(),
        None => facts
            .chain_raw
            .first()
            .map(|n| n.file.clone())
            .unwrap_or_default(),
    };
    let files = flow_name_files(conn, entry)?;
    let local_ids = flow_same_file_ids(conn, entry, &entry_file)?;
    Ok(coverage_resource(
        entry,
        &entry_file,
        &files,
        &local_ids,
        row.as_ref().map_or("", |r| &r.id),
    ))
}

/// The flow compass concept for an entry symbol (caller runs the critic).
pub fn generate_flow_compass(
    conn: &Connection,
    entry: &str,
    entry_id: Option<&str>,
    facts: &FlowFacts,
    resource: Option<&str>,
    title: Option<&str>,
) -> Result<Concept, rusqlite::Error> {
    let body = flow_template_body(facts);
    let key = match resource {
        Some(r) => r.to_string(),
        None => flow_coverage_key(conn, entry, entry_id, facts)?,
    };
    let safe_id = key.replace(['/', '.', '#'], "-");
    let last_segment = entry.rsplit('.').next().unwrap_or(entry).to_string();
    Ok(Concept {
        type_: "Compass".to_string(),
        title: Some(title.map_or_else(|| format!("Flow: {entry}"), str::to_string)),
        description: Some(format!("Execution flow traced from `{entry}`")),
        resource: Some(key),
        tags: vec!["flow".to_string(), last_segment],
        timestamp: Some(iso_utc_now()),
        concept_id: format!("compass/flow-{safe_id}"),
        body,
        ..Default::default()
    })
}

/// Convert flow facts into bounded annotated workflow steps.
pub fn flow_to_workflow(facts: &FlowFacts, max_steps: usize) -> Vec<WorkflowStep> {
    let branches: HashMap<&str, &Vec<String>> = facts
        .branches
        .iter()
        .map(|(s, c)| (s.as_str(), c))
        .collect();
    let entry = &facts.entry;
    let mut steps: Vec<WorkflowStep> = Vec::new();
    for node in &facts.chain_raw {
        if steps.len() >= max_steps {
            break;
        }
        let sym = &node.symbol;
        let mut desc_parts: Vec<String> = Vec::new();
        if *sym == *entry && node.depth == 0 {
            desc_parts.push("Entry point".to_string());
        }
        if !node.kind.is_empty() {
            desc_parts.push(node.kind.clone());
        }
        if let Some(parent) = &node.parent {
            desc_parts.push(format!("called by `{parent}`"));
        }
        if let Some(callees) = branches.get(sym.as_str()) {
            let mut callee_str = callees
                .iter()
                .take(4)
                .map(|c| format!("`{c}`"))
                .collect::<Vec<_>>()
                .join(", ");
            if callees.len() > 4 {
                callee_str += &format!(", +{} more", callees.len() - 4);
            }
            desc_parts.push(format!("branches to {callee_str}"));
        }
        if facts.leaves.iter().any(|l| l == sym) {
            desc_parts.push("terminal — side effect".to_string());
        }
        steps.push(WorkflowStep {
            name: Some(sym.clone()),
            description: Some(desc_parts.join("; ")),
            symbol: Some(sym.clone()),
            file: Some(node.file.clone()),
        });
    }
    if facts.chain_raw.len() > max_steps {
        let omitted = facts.chain_raw.len() - max_steps;
        steps.push(WorkflowStep {
            name: Some(format!("(trace truncated — {omitted} more steps omitted)")),
            description: Some("Raise --max-steps to include deeper calls.".to_string()),
            symbol: None,
            file: None,
        });
    }
    steps
}

/// `generate_flow`: critic-gated flow compass (and optionally a workflow).
pub fn generate_flow_tool(
    bundle: &Bundle,
    conn: &Connection,
    entry: &str,
    as_workflow: bool,
    max_steps: usize,
    read_only: bool,
) -> Result<String, ToolError> {
    if read_only {
        return Ok(
            "read-only server: flow generation writes the knowledge store; \
             run it on the writable side"
                .to_string(),
        );
    }
    let facts = gather_flow_facts(conn, entry, None).map_err(|e| ToolError(e.to_string()))?;
    if facts.total_steps <= 1 {
        return Ok(format!(
            "No outgoing calls traced from '{entry}' — nothing to document."
        ));
    }

    let mut results: Vec<String> = Vec::new();
    if as_workflow {
        let steps = flow_to_workflow(&facts, max_steps);
        if steps.is_empty() {
            return Err(ToolError("add_workflow requires at least one step".into()));
        }
        let title = format!("Flow: {entry}");
        let mut body_lines = vec![format!("# {title}\n")];
        for (i, step) in steps.iter().enumerate() {
            let name = step
                .name
                .clone()
                .unwrap_or_else(|| format!("Step {}", i + 1));
            body_lines.push(format!("{}. **{name}**", i + 1));
            if let Some(desc) = &step.description
                && !desc.is_empty()
            {
                body_lines.push(format!("   {desc}"));
            }
            if let Some(symbol) = &step.symbol {
                body_lines.push(format!("   - symbol: `{symbol}`"));
            }
            if let Some(file) = &step.file
                && !file.is_empty()
            {
                body_lines.push(format!("   - file: `{file}`"));
            }
        }
        let last_segment = entry.rsplit('.').next().unwrap_or(entry).to_string();
        let cid = add_document(
            bundle,
            &title,
            &format!("{}\n", body_lines.join("\n")),
            "workflow",
            vec!["flow".to_string(), last_segment],
            Vec::new(),
            Vec::new(),
            Some(entry),
            None,
            None,
            Some(steps),
            None,
            "",
        )?;
        results.push(format!("Workflow: {cid} ({max_steps} steps max)"));
    }

    let concept = generate_flow_compass(conn, entry, None, &facts, None, None)
        .map_err(|e| ToolError(e.to_string()))?;
    let result = critic_concept(&concept, conn)?;
    if !result.passed {
        results.push(format!(
            "Compass REJECTED by critic (quality={:.2}, {} errors, {} warnings). \
             Workflow (if any) was still written.",
            result.quality_score,
            result.errors.len(),
            result.warnings.len()
        ));
        for e in result.errors.iter().take(8) {
            results.push(format!("  ERROR: {e}"));
        }
        for w in result.warnings.iter().take(4) {
            results.push(format!("  warn: {w}"));
        }
        if !result.errors.is_empty() {
            results.push(
                "The body cited backtick references not found in the graph — \
                 rebuild (cairn build) or fix the references before promoting."
                    .to_string(),
            );
        }
        results.push(critic_verdict_block(&result));
        return Ok(results.join("\n"));
    }

    let mut concept = concept;
    bundle.write_concept(&mut concept)?;
    results.push(format!(
        "Compass: {} (quality={:.2})",
        concept.concept_id, result.quality_score
    ));
    results.push(critic_verdict_block(&result));
    Ok(results.join("\n"))
}

// --- module compass + coverage gaps (compass/generator.py + compass/gaps.py port) ---

/// Module argument is ambiguous across repos or empty.
#[derive(Debug, Clone)]
pub struct ModuleResolutionError(pub String);

/// The flow facts in the `_gather_flow_facts` dict shape, for queue tasks.
pub fn flow_facts_yaml(facts: &FlowFacts) -> Hash {
    let mut h = Hash::new();
    h.insert(ys("entry"), ys(&facts.entry));
    h.insert(ys("mode"), ys("flow"));
    h.insert(ys("total_steps"), Yaml::Integer(facts.total_steps as i64));
    h.insert(ys("truncated"), Yaml::Boolean(facts.truncated));
    h.insert(
        ys("chain"),
        Yaml::Array(facts.chain_lines.iter().map(ys).collect()),
    );
    h.insert(
        ys("chain_raw"),
        Yaml::Array(
            facts
                .chain_raw
                .iter()
                .map(|n| {
                    let mut node = Hash::new();
                    node.insert(ys("symbol"), ys(&n.symbol));
                    node.insert(ys("kind"), ys(&n.kind));
                    node.insert(ys("file"), ys(&n.file));
                    node.insert(ys("repo"), ys(&n.repo));
                    node.insert(ys("depth"), Yaml::Integer(n.depth));
                    node.insert(
                        ys("parent"),
                        match &n.parent {
                            Some(p) => ys(p),
                            None => Yaml::Null,
                        },
                    );
                    Yaml::Hash(node)
                })
                .collect(),
        ),
    );
    h.insert(
        ys("branches"),
        Yaml::Array(
            facts
                .branches
                .iter()
                .map(|(symbol, callees)| {
                    let mut b = Hash::new();
                    b.insert(ys("symbol"), ys(symbol));
                    b.insert(ys("callees"), Yaml::Array(callees.iter().map(ys).collect()));
                    Yaml::Hash(b)
                })
                .collect(),
        ),
    );
    h.insert(
        ys("leaves"),
        Yaml::Array(facts.leaves.iter().map(ys).collect()),
    );
    h.insert(
        ys("modules"),
        Yaml::Array(facts.modules.iter().map(ys).collect()),
    );
    h.insert(
        ys("cycles"),
        Yaml::Array(
            facts
                .cycles
                .iter()
                .map(|(symbol, depth)| {
                    let mut c = Hash::new();
                    c.insert(ys("symbol"), ys(symbol));
                    c.insert(ys("depth"), Yaml::Integer(*depth));
                    Yaml::Hash(c)
                })
                .collect(),
        ),
    );
    h
}

/// One traced step as an editable Knowledge-workflow (writes the concept).
pub fn generate_flow_workflow(
    bundle: &Bundle,
    entry: &str,
    facts: &FlowFacts,
    max_steps: usize,
) -> Result<String, ToolError> {
    let steps = flow_to_workflow(facts, max_steps);
    let title = format!("Flow: {entry}");
    let last_segment = entry.rsplit('.').next().unwrap_or(entry).to_string();
    add_workflow(
        bundle,
        &title,
        steps,
        vec!["flow".to_string(), last_segment],
        Vec::new(),
        Vec::new(),
        Some(entry),
    )
}

/// Segment-anchored module-match params: exact, root-anchored, mid-path.
pub fn module_match_params(module_path: &str) -> (String, String, String) {
    let e = escape_like(module_path);
    (
        module_path.to_string(),
        format!("{e}/%"),
        format!("%/{e}/%"),
    )
}

pub fn resolve_module(
    conn: &Connection,
    module_path: &str,
    repo: Option<&str>,
) -> Result<(Option<String>, String), ModuleResolutionError> {
    let module_path = module_path.trim_matches('/').to_string();
    if module_path.is_empty() {
        return Err(ModuleResolutionError("module path is empty".into()));
    }
    let repo = match repo {
        Some(r) => Some(r.to_string()),
        None => infer_repo(conn, &module_path)?,
    };
    let normalized = repo
        .as_ref()
        .and_then(|r| module_path.strip_prefix(&format!("{r}/")))
        .unwrap_or(&module_path)
        .to_string();
    Ok((repo, normalized))
}

fn infer_repo(
    conn: &Connection,
    module_path: &str,
) -> Result<Option<String>, ModuleResolutionError> {
    let rows: Vec<(String, Option<String>)> = conn
        .prepare("SELECT id, path FROM repos")
        .and_then(|mut stmt| {
            stmt.query_map([], |r| {
                Ok((r.get::<_, String>(0)?, r.get::<_, Option<String>>(1)?))
            })?
            .collect::<Result<_, _>>()
        })
        .map_err(|e| ModuleResolutionError(e.to_string()))?;
    let repos: Vec<(String, String)> = rows
        .into_iter()
        .map(|(rid, path)| (rid, path.unwrap_or_default()))
        .collect();
    for (rid, _) in &repos {
        if module_path == rid || module_path.starts_with(&format!("{rid}/")) {
            return Ok(Some(rid.clone()));
        }
    }
    let rooted: Vec<String> = {
        let mut v: Vec<String> = repos
            .iter()
            .filter(|(_, path)| {
                let root = path.trim_end_matches('/');
                !root.is_empty() && module_path.starts_with(&format!("{root}/"))
            })
            .map(|(rid, _)| rid.clone())
            .collect();
        v.sort();
        v
    };
    if rooted.len() > 1 {
        return Err(ModuleResolutionError(format!(
            "module '{}' matches multiple repos ({}); pass --repo to pick one",
            module_path,
            rooted.join(", ")
        )));
    }
    if let Some(first) = rooted.first() {
        return Ok(Some(first.clone()));
    }
    let (m0, m1, m2) = module_match_params(module_path);
    let rows: Vec<String> = conn
        .prepare(
            "SELECT DISTINCT repo_id FROM files \
             WHERE (path = ? OR path LIKE ? ESCAPE '\\' OR path LIKE ? ESCAPE '\\')",
        )
        .and_then(|mut stmt| {
            stmt.query_map(rusqlite::params![m0, m1, m2], |r| r.get::<_, String>(0))?
                .collect::<Result<_, _>>()
        })
        .map_err(|e| ModuleResolutionError(e.to_string()))?;
    if rows.len() > 1 {
        let mut candidates = rows;
        candidates.sort();
        return Err(ModuleResolutionError(format!(
            "module '{}' exists in multiple repos ({}); pass --repo to pick one",
            module_path,
            candidates.join(", ")
        )));
    }
    if let Some(only) = rows.first() {
        return Ok(Some(only.clone()));
    }
    let segment: Vec<String> = {
        let mut v: Vec<String> = repos
            .iter()
            .map(|(rid, _)| rid.clone())
            .filter(|rid| {
                rid != module_path && format!("/{module_path}/").contains(&format!("/{rid}/"))
            })
            .collect();
        v.sort();
        v
    };
    if segment.len() > 1 {
        return Err(ModuleResolutionError(format!(
            "module '{}' matches multiple repos ({}); pass --repo to pick one",
            module_path,
            segment.join(", ")
        )));
    }
    if let Some(first) = segment.first() {
        return Ok(Some(first.clone()));
    }
    if repos.len() == 1 {
        return Ok(Some(repos[0].0.clone()));
    }
    Ok(None)
}

fn symbols_in_module(
    conn: &Connection,
    module_path: &str,
    repo: Option<&str>,
) -> Result<Vec<crate::graph::SymbolRow>, rusqlite::Error> {
    let mut sql = "SELECT s.id, s.name, s.kind, s.qualified_name, s.line_start, \
                  f.path, f.repo_id \
                  FROM symbols s JOIN files f ON s.file_id = f.id \
                  WHERE (f.path = ? OR f.path LIKE ? ESCAPE '\\' \
                  OR f.path LIKE ? ESCAPE '\\')"
        .to_string();
    let (m0, m1, m2) = module_match_params(module_path);
    let mut params = vec![m0, m1, m2];
    if let Some(repo) = repo {
        sql.push_str(" AND f.repo_id = ?");
        params.push(repo.to_string());
    }
    let mut stmt = conn.prepare(&sql)?;
    let mut rows = stmt.query(rusqlite::params_from_iter(params))?;
    let mut out = Vec::new();
    while let Some(r) = rows.next()? {
        out.push(crate::graph::SymbolRow {
            id: r.get(0)?,
            name: r.get(1)?,
            kind: r.get(2)?,
            qualified_name: r.get(3)?,
            line_start: r.get(4)?,
            line_end: None,
            file_path: r.get(5)?,
            repo: r.get(6)?,
        });
    }
    Ok(out)
}

/// One key file with its incoming-reference score and sample symbols.
pub struct KeyFile {
    pub path: String,
    pub repo: String,
    pub score: i64,
    pub symbols: Vec<String>,
}

fn rank_key_files(
    conn: &Connection,
    symbols: &[crate::graph::SymbolRow],
    top: usize,
) -> Result<Vec<KeyFile>, rusqlite::Error> {
    if symbols.is_empty() {
        return Ok(Vec::new());
    }
    let ids: Vec<String> = symbols.iter().map(|s| s.id.clone()).collect();
    let placeholders = vec!["?"; ids.len()].join(",");
    let sql = format!(
        "SELECT target_id, COUNT(*) AS c FROM edges \
         WHERE target_id IN ({placeholders}) GROUP BY target_id"
    );
    let mut incoming: HashMap<String, i64> = HashMap::new();
    let mut stmt = conn.prepare(&sql)?;
    let mut rows = stmt.query(rusqlite::params_from_iter(ids.iter()))?;
    while let Some(r) = rows.next()? {
        incoming.insert(r.get::<_, String>(0)?, r.get::<_, i64>(1)?);
    }
    // Insertion order preserves python's dict order for tied scores.
    let mut order: Vec<String> = Vec::new();
    let mut scores: HashMap<String, KeyFile> = HashMap::new();
    for s in symbols {
        let n = incoming.get(&s.id).copied().unwrap_or(0);
        if !scores.contains_key(&s.file_path) {
            order.push(s.file_path.clone());
            scores.insert(
                s.file_path.clone(),
                KeyFile {
                    path: s.file_path.clone(),
                    repo: s.repo.clone(),
                    score: 0,
                    symbols: Vec::new(),
                },
            );
        }
        let entry = scores.get_mut(&s.file_path).expect("just inserted");
        entry.score += n;
        entry.symbols.push(s.name.clone());
    }
    let mut ranked: Vec<KeyFile> = order
        .into_iter()
        .map(|p| scores.remove(&p).expect("key order"))
        .collect();
    ranked.sort_by_key(|k| std::cmp::Reverse(k.score));
    ranked.truncate(top);
    Ok(ranked)
}

fn cross_module_deps(
    conn: &Connection,
    module_path: &str,
    repo: Option<&str>,
) -> Result<Vec<String>, rusqlite::Error> {
    let legacy_repo_root = match repo {
        Some(r) => conn
            .prepare("SELECT path FROM repos WHERE id = ?")
            .and_then(|mut stmt| stmt.query_row([r], |row| row.get::<_, String>(0)))
            .unwrap_or_default(),
        None => String::new(),
    };
    let outside = match repo {
        Some(_) => {
            "NOT ((f2.path = ? OR f2.path LIKE ? ESCAPE '\\' OR f2.path LIKE ? ESCAPE '\\') \
             AND f2.repo_id = ?)"
        }
        None => "NOT (f2.path = ? OR f2.path LIKE ? ESCAPE '\\' OR f2.path LIKE ? ESCAPE '\\')",
    };
    let mut sql = format!(
        "SELECT DISTINCT f2.path AS target_path, f2.repo_id AS target_repo \
         FROM edges e \
         JOIN symbols s1 ON e.source_id = s1.id \
         JOIN files f1 ON s1.file_id = f1.id \
         JOIN symbols s2 ON e.target_id = s2.id \
         JOIN files f2 ON s2.file_id = f2.id \
         WHERE (f1.path = ? OR f1.path LIKE ? ESCAPE '\\' OR f1.path LIKE ? ESCAPE '\\') \
         AND {outside}"
    );
    let mut params: Vec<String> = Vec::new();
    let (f0, f1p, f2p) = module_match_params(module_path);
    params.extend([f0.clone(), f1p.clone(), f2p.clone()]);
    params.extend([f0, f1p, f2p]);
    if let Some(r) = repo {
        params.push(r.to_string());
        sql.push_str(" AND f1.repo_id = ?");
        params.push(r.to_string());
    }
    let mut stmt = conn.prepare(&sql)?;
    let mut rows = stmt.query(rusqlite::params_from_iter(params.iter()))?;
    let mut mods: Vec<String> = Vec::new();
    while let Some(r) = rows.next()? {
        let target_repo: String = r.get(1)?;
        let mut rel: String = r.get(0)?;
        if !legacy_repo_root.is_empty()
            && let Some(stripped) = rel.strip_prefix(&format!("{legacy_repo_root}/"))
        {
            rel = stripped.to_string();
        }
        let parts: Vec<&str> = rel
            .split('/')
            .filter(|p| !p.is_empty() && !matches!(*p, "src" | "main" | "java" | "kotlin"))
            .collect();
        let module = if parts.is_empty() {
            target_repo.clone()
        } else {
            format!("{}/{}", target_repo, parts[..parts.len().min(2)].join("/"))
        };
        if !mods.contains(&module) {
            mods.push(module);
        }
    }
    mods.sort();
    mods.truncate(8);
    Ok(mods)
}

/// Top communities by (-size, label) with each hub's qualified name.
fn subsystems_fact(conn: &Connection) -> Vec<(String, i64, Option<String>)> {
    let Ok(mut stmt) = conn
        .prepare("SELECT id, label, size FROM communities ORDER BY size DESC, label ASC LIMIT 5")
    else {
        return Vec::new();
    };
    let rows: Vec<(String, String, i64)> = match stmt
        .query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))
        .and_then(|rows| rows.collect::<Result<_, _>>())
    {
        Ok(rows) => rows,
        Err(_) => return Vec::new(),
    };
    rows.into_iter()
        .map(|(id, label, size)| {
            let hub = conn
                .prepare(
                    "SELECT s.qualified_name \
                     FROM symbol_communities sc \
                     JOIN symbols s ON s.id = sc.symbol_id \
                     JOIN files f ON f.id = s.file_id \
                     WHERE sc.community_id = ? \
                     ORDER BY sc.structural_degree DESC, f.path ASC, s.qualified_name ASC \
                     LIMIT 1",
                )
                .and_then(|mut stmt| stmt.query_row([&id], |r| r.get::<_, Option<String>>(0)))
                .ok()
                .flatten();
            (label, size, hub)
        })
        .collect()
}

fn is_gradle_repo(conn: &Connection, repo: &str) -> bool {
    let names = [
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
    ];
    let patterns: Vec<String> = names
        .iter()
        .flat_map(|n| [(*n).to_string(), format!("/{n}")])
        .collect();
    let ph = vec!["path LIKE ?"; patterns.len()].join(" OR ");
    let sql = format!("SELECT 1 FROM files WHERE repo_id = ? AND ({ph}) LIMIT 1");
    conn.prepare(&sql)
        .and_then(|mut stmt| {
            stmt.exists(rusqlite::params_from_iter(
                std::iter::once(repo.to_string()).chain(patterns),
            ))
        })
        .unwrap_or(false)
}

fn quick_commands(conn: &Connection, repo: Option<&str>) -> Vec<String> {
    let mut cmds = Vec::new();
    if let Some(repo) = repo
        && is_gradle_repo(conn, repo)
    {
        cmds.push(format!(
            "Build {repo}: `./gradlew :assembleDebug` (run in {repo}/)"
        ));
    }
    cmds.push("Find callers: `cairn callers <symbol>`".to_string());
    cmds
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
        parts.first().copied().unwrap_or(module_path).to_string()
    }
}

/// The deterministic 5-question compass body.
fn template_body(
    symbols: &[crate::graph::SymbolRow],
    key_files: &[KeyFile],
    cross_deps: &[String],
    subsystems: &[(String, i64, Option<String>)],
) -> String {
    let mut lines = vec!["# What Does This Module Do?".to_string()];
    if symbols.is_empty() {
        lines.push("- (no symbols detected in this module)".to_string());
    } else {
        let mut kinds: Vec<(String, i64)> = Vec::new();
        for s in symbols {
            match kinds.iter_mut().find(|(k, _)| *k == s.kind) {
                Some((_, n)) => *n += 1,
                None => kinds.push((s.kind.clone(), 1)),
            }
        }
        kinds.sort_by_key(|(_, n)| std::cmp::Reverse(*n));
        let summary = kinds
            .iter()
            .take(5)
            .map(|(k, v)| format!("{k}({v})"))
            .collect::<Vec<_>>()
            .join(", ");
        lines.push(format!("- {} symbols: {summary}", symbols.len()));
    }
    lines.push(String::new());
    if !subsystems.is_empty() {
        lines.push("# Subsystems".to_string());
        for (label, size, hub) in subsystems {
            match hub {
                Some(h) => lines.push(format!("- {label} ({size} symbols) - hub `{h}`")),
                None => lines.push(format!("- {label} ({size} symbols)")),
            }
        }
        lines.push(String::new());
    }
    lines.push("# Common Modification Patterns".to_string());
    if key_files.is_empty() {
        lines.push("- (no files with incoming references detected)".to_string());
    } else {
        for kf in key_files {
            let fname = kf.path.rsplit('/').next().unwrap_or(&kf.path);
            let sample: Vec<&str> = kf.symbols.iter().take(3).map(String::as_str).collect();
            let sample = if sample.is_empty() {
                "various"
            } else {
                &sample.join(", ")
            };
            lines.push(format!(
                "- `{fname}` - {} (refs: {}; e.g. {sample})",
                kf.repo, kf.score
            ));
        }
    }
    lines.push(String::new());
    lines.push("# Build-Failure Patterns".to_string());
    let mut section: Vec<String> = Vec::new();
    let top_score = key_files.first().map(|k| k.score).unwrap_or(0);
    if top_score > 20 {
        let top = key_files[0]
            .path
            .rsplit('/')
            .next()
            .unwrap_or(&key_files[0].path);
        section.push(format!(
            "- `{top}` is a high-traffic file ({top_score} incoming refs); changes ripple \
             widely — check `cairn impact` before modifying."
        ));
    }
    if !cross_deps.is_empty() {
        section.push(format!(
            "- This module reaches {} other modules; verify cross-module impact before \
             refactoring.",
            cross_deps.len()
        ));
    }
    if symbols.len() > 50 {
        section.push(format!(
            "- Large module ({} symbols); consider splitting if adding features.",
            symbols.len()
        ));
    }
    if section.is_empty() {
        section.push(
            "- (run the critic pass with an LLM to surface deeper tribal knowledge)".to_string(),
        );
    }
    lines.extend(section);
    lines.push(String::new());
    lines.push("# Cross-Module Dependencies".to_string());
    for m in cross_deps.iter().take(4) {
        lines.push(format!("- {} (`{m}`)", m.rsplit('/').next().unwrap_or(m)));
    }
    if cross_deps.is_empty() {
        lines.push("- (no cross-module references found)".to_string());
    }
    lines.push(String::new());
    lines.push("# Tribal Knowledge".to_string());
    lines.push("- (populated from code comments and TODOs when an LLM is available)".to_string());
    lines.join("\n") + "\n"
}

/// The one compass concept identity for a module.
fn compass_concept(module_path: &str, repo: Option<&str>, body: String) -> Concept {
    let title = derive_title(module_path);
    let concept_id = format!(
        "compass/{}",
        module_path.trim_matches('/').replace('/', "-")
    );
    let mut tags: Vec<String> = repo
        .filter(|r| !r.is_empty())
        .map(str::to_string)
        .into_iter()
        .collect();
    tags.extend(
        module_path
            .split('/')
            .filter(|p| !p.is_empty())
            .map(str::to_string),
    );
    tags.truncate(6);
    Concept {
        type_: "Compass".to_string(),
        title: Some(title),
        description: Some(format!("Navigation guide for {module_path}")),
        resource: Some(module_path.to_string()),
        tags,
        timestamp: Some(iso_utc_now()),
        concept_id,
        body,
        ..Default::default()
    }
}

/// Generate the deterministic compass concept for a module path.
pub fn generate_compass(
    conn: &Connection,
    bundle: &Bundle,
    module_path: &str,
    repo: Option<&str>,
) -> Result<Concept, ModuleResolutionError> {
    let (repo, module_path) = resolve_module(conn, module_path, repo)?;
    let symbols = symbols_in_module(conn, &module_path, repo.as_deref())
        .map_err(|e| ModuleResolutionError(e.to_string()))?;
    let key_files =
        rank_key_files(conn, &symbols, 5).map_err(|e| ModuleResolutionError(e.to_string()))?;
    let cross_deps = cross_module_deps(conn, &module_path, repo.as_deref())
        .map_err(|e| ModuleResolutionError(e.to_string()))?;
    let body = template_body(&symbols, &key_files, &cross_deps, &subsystems_fact(conn));
    let mut concept = compass_concept(&module_path, repo.as_deref(), body.clone());
    concept.body = preserved_concept_notes(bundle, &concept.concept_id, &body);
    Ok(concept)
}

/// The compass concept for an externally synthesized body (resolved module).
pub fn compass_concept_from_body(
    conn: &Connection,
    bundle: &Bundle,
    module_path: &str,
    repo: Option<&str>,
    body: &str,
) -> Result<Concept, ModuleResolutionError> {
    let (repo, module_path) = resolve_module(conn, module_path, repo)?;
    let mut concept = compass_concept(&module_path, repo.as_deref(), body.to_string());
    concept.body = preserved_concept_notes(bundle, &concept.concept_id, body);
    Ok(concept)
}

/// Graph-grounded module facts for the compass-synthesize queue task.
pub struct CompassFacts {
    pub resource: String,
    pub module: String,
    /// The resolved owning repo (not part of the queue-task facts dict).
    pub repo: Option<String>,
    pub symbol_count: usize,
    pub key_files: Vec<(String, i64)>,
    pub cross_deps: Vec<String>,
    pub quick_commands: Vec<String>,
    pub subsystems: Vec<(String, i64, Option<String>)>,
    pub symbol_names: Vec<String>,
}

/// The `_gather_facts` dict shape, for the queue task.
pub fn compass_facts_yaml(facts: &CompassFacts) -> Hash {
    let mut h = Hash::new();
    h.insert(ys("resource"), ys(&facts.resource));
    h.insert(ys("module"), ys(&facts.module));
    h.insert(ys("symbol_count"), Yaml::Integer(facts.symbol_count as i64));
    h.insert(
        ys("key_files"),
        Yaml::Array(
            facts
                .key_files
                .iter()
                .map(|(file, refs)| {
                    let mut kf = Hash::new();
                    kf.insert(ys("file"), ys(file));
                    kf.insert(ys("refs"), Yaml::Integer(*refs));
                    Yaml::Hash(kf)
                })
                .collect(),
        ),
    );
    h.insert(
        ys("cross_deps"),
        Yaml::Array(facts.cross_deps.iter().map(ys).collect()),
    );
    h.insert(
        ys("quick_commands"),
        Yaml::Array(facts.quick_commands.iter().map(ys).collect()),
    );
    h.insert(
        ys("subsystems"),
        Yaml::Array(
            facts
                .subsystems
                .iter()
                .map(|(label, size, hub)| {
                    let mut s = Hash::new();
                    s.insert(ys("label"), ys(label));
                    s.insert(ys("size"), Yaml::Integer(*size));
                    s.insert(ys("hub"), hub.as_deref().map(ys).unwrap_or(Yaml::Null));
                    Yaml::Hash(s)
                })
                .collect(),
        ),
    );
    h.insert(
        ys("symbol_names"),
        Yaml::Array(facts.symbol_names.iter().map(ys).collect()),
    );
    h
}

/// Gather the module facts (single source of truth for queue synthesis).
pub fn gather_module_facts(
    conn: &Connection,
    module_path: &str,
    repo: Option<&str>,
) -> Result<CompassFacts, ModuleResolutionError> {
    let (repo, module_path) = resolve_module(conn, module_path, repo)?;
    let symbols = symbols_in_module(conn, &module_path, repo.as_deref())
        .map_err(|e| ModuleResolutionError(e.to_string()))?;
    let key_files =
        rank_key_files(conn, &symbols, 5).map_err(|e| ModuleResolutionError(e.to_string()))?;
    let cross_deps = cross_module_deps(conn, &module_path, repo.as_deref())
        .map_err(|e| ModuleResolutionError(e.to_string()))?;
    let commands = quick_commands(conn, repo.as_deref());
    Ok(CompassFacts {
        key_files: key_files
            .iter()
            .map(|kf| {
                (
                    kf.path.rsplit('/').next().unwrap_or(&kf.path).to_string(),
                    kf.score,
                )
            })
            .collect(),
        resource: module_path.clone(),
        module: module_path,
        repo,
        symbol_count: symbols.len(),
        cross_deps,
        quick_commands: commands,
        subsystems: subsystems_fact(conn),
        symbol_names: symbols.iter().take(40).map(|s| s.name.clone()).collect(),
    })
}

/// Modules (per repo) that lack a compass concept, in store order.
pub fn detect_gaps(conn: &Connection, bundle: &Bundle) -> Vec<String> {
    let all_modules = gap_candidate_modules(conn);
    let repo_ids: HashSet<String> = conn
        .prepare("SELECT id FROM repos")
        .and_then(|mut stmt| {
            stmt.query_map([], |r| r.get::<_, String>(0))?
                .collect::<Result<_, _>>()
        })
        .unwrap_or_default();
    let mut covered: Vec<(Option<String>, String)> = Vec::new();
    for cid in bundle.list_concepts(Some("compass/")) {
        let Ok(concept) = bundle.read_concept(&cid) else {
            continue;
        };
        if let Some(resource) = &concept.resource
            && !resource.is_empty()
        {
            let repo = concept
                .tags
                .first()
                .filter(|t| repo_ids.contains(*t))
                .cloned();
            covered.push((repo, resource.clone()));
        }
    }
    all_modules
        .into_iter()
        .filter(|module| {
            let (repo_id, rel) = match module.split_once('/') {
                Some((r, rest)) => (r, rest),
                None => (module.as_str(), ""),
            };
            !covered.iter().any(|(c_repo, c)| {
                c_repo.as_deref().is_none_or(|cr| cr == repo_id)
                    && (rel == c.as_str()
                        || rel.starts_with(&format!("{c}/"))
                        || c.starts_with(&format!("{rel}/")))
            })
        })
        .collect()
}

fn gap_candidate_modules(conn: &Connection) -> Vec<String> {
    let repos: Vec<(String, String)> = conn
        .prepare("SELECT id, path FROM repos")
        .and_then(|mut stmt| {
            stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))?
                .collect::<Result<_, _>>()
        })
        .unwrap_or_default();
    let mut modules = Vec::new();
    for (repo_id, repo_path) in &repos {
        let paths: Vec<String> = conn
            .prepare("SELECT path FROM files WHERE repo_id = ?")
            .and_then(|mut stmt| {
                stmt.query_map([repo_id], |r| r.get::<_, String>(0))?
                    .collect::<Result<_, _>>()
            })
            .unwrap_or_default();
        let mut dirs: Vec<String> = Vec::new();
        for path in &paths {
            let rel = path.replacen(&format!("{repo_path}/"), "", 1);
            let parts: Vec<&str> = rel.split('/').collect();
            if parts.len() >= 3 {
                let segments = if parts.len() > 3 {
                    parts[..3].join("/")
                } else {
                    parts[..parts.len() - 1].join("/")
                };
                let dir = format!("{repo_id}/{segments}");
                if !dirs.contains(&dir) {
                    dirs.push(dir);
                }
            }
        }
        dirs.sort();
        for dir in dirs {
            let prefix = dir.split_once('/').map(|(_, p)| p).unwrap_or("");
            let root = repo_path.trim_end_matches('/');
            let rooted = format!("{root}/{prefix}");
            let prefixes: Vec<&str> = if root.is_empty() {
                vec![prefix]
            } else {
                vec![prefix, rooted.as_str()]
            };
            let n: i64 = prefixes
                .iter()
                .map(|p| -> i64 {
                    conn.prepare(
                        "SELECT COUNT(*) AS c FROM symbols s JOIN files f ON s.file_id=f.id \
                         WHERE f.repo_id = ? AND (f.path = ? OR f.path LIKE ? ESCAPE '\\')",
                    )
                    .and_then(|mut stmt| {
                        stmt.query_row(
                            rusqlite::params![repo_id, p, format!("{}{}", escape_like(p), "/%")],
                            |r| r.get::<_, i64>(0),
                        )
                    })
                    .unwrap_or(0)
                })
                .sum();
            if n >= 5 {
                modules.push(dir);
            }
        }
    }
    modules
}

/// One flow-coverage candidate with its classification.
pub struct FlowGapEntry {
    pub id: String,
    pub name: String,
    pub kind: String,
    pub file: String,
    pub repo: String,
    pub out_edges: i64,
    pub resource: String,
    pub colliding: bool,
    pub covered: bool,
}

/// Flow candidates over the edge threshold, split by compass coverage.
pub struct FlowGaps {
    pub uncovered: Vec<FlowGapEntry>,
    pub covered: Vec<FlowGapEntry>,
    pub unreadable: Vec<String>,
}

pub fn detect_flow_gaps(
    conn: &Connection,
    bundle: &Bundle,
    min_edges: i64,
) -> Result<FlowGaps, ToolError> {
    let candidates = flow_candidates(conn, min_edges)?;
    let mut covered_resources: HashSet<String> = HashSet::new();
    let mut unreadable: Vec<String> = Vec::new();
    for cid in bundle.list_concepts(Some("compass/flow-")) {
        match bundle.read_concept(&cid) {
            Err(_) => unreadable.push(cid),
            Ok(concept) => {
                if let Some(resource) = &concept.resource
                    && !resource.is_empty()
                {
                    covered_resources.insert(resource.clone());
                }
            }
        }
    }
    let mut uncovered = Vec::new();
    let mut covered = Vec::new();
    for c in candidates {
        let files = flow_name_files(conn, &c.name).map_err(|e| ToolError(e.to_string()))?;
        let local_ids =
            flow_same_file_ids(conn, &c.name, &c.file).map_err(|e| ToolError(e.to_string()))?;
        let resource = coverage_resource(&c.name, &c.file, &files, &local_ids, &c.id);
        let entry = FlowGapEntry {
            covered: covered_resources.contains(&resource),
            colliding: files.len() > 1 || local_ids.len() > 1,
            resource,
            id: c.id,
            name: c.name,
            kind: c.kind,
            file: c.file,
            repo: c.repo,
            out_edges: c.out_edges,
        };
        if entry.covered {
            covered.push(entry);
        } else {
            uncovered.push(entry);
        }
    }
    Ok(FlowGaps {
        uncovered,
        covered,
        unreadable,
    })
}

struct FlowCandidate {
    id: String,
    name: String,
    kind: String,
    file: String,
    repo: String,
    out_edges: i64,
}

fn flow_candidates(conn: &Connection, min_edges: i64) -> Result<Vec<FlowCandidate>, ToolError> {
    let kinds = crate::tools_nav::STRUCTURAL_EDGE_KINDS;
    let kind_ph = vec!["?"; kinds.len()].join(",");
    let sql = format!(
        "SELECT s.id, s.name, s.kind, f.path AS file_path, f.repo_id AS repo, \
         COUNT(e.id) AS out_edges \
         FROM symbols s \
         JOIN files f ON s.file_id = f.id \
         JOIN edges e ON e.source_id = s.id \
         WHERE e.target_id IS NOT NULL \
         AND e.kind IN ({kind_ph}) \
         AND s.kind IN ('function', 'method') \
         GROUP BY s.id \
         HAVING out_edges >= ? \
         ORDER BY out_edges DESC"
    );
    let mut stmt = conn.prepare(&sql).map_err(|e| ToolError(e.to_string()))?;
    let params: Vec<&dyn rusqlite::ToSql> = kinds
        .iter()
        .map(|k| k as &dyn rusqlite::ToSql)
        .chain(std::iter::once(&min_edges as &dyn rusqlite::ToSql))
        .collect();
    let mut rows = stmt
        .query(params.as_slice())
        .map_err(|e| ToolError(e.to_string()))?;
    let mut out = Vec::new();
    while let Some(r) = rows.next().map_err(|e| ToolError(e.to_string()))? {
        out.push(FlowCandidate {
            id: r.get(0).map_err(|e| ToolError(e.to_string()))?,
            name: r.get(1).map_err(|e| ToolError(e.to_string()))?,
            kind: r.get(2).map_err(|e| ToolError(e.to_string()))?,
            file: r.get(3).map_err(|e| ToolError(e.to_string()))?,
            repo: r.get(4).map_err(|e| ToolError(e.to_string()))?,
            out_edges: r.get(5).map_err(|e| ToolError(e.to_string()))?,
        });
    }
    Ok(out)
}

// --- compass router ---------------------------------------------------------------

struct IntentPattern {
    re: Regex,
    intent: &'static str,
    layer: &'static str,
}

fn intent_patterns() -> &'static Vec<IntentPattern> {
    static PATTERNS: OnceLock<Vec<IntentPattern>> = OnceLock::new();
    PATTERNS.get_or_init(|| {
        [
            (
                r"\b(business rule|business policy)\b",
                "knowledge_lookup",
                "L5",
            ),
            (
                r"\b(impact of changing|affects which|what repos)\b",
                "knowledge_impact",
                "L5",
            ),
            (r"\b(requirement|epic)\b", "knowledge_lookup", "L5"),
            (r"\bpolicy\b", "knowledge_lookup", "L5"),
            (
                r"\b(where is|defined|find definition|definition of)\b",
                "definition_lookup",
                "L1",
            ),
            (
                r"\b(calls|callers|callees|who calls|invokes)\b",
                "call_graph",
                "L1",
            ),
            (
                r"\b(impact|breaks|what if i change|blast radius)\b",
                "impact_analysis",
                "L1",
            ),
            (
                r"\b(how does|how do|flow|process|works?)\b",
                "feature_understanding",
                "L2",
            ),
            (r"\b(pattern|architecture|design)\b", "architecture", "L2"),
            (
                r"\b(navigate|where do i start|guide|onboard)\b",
                "module_navigation",
                "L3",
            ),
            (
                r"\b(gotcha|trap|non-obvious|watch out|pitfall)\b",
                "non_obvious_gotchas",
                "L3",
            ),
            (
                r"\b(why did|decision|chose|rationale)\b",
                "past_decisions",
                "L4",
            ),
            (
                r"\b(mistake|error|wrong|forgot|bug we hit)\b",
                "common_mistakes",
                "L4",
            ),
        ]
        .into_iter()
        .map(|(p, intent, layer)| IntentPattern {
            re: Regex::new(p).expect("intent regex"),
            intent,
            layer,
        })
        .collect()
    })
}

/// Classify a natural-language query into `(intent, layer)`.
pub fn classify_intent(query: &str) -> (&'static str, &'static str) {
    let q_lower = query.to_lowercase();
    for p in intent_patterns() {
        if p.re.is_match(&q_lower) {
            return (p.intent, p.layer);
        }
    }
    ("complex", "ALL")
}

const CAMELCASE_STOP: &[&str] = &[
    "The", "How", "Why", "What", "When", "Where", "Layer", "Does", "Did", "Are", "Is",
];

fn router_stop_words() -> HashSet<String> {
    let mut set: HashSet<String> = BASE_STOP_WORDS.iter().map(|s| s.to_string()).collect();
    for w in ["handle", "handles", "handled", "handling"] {
        set.insert(w.to_string());
    }
    set
}

fn extract_symbol_token(query: &str) -> String {
    static RE: OnceLock<Regex> = OnceLock::new();
    let re = RE.get_or_init(|| Regex::new(r"\b([A-Z][a-zA-Z0-9]{2,})\b").expect("camelcase regex"));
    for m in re.captures_iter(query) {
        let m = m[1].to_string();
        if !CAMELCASE_STOP.contains(&m.as_str()) {
            return m;
        }
    }
    String::new()
}

fn extract_query_tokens(query: &str) -> Vec<String> {
    static RE: OnceLock<Regex> = OnceLock::new();
    let re = RE.get_or_init(|| Regex::new(r"\b([A-Z][a-zA-Z0-9]{2,})\b").expect("camelcase regex"));
    let stop = router_stop_words();
    let mut tokens = Vec::new();
    let mut seen = HashSet::new();
    let mut seen_lower = HashSet::new();
    let q = query.trim();
    for m in re.captures_iter(q) {
        let m = m[1].to_string();
        if !CAMELCASE_STOP.contains(&m.as_str()) && !seen.contains(&m) {
            seen_lower.insert(m.to_lowercase());
            seen.insert(m.clone());
            tokens.push(m);
        }
    }
    for tok in simple_tokenize(q, Some(&stop)) {
        if !seen.contains(&tok) && !seen_lower.contains(&tok) {
            seen_lower.insert(tok.clone());
            seen.insert(tok.clone());
            tokens.push(tok);
        }
    }
    tokens
}

fn stem_token(token: &str) -> String {
    const TRANSFORMS: &[(&str, &str)] = &[
        ("ies", "y"),
        ("ied", "y"),
        ("ing", ""),
        ("tion", ""),
        ("ment", ""),
        ("ness", ""),
        ("able", ""),
        ("ible", ""),
        ("ful", ""),
        ("ous", ""),
    ];
    for (suffix, replacement) in TRANSFORMS {
        if let Some(stem) = token.strip_suffix(suffix) {
            let stem = format!("{stem}{replacement}");
            if stem.len() >= 3 {
                return stem;
            }
        }
    }
    if token.ends_with("es") && token.len() > 5 {
        return token[..token.len() - 2].to_string();
    }
    if token.ends_with('s') && token.len() > 4 {
        return token[..token.len() - 1].to_string();
    }
    if token.ends_with("ed") && token.len() > 5 {
        return token[..token.len() - 2].to_string();
    }
    String::new()
}

// --- router graph read (lexical + traversal subset the router needs) ---------------

struct SymbolRow {
    id: String,
    kind: String,
    name: String,
    file_path: String,
    line_start: Option<i64>,
    repo: String,
}

fn is_fts_prefix_pattern(pattern: &str) -> bool {
    let p = pattern.trim();
    let Some(stripped) = p.strip_suffix('*') else {
        return false;
    };
    if stripped.contains('*') || p.contains('_') {
        return false;
    }
    let token = stripped.trim();
    !token.is_empty() && !token.contains([' ', '"', ':', '(', ')'])
}

fn pattern_to_fts(pattern: &str) -> Option<String> {
    static STAR_RE: OnceLock<Regex> = OnceLock::new();
    static SPLIT_RE: OnceLock<Regex> = OnceLock::new();
    let p = pattern.trim();
    if p.is_empty() {
        return None;
    }
    if is_fts_prefix_pattern(p) {
        return Some(format!("{}*", p[..p.len() - 1].trim()));
    }
    let star_re = STAR_RE.get_or_init(|| Regex::new(r"[*%]").expect("star regex"));
    let split_re = SPLIT_RE.get_or_init(|| Regex::new(r"[^A-Za-z0-9]+").expect("split regex"));
    let cleaned = star_re.replace_all(p, " ").to_string();
    let tokens: Vec<&str> = split_re.split(&cleaned).filter(|t| !t.is_empty()).collect();
    if tokens.is_empty() {
        return None;
    }
    Some(format!("\"{}\"*", tokens.join(" ")))
}

fn like_symbol_rows(
    conn: &Connection,
    pattern: &str,
    limit: i64,
) -> Result<Vec<SymbolRow>, rusqlite::Error> {
    let mut sql_pattern: String = pattern
        .split('*')
        .map(|s| {
            s.replace('\\', "\\\\")
                .replace('%', "\\%")
                .replace('_', "\\_")
        })
        .collect::<Vec<_>>()
        .join("%");
    if !pattern.contains('*') {
        sql_pattern = format!("%{sql_pattern}%");
    }
    let mut stmt = conn.prepare(
        "SELECT s.*, f.path AS file_path, f.repo_id AS repo FROM symbols s \
         JOIN files f ON s.file_id = f.id WHERE s.name LIKE ? ESCAPE '\\' \
         ORDER BY s.name LIMIT ?",
    )?;
    let mapped = stmt.query_map(rusqlite::params![sql_pattern, limit], |row| {
        Ok(SymbolRow {
            id: row.get("id")?,
            kind: row.get("kind")?,
            name: row.get("name")?,
            file_path: row.get("file_path")?,
            line_start: row.get("line_start")?,
            repo: row.get("repo")?,
        })
    })?;
    mapped.collect()
}

fn fts_symbol_rows(
    conn: &Connection,
    fts_query: &str,
    limit: i64,
) -> Result<Vec<SymbolRow>, rusqlite::Error> {
    let mut stmt = conn.prepare(
        "SELECT s.*, f.path AS file_path, f.repo_id AS repo, bm25(symbols_fts) AS rank \
         FROM symbols_fts JOIN symbols s ON s.rowid = symbols_fts.rowid \
         JOIN files f ON s.file_id = f.id WHERE symbols_fts MATCH ? \
         ORDER BY rank LIMIT ?",
    )?;
    let mapped = stmt.query_map(rusqlite::params![fts_query, limit], |row| {
        Ok(SymbolRow {
            id: row.get("id")?,
            kind: row.get("kind")?,
            name: row.get("name")?,
            file_path: row.get("file_path")?,
            line_start: row.get("line_start")?,
            repo: row.get("repo")?,
        })
    })?;
    mapped.collect()
}

/// Router-local `search_symbols` (BM25 FTS with the LIKE fallback + merge).
fn router_search_symbols(conn: &Connection, pattern: &str, limit: i64) -> Vec<SymbolRow> {
    let Some(fts_query) = pattern_to_fts(pattern) else {
        return like_symbol_rows(conn, pattern, limit).unwrap_or_default();
    };
    let mut rows = match fts_symbol_rows(conn, &fts_query, limit) {
        Ok(rows) => rows,
        Err(_) => return like_symbol_rows(conn, pattern, limit).unwrap_or_default(),
    };
    if !is_fts_prefix_pattern(pattern) && (rows.len() as i64) < limit {
        let mut seen: HashSet<String> = rows.iter().map(|r| r.id.clone()).collect();
        if let Ok(extra) = like_symbol_rows(conn, pattern, limit) {
            for r in extra {
                let id = r.id.clone();
                if seen.insert(id) {
                    rows.push(r);
                    if rows.len() as i64 >= limit {
                        break;
                    }
                }
            }
        }
    }
    rows
}

struct EdgeRef {
    name: String,
    file_path: String,
    edge_line: Option<i64>,
}

fn router_callers(conn: &Connection, name: &str, limit: i64) -> Vec<EdgeRef> {
    let Ok(mut stmt) = conn.prepare(
        "SELECT s.name AS caller_name, f.path AS file_path, e.line AS edge_line \
         FROM edges e JOIN symbols s ON e.source_id = s.id \
         JOIN files f ON s.file_id = f.id \
         WHERE e.target_id IN (SELECT id FROM symbols WHERE name = ?) \
         ORDER BY f.repo_id, f.path, e.line, s.name LIMIT ?",
    ) else {
        return Vec::new();
    };
    let mapped = stmt.query_map(rusqlite::params![name, limit], |row| {
        Ok(EdgeRef {
            name: row.get(0)?,
            file_path: row.get(1)?,
            edge_line: row.get(2)?,
        })
    });
    mapped.map_or_else(
        |_| Vec::new(),
        |rows| rows.collect::<Result<Vec<_>, _>>().unwrap_or_default(),
    )
}

fn router_callees(conn: &Connection, name: &str, limit: i64) -> Vec<EdgeRef> {
    let Ok(mut stmt) = conn.prepare(
        "SELECT COALESCE(t.name, e.target_name) AS callee_name, f.path AS file_path, e.line AS edge_line \
         FROM edges e JOIN symbols s ON e.source_id = s.id \
         JOIN files f ON s.file_id = f.id \
         LEFT JOIN symbols t ON e.target_id = t.id \
         WHERE s.name = ? AND e.target_id IS NOT NULL \
         ORDER BY f.repo_id, f.path, e.line, COALESCE(t.name, e.target_name) LIMIT ?",
    ) else {
        return Vec::new();
    };
    let mapped = stmt.query_map(rusqlite::params![name, limit], |row| {
        Ok(EdgeRef {
            name: row.get(0)?,
            file_path: row.get(1)?,
            edge_line: row.get(2)?,
        })
    });
    mapped.map_or_else(
        |_| Vec::new(),
        |rows| rows.collect::<Result<Vec<_>, _>>().unwrap_or_default(),
    )
}

fn py_opt(v: &Option<i64>) -> String {
    match v {
        Some(n) => n.to_string(),
        None => "None".into(),
    }
}

/// Hybrid graph query: multi-token BM25 + 1-hop graph expansion.
fn query_graph(conn: &Connection, query: &str, token: &str) -> Yaml {
    let tokens = extract_query_tokens(query);
    if !tokens.is_empty() {
        return query_graph_hybrid(conn, &tokens);
    }
    let mut out = Hash::new();
    if !token.is_empty() {
        let defs = find_definition_rows(conn, token, 5).unwrap_or_default();
        let callers = router_callers(conn, token, 200);
        out.insert(
            ys("definition"),
            Yaml::Array(
                defs.iter()
                    .map(|d| {
                        Yaml::Hash({
                            let mut h = Hash::new();
                            h.insert(ys("name"), ys(&d.name));
                            h.insert(
                                ys("file"),
                                ys(format!("{}:{}", d.file_path, py_opt(&d.line_start))),
                            );
                            h.insert(ys("kind"), ys(&d.kind));
                            h
                        })
                    })
                    .collect(),
            ),
        );
        out.insert(ys("callers"), Yaml::Integer(callers.len() as i64));
    }
    Yaml::Hash(out)
}

fn find_definition_rows(
    conn: &Connection,
    name: &str,
    limit: i64,
) -> Result<Vec<SymbolRow>, rusqlite::Error> {
    let sql = "SELECT s.*, f.path AS file_path, f.repo_id AS repo FROM symbols s \
               JOIN files f ON s.file_id = f.id";
    let map = |row: &rusqlite::Row<'_>| {
        Ok(SymbolRow {
            id: row.get("id")?,
            kind: row.get("kind")?,
            name: row.get("name")?,
            file_path: row.get("file_path")?,
            line_start: row.get("line_start")?,
            repo: row.get("repo")?,
        })
    };
    let exact = {
        let mut stmt = conn.prepare(&format!("{sql} WHERE s.name = ? ORDER BY s.kind LIMIT ?"))?;
        stmt.query_map(rusqlite::params![name, limit], map)?
            .collect::<Result<Vec<_>, _>>()?
    };
    if !exact.is_empty() {
        return Ok(exact);
    }
    let qual = {
        let mut stmt = conn.prepare(&format!("{sql} WHERE s.qualified_name = ? LIMIT ?"))?;
        stmt.query_map(rusqlite::params![name, limit], map)?
            .collect::<Result<Vec<_>, _>>()?
    };
    if !qual.is_empty() {
        return Ok(qual);
    }
    let mut stmt = conn.prepare(&format!(
        "{sql} WHERE s.name LIKE ? ESCAPE '\\' ORDER BY s.kind LIMIT ?"
    ))?;
    stmt.query_map(
        rusqlite::params![format!("%{}%", escape_like(name)), limit],
        map,
    )?
    .collect()
}

fn query_graph_hybrid(conn: &Connection, tokens: &[String]) -> Yaml {
    let mut search_terms: Vec<String> = Vec::new();
    for tok in tokens {
        search_terms.push(tok.clone());
        let stemmed = stem_token(tok);
        if !stemmed.is_empty() && stemmed != *tok {
            search_terms.push(stemmed);
        }
    }
    let per_token_limit = i64::max(3, 10 / search_terms.len().max(1) as i64);

    // (kind, name, file, repo)
    let mut seeds: Vec<(String, String, String, String)> = Vec::new();
    let mut seen_ids = HashSet::new();
    for term in &search_terms {
        for r in router_search_symbols(conn, term, per_token_limit) {
            if !r.id.is_empty() {
                if seen_ids.contains(&r.id) {
                    continue;
                }
                seen_ids.insert(r.id.clone());
            }
            seeds.push((
                r.kind,
                r.name,
                format!("{}:{}", r.file_path, py_opt(&r.line_start)),
                r.repo,
            ));
        }
    }

    let mut callers_out: Vec<String> = Vec::new();
    let mut callees_out: Vec<String> = Vec::new();
    let mut seen_caller_keys: HashSet<(String, String, String, String)> = HashSet::new();
    let mut seen_callee_keys: HashSet<(String, String, String, String)> = HashSet::new();
    for (_kind, name, _file, _repo) in seeds.iter().take(5) {
        if name.is_empty() {
            continue;
        }
        for c in router_callers(conn, name, 5) {
            let key = (
                c.name.clone(),
                name.clone(),
                c.file_path.clone(),
                py_opt(&c.edge_line),
            );
            if seen_caller_keys.contains(&key) {
                continue;
            }
            seen_caller_keys.insert(key);
            callers_out.push(format!(
                "{} ({}:{})",
                c.name,
                c.file_path,
                py_opt(&c.edge_line)
            ));
        }
        for c in router_callees(conn, name, 5) {
            let key = (
                name.clone(),
                c.name.clone(),
                c.file_path.clone(),
                py_opt(&c.edge_line),
            );
            if seen_callee_keys.contains(&key) {
                continue;
            }
            seen_callee_keys.insert(key);
            callees_out.push(format!(
                "{} ({}:{})",
                c.name,
                c.file_path,
                py_opt(&c.edge_line)
            ));
        }
    }

    Yaml::Hash({
        let mut h = Hash::new();
        h.insert(
            ys("seeds"),
            Yaml::Array(
                seeds
                    .iter()
                    .map(|(kind, name, file, repo)| ys(format!("{kind} {name}  {file}  [{repo}]")))
                    .collect(),
            ),
        );
        h.insert(ys("seed_count"), Yaml::Integer(seeds.len() as i64));
        h.insert(
            ys("callers"),
            Yaml::Array(callers_out.iter().map(ys).collect()),
        );
        h.insert(
            ys("callees"),
            Yaml::Array(callees_out.iter().map(ys).collect()),
        );
        h.insert(
            ys("neighbor_count"),
            Yaml::Integer((callers_out.len() + callees_out.len()) as i64),
        );
        h
    })
}

fn search_wiki(bundle: &Bundle, query: &str) -> Vec<String> {
    bundle
        .search(query, Some(3))
        .unwrap_or_default()
        .into_iter()
        .filter(|c| c.type_ == "Wiki-Article" || c.type_ == "Wiki-Feature")
        .map(|c| c.title.unwrap_or_default())
        .collect()
}

fn get_compass_titles(token: &str, bundle: &Bundle) -> Vec<String> {
    let mut out = Vec::new();
    for cid in bundle.list_concepts(Some("compass/")) {
        let Ok(c) = bundle.read_concept(&cid) else {
            continue;
        };
        if !token.is_empty()
            && (c.resource.as_deref().is_some_and(|r| r.contains(token)) || cid.contains(token))
        {
            out.push(c.title.unwrap_or_default());
        }
    }
    out
}

fn route_memory_titles(conn: &Connection, bundle: &Bundle, query: &str) -> Vec<String> {
    let titles: Vec<String> = search_memory(conn, bundle, query, None, None, false, None)
        .unwrap_or_default()
        .into_iter()
        .take(3)
        .map(|c| c.title.unwrap_or_default())
        .collect();
    titles
}

fn route_knowledge_titles(bundle: &Bundle, query: &str) -> Vec<String> {
    bundle
        .search(query, Some(5))
        .unwrap_or_default()
        .into_iter()
        .filter(|c| {
            bundle
                .relativize_id(&c.concept_id)
                .starts_with("knowledge/")
        })
        .map(|c| c.title.unwrap_or_default())
        .collect()
}

fn layer_nonempty(v: &Yaml) -> bool {
    match v {
        Yaml::Hash(h) => {
            if let Some(Yaml::Integer(n)) = h.get(&ys("seed_count")) {
                return *n > 0;
            }
            h.values().any(layer_nonempty)
        }
        Yaml::Array(a) => !a.is_empty(),
        Yaml::String(s) => !s.is_empty(),
        Yaml::Integer(n) => *n != 0,
        Yaml::Real(x) => x.parse::<f64>().map(|v| v != 0.0).unwrap_or(true),
        Yaml::Boolean(b) => *b,
        Yaml::Null => false,
        // Alias/BadValue never appear in router-built payloads.
        _ => false,
    }
}

// --- deterministic critic (compass/critic.py port) ---------------------------------

/// `^#{1,2}([ \t]+.*)?[ \t\r]*$` — the same-or-higher heading that ends a section.
fn same_or_higher_heading(line: &str) -> bool {
    let Some(rest) = line
        .strip_prefix('#')
        .map(|r| r.strip_prefix('#').unwrap_or(r))
    else {
        return false;
    };
    if rest.trim_end_matches([' ', '\t', '\r']).is_empty() {
        return true;
    }
    rest.starts_with([' ', '\t'])
}

/// Offset of the first same-or-higher heading at/after `start` that sits
/// outside fenced code blocks; `body.len()` when none.
fn section_end(body: &str, start: usize) -> usize {
    let mut fence: Option<char> = None;
    let mut offset = start;
    for line in body[start..].split_inclusive('\n') {
        let stripped = line.trim_start_matches([' ', '\t']);
        let bare = stripped.strip_suffix('\n').unwrap_or(stripped);
        match fence {
            Some(f) if bare.starts_with(&f.to_string().repeat(3)) => fence = None,
            Some(_) => {}
            None if bare.starts_with("```") => fence = Some('`'),
            None if bare.starts_with("~~~") => fence = Some('~'),
            None if same_or_higher_heading(line.strip_suffix('\n').unwrap_or(line)) => {
                return offset;
            }
            None => {}
        }
        offset += line.len();
    }
    body.len()
}

/// `(Notes section, body without Notes)` with bytes intact.
pub fn split_notes(body: &str) -> (String, String) {
    let mut offset = 0usize;
    let mut heading: Option<usize> = None;
    for line in body.split_inclusive('\n') {
        let bare = line.strip_suffix('\n').unwrap_or(line);
        if bare.trim_end_matches([' ', '\t', '\r']) == "## Notes" {
            heading = Some(offset);
            break;
        }
        offset += line.len();
    }
    let Some(start) = heading else {
        return (String::new(), body.to_string());
    };
    let end = section_end(body, offset + "## Notes".len());
    (
        body[start..end].to_string(),
        format!("{}{}", &body[..start], &body[end..]),
    )
}

/// Body with its `## Notes` section removed (bytes intact elsewhere).
fn without_notes(body: &str) -> String {
    split_notes(body).1
}

/// Splice the existing Notes section verbatim into generated content.
pub fn splice_notes(existing_body: &str, generated_body: &str) -> String {
    let (notes, _) = split_notes(existing_body);
    if notes.is_empty() {
        return generated_body.to_string();
    }
    let mut content = without_notes(generated_body);
    if !content.is_empty() && !content.ends_with(['\n', '\r']) {
        content.push('\n');
    }
    format!("{content}{notes}")
}

/// Read an existing concept and splice its Notes into generated content.
pub fn preserved_concept_notes(bundle: &Bundle, concept_id: &str, generated_body: &str) -> String {
    match bundle.read_concept(concept_id) {
        Ok(existing) => splice_notes(&existing.body, generated_body),
        Err(_) => generated_body.to_string(),
    }
}

pub struct CriticResult {
    pub passed: bool,
    pub quality_score: f64,
    pub errors: Vec<String>,
    pub warnings: Vec<String>,
}

pub fn critic_concept(concept: &Concept, conn: &Connection) -> Result<CriticResult, ToolError> {
    critic_concept_opts(concept, conn, None, false)
}

/// `critic_concept` with the task-queue knobs: `section_vocab` replaces the
/// scored headings; `include_notes` also checks the `## Notes` section (task
/// results are promoted raw, unlike spliced regeneration bodies).
pub fn critic_concept_opts(
    concept: &Concept,
    conn: &Connection,
    section_vocab: Option<&[&str]>,
    include_notes: bool,
) -> Result<CriticResult, ToolError> {
    const SECTION_VOCAB: &[&str] = &[
        "# What Does This Module Do?",
        "# Subsystems",
        "# Common Modification Patterns",
        "# Build-Failure Patterns",
        "# Cross-Module Dependencies",
        "# What Does This Flow Do?",
        "# Call Sequence",
        "# Failure-Prone Steps",
        "# Modules Spanned",
        "# Tribal Knowledge",
    ];
    let body = if include_notes {
        concept.body.clone()
    } else {
        without_notes(&concept.body)
    };
    let mut errors = Vec::new();
    let mut warnings = Vec::new();
    let file_refs = extract_file_refs(&body);
    for r in &file_refs {
        if !file_exists(conn, r).map_err(|e| ToolError(e.to_string()))? {
            errors.push(format!("Hallucinated/unresolved file path: {r}"));
        }
    }
    let symbol_refs = extract_symbol_refs(&body);
    for s in &symbol_refs {
        if !symbol_exists(conn, s).map_err(|e| ToolError(e.to_string()))? {
            warnings.push(format!("Unknown symbol reference: {s}"));
        }
    }
    if let Some(w) = prose_heavy_warning(&body, file_refs.len() + symbol_refs.len()) {
        warnings.push(w);
    }
    let (vocab, total) = match section_vocab {
        Some(vocab) => (vocab, vocab.len().max(1) as f64),
        None => (SECTION_VOCAB, 5.0),
    };
    let sections = vocab.iter().filter(|h| body.contains(**h)).count();
    let quality = (sections as f64 / total).min(1.0);
    let threshold = if warnings.is_empty() { 0.5 } else { 0.7 };
    Ok(CriticResult {
        passed: errors.is_empty() && quality >= threshold,
        quality_score: quality,
        errors,
        warnings,
    })
}

fn prose_heavy_warning(body: &str, verified_ref_count: usize) -> Option<String> {
    let prose_only: String = body
        .lines()
        .filter(|line| !line.trim_start().starts_with('#'))
        .collect::<Vec<_>>()
        .join("\n");
    let prose_chars: usize = prose_only
        .split('`')
        .enumerate()
        .filter(|(i, _)| i % 2 == 0)
        .map(|(_, s)| s.chars().count())
        .sum();
    if prose_chars > 400 && verified_ref_count < 2 {
        return Some(format!(
            "Prose-heavy draft with few verifiable references ({verified_ref_count} backtick ref(s), \
             {prose_chars} chars of prose): deterministic critic cannot verify un-cited claims"
        ));
    }
    None
}

fn critic_verdict_block(result: &CriticResult) -> String {
    let json_str = |items: &[String]| -> String {
        items
            .iter()
            .map(|e| serde_json::to_string(e).unwrap_or_default())
            .collect::<Vec<_>>()
            .join(", ")
    };
    format!(
        "```cairn-critic\n{{\n  \"passed\": {},\n  \"quality_score\": {},\n  \"errors\": [{}],\n  \"warnings\": [{}]\n}}\n```",
        result.passed,
        fmt_score(result.quality_score),
        json_str(&result.errors),
        json_str(&result.warnings),
    )
}

// --- tool functions (the MCP surface) ------------------------------------------------

fn clamp(value: i64, lo: i64, hi: i64) -> i64 {
    i64::max(lo, i64::min(value, hi))
}

/// `recall_memory`: search past decisions/patterns/mistakes/workarounds with
/// live refs-verified fractions.
#[allow(clippy::too_many_arguments)]
pub fn recall_memory(
    conn: &Connection,
    bundle: &Bundle,
    query: &str,
    tier: &str,
    include_superseded: bool,
    as_of: Option<&str>,
    agent: Option<&str>,
    session_id: Option<&str>,
) -> Result<String, ToolError> {
    if let Some(agent) = agent {
        validate_agent_id(agent)?;
    }
    let tier = if tier.is_empty() { None } else { Some(tier) };
    let mut results = search_memory(
        conn,
        bundle,
        query,
        tier,
        session_id,
        include_superseded,
        as_of,
    )?;

    let mut shared_by_id: HashMap<String, String> = HashMap::new();
    if let Some(agent) = agent {
        let (extra, attribution) = shared_recall_entries(
            conn,
            bundle,
            agent,
            query,
            tier,
            include_superseded,
            as_of,
            &results.iter().map(|c| c.concept_id.clone()).collect(),
        )?;
        shared_by_id = attribution;
        results.extend(extra);
    }

    if results.is_empty() {
        return Ok(format!(
            "No memories matching '{query}'. Nothing was recorded under those \
             tokens. Try broader/fewer keywords, a symbol name instead of prose, \
             or `cairn memory digest` (via the CLI, no query) to see top tribal \
             memories for orientation. If you expected a memory here, it may not \
             have been captured -- see the Memory Capture Workflow in the skill."
        ));
    }
    let mut out = vec![format!("{} memories matching '{query}':", results.len())];
    for c in &results {
        let score = match c.ext("memory_score") {
            Some(Yaml::Real(s)) => s.clone(),
            Some(Yaml::Integer(i)) => i.to_string(),
            _ => "?".to_string(),
        };
        let t = c.ext_str("memory_tier").unwrap_or("?");
        let prov = c.ext_str("provenance").unwrap_or("");
        let prov_tag = if prov.is_empty() {
            String::new()
        } else {
            format!(", {prov}")
        };
        let superseded = !c.ext_bool("memory_is_latest").unwrap_or(true);
        let n_refs = extract_file_refs(&c.body).len() + extract_symbol_refs(&c.body).len();
        let refs_verified = graph_verification(c, conn)
            .map(fmt_score)
            .unwrap_or_else(|_| "?".to_string());
        let refs_display = if n_refs == 0 {
            "n/a (0 refs)".to_string()
        } else {
            refs_verified.clone()
        };
        let is_stale = n_refs > 0
            && refs_verified
                .parse::<f64>()
                .map(|v| v < 1.0)
                .unwrap_or(false);
        let tag = if superseded { " [SUPERSEDED]" } else { "" };
        let stale_tag = if is_stale { " [STALE]" } else { "" };
        let stance = c.ext_str("memory_stance").unwrap_or("");
        let stance_tag = if stance.is_empty() {
            String::new()
        } else {
            format!(", stance={stance}")
        };
        out.push(format!(
            "  [{t} {score}, refs-verified={refs_display}{prov_tag}{stance_tag}] {}{tag}{stale_tag}",
            c.title.as_deref().unwrap_or("?")
        ));
        if is_stale {
            out.push(
                "    ^ a cited file/symbol no longer exists in the graph -- verify before relying on this memory"
                    .to_string(),
            );
        }
        let peer = c.ext_str("memory_stance_peer").unwrap_or("");
        if stance == "contested" && !peer.is_empty() {
            out.push(format!(
                "    ^ contradicted by {peer} -- verify before relying on this memory"
            ));
        }
        if let Some(desc) = c.description.as_deref().filter(|d| !d.is_empty()) {
            out.push(format!("    {desc}"));
        }
        if let Some(attribution) = shared_by_id.get(&c.concept_id) {
            out.push(format!("    {attribution}"));
        }
    }
    let hint = unembedded_memory_hint(conn, bundle)?;
    if !hint.is_empty() {
        out.push(hint);
    }
    Ok(out.join("\n"))
}

#[allow(clippy::too_many_arguments)]
pub fn shared_recall_entries(
    conn: &Connection,
    bundle: &Bundle,
    agent_id: &str,
    query: &str,
    tier: Option<&str>,
    include_superseded: bool,
    as_of: Option<&str>,
    result_ids: &HashSet<String>,
) -> Result<(Vec<Concept>, HashMap<String, String>), ToolError> {
    let tokens: HashSet<String> = query
        .split(|c: char| c.is_whitespace() || c == ',' || c == ';')
        .filter(|t| !t.is_empty())
        .map(|t| t.to_lowercase())
        .collect();
    if tokens.is_empty() {
        return Ok((Vec::new(), HashMap::new()));
    }
    let instant = match as_of {
        Some(a) => parse_instant(a)
            .ok_or_else(|| ToolError(format!("as_of must be an ISO-8601 date, got {a:?}")))?,
        None => SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs() as i64)
            .unwrap_or(0),
    };
    let query_key = instant_key(instant);
    let rows: Vec<(String, String, Option<String>)> = {
        let mut stmt = conn
            .prepare(
                "SELECT agent_id, symbol, memory_id FROM agent_symbols \
                 WHERE kind = 'share' AND agent_id <> ? ORDER BY ts",
            )
            .map_err(|e| ToolError(e.to_string()))?;
        stmt.query_map(rusqlite::params![agent_id], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, Option<String>>(2)?,
            ))
        })
        .map_err(|e| ToolError(e.to_string()))?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| ToolError(e.to_string()))?
    };
    let found_ids: HashMap<String, String> = result_ids
        .iter()
        .map(|cid| {
            (
                cid.rsplit('/').next().unwrap_or(cid).to_string(),
                cid.clone(),
            )
        })
        .collect();
    let mut attribution: HashMap<String, Vec<(String, String)>> = HashMap::new();
    let mut pairs_by_memory: Vec<(String, Vec<(String, String)>)> = Vec::new();
    for (row_agent, symbol, memory_id) in rows {
        let Some(memory_id) = memory_id else { continue };
        if !tokens.contains(&symbol.to_lowercase()) {
            continue;
        }
        let key = memory_id
            .rsplit('/')
            .next()
            .unwrap_or(&memory_id)
            .to_string();
        let pair = (row_agent, symbol);
        if let Some(in_results) = found_ids.get(&key) {
            let pairs = attribution.entry(in_results.clone()).or_default();
            if !pairs.contains(&pair) {
                pairs.push(pair);
            }
            continue;
        }
        match pairs_by_memory.iter_mut().find(|(k, _)| *k == key) {
            Some((_, pairs)) => {
                if !pairs.contains(&pair) {
                    pairs.push(pair);
                }
            }
            None => pairs_by_memory.push((memory_id.clone(), vec![pair])),
        }
    }
    let mut out = Vec::new();
    for (memory_id, pairs) in &pairs_by_memory {
        let Ok(c) = bundle.read_concept(memory_id) else {
            continue; // orphaned by a since-deleted memory
        };
        if tier.is_some_and(|t| !c.ext_str("memory_tier").unwrap_or("").starts_with(t)) {
            continue;
        }
        if !include_superseded && !c.ext_bool("memory_is_latest").unwrap_or(true) {
            continue;
        }
        if !valid_at(&c, &query_key) {
            continue;
        }
        attribution.insert(c.concept_id.clone(), pairs.clone());
        out.push(c);
    }
    let mut attribution_fmt: HashMap<String, String> = HashMap::new();
    for (cid, pairs) in attribution {
        let mut by_agent: HashMap<String, Vec<String>> = HashMap::new();
        for (agent, symbol) in pairs {
            by_agent.entry(agent).or_default().push(symbol);
        }
        let parts: Vec<String> = by_agent
            .into_iter()
            .map(|(a, syms)| format!("{a} on '{}'", syms.join(", ")))
            .collect();
        attribution_fmt.insert(cid, format!("shared by {}", parts.join("; ")));
    }
    Ok((out, attribution_fmt))
}

/// `record_memory`: capture a learning (type: decision|pattern|mistake|workaround).
#[allow(clippy::too_many_arguments)]
pub fn record_memory(
    conn: &Connection,
    bundle: &Bundle,
    type_: &str,
    title: &str,
    body: &str,
    resource: &str,
    confidence: f64,
    stance: Option<&str>,
) -> Result<String, ToolError> {
    let result = capture_memory(
        conn,
        bundle,
        type_,
        title,
        body,
        if resource.is_empty() {
            None
        } else {
            Some(resource)
        },
        confidence,
        None,
        stance,
    )?;
    let mut msg = format!(
        "Recorded {type_} '{title}' -> {} (score={}, tier={})",
        result.path,
        fmt_score(result.score),
        result.tier
    );
    if let Some(superseded) = &result.superseded {
        msg.push_str(&format!(" [superseded {superseded}]"));
    }
    Ok(msg)
}

/// `knowledge_add`: ingest a business document as knowledge.
#[allow(clippy::too_many_arguments)]
pub fn knowledge_add(
    bundle: &Bundle,
    title: &str,
    body: &str,
    doc_type: &str,
    tags: &str,
    affects_modules: &str,
    affects_repos: &str,
    resource: &str,
    epic_link: &str,
    read_only: bool,
) -> Result<String, ToolError> {
    if read_only {
        return Ok(
            "read-only server: knowledge_add writes the knowledge store; run it on the writable side"
                .to_string(),
        );
    }
    let _ = std::fs::create_dir_all(&bundle.root);
    let replaced = get_document(bundle, &doc_concept_id(title, doc_type, None))?.is_some();
    let cid = add_document(
        bundle,
        title,
        body,
        doc_type,
        split_csv(tags),
        split_csv(affects_modules),
        split_csv(affects_repos),
        if resource.is_empty() {
            None
        } else {
            Some(resource)
        },
        None,
        if epic_link.is_empty() {
            None
        } else {
            Some(epic_link)
        },
        None,
        None,
        "manual",
    )?;
    let note = if replaced {
        " (replaced an existing document with the same title and doc_type)"
    } else {
        ""
    };
    Ok(format!("Stored knowledge document: {cid}{note}"))
}

/// Split a comma-separated CLI value into stripped non-empty entries.
pub fn split_csv(s: &str) -> Vec<String> {
    s.split(',')
        .map(|x| x.trim().to_string())
        .filter(|x| !x.is_empty())
        .collect()
}

/// `knowledge_search`: search business knowledge docs by meaning.
pub fn knowledge_search(
    conn: &Connection,
    bundle: &Bundle,
    query: &str,
    limit: i64,
) -> Result<String, ToolError> {
    let limit = clamp(limit, 1, 1000) as usize;
    let results = search_knowledge_docs(conn, bundle, query, limit)?;
    if results.is_empty() {
        return Ok(format!(
            "No knowledge documents matching '{query}'. The PO ingestion path \
             (knowledge_add) may not have indexed docs for this topic, or the \
             corpus isn't embedded yet (run `cairn knowledge embed`). Try broader \
             terms, or search_knowledge(query, ...) for the knowledge layer vs \
             this business-docs layer."
        ));
    }
    let mut out = vec![format!(
        "=== knowledge_search: \"{query}\" ({} doc(s)) ===",
        results.len()
    )];
    for r in &results {
        let score = if r.score < 1.0 {
            format!(" {:.2}", r.score)
        } else {
            String::new()
        };
        out.push(format!(
            "  [{}{}] {}  ({})",
            r.provenance, score, r.title, r.doc_type
        ));
        if !r.affects_repos.is_empty() {
            out.push(format!("    affects_repos: {}", r.affects_repos.join(", ")));
        }
        for (repo, deps) in &r.graph_deps {
            let Some(Yaml::Array(dependencies)) = (match deps {
                Yaml::Hash(h) => h.get(&ys("dependencies")),
                _ => None,
            }) else {
                continue;
            };
            let dep_repos: Vec<String> = dependencies
                .iter()
                .filter_map(|d| match d {
                    Yaml::Hash(dh) => str_of(dh.get(&ys("repo"))).filter(|s| !s.is_empty()),
                    _ => None,
                })
                .collect();
            if !dep_repos.is_empty() {
                out.push(format!("    {repo} → depends on: {}", dep_repos.join(", ")));
            }
        }
    }
    Ok(out.join("\n"))
}

/// `knowledge_delete`: delete a knowledge document and its embedding rows.
pub fn knowledge_delete(
    conn: &Connection,
    bundle: &Bundle,
    doc_id: &str,
    read_only: bool,
) -> Result<String, ToolError> {
    if read_only {
        return Ok(
            "read-only server: knowledge_delete writes the knowledge store; run it on the writable side"
                .to_string(),
        );
    }
    let concept = get_document(bundle, doc_id)?;
    if let Err(e) = refuse_out_of_namespace(bundle, doc_id, concept.as_ref()) {
        return Ok(e.0);
    }
    let ok = delete_document(bundle, doc_id, Some(conn), false)?;
    if !ok {
        return Ok(format!("Knowledge document not found: '{doc_id}'."));
    }
    if !delete_document(bundle, doc_id, None, true)? {
        // Embedding rows committed but the file was already gone.
    }
    Ok(format!("Deleted knowledge document: '{doc_id}'."))
}

/// `knowledge_status`: update doc_status (active → superseded → archived).
pub fn knowledge_status(
    bundle: &Bundle,
    doc_id: &str,
    new_status: &str,
    read_only: bool,
) -> Result<String, ToolError> {
    if read_only {
        return Ok(
            "read-only server: knowledge_status writes the knowledge store; run it on the writable side"
                .to_string(),
        );
    }
    let concept = get_document(bundle, doc_id)?;
    if let Err(e) = refuse_out_of_namespace(bundle, doc_id, concept.as_ref()) {
        return Ok(e.0);
    }
    let ok = update_status(bundle, doc_id, new_status)?;
    if !ok {
        return Ok(format!(
            "Not found, unknown status, or backward transition rejected: '{doc_id}' -> '{new_status}'."
        ));
    }
    Ok(format!("Updated '{doc_id}' status -> {new_status}."))
}

/// `trace_workflow`: trace a workflow's ordered steps by title/slug/concept_id.
pub fn trace_workflow_tool(bundle: &Bundle, ref_: &str) -> Result<String, ToolError> {
    let Some(trace) = trace_workflow(bundle, ref_)? else {
        return Ok(format!(
            "No workflow found matching '{ref_}'. Try knowledge_search('{ref_}') \
             or list workflows via `cairn knowledge list --type workflow`."
        ));
    };
    let status_note = if trace.doc_status != "active" {
        format!(" [{}]", trace.doc_status)
    } else {
        String::new()
    };
    let mut out = vec![format!(
        "{}{status_note} ({})",
        trace.title, trace.concept_id
    )];
    for (i, step) in trace.steps.iter().enumerate() {
        let n = i + 1;
        let name = step.name.clone().unwrap_or_else(|| format!("Step {n}"));
        out.push(format!("  {n}. {name}"));
        if let Some(desc) = &step.description {
            out.push(format!("     {desc}"));
        }
        if let Some(sym) = &step.symbol {
            out.push(format!("     symbol: {sym}"));
        }
        if let Some(file) = &step.file {
            out.push(format!("     file: {file}"));
        }
    }
    Ok(out.join("\n"))
}

/// `get_compass`: get the compass navigation guide for a module.
pub fn get_compass(bundle: &Bundle, module: &str) -> Result<String, ToolError> {
    match find_compass_concept(bundle, module)? {
        Some(concept) => Ok(format!(
            "# {}\n\n{}",
            concept.title.as_deref().unwrap_or(""),
            concept.body
        )),
        None => Ok(format!(
            "No compass file found for '{module}'. Generate with: cairn compass generate {module}"
        )),
    }
}

/// `search_knowledge`: search the knowledge base (wiki, compass, patterns, memory).
pub fn search_knowledge(
    bundle: &Bundle,
    query: &str,
    type_filter: &str,
    limit: i64,
    full_body: bool,
) -> Result<String, ToolError> {
    let limit = if limit < 0 { 0 } else { limit as usize };
    let results = search_knowledge_data(bundle, query, type_filter, limit)?;
    if results.is_empty() {
        let label = if type_filter.is_empty() {
            String::new()
        } else {
            format!(" {type_filter}")
        };
        return Ok(format!("No{label} results matching '{query}'."));
    }
    if full_body {
        let blocks: Vec<String> = results
            .iter()
            .map(|c| format!("# {}\n{}", c.title.as_deref().unwrap_or(""), c.body))
            .collect();
        return Ok(blocks.join("\n\n---\n\n"));
    }
    let mut out = vec![format!("{} results matching '{query}':", results.len())];
    for c in &results {
        out.push(format!(
            "  {} ({})",
            c.title.as_deref().unwrap_or(""),
            c.concept_id
        ));
        if let Some(desc) = c.description.as_deref().filter(|d| !d.is_empty()) {
            out.push(format!("    {desc}"));
        }
    }
    Ok(out.join("\n"))
}

/// One routed query across the layers (`compass/router.py route_query`).
pub struct RouteOutcome {
    pub intent: &'static str,
    pub layer: &'static str,
    pub layers_queried: Vec<String>,
    pub results: Vec<(String, Yaml)>,
    pub degraded: bool,
    pub empty: bool,
}

/// Classify a natural-language query and run the targeted layers, degrading
/// across the derived and asserted tiers when the target produced nothing.
pub fn route_query(conn: &Connection, bundle: &Bundle, query: &str) -> RouteOutcome {
    let (intent, layer) = classify_intent(query);
    let token = extract_symbol_token(query);
    let mut results: Vec<(String, Yaml)> = Vec::new();
    if layer == "L1" || layer == "ALL" {
        results.push(("graph".into(), query_graph(conn, query, &token)));
    }
    if layer == "L2" || layer == "ALL" {
        results.push((
            "wiki".into(),
            Yaml::Array(search_wiki(bundle, query).into_iter().map(ys).collect()),
        ));
    }
    if layer == "L3" || layer == "ALL" {
        results.push((
            "compass".into(),
            Yaml::Array(
                get_compass_titles(&token, bundle)
                    .into_iter()
                    .map(ys)
                    .collect(),
            ),
        ));
    }
    if layer == "L4" || layer == "ALL" {
        results.push((
            "memory".into(),
            Yaml::Array(
                route_memory_titles(conn, bundle, query)
                    .into_iter()
                    .map(ys)
                    .collect(),
            ),
        ));
    }
    if layer == "L5" || layer == "ALL" {
        results.push((
            "knowledge".into(),
            Yaml::Array(
                route_knowledge_titles(bundle, query)
                    .into_iter()
                    .map(ys)
                    .collect(),
            ),
        ));
    }

    let mut layers_queried: Vec<String> = results.iter().map(|(k, _)| k.clone()).collect();
    let mut degraded = false;
    if !results.iter().any(|(_, v)| layer_nonempty(v)) {
        let mut fb: Vec<(String, Yaml)> = Vec::new();
        let graph = query_graph(conn, query, &token);
        if layer_nonempty(&graph) {
            fb.push(("graph".into(), graph));
        }
        let memory = Yaml::Array(
            route_memory_titles(conn, bundle, query)
                .into_iter()
                .map(ys)
                .collect(),
        );
        if layer_nonempty(&memory) {
            fb.push(("memory".into(), memory));
        }
        let knowledge = Yaml::Array(
            route_knowledge_titles(bundle, query)
                .into_iter()
                .map(ys)
                .collect(),
        );
        if layer_nonempty(&knowledge) {
            fb.push(("knowledge".into(), knowledge));
        }
        layers_queried = fb.iter().map(|(k, _)| k.clone()).collect();
        results = fb;
        degraded = true;
    }
    let empty = !results.iter().any(|(_, v)| layer_nonempty(v));
    RouteOutcome {
        intent,
        layer,
        layers_queried,
        results,
        degraded,
        empty,
    }
}

/// `ask_compass`: route a natural-language query across graph/wiki/compass/memory.
pub fn ask_compass(
    conn: &Connection,
    bundle: &Bundle,
    query: &str,
    file_path: &str,
) -> Result<String, ToolError> {
    if !file_path.is_empty() && query.is_empty() {
        return ask_compass_file_context(conn, bundle, file_path);
    }
    let outcome = route_query(conn, bundle, query);
    let queried = if outcome.layers_queried.is_empty() {
        String::new()
    } else {
        format!("; queried: {}", outcome.layers_queried.join(", "))
    };
    let mut out = vec![format!(
        "Intent: {} (routed to {}{})",
        outcome.intent, outcome.layer, queried
    )];
    if !file_path.is_empty() {
        out.push(format!(
            "(file_path '{file_path}' is ignored in routing mode; pass it with an empty query \
             to load file-aware context.)"
        ));
    }
    if outcome.degraded {
        out.push("(Showing fallback results — targeted layer had no coverage.)".to_string());
    }
    if outcome.empty {
        out.push(
            "(No results from any layer — coverage for this query is thin. Don't conclude 'no info \
             exists'; drill down with the specific layer tool: explore() for structure, \
             search_knowledge() for docs, or recall_memory() for past decisions.)"
                .to_string(),
        );
    }
    for (layer_name, data) in &outcome.results {
        out.push(String::new());
        out.push(format!("[{layer_name}]"));
        match data {
            Yaml::Hash(h) => {
                for (k, v) in h {
                    // Hash values render as Python str(value): string-list
                    // members come out single-quoted (the router contract).
                    out.push(format!("  {}: {}", scalar_repr(k), python_repr(v)));
                }
            }
            Yaml::Array(items) => {
                for item in items {
                    out.push(format!("  {}", scalar_repr(item)));
                }
            }
            other => out.push(format!("  {}", scalar_repr(other))),
        }
    }
    Ok(out.join("\n"))
}

/// Python `str(value)` for router payload values: string list members quoted.
fn python_repr(v: &Yaml) -> String {
    match v {
        Yaml::Array(items) => {
            let inner: Vec<String> = items.iter().map(python_repr).collect();
            format!("[{}]", inner.join(", "))
        }
        Yaml::Hash(h) => {
            let inner: Vec<String> = h
                .iter()
                .map(|(k, v)| format!("{}: {}", python_repr(k), python_repr(v)))
                .collect();
            format!("{{{}}}", inner.join(", "))
        }
        Yaml::String(s) => {
            if s.contains('\'') && !s.contains('"') {
                format!("\"{s}\"")
            } else {
                format!("'{s}'")
            }
        }
        other => scalar_repr(other),
    }
}

/// Python `str(value)`: strings bare, other values as their repr.
pub fn py_str(v: &Yaml) -> String {
    match v {
        Yaml::String(s) => s.clone(),
        other => python_repr(other),
    }
}

fn ask_compass_file_context(
    conn: &Connection,
    bundle: &Bundle,
    file_path: &str,
) -> Result<String, ToolError> {
    let parts: Vec<&str> = file_path.split('/').filter(|p| !p.is_empty()).collect();
    let module_guess = if parts.len() >= 4 {
        parts[..4].join("/")
    } else {
        file_path.to_string()
    };
    let mut out = vec![
        format!("Context for {file_path}:"),
        format!("  inferred module: {module_guess}"),
    ];
    let mut loaded = None;
    for cid in bundle.list_concepts(Some("compass/")) {
        let Ok(c) = bundle.read_concept(&cid) else {
            continue;
        };
        if c.resource
            .as_deref()
            .is_some_and(|r| file_path.contains(r) || r.contains(file_path))
        {
            out.push(String::new());
            out.push(format!("# Compass: {}", c.title.as_deref().unwrap_or("")));
            out.push(c.body.clone());
            loaded = Some(c);
            break;
        }
    }
    if let Some(verdict) = loaded.as_ref().and_then(|c| critic_concept(c, conn).ok()) {
        out.push(critic_verdict_block(&verdict));
    }
    let seg = parts
        .last()
        .map(|p| p.replace(".kt", "").replace(".java", ""))
        .unwrap_or_default();
    if !seg.is_empty() {
        for c in bundle.search(&seg, Some(3))? {
            if c.type_ == "Wiki-Article" || c.type_ == "Wiki-Feature" {
                out.push(String::new());
                out.push(format!("# Wiki: {}", c.title.as_deref().unwrap_or("")));
                out.push(format!(
                    "{}...",
                    c.body.chars().take(500).collect::<String>()
                ));
                break;
            }
        }
        for c in bundle.search(&seg, Some(3))? {
            if bundle.relativize_id(&c.concept_id).starts_with("memory/") {
                out.push(String::new());
                out.push(format!("# Memory: {}", c.title.as_deref().unwrap_or("")));
                if let Some(desc) = c.description.as_deref().filter(|d| !d.is_empty()) {
                    out.push(format!("  {desc}"));
                }
                break;
            }
        }
    }
    Ok(out.join("\n"))
}

// --- knowledge index (knowledge/index.py + islands.py port) ----------------------

/// One normalized relationship neighbor (`related_docs` row).
pub struct RelatedDoc {
    pub doc_id: String,
    pub title: Option<String>,
    pub relation: String,
    pub kind: String,
    pub provenance: Option<String>,
    pub created_at: Option<f64>,
    pub direction: &'static str,
}

/// Raw knowledge_edges row: doc, related, relation, kind, provenance, created_at.
type EdgeRow = (String, String, String, String, Option<String>, Option<f64>);

/// Deterministic normalized neighbors from both knowledge_edges directions.
pub fn related_docs(
    conn: &Connection,
    bundle: &Bundle,
    doc_id: &str,
) -> Result<Vec<RelatedDoc>, ToolError> {
    let normalized = normalize_doc_id(bundle, doc_id);
    let mut neighbors: Vec<RelatedDoc> = Vec::new();
    let mut seen: HashSet<(String, String, String, String)> = HashSet::new();
    for (direction, column, order) in [
        ("outgoing", "doc_id", "related_id"),
        ("incoming", "related_id", "doc_id"),
    ] {
        let sql = format!(
            "SELECT doc_id, related_id, relation, kind, provenance, created_at \
             FROM knowledge_edges WHERE {column} = ? ORDER BY {order}"
        );
        let rows: Vec<EdgeRow> = conn
            .prepare(&sql)
            .and_then(|mut stmt| {
                stmt.query_map(rusqlite::params![normalized], |r| {
                    Ok((
                        r.get::<_, String>(0)?,
                        r.get::<_, String>(1)?,
                        r.get::<_, String>(2)?,
                        r.get::<_, String>(3)?,
                        r.get::<_, Option<String>>(4)?,
                        r.get::<_, Option<f64>>(5)?,
                    ))
                })?
                .collect()
            })
            .map_err(|e| ToolError(e.to_string()))?;
        for (doc_id, related_id, relation, kind, provenance, created_at) in rows {
            let row_key = (
                doc_id.clone(),
                related_id.clone(),
                relation.clone(),
                kind.clone(),
            );
            if !seen.insert(row_key) {
                continue;
            }
            let neighbor = if column == "doc_id" {
                related_id
            } else {
                doc_id
            };
            let Ok(concept) = bundle.read_concept(&neighbor) else {
                continue; // deleted between rebuilds: never a phantom neighbor
            };
            neighbors.push(RelatedDoc {
                doc_id: neighbor,
                title: concept.title,
                relation,
                kind,
                provenance,
                created_at,
                direction,
            });
        }
    }
    Ok(neighbors)
}

/// One member of a supersede chain.
pub struct ChainMember {
    pub concept_id: String,
    pub title: Option<String>,
    pub doc_status: String,
    pub relation: Option<&'static str>,
}

/// Resolve a knowledge doc or reject missing and foreign ids.
pub fn resolve_knowledge_doc(bundle: &Bundle, doc_id: &str) -> Result<Option<Concept>, ToolError> {
    let Some(concept) = get_document(bundle, doc_id)? else {
        return Ok(None);
    };
    refuse_out_of_namespace(bundle, doc_id, Some(&concept))?;
    Ok(Some(concept))
}

/// The oldest-to-newest supersede chain containing `doc_id`.
pub fn supersede_chain(
    conn: &Connection,
    bundle: &Bundle,
    doc_id: &str,
) -> Result<Option<Vec<ChainMember>>, ToolError> {
    let Some(concept) = resolve_knowledge_doc(bundle, doc_id)? else {
        return Ok(None);
    };
    let start = normalize_doc_id(bundle, &concept.concept_id);
    let rows: Vec<(String, String, String)> = conn
        .prepare(
            "SELECT doc_id, related_id, relation FROM knowledge_edges \
             WHERE relation IN ('supersedes', 'superseded-by')",
        )
        .and_then(|mut stmt| {
            stmt.query_map([], |r| {
                Ok((
                    r.get::<_, String>(0)?,
                    r.get::<_, String>(1)?,
                    r.get::<_, String>(2)?,
                ))
            })?
            .collect()
        })
        .map_err(|e| ToolError(e.to_string()))?;

    let mut newer_of: HashMap<String, HashSet<String>> = HashMap::new();
    let mut older_of: HashMap<String, HashSet<String>> = HashMap::new();
    let mut concepts: HashMap<String, Option<Concept>> = HashMap::new();
    let concept_for =
        |concepts: &mut HashMap<String, Option<Concept>>, cid: &str| -> Option<Concept> {
            if !concepts.contains_key(cid) {
                concepts.insert(cid.to_string(), bundle.read_concept(cid).ok());
            }
            concepts.get(cid).cloned().flatten()
        };
    for (doc_id, related_id, relation) in rows {
        let (newer, older) = if relation == "supersedes" {
            (doc_id, related_id)
        } else {
            (related_id, doc_id)
        };
        if concept_for(&mut concepts, &newer).is_none()
            || concept_for(&mut concepts, &older).is_none()
        {
            continue;
        }
        newer_of
            .entry(older.clone())
            .or_default()
            .insert(newer.clone());
        older_of.entry(newer).or_default().insert(older);
    }
    concepts
        .entry(start.clone())
        .or_insert_with(|| Some(concept.clone()));

    let mut back: Vec<String> = Vec::new();
    let mut seen: HashSet<String> = HashSet::from([start.clone()]);
    let mut current = start.clone();
    loop {
        let mut older: Vec<String> = older_of
            .get(&current)
            .map(|set| set.iter().cloned().collect())
            .unwrap_or_default();
        older.sort();
        let Some(next) = older.into_iter().find(|c| !seen.contains(c)) else {
            break;
        };
        back.push(next.clone());
        seen.insert(next.clone());
        current = next;
    }
    let mut forward: Vec<String> = Vec::new();
    let mut current = start.clone();
    loop {
        let mut newer: Vec<String> = newer_of
            .get(&current)
            .map(|set| set.iter().cloned().collect())
            .unwrap_or_default();
        newer.sort();
        let Some(next) = newer.into_iter().find(|c| !seen.contains(c)) else {
            break;
        };
        forward.push(next.clone());
        seen.insert(next.clone());
        current = next;
    }

    back.reverse();
    let mut chain_ids = back;
    chain_ids.push(start);
    chain_ids.extend(forward);

    let mut members = Vec::new();
    for (pos, cid) in chain_ids.iter().enumerate() {
        let member = concepts
            .get(cid)
            .cloned()
            .flatten()
            .or_else(|| bundle.read_concept(cid).ok());
        let (title, doc_status) = match &member {
            Some(c) => (
                c.title.clone(),
                c.ext_str("doc_status").unwrap_or("active").to_string(),
            ),
            None => (None, "active".to_string()),
        };
        members.push(ChainMember {
            concept_id: cid.clone(),
            title,
            doc_status,
            relation: if pos + 1 < chain_ids.len() {
                Some("superseded-by")
            } else {
                None
            },
        });
    }
    Ok(Some(members))
}

/// Bare concept ids of every parsable knowledge doc, sorted.
pub(crate) fn knowledge_doc_ids(bundle: &Bundle) -> Vec<String> {
    bundle
        .list_concepts(Some("knowledge/"))
        .into_iter()
        .filter(|cid| bundle.read_concept(cid).is_ok())
        .collect()
}

/// Deterministic disconnected doc pairs worth linking (`doc_islands`).
pub fn doc_islands(conn: &Connection, bundle: &Bundle) -> Vec<Vec<String>> {
    let ids = knowledge_doc_ids(bundle);
    let node: HashSet<&str> = ids.iter().map(String::as_str).collect();
    let mut adjacency: HashMap<&str, HashSet<&str>> =
        ids.iter().map(|id| (id.as_str(), HashSet::new())).collect();
    if let Ok(mut stmt) = conn.prepare("SELECT doc_id, related_id FROM knowledge_edges")
        && let Ok(mapped) =
            stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))
    {
        for (doc_id, related_id) in mapped.flatten() {
            if let (Some(a), Some(b)) = (node.get(doc_id.as_str()), node.get(related_id.as_str())) {
                adjacency.get_mut(*a).expect("node").insert(*b);
                adjacency.get_mut(*b).expect("node").insert(*a);
            }
        }
    }

    let mut components: Vec<Vec<String>> = Vec::new();
    let mut seen: HashSet<&str> = HashSet::new();
    for id in &ids {
        if seen.contains(id.as_str()) {
            continue;
        }
        let mut stack = vec![id.as_str()];
        let mut component: HashSet<&str> = HashSet::new();
        while let Some(current) = stack.pop() {
            if !component.insert(current) {
                continue;
            }
            stack.extend(adjacency[current].difference(&component).copied());
        }
        let mut sorted: Vec<String> = component.iter().map(|c| (*c).to_string()).collect();
        sorted.sort();
        seen.extend(component);
        components.push(sorted);
    }

    let mut units: Vec<Vec<String>> = Vec::new();
    let mut singletons: Vec<Vec<String>> = Vec::new();
    for component in components {
        match component.len() {
            2 => units.push(component),
            1 => singletons.push(component),
            _ => {}
        }
    }
    for pair in singletons.chunks(2) {
        if pair.len() == 2 {
            units.push(vec![pair[0][0].clone(), pair[1][0].clone()]);
        }
    }
    units
}

/// (task id, status, fact members) for doc-link tasks carrying members.
pub fn doc_link_tasks(bundle: &Bundle) -> Vec<(String, String, Vec<String>)> {
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
        if !concept
            .ext_str("task_kind")
            .unwrap_or("")
            .starts_with("doc-link")
        {
            continue;
        }
        let Some(Yaml::Hash(facts)) = concept.ext("facts") else {
            continue;
        };
        let Some(Yaml::Array(items)) = facts.get(&ys("members")) else {
            continue;
        };
        let members: Vec<String> = items
            .iter()
            .map(|i| str_of(Some(i)).unwrap_or_default())
            .filter(|s| !s.is_empty())
            .collect();
        if members.is_empty() {
            continue;
        }
        let status = concept
            .status
            .clone()
            .unwrap_or_else(|| concept.ext_str("status").unwrap_or("pending").to_string());
        let id = cid.rsplit('/').next().unwrap_or(&cid).to_string();
        out.push((id, status, members));
    }
    out
}

// --- OKF conformance + stale-path validation (okf/conformance.py, critic.py) ---

/// Check every .md file under the bundle root for OKF conformance.
pub fn check_bundle(root: &Path) -> Vec<String> {
    if !root.is_dir() {
        return vec![format!("Bundle root does not exist: {}", root.display())];
    }
    let bundle = Bundle::new(root);
    let mut errors = Vec::new();
    for cid in bundle.list_concepts(None) {
        let path = root.join(format!("{cid}.md"));
        let rel = format!("{cid}.md");
        match std::fs::read_to_string(&path) {
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                errors.push(format!("{rel}: File not found: {}", path.display()));
            }
            Err(e) => errors.push(format!("{rel}: Parse error in {}: {e}", path.display())),
            Ok(text) => {
                // Python list() on a scalar tags value raises inside from_file,
                // so the whole concept reports as a parse error.
                let scalar_tags_error = split_frontmatter(&text).and_then(|(fm, _)| {
                    let tags = fm.iter().find(|(k, _)| k == "tags").map(|(_, v)| v)?;
                    // Python's `tags or []` treats falsy scalars as no tags.
                    match tags {
                        Yaml::Integer(0) | Yaml::Boolean(false) => None,
                        Yaml::Integer(_) => Some("'int' object is not iterable"),
                        Yaml::Real(text) => {
                            let truthy = text.parse::<f64>().is_ok_and(|v| v != 0.0);
                            truthy.then_some("'float' object is not iterable")
                        }
                        Yaml::Boolean(true) => Some("'bool' object is not iterable"),
                        _ => None,
                    }
                });
                if let Some(err) = scalar_tags_error {
                    errors.push(format!("{rel}: Parse error in {}: {err}", path.display()));
                } else {
                    let concept = Concept::from_text(&text, cid.clone());
                    if concept.type_.is_empty() {
                        errors.push(format!("{rel}: Missing required field: type"));
                    }
                }
            }
        }
    }
    errors
}

/// One stale concept report entry (validate_paths).
#[derive(Debug, Clone)]
pub struct StaleConcept {
    pub concept_id: String,
    pub verified: f64,
}

/// Stale-entry report for backtick file/symbol refs (critic.validate_paths).
pub fn validate_paths_stale(conn: &Connection, bundle: &Bundle) -> Vec<StaleConcept> {
    let mut stale = Vec::new();
    for cid in bundle.list_concepts(None) {
        let concept = match bundle.read_concept(&cid) {
            Ok(concept) => concept,
            // Unreadable concepts cannot be verified: report, never drop.
            Err(_) => {
                stale.push(StaleConcept {
                    concept_id: cid,
                    verified: 0.0,
                });
                continue;
            }
        };
        let score = graph_verification(&concept, conn).unwrap_or(0.0);
        if score < 1.0 {
            stale.push(StaleConcept {
                concept_id: cid,
                verified: score,
            });
        }
    }
    stale
}

/// Live symbols sharing a dead symbol ref's identity anchors.
fn successor_candidates(conn: &Connection, ref_: &str, file_scope_refs: &[String]) -> Vec<String> {
    let bare = ref_.strip_suffix("()").unwrap_or(ref_);
    let prefix = bare.rsplit_once('.').map(|(p, _)| p).unwrap_or("");
    let mut clauses: Vec<String> = Vec::new();
    let mut params: Vec<String> = Vec::new();
    if !prefix.is_empty() {
        let esc = escape_like(prefix);
        clauses.push(
            "(s.qualified_name LIKE ?1 ESCAPE '\\' OR s.qualified_name LIKE ?2 ESCAPE '\\')".into(),
        );
        params.push(format!("{esc}.%"));
        params.push(format!("%.{esc}.%"));
    }
    let mut scope_clauses: Vec<String> = Vec::new();
    for file_ref in file_scope_refs {
        if !file_exists(conn, file_ref).unwrap_or(false) {
            continue;
        }
        let (p0, p1, p2, p3) = path_match_params(file_ref);
        let base = params.len();
        scope_clauses.push(format!(
            "(f.path = ?{a} OR f.path LIKE ?{b} ESCAPE '\\' \
             OR f.path LIKE ?{c} ESCAPE '\\' OR f.path LIKE ?{d} ESCAPE '\\')",
            a = base + 1,
            b = base + 2,
            c = base + 3,
            d = base + 4
        ));
        params.extend([p0, p1, p2, p3]);
        // Repo-qualified ref (`repo/src/x.py`): validate the remainder within
        // that repo, matching file_exists' bridge arm.
        if let Some((rid, rest)) = file_ref.split_once('/') {
            let (p0, p1, p2, p3) = path_match_params(rest);
            let base = params.len();
            scope_clauses.push(format!(
                "(f.repo_id = ?{a} AND (f.path = ?{b} OR f.path LIKE ?{c} ESCAPE '\\' \
                 OR f.path LIKE ?{d} ESCAPE '\\' OR f.path LIKE ?{e} ESCAPE '\\'))",
                a = base + 1,
                b = base + 2,
                c = base + 3,
                d = base + 4,
                e = base + 5
            ));
            params.push(rid.to_string());
            params.extend([p0, p1, p2, p3]);
        }
    }
    if !scope_clauses.is_empty() {
        clauses.push(format!("({})", scope_clauses.join(" OR ")));
    }
    if clauses.is_empty() {
        return Vec::new();
    }
    let sql = format!(
        "SELECT s.id, s.name, s.qualified_name FROM symbols s \
         JOIN files f ON f.id = s.file_id WHERE {}",
        clauses.join(" AND ")
    );
    let Ok(mut stmt) = conn.prepare(&sql) else {
        return Vec::new();
    };
    let refs: Vec<&dyn rusqlite::ToSql> =
        params.iter().map(|p| p as &dyn rusqlite::ToSql).collect();
    let Ok(mapped) = stmt.query_map(refs.as_slice(), |r| {
        Ok((
            r.get::<_, String>(0)?,
            r.get::<_, String>(1)?,
            r.get::<_, Option<String>>(2)?,
        ))
    }) else {
        return Vec::new();
    };
    let mut seen_ids: HashSet<String> = HashSet::new();
    let mut identities: Vec<String> = Vec::new();
    for row in mapped.flatten() {
        let (id, name, qualified) = row;
        if seen_ids.insert(id) {
            identities.push(qualified.unwrap_or(name));
        }
    }
    identities
}

/// Successor symbol for a memory body's dead symbol refs, or None.
pub fn resolve_successor(conn: &Connection, body: &str) -> Option<String> {
    let dead: Vec<String> = extract_symbol_refs(body)
        .into_iter()
        .filter(|r| !symbol_exists(conn, r).unwrap_or(false))
        .collect();
    if dead.is_empty() {
        return None;
    }
    let file_refs = extract_file_refs(body);
    let mut links: Vec<String> = Vec::new();
    for ref_ in &dead {
        let candidates = successor_candidates(conn, ref_, &file_refs);
        if candidates.len() == 1 {
            links.push(candidates[0].clone());
        }
    }
    let mut distinct: Vec<String> = Vec::new();
    for l in links {
        if !distinct.contains(&l) {
            distinct.push(l);
        }
    }
    if distinct.len() == 1 {
        distinct.into_iter().next()
    } else {
        None
    }
}

// --- memory sharing surface (memory/store.py: share/check/purge port) ------------

/// Validate that every symbol entry is a non-empty string.
fn validate_symbols(symbols: &[String]) -> Result<(), ToolError> {
    if symbols.iter().any(String::is_empty) {
        return err("symbols must be non-empty strings");
    }
    Ok(())
}

/// Record idempotent share rows; returns the inserted count.
pub fn share_memory(
    agent_id: &str,
    symbols: &[String],
    memory_id: Option<&str>,
    conn: &Connection,
) -> Result<i64, ToolError> {
    validate_agent_id(agent_id)?;
    validate_symbols(symbols)?;
    let ts = iso_utc_now();
    let mut inserted = 0i64;
    for symbol in symbols {
        let n = conn
            .execute(
                "INSERT INTO agent_symbols (agent_id, symbol, memory_id, kind, ts) \
                 SELECT ?, ?, ?, 'share', ? \
                 WHERE NOT EXISTS (\
                 SELECT 1 FROM agent_symbols \
                 WHERE agent_id = ? AND symbol = ? AND memory_id IS ? AND kind = 'share')",
                rusqlite::params![agent_id, symbol, memory_id, ts, agent_id, symbol, memory_id],
            )
            .map_err(|e| ToolError(e.to_string()))?;
        inserted += n as i64;
    }
    Ok(inserted)
}

/// One other-agent activity row (`check_overlap` row).
pub struct OverlapRow {
    pub agent_id: String,
    pub symbol: String,
    pub kind: String,
    pub ts: String,
}

/// Other agents' recent activity on the validated symbols.
pub fn check_overlap(
    agent_id: &str,
    symbols: &[String],
    conn: &Connection,
    recency_days: i64,
) -> Result<Vec<OverlapRow>, ToolError> {
    validate_agent_id(agent_id)?;
    validate_symbols(symbols)?;
    if recency_days < 1 {
        return err("recency_days must be at least 1");
    }
    if symbols.is_empty() {
        return Ok(Vec::new());
    }
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    let cutoff = format_iso_utc(now - recency_days * 86_400);
    let placeholders = vec!["?"; symbols.len()].join(",");
    let sql = format!(
        "SELECT agent_id, symbol, kind, ts FROM agent_symbols \
         WHERE agent_id <> ? AND symbol IN ({placeholders}) AND ts >= ? \
         ORDER BY symbol, ts, agent_id"
    );
    let mut stmt = conn.prepare(&sql).map_err(|e| ToolError(e.to_string()))?;
    let rows = stmt
        .query_map(
            rusqlite::params_from_iter(
                std::iter::once(agent_id)
                    .chain(symbols.iter().map(String::as_str))
                    .chain(std::iter::once(cutoff.as_str())),
            ),
            |r| {
                Ok(OverlapRow {
                    agent_id: r.get(0)?,
                    symbol: r.get(1)?,
                    kind: r.get(2)?,
                    ts: r.get(3)?,
                })
            },
        )
        .map_err(|e| ToolError(e.to_string()))?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| ToolError(e.to_string()))?;
    Ok(rows)
}

/// Delete archived memories older than max_days; returns the count purged.
pub fn purge_archived(
    bundle: &Bundle,
    max_days: i64,
    conn: Option<&Connection>,
) -> Result<usize, ToolError> {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    let _lock = bundle.lock()?;
    let mut purged = 0;
    for concept in list_memories(bundle, Some("archived"), None) {
        let Some(ts) = concept
            .timestamp
            .as_deref()
            .and_then(parse_memory_timestamp)
        else {
            continue;
        };
        let age = ((now - ts) as f64 / 86_400.0).floor() as i64;
        if age <= max_days {
            continue;
        }
        let file_path = bundle.root.join(format!("{}.md", concept.concept_id));
        if file_path.exists() {
            std::fs::remove_file(&file_path).map_err(|e| ToolError(e.to_string()))?;
            purged += 1;
            if let Some(conn) = conn {
                delete_memory_rows(bundle, &concept.concept_id, conn)?;
            }
        }
    }
    Ok(purged)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn check_bundle_surfaces_scalar_tags_and_missing_type() {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let root =
            std::env::temp_dir().join(format!("cairn-conformance-{}-{nanos}", std::process::id()));
        std::fs::create_dir_all(&root).expect("root");
        let write = |name: &str, text: &str| {
            std::fs::write(root.join(name), text).expect("write");
        };
        write(
            "int-tags.md",
            "---\ntype: Knowledge-spec\ntitle: t\ntags: 5\n---\nbody\n",
        );
        write(
            "bool-tags.md",
            "---\ntype: Knowledge-spec\ntitle: t\ntags: true\n---\nbody\n",
        );
        write(
            "str-tags.md",
            "---\ntype: Knowledge-spec\ntitle: t\ntags: abc\n---\nbody\n",
        );
        // falsy scalars fall back to [] in Python (`tags or []`) and report nothing
        write(
            "zero-tags.md",
            "---\ntype: Knowledge-spec\ntitle: t\ntags: 0\n---\nbody\n",
        );
        write(
            "fzero-tags.md",
            "---\ntype: Knowledge-spec\ntitle: t\ntags: 0.0\n---\nbody\n",
        );
        write(
            "false-tags.md",
            "---\ntype: Knowledge-spec\ntitle: t\ntags: false\n---\nbody\n",
        );
        write("no-type.md", "no frontmatter\n");

        let errors = check_bundle(&root);
        let msg = errors.join("\n");
        assert!(
            msg.contains("int-tags.md: Parse error in ")
                && msg.contains("'int' object is not iterable"),
            "scalar int tags must be a parse error: {msg}"
        );
        assert!(
            msg.contains("bool-tags.md: Parse error in ")
                && msg.contains("'bool' object is not iterable"),
            "scalar bool tags must be a parse error: {msg}"
        );
        assert!(
            !msg.contains("str-tags.md"),
            "a string tags value is fine: {msg}"
        );
        for fine in ["zero-tags.md", "fzero-tags.md", "false-tags.md"] {
            assert!(!msg.contains(fine), "falsy scalar tags are silent: {msg}");
        }
        assert!(
            msg.contains("no-type.md: Missing required field: type"),
            "{msg}"
        );
        std::fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn slugify_matches_python() {
        assert_eq!(
            slugify("golden mcp capture add probe"),
            "golden-mcp-capture-add-probe"
        );
        assert_eq!(slugify("  --Weird__Title!!--  "), "weird-title");
        assert_eq!(slugify("!!!"), "");
        assert_eq!(slugify("x").len(), 1);
    }

    #[test]
    fn frontmatter_split_and_roundtrip() {
        let text = "---\ntype: Tribal-pattern\ntitle: '2026-01-01 probe'\nmemory_score: 0.578\ntags:\n- pattern\n---\n\nBody line.\n";
        let c = Concept::from_text(text, "memory/tribal/x".into());
        assert_eq!(c.type_, "Tribal-pattern");
        assert_eq!(c.title.as_deref(), Some("2026-01-01 probe"));
        assert_eq!(c.ext_f64("memory_score"), Some(0.578));
        assert_eq!(c.tags, vec!["pattern"]);
        assert_eq!(c.body, "Body line.\n");
        let md = c.to_markdown();
        let reparsed = Concept::from_text(&md, c.concept_id.clone());
        assert_eq!(reparsed.title, c.title);
        assert_eq!(reparsed.ext_f64("memory_score"), Some(0.578));
        assert_eq!(reparsed.tags, c.tags);
    }

    #[test]
    fn iso_ts_roundtrip() {
        assert_eq!(format_iso_utc(0), "1970-01-01T00:00:00Z");
        let secs = parse_iso_ts("2026-09-16T19:53:21Z", false).expect("zulu");
        assert_eq!(format_iso_utc(secs), "2026-09-16T19:53:21Z");
        assert!(parse_iso_ts("2026-09-16T19:53:21", false).is_none());
        assert!(parse_iso_ts("2026-09-16T19:53:21", true).is_some());
        let off = parse_iso_ts("2026-09-16T21:53:21+02:00", false).expect("offset");
        assert_eq!(off, secs);
    }

    #[test]
    fn refs_extraction() {
        let body =
            "see `src/main.py` and `helper()` plus `pkg.Mod.fn` not `./gradlew` or `cairn embed`";
        assert_eq!(extract_file_refs(body), vec!["src/main.py"]);
        assert_eq!(extract_symbol_refs(body), vec!["helper()", "pkg.Mod.fn"]);
    }

    #[test]
    fn critic_excludes_fence_aware_notes_section() {
        let conn = Connection::open_in_memory().expect("mem db");
        conn.execute_batch(
            "CREATE TABLE files (id INTEGER PRIMARY KEY, repo_id TEXT, path TEXT);
             INSERT INTO files VALUES (1, 'r1', 'src/graph/queries.py');",
        )
        .expect("schema");
        let body = "# Title\n\n## Overview\nprose\n\n## Notes\nkept note\n\n\
                    ~~~sh\n# a shell comment, not a heading\n`no/such/file.py`\n~~~\n";
        let concept = Concept {
            type_: "Wiki-Article".into(),
            concept_id: "wiki/pages/r1/p".into(),
            body: body.into(),
            ..Default::default()
        };
        let result =
            critic_concept_opts(&concept, &conn, Some(&["## Sources"]), false).expect("critic");
        assert!(
            result.errors.is_empty(),
            "the Notes section (fence included) must be excluded from the critic: {:?}",
            result.errors
        );
        let (notes, rest) = split_notes(body);
        assert_eq!(
            notes,
            "## Notes\nkept note\n\n~~~sh\n# a shell comment, not a heading\n`no/such/file.py`\n~~~\n"
        );
        assert_eq!(rest, "# Title\n\n## Overview\nprose\n\n");
        assert_eq!(
            splice_notes("# Old\n\n## Notes\nold note\n", "# Fresh\n\nbody line"),
            "# Fresh\n\nbody line\n## Notes\nold note\n"
        );
        assert_eq!(
            splice_notes(
                "# Old\n\n## Notes\nold note\n",
                "# Fresh\n\nbody line\n\n## Notes\nnew note\n"
            ),
            "# Fresh\n\nbody line\n\n## Notes\nold note\n"
        );
    }

    #[test]
    fn strip_private_data_redacts_secrets() {
        let out = strip_private_data("<private>hidden</private> api_key=abcdefghijklmnopqrst uvw");
        assert!(out.contains("[REDACTED]"));
        assert!(!out.contains("abcdefghijklmnopqrst"));
    }

    #[test]
    fn failure_signature_matches_the_python_recurrence_key() {
        // Keys pinned from the last Python memory/recurrence.py capture.
        assert_eq!(
            failure_signature("Bash", "Traceback: /opt/venv/lib/site.py:1 boom pid 4242"),
            "086e3a1aee6ab843"
        );
        assert_eq!(
            failure_signature("Bash", "boom at run 12"),
            "cf4d6e110e671a9a"
        );
        assert_eq!(failure_signature("Bash", ""), "c74a3dc103bd23fe");
        assert_eq!(
            failure_signature("Bash", "ünïcödé Ωmega 42 /tmp/ä"),
            "908410bfb05e8f2b"
        );
        assert_eq!(
            failure_signature("Bash", &"x".repeat(500)),
            "770c9df9cbed9c4f"
        );
    }

    #[test]
    fn failure_signature_normalizes_case_paths_digits_uuid_hex() {
        let base = failure_signature("Bash", "Error: /Users/me/proj/x.js:42 not found");
        assert_eq!(
            base,
            failure_signature("Bash", "error: /var/lib/other/y.py:99 NOT FOUND")
        );
        assert_eq!(
            failure_signature("Bash", "boom attempt 1 of 3"),
            failure_signature("Bash", "boom attempt 27 of 3")
        );
        assert_eq!(
            failure_signature("Bash", "corrupt cache 550e8400-e29b-41d4-a716-446655440000"),
            failure_signature("Bash", "corrupt cache a1b2c3d4-e5f6-4a1b-8c2d-9e0f1a2b3c4d")
        );
        assert_eq!(
            failure_signature("Bash", "checksum deadbeefcafe mismatch"),
            failure_signature("Bash", "checksum 1234567890abcdef mismatch")
        );
        assert_ne!(
            base,
            failure_signature("Read", "/Users/me/proj/x.js:42 not found")
        );
    }

    #[test]
    fn lock_open_failure_does_not_defuse_later_locks() {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        // root is a regular file: opening root/.okf.lock always fails
        let root =
            std::env::temp_dir().join(format!("cairn-lockfail-{}-{nanos}", std::process::id()));
        std::fs::write(&root, "not a dir").expect("root file");
        let bundle = Bundle::new(&root);
        assert!(bundle.lock().is_err(), "first acquire must fail");
        // a failed acquire must not read as nested later: the second call has
        // to try the flock again (and fail again), never return a no-op guard
        assert!(bundle.lock().is_err(), "second acquire must also fail");
        std::fs::remove_file(&root).ok();
    }

    fn scratch_bundle(tag: &str) -> Bundle {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let root =
            std::env::temp_dir().join(format!("cairn-km-{tag}-{}-{nanos}", std::process::id()));
        std::fs::create_dir_all(&root).expect("root");
        Bundle::new(root)
    }

    fn write_memory(bundle: &Bundle, cid: &str, title: &str, body: &str, exts: &[(&str, Yaml)]) {
        let mut concept = Concept {
            type_: "Tribal-decision".into(),
            title: Some(title.into()),
            description: Some(title.into()),
            tags: vec!["decision".into()],
            concept_id: cid.into(),
            body: body.into(),
            ..Default::default()
        };
        for (k, v) in exts {
            set_ext(&mut concept.extensions, k, v.clone());
        }
        bundle.write_concept(&mut concept).expect("write");
    }

    #[test]
    fn reflect_store_sets_verdicts_and_is_idempotent() {
        let bundle = scratch_bundle("reflect");
        write_memory(
            &bundle,
            "memory/tribal/old",
            "old",
            "Cites `LiveSym`.\n",
            &[
                ("memory_tier", ys("tribal")),
                ("memory_superseded_by", ys("memory/tribal/new")),
            ],
        );
        write_memory(
            &bundle,
            "memory/tribal/new",
            "new",
            "Cites `LiveSym` too.\n",
            &[("memory_tier", ys("tribal"))],
        );
        write_memory(
            &bundle,
            "memory/tribal/dropped",
            "dropped",
            "Cites `DeadSym`.\n",
            &[
                ("memory_tier", ys("tribal")),
                ("memory_refs_baseline", Yaml::Real("1.0".into())),
            ],
        );
        let conn = Connection::open_in_memory().expect("mem db");
        conn.execute_batch(
            "CREATE TABLE symbols (name TEXT, qualified_name TEXT);
             CREATE TABLE files (repo_id TEXT, path TEXT);
             INSERT INTO symbols VALUES ('LiveSym', 'mod.LiveSym');",
        )
        .expect("schema");

        let summary = reflect_store(&bundle, &conn).expect("reflect");
        assert_eq!(summary.changed, 3, "all three change on the first pass");
        assert_eq!(summary.unchanged, 0);
        assert_eq!(summary.contested, 1);

        let old = bundle.read_concept("memory/tribal/old").expect("old");
        assert_eq!(old.ext_str("memory_stance"), Some("contested"));
        assert_eq!(old.ext_str("memory_stance_peer"), Some("memory/tribal/new"));
        assert_eq!(old.ext_f64("memory_refs_baseline"), Some(1.0));
        let new = bundle.read_concept("memory/tribal/new").expect("new");
        assert_eq!(new.ext_str("memory_stance"), Some("preferred"));
        let dropped = bundle
            .read_concept("memory/tribal/dropped")
            .expect("dropped");
        assert_eq!(dropped.ext_str("memory_stance"), Some("tentative"));
        assert_eq!(
            dropped.ext_f64("memory_refs_baseline"),
            Some(1.0),
            "the baseline is a high-water mark and must not fall"
        );

        let again = reflect_store(&bundle, &conn).expect("reflect again");
        assert_eq!(again.changed, 0, "reflect is idempotent");
        assert_eq!(again.unchanged, 3);
        assert_eq!(again.contested, 1);
        std::fs::remove_dir_all(&bundle.root).ok();
    }

    #[test]
    fn consolidate_merges_same_title_raws_into_tribal() {
        let bundle = scratch_bundle("consolidate");
        write_memory(
            &bundle,
            "memory/raw/one",
            "golden pair",
            "First body.\n",
            &[("memory_tier", ys("raw"))],
        );
        write_memory(
            &bundle,
            "memory/raw/two",
            "Golden Pair",
            "Second body.\n",
            &[("memory_tier", ys("raw"))],
        );
        write_memory(
            &bundle,
            "memory/raw/other",
            "unrelated",
            "Stays raw.\n",
            &[("memory_tier", ys("raw"))],
        );
        let conn = Connection::open_in_memory().expect("mem db");
        conn.execute_batch(
            "CREATE TABLE memory_validity \
             (concept_id TEXT PRIMARY KEY, valid_from TEXT, valid_until TEXT, \
              successor_symbol TEXT);
             CREATE TABLE memory_embeddings \
             (doc_id TEXT, chunk_index INTEGER, model TEXT, dim INTEGER, vec BLOB, \
              chunk TEXT, embedded_at TIMESTAMP);",
        )
        .expect("schema");

        let count = consolidate_memories(&bundle, Some(&conn)).expect("consolidate");
        assert_eq!(count, 2, "both same-title sources are archived");
        let remaining: Vec<String> = bundle
            .list_concepts(Some("memory/raw"))
            .into_iter()
            .filter(|cid| cid != "memory/raw/other")
            .collect();
        assert!(remaining.is_empty(), "sources leave memory/raw");
        let unified = bundle
            .list_concepts(Some("memory/tribal"))
            .into_iter()
            .find_map(|cid| bundle.read_concept(&cid).ok())
            .expect("unified tribal concept");
        assert_eq!(unified.title.as_deref(), Some("golden pair"));
        assert!(unified.body.contains("First body."));
        assert!(unified.body.contains("---"));
        assert!(unified.body.contains("Second body."));
        let sources = unified.ext("consolidated_from").expect("consolidated_from");
        match sources {
            Yaml::Array(items) => assert_eq!(items.len(), 2),
            other => panic!("consolidated_from must be a list: {other:?}"),
        }
        assert_eq!(
            bundle.list_concepts(Some("memory/archived")).len(),
            2,
            "both sources are archived"
        );
        std::fs::remove_dir_all(&bundle.root).ok();
    }

    fn compass_conn() -> Connection {
        let conn = Connection::open_in_memory().expect("mem db");
        conn.execute_batch(
            "CREATE TABLE repos (id TEXT, path TEXT);
             CREATE TABLE files (id INTEGER PRIMARY KEY, repo_id TEXT, path TEXT);
             CREATE TABLE symbols (id TEXT PRIMARY KEY, file_id INTEGER, name TEXT, \
              kind TEXT, qualified_name TEXT, line_start INTEGER);
             CREATE TABLE edges (id INTEGER PRIMARY KEY, source_id TEXT, target_id TEXT, kind TEXT);",
        )
        .expect("schema");
        conn
    }

    #[test]
    fn resolve_module_infers_repo_and_errors_on_ambiguity() {
        let conn = compass_conn();
        conn.execute_batch(
            "INSERT INTO repos VALUES ('ra', '/x/ra'), ('rb', '/x/rb');
             INSERT INTO files VALUES (1, 'ra', 'app/mod/a.py');
             INSERT INTO files VALUES (2, 'rb', 'app/mod/b.py');",
        )
        .expect("seed");
        let err = resolve_module(&conn, "app/mod", None).expect_err("ambiguous");
        assert!(
            err.0.contains("exists in multiple repos (ra, rb)"),
            "ambiguity error: {}",
            err.0
        );
        let (repo, module) = resolve_module(&conn, "rb/app/mod", None).expect("anchor");
        assert_eq!(repo.as_deref(), Some("rb"));
        assert_eq!(module, "app/mod");
        let err = resolve_module(&conn, "/", None).expect_err("empty");
        assert_eq!(err.0, "module path is empty");
    }

    #[test]
    fn coverage_resource_collision_classes() {
        let unique = vec!["a/x.py".to_string()];
        assert_eq!(
            coverage_resource("f", "a/x.py", &unique, &["1".to_string()], "1"),
            "f"
        );
        let distinct_base = vec!["a/x.py".to_string(), "b/y.py".to_string()];
        assert_eq!(
            coverage_resource("f", "a/x.py", &distinct_base, &["1".to_string()], "1"),
            "f#x.py"
        );
        let same_base = vec!["a/x.py".to_string(), "b/x.py".to_string()];
        assert_eq!(
            coverage_resource("f", "a/x.py", &same_base, &["1".to_string()], "1"),
            "f#a/x.py"
        );
        assert_eq!(
            coverage_resource(
                "f",
                "a/x.py",
                &same_base,
                &["1".to_string(), "2".to_string()],
                "9"
            ),
            "f#a/x.py-9"
        );
    }

    #[test]
    fn detect_gaps_matches_coverage_on_segment_boundaries() {
        let conn = compass_conn();
        let mut seed = String::from("INSERT INTO repos VALUES ('ra', '/x/ra');");
        for i in 0..5 {
            seed.push_str(&format!(
                "INSERT INTO files VALUES ({}, 'ra', 'app/foo/a{}.py');",
                i + 1,
                i
            ));
            seed.push_str(&format!(
                "INSERT INTO symbols VALUES ('s{}', {}, 'sym{}', 'function', 'sym{}', 1);",
                i,
                i + 1,
                i,
                i
            ));
        }
        for i in 0..5 {
            seed.push_str(&format!(
                "INSERT INTO files VALUES ({}, 'ra', 'app/foobar/b{}.py');",
                i + 10,
                i
            ));
            seed.push_str(&format!(
                "INSERT INTO symbols VALUES ('t{}', {}, 'tym{}', 'function', 'tym{}', 1);",
                i,
                i + 10,
                i,
                i
            ));
        }
        conn.execute_batch(&seed).expect("seed modules");
        let bundle = scratch_bundle("gaps");
        let mut concept = Concept {
            type_: "Compass".to_string(),
            title: Some("app / foo".into()),
            resource: Some("app/foo".into()),
            tags: vec!["ra".to_string()],
            concept_id: "compass/app-foo".into(),
            body: String::new(),
            ..Default::default()
        };
        bundle.write_concept(&mut concept).expect("write");

        let gaps = detect_gaps(&conn, &bundle);
        assert_eq!(
            gaps,
            vec!["ra/app/foobar".to_string()],
            "app/foo covers only its own segment: {gaps:?}"
        );
        std::fs::remove_dir_all(&bundle.root).ok();
    }

    #[test]
    fn generate_compass_builds_identity_and_kind_summary() {
        let conn = compass_conn();
        let mut seed = String::from("INSERT INTO repos VALUES ('ra', '/x/ra');");
        let mut edges = String::new();
        for i in 0..4 {
            seed.push_str(&format!(
                "INSERT INTO files VALUES ({}, 'ra', 'app/mod/a{}.py');",
                i + 1,
                i
            ));
            let kind = if i == 3 { "class" } else { "function" };
            seed.push_str(&format!(
                "INSERT INTO symbols VALUES ('s{}', {}, 'sym{}', '{}', 'sym{}', 1);",
                i,
                i + 1,
                i,
                kind,
                i
            ));
            for e in 0..(if i == 0 { 21 } else { 6 }) {
                edges.push_str(&format!(
                    "INSERT INTO edges VALUES (NULL, 'ext{}', 's{}', 'calls');",
                    e, i
                ));
            }
        }
        conn.execute_batch(&seed).expect("seed");
        conn.execute_batch(&edges).expect("edges");
        let bundle = scratch_bundle("gencompass");
        let concept = generate_compass(&conn, &bundle, "app/mod", None).expect("generate");
        assert_eq!(concept.concept_id, "compass/app-mod");
        assert_eq!(concept.title.as_deref(), Some("app / mod"));
        assert_eq!(concept.tags, vec!["ra", "app", "mod"]);
        assert!(concept.body.contains("- 4 symbols: function(3), class(1)"));
        assert!(
            concept
                .body
                .contains("- `a0.py` - ra (refs: 21; e.g. sym0)")
        );
        assert!(
            concept
                .body
                .contains("- `a0.py` is a high-traffic file (21 incoming refs); changes ripple")
        );
        assert!(concept.body.contains("# Tribal Knowledge"));
        std::fs::remove_dir_all(&bundle.root).ok();
    }

    #[test]
    fn module_match_is_segment_anchored_mid_path() {
        let conn = compass_conn();
        conn.execute_batch(
            "INSERT INTO repos VALUES ('ra', '/x/ra');
             INSERT INTO files VALUES (1, 'ra', 'src/cairn/graph/a.py');
             INSERT INTO files VALUES (2, 'ra', 'other/mod/b.py');
             INSERT INTO symbols VALUES ('s1', 1, 'sym1', 'function', 'sym1', 1);
             INSERT INTO symbols VALUES ('s2', 2, 'sym2', 'function', 'sym2', 1);",
        )
        .expect("seed");
        let (repo, module) = resolve_module(&conn, "cairn/graph", None).expect("resolve");
        assert_eq!(repo.as_deref(), Some("ra"));
        assert_eq!(module, "cairn/graph");
        let symbols = symbols_in_module(&conn, "cairn/graph", repo.as_deref()).expect("symbols");
        assert_eq!(
            symbols.iter().map(|s| s.name.as_str()).collect::<Vec<_>>(),
            vec!["sym1"],
            "the mid-path `%/{module}/%` arm must match src/cairn/graph/a.py"
        );
    }

    #[test]
    fn cross_module_deps_survives_single_segment_targets() {
        let conn = compass_conn();
        conn.execute_batch(
            "INSERT INTO repos VALUES ('ra', '/x/ra');
             INSERT INTO files VALUES (1, 'ra', 'src/app/mod/a.py');
             INSERT INTO files VALUES (2, 'ra', 'conftest.py');
             INSERT INTO symbols VALUES ('s1', 1, 'in_mod', 'function', 'in_mod', 1);
             INSERT INTO symbols VALUES ('s2', 2, 'conf_fn', 'function', 'conf_fn', 1);
             INSERT INTO edges VALUES (1, 's1', 's2', 'calls');",
        )
        .expect("seed");
        let deps = cross_module_deps(&conn, "src/app/mod", Some("ra")).expect("deps");
        assert_eq!(deps, vec!["ra/conftest.py".to_string()]);
    }
}
