//! `skill-evals`: structural validation of skill eval frontmatter against the real tool surfaces.

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::sync::LazyLock;

use regex::Regex;
use yaml_rust2::{Yaml, YamlLoader};

use crate::audit_status::py_repr_str;
use crate::check_doc_links::repo_root;

const TOP_LEVEL_ENUMS: [&str; 3] = ["CoreCommand", "KnowledgeCommand", "SystemCommand"];
const REQUIRED_KEYS: [&str; 6] = [
    "id",
    "rule",
    "title",
    "scenario",
    "expected_calls",
    "wrong_calls",
];
const EXPECTED_CALL_FIELDS: [&str; 2] = ["tool", "reason"];
const WRONG_CALL_FIELDS: [&str; 2] = ["tool", "why"];

type CliCommands = BTreeMap<String, BTreeSet<String>>;

macro_rules! re {
    ($name:ident, $pat:expr) => {
        static $name: LazyLock<Regex> = LazyLock::new(|| Regex::new($pat).unwrap());
    };
}
re!(TOOL_ENTRY, r#"\b\w+_tool\(\s*\n\s*"([a-z_]+)""#);
re!(ENUM, r"(?m)^pub enum (\w+)\s*\{");
re!(VARIANT, r"^\s{4}([A-Z]\w*)\s*(\(|\{|,)");
re!(NAME_ATTR, r##"#\[command\([^\]]*name\s*=\s*"([^"]+)""##);
re!(
    SUBCOMMAND,
    r"#\[command\(subcommand\)\]\s*(?:pub\s+)?\w+:\s*(?:Option<)?(\w+)"
);
re!(MCP_REMOTE_PREFIX, r"^mcp__(?:[A-Za-z0-9_]+)__(.+)$");
re!(FRONTMATTER, r"(?s)^---\s*\n(.*?)\n---\s*(?:\n|$)");

struct Paths {
    root: PathBuf,
}

impl Paths {
    fn evals_dir(&self) -> PathBuf {
        self.root
            .join("rust/crates/cairn-cli/templates/agent_integration/skill/evals")
    }
    fn tools_file(&self) -> PathBuf {
        self.root.join("rust/crates/cairn-daemon/src/tools.rs")
    }
    fn cli_dir(&self) -> PathBuf {
        self.root.join("rust/crates/cairn-cli/src")
    }
    fn scripts_dir(&self) -> PathBuf {
        self.root.join("scripts")
    }
}

fn discover_mcp_tools(path: &Path) -> Result<BTreeSet<String>, String> {
    let text = fs::read_to_string(path).map_err(|e| format!("{}: {e}", path.display()))?;
    Ok(TOOL_ENTRY
        .captures_iter(&text)
        .map(|c| c[1].to_string())
        .collect())
}

fn brace_body(text: &str, open: usize) -> Result<&str, String> {
    let mut depth = 0;
    for (i, b) in text.bytes().enumerate().skip(open) {
        match b {
            b'{' => depth += 1,
            b'}' => {
                depth -= 1;
                if depth == 0 {
                    return Ok(&text[open + 1..i]);
                }
            }
            _ => {}
        }
    }
    Err("unbalanced braces".into())
}

fn enum_bodies(cli_dir: &Path) -> Result<BTreeMap<String, String>, String> {
    let mut files: Vec<PathBuf> = fs::read_dir(cli_dir)
        .map_err(|e| format!("{}: {e}", cli_dir.display()))?
        .filter_map(Result::ok)
        .map(|e| e.path())
        .filter(|p| {
            p.file_name()
                .and_then(|n| n.to_str())
                .is_some_and(|n| n.starts_with("cmds_") && n.ends_with(".rs"))
        })
        .collect();
    files.sort();
    let mut bodies = BTreeMap::new();
    for path in files {
        let text = fs::read_to_string(&path).map_err(|e| format!("{}: {e}", path.display()))?;
        for m in ENUM.captures_iter(&text) {
            let whole = m.get(0).unwrap();
            bodies.insert(
                m[1].to_string(),
                brace_body(&text, whole.end() - 1)?.to_string(),
            );
        }
    }
    Ok(bodies)
}

fn kebab(ident: &str) -> String {
    let mut out = String::new();
    for (i, c) in ident.chars().enumerate() {
        if i > 0 && c.is_ascii_uppercase() {
            out.push('-');
        }
        out.push(c.to_ascii_lowercase());
    }
    out
}

fn variants(body: &str) -> Result<Vec<(String, Option<String>)>, String> {
    let mut out = Vec::new();
    let mut pending: Vec<String> = Vec::new();
    let mut pos = 0;
    while pos < body.len() {
        let nl = body[pos..].find('\n').map(|i| pos + i);
        let line = &body[pos..nl.unwrap_or(body.len())];
        let stripped = line.trim();
        if stripped.starts_with("#[") {
            pending.push(stripped.to_string());
        }
        if let Some(m) = VARIANT.captures(line) {
            let explicit = pending
                .iter()
                .find_map(|a| NAME_ATTR.captures(a).map(|c| c[1].to_string()));
            let name = explicit.unwrap_or_else(|| kebab(&m[1]));
            pending.clear();
            if &m[2] == "{" {
                let brace = line.find('{').unwrap();
                let variant_body = brace_body(body, pos + brace)?;
                let sub = SUBCOMMAND.captures(variant_body).map(|c| c[1].to_string());
                pos += brace + variant_body.len() + 2;
                out.push((name, sub));
                continue;
            }
            out.push((name, None));
        } else if !stripped.starts_with("///") && !stripped.starts_with("#[") {
            pending.clear();
        }
        pos = nl.map_or(body.len(), |i| i + 1);
    }
    Ok(out)
}

fn walk(
    bodies: &BTreeMap<String, String>,
    enum_name: &str,
    bucket: &str,
    commands: &mut CliCommands,
) -> Result<(), String> {
    let body = bodies
        .get(enum_name)
        .ok_or_else(|| format!("clap enum not found: {enum_name}"))?;
    for (name, sub_enum) in variants(body)? {
        commands
            .entry(bucket.to_string())
            .or_default()
            .insert(name.clone());
        if let Some(sub) = sub_enum.filter(|s| bodies.contains_key(s)) {
            walk(bodies, &sub, &name, commands)?;
        }
    }
    Ok(())
}

fn discover_cli_commands(cli_dir: &Path) -> Result<CliCommands, String> {
    let bodies = enum_bodies(cli_dir)?;
    let mut commands = CliCommands::new();
    commands.insert("main".into(), BTreeSet::new());
    for enum_name in TOP_LEVEL_ENUMS {
        walk(&bodies, enum_name, "main", &mut commands)?;
    }
    Ok(commands)
}

fn discover_scripts(dir: &Path) -> BTreeSet<String> {
    fs::read_dir(dir)
        .into_iter()
        .flatten()
        .flatten()
        .filter(|e| e.path().is_file())
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .filter(|n| n.ends_with(".py") || n.ends_with(".sh"))
        .collect()
}

fn resolve_tool(
    tool: &str,
    mcp_tools: &BTreeSet<String>,
    cli: &CliCommands,
    scripts: &BTreeSet<String>,
    allow_unregistered_mcp: bool,
) -> (bool, String) {
    let t = tool.trim();
    if t.is_empty() {
        return (false, "empty/missing tool name".into());
    }
    if let Some(m) = MCP_REMOTE_PREFIX.captures(t) {
        let tail = &m[1];
        if mcp_tools.contains(tail) {
            return (
                true,
                format!("MCP tool (fully-qualified, registered: {tail})"),
            );
        }
        if allow_unregistered_mcp {
            return (
                true,
                format!("MCP tool name (documented-as-nonexistent: {tail})"),
            );
        }
        return (
            false,
            format!(
                "unregistered MCP tool: {} (tail {} matches no catalog tool; expected_calls must name real tools)",
                py_repr_str(t),
                py_repr_str(tail)
            ),
        );
    }
    if mcp_tools.contains(t) {
        return (true, format!("MCP tool ({t})"));
    }
    let cleaned = t
        .strip_prefix("cairn ")
        .or_else(|| t.strip_prefix("cg "))
        .unwrap_or(t)
        .trim();
    let parts: Vec<&str> = cleaned
        .split_whitespace()
        .filter(|p| !p.starts_with('-'))
        .collect();
    if let [grp, cmd] = parts[..]
        && let Some(set) = cli.get(grp)
    {
        if set.contains(cmd) {
            return (true, format!("CLI command (cairn {grp} {cmd})"));
        }
        let hyphen = format!("{grp}-{cmd}");
        if set.contains(&hyphen) {
            return (true, format!("CLI command (cairn {grp} {hyphen})"));
        }
    }
    if let [only] = parts[..]
        && cli["main"].contains(only)
    {
        return (true, format!("CLI command (cairn {only})"));
    }
    let script_name = Path::new(t)
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default();
    if scripts.contains(&script_name) {
        return (true, format!("script ({script_name})"));
    }
    (
        false,
        format!("unknown tool/command/script: {}", py_repr_str(t)),
    )
}

fn parse_frontmatter(text: &str) -> Result<Yaml, String> {
    if !text.starts_with("---") {
        return Err("file does not start with a '---' frontmatter block".into());
    }
    let Some(m) = FRONTMATTER.captures(text) else {
        return Err("frontmatter block has no closing '---' delimiter".into());
    };
    let docs = YamlLoader::load_from_str(&m[1]).map_err(|e| format!("YAML parse error: {e}"))?;
    match docs.into_iter().next() {
        Some(data @ Yaml::Hash(_)) => Ok(data),
        _ => Err("frontmatter did not parse to a YAML mapping".into()),
    }
}

fn type_name(value: &Yaml) -> &'static str {
    match value {
        Yaml::Integer(_) => "int",
        Yaml::Real(_) => "float",
        Yaml::Boolean(_) => "bool",
        Yaml::String(_) => "str",
        Yaml::Array(_) => "list",
        Yaml::Hash(_) => "dict",
        _ => "NoneType",
    }
}

fn get<'a>(map: &'a Yaml, key: &str) -> Option<&'a Yaml> {
    match &map[key] {
        Yaml::BadValue => None,
        v => Some(v),
    }
}

