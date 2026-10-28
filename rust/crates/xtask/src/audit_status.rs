//! `audit-status`: remaining-versus-total audit-finding counts from docs/audits.

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::Path;
use std::process::ExitCode;
use std::sync::LazyLock;

use clap::Parser;
use regex::Regex;
use serde_json::{Map, Value, json};

const PRIORITIES: [&str; 4] = ["P0", "P1", "P2", "P3"];

static INDEX_HEADING: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^## Findings index\b").unwrap());
static PRIORITY_HEADING: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^### (P[0-3])\b.*\((\d+)\)\s*$").unwrap());
static FINDING_ID: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^(?:C|Q|SY|S)\d+$").unwrap());
static SEPARATOR_CELL: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^:?-+:?$").unwrap());

type Findings = BTreeMap<String, Vec<String>>;

pub(crate) fn py_repr_str(s: &str) -> String {
    let quote = if s.contains('\'') && !s.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::from(quote);
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

fn py_repr(value: &Value) -> String {
    match value {
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::String(s) => py_repr_str(s),
        other => other.to_string(),
    }
}

fn sorted_value(value: &Value) -> Value {
    match value {
        Value::Object(map) => {
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            Value::Object(
                keys.into_iter()
                    .map(|k| (k.clone(), sorted_value(&map[k])))
                    .collect(),
            )
        }
        Value::Array(items) => Value::Array(items.iter().map(sorted_value).collect()),
        other => other.clone(),
    }
}

fn ascii_escape(json: &str) -> String {
    let mut out = String::with_capacity(json.len());
    for c in json.chars() {
        if c.is_ascii() {
            out.push(c);
        } else {
            let mut units = [0u16; 2];
            for unit in c.encode_utf16(&mut units) {
                out.push_str(&format!("\\u{unit:04x}"));
            }
        }
    }
    out
}

/// Mirrors Python `json.dumps(value, indent=2[, sort_keys=True])` including ASCII escaping.
pub(crate) fn py_json_pretty(value: &Value, sort_keys: bool) -> String {
    let rendered = if sort_keys {
        serde_json::to_string_pretty(&sorted_value(value))
    } else {
        serde_json::to_string_pretty(value)
    };
    ascii_escape(&rendered.unwrap_or_default())
}

fn read(path: &Path, rel: &str) -> Result<String, String> {
    fs::read_to_string(path).map_err(|_| format!("{rel}: unreadable"))
}

fn parse_index(text: &str, rel: &str) -> Result<Findings, String> {
    let lines: Vec<&str> = text.lines().collect();
    let start = lines
        .iter()
        .position(|l| INDEX_HEADING.is_match(l))
        .map(|i| i + 1)
        .ok_or_else(|| format!("{rel}: no findings-index section"))?;
    let end = (start..lines.len())
        .find(|&i| lines[i].starts_with("## "))
        .unwrap_or(lines.len());
    let mut findings = Findings::new();
    let mut declared: BTreeMap<String, usize> = BTreeMap::new();
    let mut heading_counts: BTreeMap<String, usize> = BTreeMap::new();
    let mut seen = BTreeSet::new();
    let mut current: Option<String> = None;
    for line in &lines[start..end] {
        if let Some(cap) = PRIORITY_HEADING.captures(line) {
            let priority = cap[1].to_string();
            declared.insert(priority.clone(), cap[2].parse().unwrap_or(usize::MAX));
            *heading_counts.entry(priority.clone()).or_default() += 1;
            findings.insert(priority.clone(), Vec::new());
            current = Some(priority);
        } else if line.starts_with('#') {
            current = None;
        } else if let (Some(priority), true) = (&current, line.starts_with('|')) {
            let cells: Vec<&str> = line
                .trim()
                .trim_matches('|')
                .split('|')
                .map(str::trim)
                .collect();
            if cells[0].eq_ignore_ascii_case("id") {
                continue;
            }
            if cells
                .iter()
                .filter(|c| !c.is_empty())
                .all(|c| SEPARATOR_CELL.is_match(c))
            {
                continue;
            }
            let id = cells[0];
            if !FINDING_ID.is_match(id) {
                return Err(format!(
                    "{rel}: invalid finding ID {} in {priority} index",
                    py_repr_str(id)
                ));
            }
            if !seen.insert(id.to_string()) {
                return Err(format!("{rel}: duplicate finding ID {id} in index"));
            }
            findings
                .entry(priority.clone())
                .or_default()
                .push(id.to_string());
        }
    }
    for priority in PRIORITIES {
        let count = heading_counts.get(priority).copied().unwrap_or(0);
        if count != 1 {
            return Err(format!(
                "{rel}: {priority} findings-index heading occurs {count} times"
            ));
        }
        let rows = findings[priority].len();
        if rows != declared[priority] {
            return Err(format!(
                "{rel}: {priority} declares {} findings, index has {rows}",
                declared[priority]
            ));
        }
    }
    Ok(findings)
}

fn load_sidecar(path: &Path, rel: &str, known: &BTreeSet<&String>) -> Result<Vec<String>, String> {
    let data: Value =
        serde_json::from_str(&read(path, rel)?).map_err(|_| format!("{rel}: invalid JSON"))?;
    let Some(object) = data.as_object() else {
        return Err(format!("{rel}: top level must be a JSON object"));
    };
    let version = object.get("schema_version");
    let is_one = match version {
        Some(Value::Number(n)) => n.as_f64() == Some(1.0),
        Some(Value::Bool(b)) => *b,
        _ => false,
    };
    if !is_one {
        let shown = version.map_or_else(|| "None".to_string(), py_repr);
        return Err(format!("{rel}: unsupported schema_version {shown}"));
    }
    let fixed: Option<Vec<String>> =
        object
            .get("fixed")
            .and_then(Value::as_array)
            .and_then(|items| {
                items
                    .iter()
                    .map(|v| v.as_str().map(str::to_owned))
                    .collect()
            });
    let Some(fixed) = fixed else {
        return Err(format!("{rel}: fixed must be a list of finding IDs"));
    };
    let unique: BTreeSet<&String> = fixed.iter().collect();
    if unique.len() != fixed.len() {
        return Err(format!("{rel}: duplicate fixed finding ID"));
    }
    if let Some(unknown) = unique.iter().find(|id| !known.contains(**id)) {
        return Err(format!("{rel}: unknown fixed finding ID {unknown}"));
    }
    Ok(fixed)
}

fn stems_with_suffix(dir: &Path, suffix: &str) -> BTreeMap<String, std::path::PathBuf> {
    let mut out = BTreeMap::new();
    for entry in fs::read_dir(dir).into_iter().flatten().flatten() {
        let name = entry.file_name().to_string_lossy().into_owned();
        if let Some(stem) = name.strip_suffix(suffix)
            && !stem.is_empty()
        {
            out.insert(stem.to_string(), entry.path());
        }
    }
    out
}

pub(crate) fn collect_audit_status(root: &Path) -> Result<Value, String> {
    let dir = root.join("docs").join("audits");
    let documents = stems_with_suffix(&dir, ".md");
    let sidecars = stems_with_suffix(&dir, ".status.json");
    let mut totals: BTreeMap<&str, [usize; 2]> = PRIORITIES.iter().map(|p| (*p, [0, 0])).collect();
    let mut audits = Vec::new();
    let stems: BTreeSet<&String> = documents.keys().chain(sidecars.keys()).collect();
    for stem in stems {
        let audit_rel = format!("docs/audits/{stem}.md");
        let sidecar_rel = format!("docs/audits/{stem}.status.json");
        let Some(sidecar) = sidecars.get(stem) else {
            return Err(format!("missing status sidecar: {sidecar_rel}"));
        };
        let Some(document) = documents.get(stem) else {
            return Err(format!("missing audit document: {audit_rel}"));
        };
        let findings = parse_index(&read(document, &audit_rel)?, &audit_rel)?;
        let known: BTreeMap<&String, &str> = findings
            .iter()
            .flat_map(|(p, ids)| ids.iter().map(move |id| (id, p.as_str())))
            .collect();
        let known_ids = known.keys().copied().collect();
        for id in load_sidecar(sidecar, &sidecar_rel, &known_ids)? {
            totals.get_mut(known[&id]).unwrap()[1] += 1;
        }
        for (priority, ids) in &findings {
            totals.get_mut(priority.as_str()).unwrap()[0] += ids.len();
        }
        audits.push(json!({"audit": audit_rel, "sidecar": sidecar_rel}));
    }
    let mut totals_json = Map::new();
    for priority in PRIORITIES {
        let [total, fixed] = totals[priority];
        totals_json.insert(
            priority.into(),
            json!({"total": total, "fixed": fixed, "remaining": total - fixed}),
        );
    }
    let total: usize = totals.values().map(|t| t[0]).sum();
    let fixed: usize = totals.values().map(|t| t[1]).sum();
    Ok(json!({
        "audits": audits,
        "totals": totals_json,
        "total": total,
        "remaining": total - fixed,
    }))
}

#[derive(Parser)]
#[command(
    name = "audit-status",
    about = "Report remaining audit findings by priority."
)]
struct Args {
    #[arg(long, help = "emit machine-readable JSON")]
    json: bool,
}