struct Surfaces {
    mcp_tools: BTreeSet<String>,
    cli: CliCommands,
    scripts: BTreeSet<String>,
}

fn check_call_list(
    fm: &Yaml,
    field: &str,
    required: &[&str],
    s: &Surfaces,
    problems: &mut Vec<String>,
) {
    let Some(calls) = get(fm, field) else { return };
    let items = match calls {
        Yaml::Null => {
            problems.push(format!("'{field}' is null; expected a list"));
            return;
        }
        Yaml::Array(items) => items,
        other => {
            problems.push(format!(
                "'{field}' must be a list, got {}",
                type_name(other)
            ));
            return;
        }
    };
    for (idx, entry) in items.iter().enumerate() {
        let tag = format!("{field}[{idx}]");
        if !matches!(entry, Yaml::Hash(_)) {
            problems.push(format!(
                "{tag}: entry must be a mapping, got {}",
                type_name(entry)
            ));
            continue;
        }
        for rf in required {
            match get(entry, rf) {
                None => problems.push(format!("{tag}: missing required field '{rf}'")),
                Some(Yaml::String(v)) if !v.trim().is_empty() => {}
                Some(_) => problems.push(format!("{tag}: field '{rf}' must be a non-empty string")),
            }
        }
        match get(entry, "tool") {
            None | Some(Yaml::Null) => {}
            Some(Yaml::String(tool)) => {
                let (ok, why) = resolve_tool(
                    tool,
                    &s.mcp_tools,
                    &s.cli,
                    &s.scripts,
                    field == "wrong_calls",
                );
                if !ok {
                    problems.push(format!("{tag}: {why}"));
                }
            }
            Some(_) => problems.push(format!("{tag}: empty/missing tool name")),
        }
    }
}