pub fn run(args: &[String]) -> ExitCode {
    let parsed = match Args::try_parse_from(
        std::iter::once("audit-status".to_string()).chain(args.iter().cloned()),
    ) {
        Ok(parsed) => parsed,
        Err(e) => {
            let _ = e.print();
            return ExitCode::from(e.exit_code() as u8);
        }
    };
    let root = std::env::current_dir().unwrap_or_default();
    match collect_audit_status(&root) {
        Err(message) => {
            if parsed.json {
                let error = ascii_escape(&Value::String(message).to_string());
                println!("{{\"error\": {error}}}");
            } else {
                eprintln!("audit-status: {message}");
            }
            ExitCode::FAILURE
        }
        Ok(status) => {
            if parsed.json {
                println!("{}", py_json_pretty(&status, false));
            } else {
                for priority in PRIORITIES {
                    let counts = &status["totals"][priority];
                    println!(
                        "{priority} {}/{} remaining",
                        counts["remaining"], counts["total"]
                    );
                }
                println!(
                    "total {}/{} remaining",
                    status["remaining"], status["total"]
                );
            }
            ExitCode::SUCCESS
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn index_markdown(groups: &[(&str, &[&str])]) -> String {
        let mut out = String::from("# Fixture audit\n\n## Findings index\n");
        for (priority, ids) in groups {
            out.push_str(&format!(
                "\n### {priority} - label ({})\n\n| ID | Location | Issue |\n|----|----|----|\n",
                ids.len()
            ));
            for id in *ids {
                out.push_str(&format!("| {id} | `x.py:1` | issue |\n"));
            }
        }
        out.push_str("\n## Appendix\n\n### P0 (2/2 confirmed)\n\n| C77 | CONFIRMED | skipped |\n");
        out
    }

    const GROUPS: [(&str, &[&str]); 4] = [
        ("P0", &["C77", "C1"]),
        ("P1", &["Q1"]),
        ("P2", &["S1"]),
        ("P3", &["SY1"]),
    ];

    fn make_repo(name: &str, sidecar: &str) -> std::path::PathBuf {
        let root = std::env::temp_dir().join(format!("xtask-audit-{}-{name}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        let audits = root.join("docs/audits");
        fs::create_dir_all(&audits).unwrap();
        fs::write(audits.join("2026-10-02.md"), index_markdown(&GROUPS)).unwrap();
        fs::write(audits.join("2026-10-02.status.json"), sidecar).unwrap();
        root
    }

    #[test]
    fn counts_come_from_index_and_fixed_ids_burn_down() {
        let root = make_repo("counts", r#"{"schema_version": 1, "fixed": ["C77"]}"#);
        let status = collect_audit_status(&root).unwrap();
        assert_eq!(
            status["totals"]["P0"],
            json!({"total": 2, "fixed": 1, "remaining": 1})
        );
        assert_eq!(status["total"], 5);
        assert_eq!(status["remaining"], 4);
        assert_eq!(
            status["audits"][0]["sidecar"],
            "docs/audits/2026-10-02.status.json"
        );
    }

    #[test]
    fn invalid_sidecars_name_the_sidecar() {
        for (raw, needle) in [
            (r#"{"schema_version": 2, "fixed": []}"#, "schema_version"),
            (r#"{"schema_version": 1, "fixed": {}}"#, "fixed"),
            (
                r#"{"schema_version": 1, "fixed": ["C77", "C77"]}"#,
                "duplicate",
            ),
            (r#"{"schema_version": 1, "fixed": ["NOPE1"]}"#, "unknown"),
            ("not-json{", "invalid JSON"),
        ] {
            let root = make_repo("sidecar", raw);
            let err = collect_audit_status(&root).unwrap_err();
            assert!(err.contains(needle), "{err}");
            assert!(err.contains("docs/audits/2026-10-02.status.json"), "{err}");
        }
    }

    #[test]
    fn malformed_index_names_the_audit() {
        let root = make_repo("index", r#"{"schema_version": 1, "fixed": []}"#);
        let audit = root.join("docs/audits/2026-10-02.md");
        let text = fs::read_to_string(&audit)
            .unwrap()
            .replace("label (1)", "label (2)");
        fs::write(&audit, text).unwrap();
        let err = collect_audit_status(&root).unwrap_err();
        assert!(
            err.starts_with("docs/audits/2026-10-02.md") && err.contains("P1"),
            "{err}"
        );
    }

    #[test]
    fn missing_sidecar_fails_and_no_audits_count_zero() {
        let root = make_repo("missing", "{}");
        fs::remove_file(root.join("docs/audits/2026-10-02.status.json")).unwrap();
        let err = collect_audit_status(&root).unwrap_err();
        assert_eq!(
            err,
            "missing status sidecar: docs/audits/2026-10-02.status.json"
        );
        fs::remove_file(root.join("docs/audits/2026-10-02.md")).unwrap();
        let status = collect_audit_status(&root).expect("no audits is a clean slate");
        assert_eq!(status["remaining"], 0, "{status}");
    }
}