fn validate_spec(path: &Path, s: &Surfaces) -> Vec<String> {
    let text = fs::read(path)
        .map(|b| String::from_utf8_lossy(&b).into_owned())
        .unwrap_or_default();
    let fm = match parse_frontmatter(&text) {
        Ok(fm) => fm,
        Err(e) => return vec![format!("frontmatter: {e}")],
    };
    let mut problems = Vec::new();
    let missing: Vec<&str> = REQUIRED_KEYS
        .iter()
        .copied()
        .filter(|k| get(&fm, k).is_none())
        .collect();
    if !missing.is_empty() {
        problems.push(format!("missing required key(s): {}", missing.join(", ")));
    }
    if let Some(rule) = get(&fm, "rule")
        && !matches!(rule, Yaml::Integer(_) | Yaml::Boolean(_))
    {
        problems.push(format!(
            "'rule' must be an integer, got {}",
            type_name(rule)
        ));
    }
    for scalar in ["id", "title", "scenario"] {
        if get(&fm, scalar).is_some_and(|v| !matches!(v, Yaml::String(_))) {
            problems.push(format!("'{scalar}' must be a string"));
        }
    }
    check_call_list(
        &fm,
        "expected_calls",
        &EXPECTED_CALL_FIELDS,
        s,
        &mut problems,
    );
    check_call_list(&fm, "wrong_calls", &WRONG_CALL_FIELDS, s, &mut problems);
    let stem = path
        .file_stem()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default();
    if let Some(Yaml::String(id)) = get(&fm, "id")
        && *id != stem
    {
        problems.push(format!(
            "'id' ({}) does not match filename stem ({})",
            py_repr_str(id),
            py_repr_str(&stem)
        ));
    }
    problems
}

fn execute(paths: &Paths) -> Result<ExitCode, String> {
    let evals_dir = paths.evals_dir();
    if !evals_dir.is_dir() {
        eprintln!("ERROR: evals directory not found: {}", evals_dir.display());
        return Ok(ExitCode::from(2));
    }
    let mut specs: Vec<PathBuf> = fs::read_dir(&evals_dir)
        .map_err(|e| e.to_string())?
        .filter_map(Result::ok)
        .map(|e| e.path())
        .filter(|p| {
            p.file_name()
                .and_then(|n| n.to_str())
                .is_some_and(|n| n.starts_with("rule") && n.ends_with(".md"))
        })
        .collect();
    specs.sort();
    if specs.is_empty() {
        eprintln!("ERROR: no rule*.md specs found in {}", evals_dir.display());
        return Ok(ExitCode::from(2));
    }

    println!("Discovering registered surfaces...");
    let surfaces = Surfaces {
        mcp_tools: discover_mcp_tools(&paths.tools_file())?,
        cli: discover_cli_commands(&paths.cli_dir())?,
        scripts: discover_scripts(&paths.scripts_dir()),
    };
    let n_cli: usize = surfaces.cli.values().map(BTreeSet::len).sum();
    let tool_names: Vec<&str> = surfaces
        .mcp_tools
        .iter()
        .take(8)
        .map(String::as_str)
        .collect();
    let script_names: Vec<&str> = surfaces.scripts.iter().map(String::as_str).collect();
    println!(
        "  MCP tools : {} registered ({}{})",
        surfaces.mcp_tools.len(),
        tool_names.join(", "),
        if surfaces.mcp_tools.len() > 8 {
            "..."
        } else {
            ""
        }
    );
    println!(
        "  CLI       : {} groups, {n_cli} commands total",
        surfaces.cli.len() - 1
    );
    println!(
        "  scripts   : {} ({})",
        surfaces.scripts.len(),
        script_names.join(", ")
    );

    println!("\n=== Validating {} eval spec(s) ===", specs.len());
    let mut total_problems = 0;
    let mut failing = Vec::new();
    for spec in &specs {
        let problems = validate_spec(spec, &surfaces);
        let name = spec.file_name().unwrap().to_string_lossy();
        let (tag, status) = if problems.is_empty() {
            ("PASS", "OK".to_string())
        } else {
            ("FAIL", format!("{} problem(s)", problems.len()))
        };
        println!("  [{tag}] {name}  ({status})");
        for p in &problems {
            println!("        - {p}");
        }
        total_problems += problems.len();
        if !problems.is_empty() {
            failing.push(spec);
        }
    }

    println!("\n=== Summary ===");
    println!("  specs scanned : {}", specs.len());
    println!("  valid         : {}", specs.len() - failing.len());
    println!("  failing       : {}", failing.len());
    println!("  total problems: {total_problems}");
    if !failing.is_empty() {
        println!("\nFailing specs:");
        for spec in failing {
            println!(
                "  - {}",
                spec.strip_prefix(&paths.root).unwrap_or(spec).display()
            );
        }
        println!(
            "\nThis is a STRUCTURAL validator: it checks that each spec's frontmatter\nis well-formed and that every referenced tool/command actually exists.\nA failure usually means a tool was renamed/removed or the frontmatter is\nincomplete -- fix the spec, not the runner."
        );
        return Ok(ExitCode::FAILURE);
    }
    println!("\nAll specs structurally valid. (Agent behavior itself is still manual.)");
    Ok(ExitCode::SUCCESS)
}

pub fn run(_args: &[String]) -> ExitCode {
    match execute(&Paths { root: repo_root() }) {
        Ok(code) => code,
        Err(e) => {
            eprintln!("xtask skill-evals: {e}");
            ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn set(items: &[&str]) -> BTreeSet<String> {
        items.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn fully_qualified_mcp_tail_must_be_registered_unless_wrong_call() {
        let tools = set(&["impact_analysis", "rebuild_graph"]);
        let cli = CliCommands::new();
        let none = BTreeSet::new();
        let (ok, reason) = resolve_tool("mcp__cairn__rebuild_graph", &tools, &cli, &none, false);
        assert!(ok && reason.contains("registered"));
        let (ok, reason) = resolve_tool("mcp__cairn__rebuild_graphh", &tools, &cli, &none, false);
        assert!(!ok && reason.contains("unregistered MCP tool"));
        assert!(resolve_tool("mcp__cairn__no_such_tool", &tools, &cli, &none, true).0);
    }

    #[test]
    fn cli_flag_tokens_are_ignored_when_resolving() {
        let cli: CliCommands = [
            ("main".to_string(), set(&["update", "impact"])),
            ("dataflow".to_string(), set(&["build"])),
        ]
        .into();
        let none = BTreeSet::new();
        assert!(resolve_tool("cairn impact --guard", &BTreeSet::new(), &cli, &none, false).0);
        assert!(
            resolve_tool(
                "cairn dataflow build --json",
                &BTreeSet::new(),
                &cli,
                &none,
                false
            )
            .0
        );
        assert!(!resolve_tool("cairn nope --guard", &BTreeSet::new(), &cli, &none, false).0);
    }

    #[test]
    fn committed_skill_evals_are_structurally_valid() {
        let code = execute(&Paths { root: repo_root() }).unwrap();
        assert_eq!(format!("{code:?}"), format!("{:?}", ExitCode::SUCCESS));
    }
}
