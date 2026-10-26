//! Agent-integration installer: detection, JSON merges, and per-client installers.

use std::collections::BTreeMap;
use std::io::Write as _;
use std::path::{Path, PathBuf};

use serde_json::{Map, Value, json};

use crate::agent_templates as templates;

/// All supported clients, in display order.
pub const CLIENTS: [&str; 9] = [
    "claude",
    "claude-desktop",
    "cursor",
    "droid",
    "zcode",
    "agy",
    "opencode",
    "kilo",
    "omp",
];

/// Slash commands provided by cairn.
pub const SLASH_COMMANDS: [&str; 5] = [
    "cairn",
    "cairn-prep",
    "cairn-ship",
    "cairn-audit",
    "cairn-refresh",
];

const MARKER: &str = "cairn";
const HOOK_ENTRYPOINTS: [&str; 4] = [
    "post_edit",
    "session_end",
    "post_tool_failure",
    "session_start",
];

// --- result tracking -----------------------------------------------------------

/// Outcome of one client installer run, with written paths and verification verdict.
#[derive(Debug, Default, Clone)]
pub struct InstallResult {
    pub client: String,
    pub written: Vec<String>,
    pub skipped: Vec<String>,
    pub notes: Vec<String>,
    pub verification_status: String,
    pub verification_detail: String,
}

impl InstallResult {
    pub fn new(client: &str) -> Self {
        Self {
            client: client.to_string(),
            verification_status: "skipped".to_string(),
            ..Default::default()
        }
    }

    fn add(&mut self, path: &Path, existed: bool) {
        let rendered = path.to_string_lossy().into_owned();
        if existed {
            self.skipped.push(rendered);
        } else {
            self.written.push(rendered);
        }
    }
}

/// Detection row for one client.
pub struct Detection {
    pub client: &'static str,
    pub detected: bool,
    pub reason: String,
}

/// Detection + install state for every client.
pub struct InstallReport {
    pub detections: Vec<Detection>,
    pub results: Vec<InstallResult>,
    pub cross_tool: Option<InstallResult>,
    pub git_hooks_installed: Vec<String>,
    pub git_hooks_error: Option<String>,
}

// --- shared path / config resolution ---------------------------------------------

fn home_dir() -> PathBuf {
    cairn_store::home::platform_home().unwrap_or_else(|| PathBuf::from("/"))
}

pub(crate) fn which(name: &str) -> Option<PathBuf> {
    cairn_store::exec::which(name)
}

/// The managed daemon's MCP URL (its actual bind when discoverable, else the default).
pub fn daemon_mcp_url() -> String {
    let (port, host) = cairn_daemon::lifecycle::daemon_bind();
    cairn_daemon::lifecycle::mcp_url(
        port.unwrap_or(cairn_daemon::lifecycle::DEFAULT_PORT),
        host.as_deref()
            .unwrap_or(cairn_daemon::lifecycle::DEFAULT_HOST),
    )
}

/// Persisted-key Authorization header object, or an empty object.
pub fn auth_headers() -> Value {
    match cairn_daemon::auth::persisted_api_key() {
        Some(key) => json!({ "Authorization": format!("Bearer {key}") }),
        None => json!({}),
    }
}

fn url_entry(url_key: &str) -> Value {
    let mut entry = Map::new();
    entry.insert("type".into(), Value::String("http".into()));
    entry.insert(url_key.into(), Value::String(daemon_mcp_url()));
    let headers = auth_headers();
    if let Some(obj) = headers.as_object()
        && !obj.is_empty()
    {
        entry.insert("headers".into(), headers);
    }
    Value::Object(entry)
}

/// OpenCode/Kilo MCP entry pointing at the shared daemon URL.
pub fn opencode_format_mcp_config_json() -> Value {
    let mut entry = Map::new();
    entry.insert("type".into(), Value::String("remote".into()));
    entry.insert("url".into(), Value::String(daemon_mcp_url()));
    entry.insert("enabled".into(), Value::Bool(true));
    let headers = auth_headers();
    if let Some(obj) = headers.as_object()
        && !obj.is_empty()
    {
        entry.insert("headers".into(), headers);
    }
    json!({ "mcp": { "cairn": Value::Object(entry) } })
}

/// The shared mcpServers config: a URL+key entry for the daemon.
pub fn mcp_config_json() -> Value {
    json!({ "mcpServers": { "cairn": url_entry("url") } })
}

/// ZCode MCP entry nested under mcp.servers.<name>.
pub fn zcode_mcp_config_json() -> Value {
    json!({ "mcp": { "servers": { "cairn": url_entry("url") } } })
}

/// AGY MCP entry (serverUrl surface) pointing at the daemon URL.
pub fn agy_mcp_config_json() -> Value {
    json!({ "mcpServers": { "cairn": url_entry("serverUrl") } })
}

/// Claude Desktop's per-OS global MCP config path.
pub fn claude_desktop_config_path() -> PathBuf {
    #[cfg(target_os = "macos")]
    return home_dir()
        .join("Library")
        .join("Application Support")
        .join("Claude")
        .join("claude_desktop_config.json");
    #[cfg(windows)]
    return std::env::var("APPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(|_| home_dir())
        .join("Claude")
        .join("claude_desktop_config.json");
    #[cfg(not(any(target_os = "macos", windows)))]
    return home_dir()
        .join(".config")
        .join("Claude")
        .join("claude_desktop_config.json");
}

/// agy's per-OS global MCP config path.
pub fn agy_config_path() -> PathBuf {
    #[cfg(windows)]
    return std::env::var("APPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(|_| home_dir())
        .join("gemini")
        .join("config")
        .join("mcp_config.json");
    #[cfg(target_os = "macos")]
    return home_dir()
        .join(".gemini")
        .join("config")
        .join("mcp_config.json");
    #[cfg(not(any(target_os = "macos", windows)))]
    {
        let base = std::env::var("XDG_CONFIG_HOME")
            .ok()
            .filter(|v| !v.trim().is_empty())
            .map(PathBuf::from)
            .unwrap_or_else(|| home_dir().join(".config"));
        base.join("gemini").join("config").join("mcp_config.json")
    }
}

// --- template readers (with transformations) -----------------------------------------

fn template(rel: &str) -> String {
    templates::read(rel)
        .unwrap_or_else(|| panic!("template {rel} not vendored"))
        .to_string()
}

/// Slash-command body with Claude Code frontmatter prepended.
pub fn claude_command_md(name: &str) -> String {
    let body = template(&format!("commands/{name}.md"));
    let first_line = body
        .lines()
        .find(|ln| !ln.trim().is_empty())
        .unwrap_or(name);
    let desc = first_line
        .trim_start_matches('#')
        .trim()
        .chars()
        .take(120)
        .collect::<String>();
    format!("---\ndescription: {desc}\n---\n\n{body}")
}

fn subagent_def(template_name: &str) -> Map<String, Value> {
    let parsed: Value = serde_json::from_str(&template(template_name))
        .unwrap_or_else(|e| panic!("subagent template {template_name}: {e}"));
    parsed.as_object().cloned().expect("subagent object")
}

fn subagent_tools_line(sub: &Map<String, Value>) -> (String, bool) {
    let readonly = sub
        .get("readonly")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let mcp_tools = sub
        .get("tools")
        .and_then(Value::as_array)
        .map(|tools| {
            tools
                .iter()
                .filter_map(Value::as_str)
                .map(|t| format!("mcp__cairn__{t}"))
                .collect::<Vec<_>>()
                .join(", ")
        })
        .unwrap_or_default();
    let builtins: Vec<String> = if readonly {
        ["Read", "Grep", "Glob", "LS"]
            .into_iter()
            .map(String::from)
            .collect()
    } else {
        sub.get("extra_tools")
            .and_then(Value::as_array)
            .map(|tools| {
                tools
                    .iter()
                    .filter_map(Value::as_str)
                    .map(String::from)
                    .collect()
            })
            .unwrap_or_default()
    };
    let mut parts = builtins;
    if !mcp_tools.is_empty() {
        parts.push(mcp_tools);
    }
    (parts.join(", "), !parts.is_empty())
}

/// Cursor subagent definition rendered as Claude Code agent markdown.
pub fn claude_agent_md(template_name: &str) -> String {
    let sub = subagent_def(template_name);
    let model = sub
        .get("model")
        .and_then(Value::as_str)
        .unwrap_or("inherit");
    let (tools_line, has_tools) = subagent_tools_line(&sub);
    let mut lines = vec![
        "---".to_string(),
        format!("name: {}", scalar_str(sub.get("name"), "")),
        format!("description: {}", scalar_str(sub.get("description"), "")),
    ];
    if has_tools {
        lines.push(format!("tools: {tools_line}"));
    }
    lines.push(format!("model: {model}"));
    lines.push(r#"mcpServers: ["cairn"]"#.to_string());
    lines.push(r#"skills: ["cairn"]"#.to_string());
    lines.push("effort: low".to_string());
    if sub
        .get("is_background")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        lines.push("background: true".to_string());
    }
    lines.push("---".to_string());
    lines.push(String::new());
    lines.push(scalar_str(sub.get("prompt"), ""));
    lines.join("\n") + "\n"
}

fn scalar_str(value: Option<&Value>, default: &str) -> String {
    value
        .and_then(Value::as_str)
        .map(String::from)
        .unwrap_or_else(|| default.to_string())
}

/// Cursor subagent definition rendered as an omp task-agent file.
pub fn omp_agent_md(template_name: &str) -> String {
    let sub = subagent_def(template_name);
    let readonly = sub
        .get("readonly")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let mut tools: Vec<String> = if readonly {
        ["read", "grep", "glob"]
            .into_iter()
            .map(String::from)
            .collect()
    } else {
        sub.get("extra_tools")
            .and_then(Value::as_array)
            .map(|ts| {
                ts.iter()
                    .filter_map(Value::as_str)
                    .map(|t| t.to_lowercase())
                    .collect()
            })
            .unwrap_or_default()
    };
    if let Some(ts) = sub.get("tools").and_then(Value::as_array) {
        tools.extend(
            ts.iter()
                .filter_map(Value::as_str)
                .map(|t| format!("mcp__cairn_{t}")),
        );
    }
    let mut lines = vec![
        "---".to_string(),
        format!("name: {}", scalar_str(sub.get("name"), "")),
        format!("description: {}", scalar_str(sub.get("description"), "")),
    ];
    if !tools.is_empty() {
        lines.push(format!("tools: {}", tools.join(", ")));
    }
    lines.push("---".to_string());
    lines.push(String::new());
    lines.push(scalar_str(sub.get("prompt"), ""));
    lines.join("\n") + "\n"
}

fn transport_note() -> String {
    format!(
        "- Transport: HTTP, shared daemon at {} (one process serves every MCP \
         client; authenticated by the persisted API key).\n- Start the daemon \
         with `cairn connect`; manage it via `cairn serve start` / `stop` / \
         `status`. Do not run a second `cairn serve` against the same store.",
        daemon_mcp_url()
    )
}

fn instructions_body() -> String {
    include_str!("../templates/instructions_body.md").to_string()
}

/// CLAUDE.md instruction file content.
pub fn claude_instructions() -> String {
    format!(
        "# Codebase Intelligence (Cairn)\n\nThis workspace is connected to the \
         cairn knowledge system. Use these tools\nto understand the codebase \
         before making changes. Start with the router, drill\ndown with \
         layer-specific tools.\n\n{}\n\n{}",
        transport_note(),
        instructions_body()
    )
}

/// AGENTS.md instruction file content.
pub fn agents_instructions() -> String {
    format!(
        "# Codebase Intelligence System\n\nThis workspace uses a local \
         knowledge graph (cairn) for codebase intelligence.\nAll AI coding \
         agents working in this workspace should use these tools.\n\n## MCP \
         Server\n- Name: `cairn` (auto-connected at session start)\n{}\n- 26 \
         tools across 4 layers: graph + federation (13), knowledge base + \
         compass (5), memory (2), knowledge docs + wiki (6)\n  (`explore` is \
         the recommended first call -- it aggregates the graph layer)\n\n{}",
        transport_note(),
        instructions_body()
    )
}

// --- hooks ---------------------------------------------------------------------

/// The `cairn` an installed hook command runs: the PATH entry (stable across
/// upgrades), else this binary, else a bare name for the agent's shell.
fn cairn_for_hooks() -> String {
    if let Some(found) = which("cairn") {
        return found.to_string_lossy().into_owned();
    }
    if let Ok(exe) = std::env::current_exe() {
        if exe
            .file_name()
            .is_some_and(cairn_daemon::lifecycle::is_cairn_binary_name)
        {
            return exe.to_string_lossy().into_owned();
        }
        // Prefer the cargo bin sibling over a test-harness exe so hook
        // commands keep the `cairn hooks run` shape in any environment.
        if let Some(bin) = exe
            .parent()
            .and_then(|deps| deps.parent())
            .map(|dir| dir.join("cairn"))
            && bin.is_file()
        {
            return bin.to_string_lossy().into_owned();
        }
        return exe.to_string_lossy().into_owned();
    }
    "cairn".to_string()
}

fn shell_quote(text: &str) -> String {
    format!("'{}'", text.replace('\'', "'\\''"))
}

fn cairn_home_prefix() -> String {
    let home = crate::store_paths::current_cairn_home();
    let default = home_dir().join(".cairn");
    if home == crate::store_paths::resolve_path(&default) {
        return String::new();
    }
    format!("CAIRN_HOME={} ", shell_quote(&home.to_string_lossy()))
}

/// Quoted hook command for one entrypoint, with a non-default home prefix.
pub fn claude_hook_command(entrypoint: &str) -> String {
    format!(
        "{}{} hooks run {entrypoint}",
        cairn_home_prefix(),
        shell_quote(&cairn_for_hooks())
    )
}

/// Substrings identifying a cairn-written hook command for `entrypoint`:
/// the binary form plus the retired python-module forms.
fn hook_command_markers(entrypoint: &str) -> [String; 4] {
    [
        format!("cairn' hooks run {entrypoint}"),
        format!("cairn hooks run {entrypoint}"),
        format!("cairn.hooks.claude_hooks {entrypoint}"),
        format!("src.hooks.claude_hooks {entrypoint}"),
    ]
}

/// Shape test for a cairn-written hook command, robust to the binary's
/// platform spelling (quoted, `.exe`, absolute path, home export prefix).
fn looks_like_cairn_hook(cmd: &str) -> bool {
    cmd.contains(" hooks run ") && cmd.to_lowercase().contains("cairn")
}

fn hook_markers() -> Vec<&'static str> {
    HOOK_ENTRYPOINTS
        .iter()
        .flat_map(|ep| hook_command_markers(ep))
        .map(box_str)
        .collect()
}

fn box_str(text: String) -> &'static str {
    Box::leak(text.into_boxed_str())
}

/// Claude Code hooks block (flat hooks.<Event> shape).
pub fn claude_hooks_block() -> Value {
    let command = |ep: &str| json!({ "type": "command", "command": claude_hook_command(ep) });
    json!({
        "PostToolUse": [
            { "matcher": "Edit|Write|MultiEdit", "hooks": [command("post_edit")] },
            { "hooks": [command("post_tool_failure")] },
        ],
        "Stop": [ { "hooks": [command("session_end")] } ],
        "SessionStart": [
            { "matcher": "startup", "hooks": [command("session_start")] },
        ],
    })
}

/// Cursor hooks.json content.
pub fn cursor_hooks_json() -> Value {
    json!({
        "hooks": {
            "afterFileEdit": [
                { "command": claude_hook_command("post_edit"), "timeout": 10000 }
            ],
            "afterSessionEnd": [
                { "command": claude_hook_command("session_end"), "timeout": 60000 }
            ],
        }
    })
}

fn entry_commands(entry: &Map<String, Value>) -> Vec<String> {
    let mut commands = Vec::new();
    if let Some(inner) = entry.get("hooks").and_then(Value::as_array) {
        for hook in inner {
            if let Some(cmd) = hook.get("command").and_then(Value::as_str) {
                commands.push(cmd.to_string());
            }
        }
    }
    if let Some(cmd) = entry.get("command").and_then(Value::as_str) {
        commands.push(cmd.to_string());
    }
    commands
}

/// Hook commands in `path` that still invoke the retired python module runner.
pub(crate) fn retired_hook_commands(path: &Path) -> Vec<String> {
    let Some(data) = load_json_or_none(path) else {
        return Vec::new();
    };
    let Some(hooks) = data.get("hooks").and_then(Value::as_object) else {
        return Vec::new();
    };
    hooks
        .values()
        .filter_map(Value::as_array)
        .flatten()
        .filter_map(Value::as_object)
        .flat_map(entry_commands)
        .filter(|cmd| cmd.contains(".hooks.claude_hooks "))
        .collect()
}

fn entry_entrypoints(entry: &Map<String, Value>) -> Vec<&'static str> {
    let mut found = Vec::new();
    for cmd in entry_commands(entry) {
        for ep in HOOK_ENTRYPOINTS {
            if hook_command_markers(ep).iter().any(|m| cmd.contains(m)) {
                found.push(ep);
            }
        }
    }
    found
}

// --- file writers ------------------------------------------------------------------

fn atomic_write_text(path: &Path, content: &str) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let tmp = path.with_file_name(format!(
        ".{}.{}.tmp",
        path.file_name().unwrap_or_default().to_string_lossy(),
        std::process::id()
    ));
    {
        let mut file = std::fs::File::create(&tmp)?;
        file.write_all(content.as_bytes())?;
        file.sync_all()?;
    }
    std::fs::rename(&tmp, path)
}

fn load_json_or_none(path: &Path) -> Option<Value> {
    let text = std::fs::read_to_string(path).ok()?;
    serde_json::from_str(&text).ok()
}

fn is_script(path: &Path) -> bool {
    let in_scripts = path.components().any(|c| c.as_os_str() == "scripts");
    matches!(
        path.extension().and_then(|e| e.to_str()),
        Some("py") | Some("sh")
    ) && in_scripts
}

fn write_file(path: &Path, content: &str, force: bool, res: &mut InstallResult, dry_run: bool) {
    if path.exists() && !force {
        res.add(path, true);
        return;
    }
    if dry_run {
        res.written.push(format!("would write {}", path.display()));
        return;
    }
    if atomic_write_text(path, content).is_err() {
        return;
    }
    if is_script(path)
        && let Ok(meta) = std::fs::metadata(path)
    {
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt as _;
            let mut perms = meta.permissions();
            perms.set_mode(perms.mode() | 0o111);
            let _ = std::fs::set_permissions(path, perms);
        }
        #[cfg(not(unix))]
        let _ = meta;
    }
    res.add(path, false);
}

fn write_tree(dest_dir: &Path, force: bool, res: &mut InstallResult, dry_run: bool) {
    for (rel, content) in templates::skill_tree() {
        write_file(&dest_dir.join(rel), content, force, res, dry_run);
    }
}

pub(crate) fn copy_to_backup(path: &Path, backup: &Path) -> bool {
    std::fs::read(path)
        .ok()
        .and_then(|bytes| std::fs::write(backup, bytes).ok())
        .is_some()
}

fn backup_to_bak(path: &Path, reason: &str, res: &mut InstallResult, dry_run: bool) -> bool {
    let mut backup = path.with_file_name(format!(
        "{}.bak",
        path.file_name().unwrap_or_default().to_string_lossy()
    ));
    if backup.exists() {
        let mut n = 1;
        loop {
            backup = path.with_file_name(format!(
                "{}.bak.{n}",
                path.file_name().unwrap_or_default().to_string_lossy()
            ));
            if !backup.exists() {
                break;
            }
            n += 1;
        }
    }
    if dry_run {
        res.written.push(format!(
            "would back up {reason} {} -> {}",
            path.display(),
            backup.display()
        ));
        return true;
    }
    if copy_to_backup(path, &backup) {
        res.written.push(format!(
            "backed up {reason} {} -> {}",
            path.display(),
            backup.display()
        ));
        true
    } else {
        res.notes.push(format!(
            "install failed: refusing to overwrite {reason} config {}: could not back it up; \
             fix or remove the file and re-run. Other clients continued.",
            path.display()
        ));
        false
    }
}

fn merge_keys_non_object(existing: &Map<String, Value>, merger: &Map<String, Value>) -> bool {
    ["mcpServers", "mcp", "hooks"]
        .iter()
        .any(|key| merger.contains_key(*key) && existing.get(*key).is_some_and(|v| !v.is_object()))
}

fn merge_json_file(
    path: &Path,
    merger: &Value,
    force: bool,
    res: &mut InstallResult,
    config_key: &str,
    dry_run: bool,
) -> bool {
    let mut existing = Map::new();
    if path.exists() {
        match load_json_or_none(path) {
            None => {
                if !backup_to_bak(path, "malformed", res, dry_run) {
                    return false;
                }
            }
            Some(loaded @ Value::Object(_)) => {
                let obj = loaded.as_object().expect("object");
                if merge_keys_non_object(obj, merger.as_object().unwrap_or(obj))
                    && !backup_to_bak(path, "non-object", res, dry_run)
                {
                    return false;
                }
                existing = obj.clone();
            }
            Some(_) => {
                if !backup_to_bak(path, "non-object", res, dry_run) {
                    return false;
                }
            }
        }
    }

    let merger_obj = merger.as_object().cloned().unwrap_or_default();
    if already_installed(&existing, &merger_obj, config_key) && !force {
        res.add(path, true);
        return true;
    }

    if dry_run {
        res.written
            .push(format!("would merge into {}", path.display()));
        return true;
    }

    let merged = deep_merge(existing, merger_obj, config_key);
    let body = crate::py_json::dumps(&Value::Object(merged)) + "\n";
    if atomic_write_text(path, &body).is_ok() {
        res.add(path, false);
    }
    true
}

fn note_mcp_merge(res: &mut InstallResult, path: &Path, detail: &str) {
    let shown = path.to_string_lossy();
    if res.skipped.iter().any(|p| p == &shown) {
        res.notes.push(format!(
            "MCP server already registered in {shown}; no change."
        ));
    } else if res
        .written
        .iter()
        .any(|p| p == &format!("would merge into {shown}"))
    {
        res.notes.push(format!(
            "[dry-run] Would merge the MCP server into {shown} {detail}."
        ));
    } else {
        res.notes
            .push(format!("MCP server written to {shown} {detail}."));
    }
}

// --- merge / idempotency -------------------------------------------------------------

fn value_json_text(value: &Value) -> String {
    serde_json::to_string(value).unwrap_or_default()
}

fn headers_equal(left: Option<&Value>, right: Option<&Value>) -> bool {
    let empty = Value::Object(Map::new());
    let left = left.filter(|v| v.is_object()).unwrap_or(&empty);
    let right = right.filter(|v| v.is_object()).unwrap_or(&empty);
    value_json_text(left) == value_json_text(right)
}

fn env_equal(left: Option<&Value>, right: Option<&Value>) -> bool {
    headers_equal(left, right)
}

fn joined_command(entry: &Map<String, Value>) -> String {
    let command = entry
        .get("command")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let args: Vec<&str> = entry
        .get("args")
        .and_then(Value::as_array)
        .map(|a| a.iter().filter_map(Value::as_str).collect())
        .unwrap_or_default();
    format!("{command} {}", args.join(" "))
}

fn array_joined_command(entry: &Map<String, Value>) -> String {
    entry
        .get("command")
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .filter_map(Value::as_str)
                .collect::<Vec<_>>()
                .join(" ")
        })
        .unwrap_or_default()
}

/// True when every required cairn entry already exists and matches.
pub fn already_installed(
    existing: &Map<String, Value>,
    merger: &Map<String, Value>,
    config_key: &str,
) -> bool {
    if config_key == "zcode" && merger.contains_key("mcp") {
        let Some(new) = merger
            .get("mcp")
            .and_then(|m| m.get("servers"))
            .and_then(|s| s.get("cairn"))
            .and_then(Value::as_object)
        else {
            return false;
        };
        let Some(cur) = existing
            .get("mcp")
            .and_then(|m| m.get("servers"))
            .and_then(|s| s.get("cairn"))
            .and_then(Value::as_object)
        else {
            return false;
        };
        if !new.contains_key("command") {
            return cur.get("url") == new.get("url")
                && headers_equal(cur.get("headers"), new.get("headers"));
        }
        if joined_command(cur).trim() != joined_command(new).trim() {
            return false;
        }
        return env_equal(cur.get("env"), new.get("env"));
    }
    if (config_key == "opencode" || config_key == "kilo") && merger.contains_key("mcp") {
        let Some(new) = merger
            .get("mcp")
            .and_then(|m| m.get("cairn"))
            .and_then(Value::as_object)
        else {
            return false;
        };
        let Some(cur) = existing
            .get("mcp")
            .and_then(|m| m.get("cairn"))
            .and_then(Value::as_object)
        else {
            return false;
        };
        if new.get("type").and_then(Value::as_str) == Some("remote") {
            return cur.get("url") == new.get("url")
                && cur.get("enabled") == new.get("enabled")
                && headers_equal(cur.get("headers"), new.get("headers"))
                && env_equal(cur.get("env"), new.get("env"));
        }
        if array_joined_command(cur).trim() != array_joined_command(new).trim() {
            return false;
        }
        return env_equal(cur.get("env"), new.get("env"));
    }
    if let Some(servers) = merger.get("mcpServers").and_then(Value::as_object) {
        let Some(new) = servers.get(MARKER).and_then(Value::as_object) else {
            return false;
        };
        let Some(cur) = existing
            .get("mcpServers")
            .and_then(|s| s.get(MARKER))
            .and_then(Value::as_object)
        else {
            return false;
        };
        let url_key = ["url", "serverUrl"]
            .into_iter()
            .find(|k| new.contains_key(*k));
        if !new.contains_key("command")
            && let Some(url_key) = url_key
        {
            return cur.get(url_key) == new.get(url_key)
                && headers_equal(cur.get("headers"), new.get("headers"));
        }
        if joined_command(cur).trim() != joined_command(new).trim() {
            return false;
        }
        return env_equal(cur.get("env"), new.get("env"));
    }
    if let Some(hooks) = merger.get("hooks").and_then(Value::as_object) {
        let mut found: Vec<&str> = Vec::new();
        let mut commands: Vec<String> = Vec::new();
        if let Some(existing_hooks) = existing.get("hooks").and_then(Value::as_object) {
            for entries in existing_hooks.values() {
                let Some(entries) = entries.as_array() else {
                    continue;
                };
                for entry in entries {
                    if let Some(obj) = entry.as_object() {
                        found.extend(entry_entrypoints(obj));
                        commands.extend(entry_commands(obj));
                    }
                }
            }
        }
        let mut expected: Vec<String> = Vec::new();
        for entries in hooks.values() {
            let Some(entries) = entries.as_array() else {
                continue;
            };
            for entry in entries {
                if let Some(obj) = entry.as_object() {
                    expected.extend(entry_commands(obj));
                }
            }
        }
        if !expected.iter().all(|c| commands.contains(c)) {
            return false;
        }
        return HOOK_ENTRYPOINTS.iter().all(|ep| found.contains(ep));
    }
    false
}

fn deep_merge(
    existing: Map<String, Value>,
    addition: Map<String, Value>,
    config_key: &str,
) -> Map<String, Value> {
    let mut out = existing;
    for (key, val) in addition {
        if key == "mcpServers" && val.is_object() {
            let servers = out
                .get("mcpServers")
                .and_then(Value::as_object)
                .cloned()
                .unwrap_or_default();
            let mut servers = servers;
            servers.extend(val.as_object().expect("object").clone());
            out.insert("mcpServers".into(), Value::Object(servers));
        } else if key == "mcp" && config_key == "zcode" && val.is_object() {
            let mut mcp_out = out
                .get("mcp")
                .and_then(Value::as_object)
                .cloned()
                .unwrap_or_default();
            let mut servers = mcp_out
                .get("servers")
                .and_then(Value::as_object)
                .cloned()
                .unwrap_or_default();
            servers.extend(
                val.as_object()
                    .expect("object")
                    .get("servers")
                    .and_then(Value::as_object)
                    .cloned()
                    .unwrap_or_default(),
            );
            mcp_out.insert("servers".into(), Value::Object(servers));
            out.insert("mcp".into(), Value::Object(mcp_out));
            // Drop only a stale cairn-stamped mcpServers entry; keep user servers.
            if let Some(prev_top) = out.get("mcpServers").and_then(Value::as_object).cloned()
                && prev_top.contains_key(MARKER)
            {
                let remaining: Map<String, Value> =
                    prev_top.into_iter().filter(|(k, _)| k != MARKER).collect();
                if remaining.is_empty() {
                    out.remove("mcpServers");
                } else {
                    out.insert("mcpServers".into(), Value::Object(remaining));
                }
            }
        } else if key == "mcp"
            && (config_key == "opencode" || config_key == "kilo")
            && val.is_object()
        {
            let mut mcp_out = out
                .get("mcp")
                .and_then(Value::as_object)
                .cloned()
                .unwrap_or_default();
            mcp_out.extend(val.as_object().expect("object").clone());
            out.insert("mcp".into(), Value::Object(mcp_out));
        } else if key == "hooks" && val.is_object() {
            let mut out_hooks = out
                .get("hooks")
                .and_then(Value::as_object)
                .cloned()
                .unwrap_or_default();
            for (event, entries) in val.as_object().expect("object") {
                let Some(entries) = entries.as_array() else {
                    continue;
                };
                let mut cur = out_hooks
                    .get(event.as_str())
                    .and_then(Value::as_array)
                    .cloned()
                    .unwrap_or_default();
                cur.retain(|e| {
                    e.as_object()
                        .map(|obj| entry_entrypoints(obj).is_empty())
                        .unwrap_or(true)
                });
                cur.extend(entries.clone());
                out_hooks.insert(event.clone(), Value::Array(cur));
            }
            out.insert("hooks".into(), Value::Object(out_hooks));
        } else if val.is_object() && out.get(&key).is_some_and(Value::is_object) {
            let merged = deep_merge(
                out.get(&key)
                    .and_then(Value::as_object)
                    .cloned()
                    .unwrap_or_default(),
                val.as_object().expect("object").clone(),
                config_key,
            );
            out.insert(key, Value::Object(merged));
        } else {
            out.insert(key, val);
        }
    }
    out
}

// --- uninstall strip helpers ----------------------------------------------------------

fn rm_if_exists(path: &Path, res: &mut InstallResult) {
    if path.exists() && std::fs::remove_file(path).is_ok() {
        res.written.push(format!("removed {}", path.display()));
    }
}

fn rmdir_if_empty(path: &Path, res: &mut InstallResult) {
    if path.is_dir() && std::fs::remove_dir(path).is_ok() {
        res.written
            .push(format!("removed empty dir {}", path.display()));
    }
}

fn rm_tree_if_cairn(path: &Path, res: &mut InstallResult) {
    if !path.is_dir() {
        return;
    }
    if path.file_name().unwrap_or_default() != MARKER {
        res.notes.push(format!(
            "refused to remove {}/: not cairn-scoped (directory must be named \
             'cairn'). Remove manually if intended.",
            path.display()
        ));
        return;
    }
    let mut kept = 0usize;
    let mut files: Vec<PathBuf> = walk_files(path);
    files.sort();
    for file in files {
        let is_cache = matches!(
            file.extension().and_then(|e| e.to_str()),
            Some("pyc") | Some("pyo")
        ) || file
            .parent()
            .is_some_and(|p| p.file_name() == Some(std::ffi::OsStr::new("__pycache__")));
        if is_cache {
            let _ = std::fs::remove_file(&file);
            continue;
        }
        let rel = file.strip_prefix(path).unwrap_or(&file);
        let rel = rel.to_string_lossy().replace('\\', "/");
        let ours = templates::read(&format!("skill/{rel}"))
            .map(|expected| std::fs::read(&file).is_ok_and(|bytes| bytes == expected.as_bytes()))
            .unwrap_or(false);
        if ours {
            let _ = std::fs::remove_file(&file);
        } else {
            kept += 1;
        }
    }
    let mut dirs: Vec<PathBuf> = walk_dirs(path);
    dirs.sort_by_key(|p| std::cmp::Reverse(p.components().count()));
    for dir in dirs {
        rmdir_if_empty(&dir, res);
    }
    if std::fs::remove_dir(path).is_ok() {
        res.written.push(format!("removed {}/", path.display()));
    } else {
        res.notes.push(format!(
            "kept {kept} modified/non-template file(s) in {}/; remove manually if intended.",
            path.display()
        ));
    }
}

fn walk_files(root: &Path) -> Vec<PathBuf> {
    let mut out = Vec::new();
    let Ok(entries) = std::fs::read_dir(root) else {
        return out;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            out.extend(walk_files(&path));
        } else {
            out.push(path);
        }
    }
    out
}

fn walk_dirs(root: &Path) -> Vec<PathBuf> {
    let mut out = Vec::new();
    let Ok(entries) = std::fs::read_dir(root) else {
        return out;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            out.push(path.clone());
            out.extend(walk_dirs(&path));
        }
    }
    out
}

fn rm_if_ours(path: &Path, expected: &str, res: &mut InstallResult) {
    if !path.exists() {
        return;
    }
    let ours = std::fs::read_to_string(path).is_ok_and(|content| content == expected);
    if !ours {
        res.skipped.push(format!(
            "{} (differs from the current cairn template; left in place)",
            path.display()
        ));
        return;
    }
    if std::fs::remove_file(path).is_ok() {
        res.written.push(format!("removed {}", path.display()));
    }
}

fn write_json(path: &Path, data: &Value) {
    let body = crate::py_json::dumps(data) + "\n";
    let _ = atomic_write_text(path, &body);
}

fn strip_mcp(path: &Path, res: &mut InstallResult) {
    let Some(data) = load_json_or_none(path).filter(Value::is_object) else {
        return;
    };
    let mut data = data;
    let Some(servers) = data.get_mut("mcpServers").and_then(Value::as_object_mut) else {
        return;
    };
    if servers.remove(MARKER).is_none() {
        return;
    }
    write_json(path, &data);
    res.written
        .push(format!("stripped cairn from {}", path.display()));
}

fn strip_mcp_zcode(path: &Path, res: &mut InstallResult, dry_run: bool) {
    let Some(data) = load_json_or_none(path).filter(Value::is_object) else {
        return;
    };
    let mut data = data.as_object().expect("object").clone();
    let Some(mcp) = data.get_mut("mcp").and_then(Value::as_object_mut) else {
        return;
    };
    let Some(servers) = mcp.get_mut("servers").and_then(Value::as_object_mut) else {
        return;
    };
    if servers.remove(MARKER).is_none() {
        return;
    }
    if servers.is_empty() {
        mcp.remove("servers");
    }
    if mcp.is_empty() {
        data.remove("mcp");
    }
    if dry_run {
        res.written
            .push(format!("would strip cairn from {}", path.display()));
        return;
    }
    write_json(path, &Value::Object(data));
    res.written
        .push(format!("stripped cairn from {}", path.display()));
}

fn strip_mcp_kilo(path: &Path, res: &mut InstallResult) {
    let Some(data) = load_json_or_none(path).filter(Value::is_object) else {
        return;
    };
    let mut data = data.as_object().expect("object").clone();
    let Some(mcp) = data.get_mut("mcp").and_then(Value::as_object_mut) else {
        return;
    };
    if mcp.remove(MARKER).is_none() {
        return;
    }
    if mcp.is_empty() {
        data.remove("mcp");
    }
    write_json(path, &Value::Object(data));
    res.written
        .push(format!("stripped cairn from {}", path.display()));
}

fn strip_mcp_opencode(path: &Path, res: &mut InstallResult) {
    strip_mcp_kilo(path, res);
    // Cleanup: strip the cairn server from a stray .opencode/mcp.json, deleting
    // it only when no other servers or keys remain.
    let stray = path
        .parent()
        .unwrap_or(Path::new("."))
        .join(".opencode")
        .join("mcp.json");
    let Some(data) = load_json_or_none(&stray).filter(Value::is_object) else {
        return;
    };
    let mut data = data.as_object().expect("object").clone();
    let Some(servers) = data.get_mut("mcpServers").and_then(Value::as_object_mut) else {
        return;
    };
    if servers.remove(MARKER).is_none() {
        return;
    }
    if servers.is_empty() {
        data.remove("mcpServers");
    }
    if !data.is_empty() {
        write_json(&stray, &Value::Object(data));
        res.written
            .push(format!("stripped cairn from {}", stray.display()));
    } else {
        let _ = std::fs::remove_file(&stray);
        res.written
            .push(format!("removed stray {}", stray.display()));
    }
}

fn strip_hooks_matching(path: &Path, res: &mut InstallResult, cursor_shape: bool) {
    let Some(data) = load_json_or_none(path).filter(Value::is_object) else {
        return;
    };
    let mut data = data.as_object().expect("object").clone();
    let Some(hooks) = data.get_mut("hooks").and_then(Value::as_object_mut) else {
        return;
    };
    let markers = hook_markers();
    let mut changed = false;
    let keys: Vec<String> = hooks.keys().cloned().collect();
    for event in keys {
        let Some(entries) = hooks.get(&event).and_then(Value::as_array).cloned() else {
            continue;
        };
        let mut kept = Vec::new();
        for entry in entries {
            let matches = entry.as_object().is_some_and(|obj| {
                if cursor_shape {
                    entry_commands(obj).iter().any(|cmd| {
                        looks_like_cairn_hook(cmd) || markers.iter().any(|m| cmd.contains(m))
                    })
                } else {
                    // Claude shape: cairn entries nest their commands under "hooks".
                    obj.get("hooks")
                        .and_then(Value::as_array)
                        .map(|inner| {
                            inner.iter().any(|h| {
                                h.as_object()
                                    .and_then(|hobj| hobj.get("command"))
                                    .and_then(Value::as_str)
                                    .map(|cmd| {
                                        looks_like_cairn_hook(cmd)
                                            || markers.iter().any(|m| cmd.contains(m))
                                    })
                                    .unwrap_or(false)
                            })
                        })
                        .unwrap_or(false)
                }
            });
            if matches {
                changed = true;
            } else {
                kept.push(entry);
            }
        }
        if kept.is_empty() {
            hooks.remove(&event);
            changed = true;
        } else {
            hooks.insert(event, Value::Array(kept));
        }
    }
    if !changed {
        return;
    }
    if data
        .get("hooks")
        .and_then(Value::as_object)
        .is_some_and(Map::is_empty)
    {
        data.remove("hooks");
    }
    write_json(path, &Value::Object(data));
    res.written
        .push(format!("stripped cairn hooks from {}", path.display()));
}

fn strip_hooks(path: &Path, res: &mut InstallResult) {
    strip_hooks_matching(path, res, false);
}

fn strip_cursor_hooks(path: &Path, res: &mut InstallResult) {
    strip_hooks_matching(path, res, true);
}

// --- detection ---------------------------------------------------------------------

/// Detect which AI coding clients are present for this workspace.
pub fn detect_clients(workspace: &Path) -> Vec<Detection> {
    let home = home_dir();
    let claude_cli = which("claude").is_some();
    let claude_ws = workspace.join(".claude").is_dir();
    let cursor = cursor_detection(workspace);
    let droid = droid_detection(workspace);
    let zcode = zcode_detection(&home);
    let desktop = claude_desktop_detection();
    let agy = agy_detection(&home);
    let opencode = opencode_detection(workspace, &home);
    let kilo = kilo_detection(workspace, &home);
    let omp = omp_detection(workspace, &home);
    let claude = Detection {
        client: "claude",
        detected: claude_cli || home.join(".claude").is_dir() || claude_ws,
        reason: if claude_cli {
            "claude CLI on PATH".into()
        } else if home.join(".claude").is_dir() {
            "~/.claude exists".into()
        } else if claude_ws {
            ".claude/ in workspace".into()
        } else {
            "not found".into()
        },
    };
    vec![
        claude, cursor, droid, zcode, desktop, agy, opencode, kilo, omp,
    ]
}

fn claude_desktop_detection() -> Detection {
    let cfg = claude_desktop_config_path();
    let file = cfg.exists();
    let dir = cfg.parent().is_some_and(Path::is_dir);
    Detection {
        client: "claude-desktop",
        detected: file || dir,
        reason: if file {
            "config file exists".into()
        } else if dir {
            format!(
                "{}/ app dir exists",
                cfg.parent()
                    .unwrap_or(Path::new(""))
                    .file_name()
                    .unwrap_or_default()
                    .to_string_lossy()
            )
        } else {
            "not found".into()
        },
    }
}

fn cursor_detection(workspace: &Path) -> Detection {
    let cli = which("cursor").is_some();
    let app = Path::new("/Applications/Cursor.app").exists();
    Detection {
        client: "cursor",
        detected: cli || app || workspace.join(".cursor").is_dir(),
        reason: if cli {
            "cursor CLI on PATH".into()
        } else if app {
            "Cursor.app in /Applications".into()
        } else if workspace.join(".cursor").is_dir() {
            ".cursor/ in workspace".into()
        } else {
            "not found".into()
        },
    }
}

fn droid_detection(workspace: &Path) -> Detection {
    let cli = which("droid").is_some();
    Detection {
        client: "droid",
        detected: cli || workspace.join(".factory").is_dir(),
        reason: if cli {
            "droid CLI on PATH".into()
        } else if workspace.join(".factory").is_dir() {
            ".factory/ in workspace".into()
        } else {
            "not found".into()
        },
    }
}

fn zcode_detection(home: &Path) -> Detection {
    let found = home.join(".zcode").is_dir();
    Detection {
        client: "zcode",
        detected: found,
        reason: if found {
            "~/.zcode exists".into()
        } else {
            "not found".into()
        },
    }
}

fn agy_detection(home: &Path) -> Detection {
    let cli = which("agy").is_some();
    let dir = home.join(".gemini").join("antigravity-cli").is_dir();
    Detection {
        client: "agy",
        detected: cli || dir,
        reason: if cli {
            "agy CLI on PATH".into()
        } else if dir {
            "~/.gemini/antigravity-cli exists".into()
        } else {
            "not found".into()
        },
    }
}

fn opencode_detection(workspace: &Path, home: &Path) -> Detection {
    let cli = which("opencode").is_some();
    let ws = workspace.join(".opencode").is_dir();
    let global = home.join(".config").join("opencode").is_dir();
    Detection {
        client: "opencode",
        detected: cli || ws || global,
        reason: if cli {
            "opencode CLI on PATH".into()
        } else if ws {
            ".opencode/ in workspace".into()
        } else if global {
            "~/.config/opencode exists".into()
        } else {
            "not found".into()
        },
    }
}

fn kilo_detection(workspace: &Path, home: &Path) -> Detection {
    let cli = which("kilo").is_some();
    let ws_file = workspace.join("kilo.json").exists();
    let ws_dir = workspace.join(".kilo").is_dir();
    let global = home.join(".config").join("kilo").is_dir();
    Detection {
        client: "kilo",
        detected: cli || ws_file || ws_dir || global,
        reason: if cli {
            "kilo CLI on PATH".into()
        } else if ws_file {
            "kilo.json in workspace".into()
        } else if ws_dir {
            ".kilo/ in workspace".into()
        } else if global {
            "~/.config/kilo exists".into()
        } else {
            "not found".into()
        },
    }
}

fn omp_detection(workspace: &Path, home: &Path) -> Detection {
    let cli = which("omp").is_some();
    let ws = workspace.join(".omp").is_dir();
    let global = home.join(".omp").is_dir();
    Detection {
        client: "omp",
        detected: cli || ws || global,
        reason: if cli {
            "omp CLI on PATH".into()
        } else if ws {
            ".omp/ in workspace".into()
        } else if global {
            "~/.omp exists".into()
        } else {
            "not found".into()
        },
    }
}

/// True if a JSON file has a 'cairn' key in any supported MCP shape.
fn json_has_cairn(path: &Path) -> bool {
    let Some(data) = load_json_or_none(path).filter(Value::is_object) else {
        return false;
    };
    let obj = data.as_object().expect("object");
    if obj
        .get("mcpServers")
        .and_then(Value::as_object)
        .is_some_and(|s| s.contains_key(MARKER))
    {
        return true;
    }
    if let Some(cairn) = obj.get(MARKER).and_then(Value::as_object)
        && ["url", "serverUrl", "command"]
            .iter()
            .any(|k| cairn.contains_key(*k))
    {
        return true;
    }
    if let Some(mcp) = obj.get("mcp").and_then(Value::as_object) {
        if mcp
            .get("servers")
            .and_then(Value::as_object)
            .is_some_and(|s| s.contains_key(MARKER))
        {
            return true;
        }
        if mcp.contains_key(MARKER) {
            return true;
        }
    }
    false
}

fn hooks_have_cairn(path: &Path) -> bool {
    let Some(data) = load_json_or_none(path).filter(Value::is_object) else {
        return false;
    };
    let Some(hooks) = data.get("hooks").and_then(Value::as_object) else {
        return false;
    };
    hooks.values().any(|entries| {
        entries
            .as_array()
            .map(|list| {
                list.iter()
                    .filter_map(Value::as_object)
                    .any(|obj| !entry_entrypoints(obj).is_empty())
            })
            .unwrap_or(false)
    })
}

/// Each client's current cairn installation state, keyed in CLIENTS order.
pub fn check_installed(workspace: &Path) -> BTreeMap<String, bool> {
    let ws = workspace;
    let home = home_dir();
    let mut result = BTreeMap::new();
    result.insert(
        "claude".into(),
        json_has_cairn(&ws.join(".mcp.json"))
            || ws
                .join(".claude")
                .join("skills")
                .join("cairn")
                .join("SKILL.md")
                .exists()
            || hooks_have_cairn(&home.join(".claude").join("settings.json"))
            || json_has_cairn(&home.join(".claude.json")),
    );
    result.insert(
        "claude-desktop".into(),
        json_has_cairn(&claude_desktop_config_path()),
    );
    result.insert(
        "cursor".into(),
        json_has_cairn(&ws.join(".cursor").join("mcp.json"))
            || ws.join(".cursor").join("rules").join("cairn.mdc").exists()
            || json_has_cairn(&home.join(".cursor").join("mcp.json")),
    );
    result.insert(
        "droid".into(),
        ws.join(".factory")
            .join("skills")
            .join("cairn")
            .join("SKILL.md")
            .exists()
            || json_has_cairn(&ws.join(".factory").join("mcp.json"))
            || json_has_cairn(&home.join(".factory").join("mcp.json")),
    );
    result.insert(
        "zcode".into(),
        json_has_cairn(&ws.join(".zcode").join("config.json"))
            || json_has_cairn(&home.join(".zcode").join("config.json"))
            || json_has_cairn(&home.join(".zcode").join("cli").join("config.json")),
    );
    result.insert("agy".into(), json_has_cairn(&agy_config_path()));
    result.insert(
        "opencode".into(),
        json_has_cairn(&ws.join("opencode.json"))
            || json_has_cairn(&home.join(".config").join("opencode").join("opencode.json")),
    );
    result.insert(
        "kilo".into(),
        json_has_cairn(&ws.join("kilo.json"))
            || json_has_cairn(&home.join(".config").join("kilo").join("kilo.json")),
    );
    result.insert(
        "omp".into(),
        json_has_cairn(&ws.join(".omp").join("mcp.json"))
            || json_has_cairn(&home.join(".omp").join("agent").join("mcp.json")),
    );
    result
}

// --- registration entry readers ------------------------------------------------------

/// The cairn registration entry from any supported client config shape.
pub fn registration_entry(config_path: &Path) -> Option<Value> {
    if !config_path.to_string_lossy().ends_with(".json") {
        return None;
    }
    let data = load_json_or_none(config_path).filter(Value::is_object)?;
    let obj = data.as_object()?;
    let mut candidates: Vec<Value> = Vec::new();
    if let Some(servers) = obj.get("mcpServers").and_then(Value::as_object)
        && let Some(entry) = servers.get(MARKER)
    {
        candidates.push(entry.clone());
    }
    if let Some(mcp) = obj.get("mcp").and_then(Value::as_object) {
        if let Some(nested) = mcp.get("servers").and_then(Value::as_object)
            && let Some(entry) = nested.get(MARKER)
        {
            candidates.push(entry.clone());
        }
        if let Some(entry) = mcp.get(MARKER) {
            candidates.push(entry.clone());
        }
    }
    if let Some(entry) = obj.get(MARKER) {
        candidates.push(entry.clone());
    }
    candidates.into_iter().find(Value::is_object)
}

/// The daemon URL of a URL-based registration (`url` / agy's `serverUrl`).
pub fn registration_url(entry: &Value) -> Option<String> {
    for key in ["url", "serverUrl"] {
        if let Some(value) = entry.get(key).and_then(Value::as_str)
            && !value.is_empty()
        {
            return Some(value.to_string());
        }
    }
    None
}

// --- per-client installers ------------------------------------------------------------

fn uninstall_bases(ws: &Path, scope: &str) -> Vec<PathBuf> {
    match scope {
        "global" => vec![home_dir()],
        "all" => vec![ws.to_path_buf(), home_dir()],
        _ => vec![ws.to_path_buf()],
    }
}

fn install_claude(workspace: &Path, force: bool, dry_run: bool, scope: &str) -> InstallResult {
    let mut res = InstallResult::new("claude");
    let base = if scope == "global" {
        home_dir()
    } else {
        workspace.to_path_buf()
    };

    if scope == "workspace" {
        if !merge_json_file(
            &base.join(".mcp.json"),
            &mcp_config_json(),
            force,
            &mut res,
            "mcpServers",
            dry_run,
        ) {
            return res;
        }
    } else if dry_run {
        res.notes.push(
            "[dry-run] Would register MCP globally via `claude mcp add --scope user`.".into(),
        );
    } else if which("claude").is_some() {
        let mut argv = vec![
            "claude".to_string(),
            "mcp".to_string(),
            "add".to_string(),
            "--transport".to_string(),
            "http".to_string(),
            "--scope".to_string(),
            "user".to_string(),
            "cairn".to_string(),
            daemon_mcp_url(),
        ];
        if let Some(headers) = auth_headers().as_object() {
            for (name, value) in headers {
                if let Some(value) = value.as_str() {
                    argv.push("--header".into());
                    argv.push(format!("{name}: {value}"));
                }
            }
        }
        match run_client_command(&argv, 15) {
            Ok(output) if output.success => {
                res.notes
                    .push("Registered MCP globally via `claude mcp add --scope user`.".into());
            }
            Ok(output) => res.notes.push(format!(
                "WARNING: `claude mcp add --scope user` exited {}: {}; MCP not \
                 registered globally. Re-run with the claude CLI on PATH or \
                 install per-workspace (scope=workspace).",
                output.code,
                truncate_200(&output.message)
            )),
            Err(e) => res.notes.push(format!(
                "WARNING: `claude mcp add --scope user` failed ({e}); MCP not \
                 registered globally. Re-run with the claude CLI on PATH or \
                 install per-workspace (scope=workspace)."
            )),
        }
    } else {
        res.notes.push(
            "WARNING: global MCP requires the `claude` CLI on PATH (`claude mcp \
             add --scope user`); it was not found, so MCP was NOT registered. \
             Claude Code does not read a global ~/.mcp.json. Re-run with claude \
             on PATH or use scope=workspace."
                .into(),
        );
    }

    write_tree(
        &base.join(".claude").join("skills").join("cairn"),
        force,
        &mut res,
        dry_run,
    );
    for name in SLASH_COMMANDS {
        write_file(
            &base
                .join(".claude")
                .join("commands")
                .join(format!("{name}.md")),
            &claude_command_md(name),
            force,
            &mut res,
            dry_run,
        );
    }
    write_file(
        &base
            .join(".claude")
            .join("agents")
            .join("cairn-explorer.md"),
        &claude_agent_md("cursor/cairn-explorer.json"),
        force,
        &mut res,
        dry_run,
    );
    write_file(
        &base
            .join(".claude")
            .join("agents")
            .join("knowledge-steward.md"),
        &claude_agent_md("cursor/knowledge-steward.json"),
        force,
        &mut res,
        dry_run,
    );
    if !merge_json_file(
        &base.join(".claude").join("settings.json"),
        &json!({ "hooks": claude_hooks_block() }),
        force,
        &mut res,
        "mcpServers",
        dry_run,
    ) {
        return res;
    }

    if scope == "workspace" {
        let claude_md = workspace.join("CLAUDE.md");
        if !claude_md.exists() {
            write_file(&claude_md, &claude_instructions(), false, &mut res, dry_run);
        } else {
            res.skipped
                .push(format!("{} (exists; not overwritten)", claude_md.display()));
        }
    }
    res
}

struct CommandOutput {
    success: bool,
    code: i32,
    message: String,
}

fn run_client_command(argv: &[String], timeout_secs: u64) -> Result<CommandOutput, String> {
    let mut command = std::process::Command::new(&argv[0]);
    command.args(&argv[1..]);
    let output = spawn_with_timeout(
        &mut command,
        std::time::Duration::from_secs(timeout_secs),
        None,
    )?;
    let message = if !output.stderr.trim().is_empty() {
        output.stderr.clone()
    } else {
        output.stdout.clone()
    };
    Ok(CommandOutput {
        success: output.code == Some(0),
        code: output.code.unwrap_or(-1),
        message,
    })
}

pub(crate) struct TimeoutOutput {
    pub(crate) code: Option<i32>,
    pub(crate) stdout: String,
    pub(crate) stderr: String,
}

/// Run `command` to completion within `timeout` (killed past it), feeding
/// `stdin` when given; stdin, stdout and stderr are each served by their own
/// thread so a chatty child never deadlocks on a full pipe.
pub(crate) fn spawn_with_timeout(
    command: &mut std::process::Command,
    timeout: std::time::Duration,
    stdin: Option<&str>,
) -> Result<TimeoutOutput, String> {
    use std::io::{Read, Write};
    use std::process::Stdio;
    command.stdout(Stdio::piped()).stderr(Stdio::piped());
    command.stdin(if stdin.is_some() {
        Stdio::piped()
    } else {
        Stdio::null()
    });
    let mut child = command.spawn().map_err(|e| e.to_string())?;
    let feed = stdin.and_then(|input| {
        let input = input.to_owned();
        child.stdin.take().map(|mut pipe| {
            std::thread::spawn(move || {
                let _ = pipe.write_all(input.as_bytes());
            })
        })
    });
    let drain = |pipe: Option<Box<dyn Read + Send>>| {
        std::thread::spawn(move || {
            let mut buf = Vec::new();
            if let Some(mut pipe) = pipe {
                let _ = pipe.read_to_end(&mut buf);
            }
            String::from_utf8_lossy(&buf).into_owned()
        })
    };
    let stdout = drain(
        child
            .stdout
            .take()
            .map(|p| Box::new(p) as Box<dyn Read + Send>),
    );
    let stderr = drain(
        child
            .stderr
            .take()
            .map(|p| Box::new(p) as Box<dyn Read + Send>),
    );
    let deadline = std::time::Instant::now() + timeout;
    loop {
        match child.try_wait() {
            Ok(Some(status)) => {
                if let Some(feed) = feed {
                    let _ = feed.join();
                }
                return Ok(TimeoutOutput {
                    code: status.code(),
                    stdout: stdout.join().unwrap_or_default(),
                    stderr: stderr.join().unwrap_or_default(),
                });
            }
            Ok(None) => {
                if std::time::Instant::now() >= deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err("timed out".to_string());
                }
                std::thread::sleep(std::time::Duration::from_millis(20));
            }
            Err(e) => return Err(e.to_string()),
        }
    }
}

fn truncate_200(text: &str) -> String {
    text.trim().chars().take(200).collect()
}

fn install_claude_desktop(
    _workspace: &Path,
    _force: bool,
    _dry_run: bool,
    _scope: &str,
) -> InstallResult {
    let mut res = InstallResult::new("claude-desktop");
    res.notes.push(
        "Claude Desktop supports no remote/URL MCP registration — unsupported, \
         skipped (no config written)."
            .into(),
    );
    res
}

fn install_cursor(workspace: &Path, force: bool, dry_run: bool, scope: &str) -> InstallResult {
    let mut res = InstallResult::new("cursor");
    let base = if scope == "global" {
        home_dir()
    } else {
        workspace.to_path_buf()
    };
    if !merge_json_file(
        &base.join(".cursor").join("mcp.json"),
        &mcp_config_json(),
        force,
        &mut res,
        "mcpServers",
        dry_run,
    ) {
        return res;
    }
    write_file(
        &base.join(".cursor").join("rules").join("cairn.mdc"),
        &template("cursor/cairn.mdc"),
        force,
        &mut res,
        dry_run,
    );
    write_file(
        &base
            .join(".cursor")
            .join("subagents")
            .join("cairn-explorer.json"),
        &template("cursor/cairn-explorer.json"),
        force,
        &mut res,
        dry_run,
    );
    write_file(
        &base
            .join(".cursor")
            .join("subagents")
            .join("knowledge-steward.json"),
        &template("cursor/knowledge-steward.json"),
        force,
        &mut res,
        dry_run,
    );
    if !merge_json_file(
        &base.join(".cursor").join("hooks.json"),
        &cursor_hooks_json(),
        force,
        &mut res,
        "mcpServers",
        dry_run,
    ) {
        return res;
    }
    res.notes
        .push("Restart Cursor (or reload the window) to load the new MCP server.".into());
    res
}

fn install_droid(workspace: &Path, force: bool, dry_run: bool, _scope: &str) -> InstallResult {
    let mut res = InstallResult::new("droid");
    write_tree(
        &workspace.join(".factory").join("skills").join("cairn"),
        force,
        &mut res,
        dry_run,
    );
    for name in SLASH_COMMANDS {
        write_file(
            &workspace
                .join(".factory")
                .join("commands")
                .join(format!("{name}.md")),
            &template(&format!("commands/{name}.md")),
            force,
            &mut res,
            dry_run,
        );
    }
    write_file(
        &workspace
            .join(".factory")
            .join("droids")
            .join("cairn-explorer.md"),
        &claude_agent_md("cursor/cairn-explorer.json"),
        force,
        &mut res,
        dry_run,
    );
    write_file(
        &workspace
            .join(".factory")
            .join("droids")
            .join("knowledge-steward.md"),
        &claude_agent_md("cursor/knowledge-steward.json"),
        force,
        &mut res,
        dry_run,
    );

    if which("droid").is_some() {
        if dry_run {
            res.notes.push(
                "Would register MCP via `droid mcp add` (falls back to \
                 .factory/mcp.json on failure)."
                    .into(),
            );
        } else {
            let mut argv = vec![
                "droid".to_string(),
                "mcp".to_string(),
                "add".to_string(),
                "cairn".to_string(),
                daemon_mcp_url(),
                "--type".to_string(),
                "http".to_string(),
            ];
            if let Some(headers) = auth_headers().as_object() {
                for (name, value) in headers {
                    if let Some(value) = value.as_str() {
                        argv.push("--header".into());
                        argv.push(format!("{name}: {value}"));
                    }
                }
            }
            let mut registered = false;
            match run_client_command(&argv, 10) {
                Ok(output) if output.success => {
                    registered = true;
                    res.notes.push("Registered MCP via `droid mcp add`.".into());
                }
                Ok(output) => res.notes.push(format!(
                    "WARNING: `droid mcp add` exited {}: {}.",
                    output.code,
                    truncate_200(&output.message)
                )),
                Err(e) => res
                    .notes
                    .push(format!("WARNING: `droid mcp add` failed ({e}).")),
            }
            if !registered {
                if !merge_json_file(
                    &workspace.join(".factory").join("mcp.json"),
                    &mcp_config_json(),
                    force,
                    &mut res,
                    "mcpServers",
                    false,
                ) {
                    return res;
                }
                res.notes.push(
                    "Wrote .factory/mcp.json so the registration is present on \
                     the next droid run."
                        .into(),
                );
            }
        }
    } else {
        if !merge_json_file(
            &workspace.join(".factory").join("mcp.json"),
            &mcp_config_json(),
            force,
            &mut res,
            "mcpServers",
            dry_run,
        ) {
            return res;
        }
        res.notes.push(
            "droid CLI not found; wrote .factory/mcp.json (registers on next \
             droid run)."
                .into(),
        );
    }
    res.notes
        .push("Cron automation (e.g. `droid cron create`) requires the droid CLI on PATH.".into());
    res
}

fn install_zcode(workspace: &Path, force: bool, dry_run: bool, scope: &str) -> InstallResult {
    let mut res = InstallResult::new("zcode");
    let base = if scope == "global" {
        home_dir()
    } else {
        workspace.to_path_buf()
    };
    let mcp_path = if scope == "global" {
        strip_mcp_zcode(&base.join(".zcode").join("config.json"), &mut res, dry_run);
        base.join(".zcode").join("cli").join("config.json")
    } else {
        base.join(".zcode").join("config.json")
    };
    if !merge_json_file(
        &mcp_path,
        &zcode_mcp_config_json(),
        force,
        &mut res,
        "zcode",
        dry_run,
    ) {
        return res;
    }

    write_tree(
        &base.join(".zcode").join("skills").join("cairn"),
        force,
        &mut res,
        dry_run,
    );
    for name in SLASH_COMMANDS {
        write_file(
            &base
                .join(".zcode")
                .join("commands")
                .join(format!("{name}.md")),
            &claude_command_md(name),
            force,
            &mut res,
            dry_run,
        );
    }
    write_file(
        &base.join(".zcode").join("agents").join("cairn-explorer.md"),
        &claude_agent_md("cursor/cairn-explorer.json"),
        force,
        &mut res,
        dry_run,
    );
    write_file(
        &base
            .join(".zcode")
            .join("agents")
            .join("knowledge-steward.md"),
        &claude_agent_md("cursor/knowledge-steward.json"),
        force,
        &mut res,
        dry_run,
    );

    let agents_md = workspace.join("AGENTS.md");
    if !agents_md.exists() {
        write_file(&agents_md, &agents_instructions(), false, &mut res, dry_run);
    } else {
        res.skipped
            .push(format!("{} (exists; not overwritten)", agents_md.display()));
    }
    res.notes
        .push("ZCode hook schema differs; use `cairn hooks install` for git automation.".into());
    res
}

fn install_agy(_workspace: &Path, force: bool, dry_run: bool, scope: &str) -> InstallResult {
    let mut res = InstallResult::new("agy");
    let cfg = agy_config_path();
    if !merge_json_file(
        &cfg,
        &agy_mcp_config_json(),
        force,
        &mut res,
        "mcpServers",
        dry_run,
    ) {
        return res;
    }
    res.notes
        .push(format!("Global MCP config: {}", cfg.display()));
    if scope == "workspace" {
        res.notes.push(
            "agy reads only a global MCP config; the global path was written \
             even though workspace scope was requested."
                .into(),
        );
    }
    res.notes
        .push("Start or restart `agy` CLI to load the server.".into());
    res
}

fn opencode_config_path(workspace: &Path, scope: &str) -> PathBuf {
    if scope == "global" {
        home_dir()
            .join(".config")
            .join("opencode")
            .join("opencode.json")
    } else {
        workspace.join("opencode.json")
    }
}

fn install_opencode(workspace: &Path, force: bool, dry_run: bool, scope: &str) -> InstallResult {
    let mut res = InstallResult::new("opencode");
    let p = opencode_config_path(workspace, scope);
    if !merge_json_file(
        &p,
        &opencode_format_mcp_config_json(),
        force,
        &mut res,
        "opencode",
        dry_run,
    ) {
        return res;
    }
    note_mcp_merge(&mut res, &p, "under the `mcp` key (opencode's schema).");
    res.notes.push(
        "Skill (golden rules + tool-behaviors) reaches opencode via the \
         .agents/skills/ fallback."
            .into(),
    );
    res
}

fn kilo_config_path(workspace: &Path, scope: &str) -> PathBuf {
    if scope == "global" || scope == "all" {
        home_dir().join(".config").join("kilo").join("kilo.json")
    } else {
        workspace.join("kilo.json")
    }
}

fn install_kilo(workspace: &Path, force: bool, dry_run: bool, scope: &str) -> InstallResult {
    let mut res = InstallResult::new("kilo");
    let p = kilo_config_path(workspace, scope);
    if !merge_json_file(
        &p,
        &opencode_format_mcp_config_json(),
        force,
        &mut res,
        "kilo",
        dry_run,
    ) {
        return res;
    }
    note_mcp_merge(&mut res, &p, "under the `mcp` key (kilo's schema).");
    res.notes.push(
        "Skill (golden rules + tool-behaviors) may reach kilo via the \
         .agents/skills/ fallback."
            .into(),
    );
    res
}

fn omp_mcp_path(workspace: &Path, scope: &str) -> PathBuf {
    if scope == "global" {
        home_dir().join(".omp").join("agent").join("mcp.json")
    } else {
        workspace.join(".omp").join("mcp.json")
    }
}

fn omp_agents_dir(workspace: &Path, scope: &str) -> PathBuf {
    if scope == "global" {
        home_dir().join(".omp").join("agent").join("agents")
    } else {
        workspace.join(".omp").join("agents")
    }
}

fn install_omp(workspace: &Path, force: bool, dry_run: bool, scope: &str) -> InstallResult {
    let mut res = InstallResult::new("omp");
    let mcp_path = omp_mcp_path(workspace, scope);
    if !merge_json_file(
        &mcp_path,
        &mcp_config_json(),
        force,
        &mut res,
        "mcpServers",
        dry_run,
    ) {
        return res;
    }
    note_mcp_merge(&mut res, &mcp_path, "under the `mcpServers` key.");
    let agents_dir = omp_agents_dir(workspace, scope);
    write_file(
        &agents_dir.join("cairn-explorer.md"),
        &omp_agent_md("cursor/cairn-explorer.json"),
        force,
        &mut res,
        dry_run,
    );
    write_file(
        &agents_dir.join("knowledge-steward.md"),
        &omp_agent_md("cursor/knowledge-steward.json"),
        force,
        &mut res,
        dry_run,
    );
    res.notes.push(
        "Skill (golden rules + tool-behaviors) may reach omp via the \
         .agents/skills/ fallback (omp discovers `.agent[s]/skills`)."
            .into(),
    );
    res
}

// --- per-client uninstallers ------------------------------------------------------------

fn mcp_remove_user_scope(res: &mut InstallResult) {
    if which("claude").is_none() {
        res.notes.push(
            "NOTE: could not remove a user-scope MCP registration -- the `claude` \
             CLI was not found on PATH."
                .into(),
        );
        return;
    }
    let argv = [
        "claude".to_string(),
        "mcp".to_string(),
        "remove".to_string(),
        "cairn".to_string(),
        "--scope".to_string(),
        "user".to_string(),
    ];
    match run_client_command(&argv, 15) {
        Ok(output) if output.success => res.notes.push(
            "Removed user-scope MCP registration via `claude mcp remove cairn \
             --scope user`."
                .into(),
        ),
        Ok(output) => res.notes.push(format!(
            "WARNING: `claude mcp remove cairn --scope user` exited {}: {}; the \
             registration may remain.",
            output.code,
            truncate_200(&output.message)
        )),
        Err(e) => res.notes.push(format!(
            "WARNING: `claude mcp remove cairn --scope user` failed ({e}); the \
             registration may remain."
        )),
    }
}

fn uninstall_claude(ws: &Path, res: &mut InstallResult, scope: &str) {
    for base in uninstall_bases(ws, scope) {
        strip_mcp(&base.join(".mcp.json"), res);
        rm_tree_if_cairn(&base.join(".claude").join("skills").join("cairn"), res);
        for name in SLASH_COMMANDS {
            rm_if_ours(
                &base
                    .join(".claude")
                    .join("commands")
                    .join(format!("{name}.md")),
                &claude_command_md(name),
                res,
            );
        }
        rm_if_ours(
            &base
                .join(".claude")
                .join("agents")
                .join("cairn-explorer.md"),
            &claude_agent_md("cursor/cairn-explorer.json"),
            res,
        );
        rm_if_ours(
            &base
                .join(".claude")
                .join("agents")
                .join("knowledge-steward.md"),
            &claude_agent_md("cursor/knowledge-steward.json"),
            res,
        );
        rm_if_exists(
            &base.join(".claude").join("agents").join("cairn-agent.md"),
            res,
        );
        strip_hooks(&base.join(".claude").join("settings.json"), res);
        rmdir_if_empty(&base.join(".claude").join("commands"), res);
        rmdir_if_empty(&base.join(".claude").join("agents"), res);
        rmdir_if_empty(&base.join(".claude").join("skills"), res);
        rmdir_if_empty(&base.join(".claude"), res);
    }
    if scope == "workspace" || scope == "all" {
        rm_if_ours(&ws.join("CLAUDE.md"), &claude_instructions(), res);
    }
    if scope == "global" || scope == "all" {
        mcp_remove_user_scope(res);
    }
}

fn uninstall_claude_desktop(_ws: &Path, res: &mut InstallResult, _scope: &str) {
    strip_mcp(&claude_desktop_config_path(), res);
}

fn uninstall_cursor(ws: &Path, res: &mut InstallResult, scope: &str) {
    for base in uninstall_bases(ws, scope) {
        strip_mcp(&base.join(".cursor").join("mcp.json"), res);
        rm_if_ours(
            &base.join(".cursor").join("rules").join("cairn.mdc"),
            &template("cursor/cairn.mdc"),
            res,
        );
        rm_if_ours(
            &base
                .join(".cursor")
                .join("subagents")
                .join("cairn-explorer.json"),
            &template("cursor/cairn-explorer.json"),
            res,
        );
        rm_if_ours(
            &base
                .join(".cursor")
                .join("subagents")
                .join("knowledge-steward.json"),
            &template("cursor/knowledge-steward.json"),
            res,
        );
        strip_cursor_hooks(&base.join(".cursor").join("hooks.json"), res);
        rmdir_if_empty(&base.join(".cursor").join("rules"), res);
        rmdir_if_empty(&base.join(".cursor").join("subagents"), res);
        rmdir_if_empty(&base.join(".cursor"), res);
    }
}

fn mcp_remove_droid(res: &mut InstallResult) {
    if which("droid").is_none() {
        return;
    }
    let argv = [
        "droid".to_string(),
        "mcp".to_string(),
        "remove".to_string(),
        "cairn".to_string(),
    ];
    match run_client_command(&argv, 10) {
        Ok(output) if output.success => {
            res.notes
                .push("Removed MCP registration via `droid mcp remove cairn`.".into());
        }
        Ok(output) => res.notes.push(format!(
            "WARNING: `droid mcp remove cairn` exited {}: {}; the registration \
             may remain.",
            output.code,
            truncate_200(&output.message)
        )),
        Err(e) => res.notes.push(format!(
            "WARNING: `droid mcp remove cairn` failed ({e}); the registration \
             may remain."
        )),
    }
}

fn uninstall_droid(ws: &Path, res: &mut InstallResult, _scope: &str) {
    rm_tree_if_cairn(&ws.join(".factory").join("skills").join("cairn"), res);
    for name in SLASH_COMMANDS {
        rm_if_ours(
            &ws.join(".factory")
                .join("commands")
                .join(format!("{name}.md")),
            &template(&format!("commands/{name}.md")),
            res,
        );
    }
    rm_if_ours(
        &ws.join(".factory").join("droids").join("cairn-explorer.md"),
        &claude_agent_md("cursor/cairn-explorer.json"),
        res,
    );
    rm_if_ours(
        &ws.join(".factory")
            .join("droids")
            .join("knowledge-steward.md"),
        &claude_agent_md("cursor/knowledge-steward.json"),
        res,
    );
    rm_if_exists(
        &ws.join(".factory").join("droids").join("cairn-agent.md"),
        res,
    );
    strip_mcp(&ws.join(".factory").join("mcp.json"), res);
    mcp_remove_droid(res);
}

fn uninstall_zcode(ws: &Path, res: &mut InstallResult, scope: &str) {
    for base in uninstall_bases(ws, scope) {
        strip_mcp_zcode(
            &base.join(".zcode").join("cli").join("config.json"),
            res,
            false,
        );
        strip_mcp_zcode(&base.join(".zcode").join("config.json"), res, false);
        rm_tree_if_cairn(&base.join(".zcode").join("skills").join("cairn"), res);
        for name in SLASH_COMMANDS {
            rm_if_ours(
                &base
                    .join(".zcode")
                    .join("commands")
                    .join(format!("{name}.md")),
                &claude_command_md(name),
                res,
            );
        }
        rm_if_ours(
            &base.join(".zcode").join("agents").join("cairn-explorer.md"),
            &claude_agent_md("cursor/cairn-explorer.json"),
            res,
        );
        rm_if_ours(
            &base
                .join(".zcode")
                .join("agents")
                .join("knowledge-steward.md"),
            &claude_agent_md("cursor/knowledge-steward.json"),
            res,
        );
        rmdir_if_empty(&base.join(".zcode").join("commands"), res);
        rmdir_if_empty(&base.join(".zcode").join("agents"), res);
        rmdir_if_empty(&base.join(".zcode").join("skills"), res);
        rmdir_if_empty(&base.join(".zcode"), res);
    }
    if scope == "workspace" || scope == "all" {
        rm_if_ours(&ws.join("AGENTS.md"), &agents_instructions(), res);
    }
}

fn uninstall_agy(_ws: &Path, res: &mut InstallResult, _scope: &str) {
    strip_mcp(&agy_config_path(), res);
}

fn uninstall_opencode(ws: &Path, res: &mut InstallResult, scope: &str) {
    let scopes: Vec<&str> = if scope == "all" {
        vec!["workspace", "global"]
    } else {
        vec![scope]
    };
    for s in scopes {
        strip_mcp_opencode(&opencode_config_path(ws, s), res);
    }
}

fn uninstall_kilo(ws: &Path, res: &mut InstallResult, scope: &str) {
    let scopes: Vec<&str> = if scope == "all" {
        vec!["workspace", "global"]
    } else {
        vec![scope]
    };
    for s in scopes {
        strip_mcp_kilo(&kilo_config_path(ws, s), res);
    }
}

fn uninstall_omp(ws: &Path, res: &mut InstallResult, scope: &str) {
    let scopes: Vec<&str> = if scope == "all" {
        vec!["workspace", "global"]
    } else {
        vec![scope]
    };
    for s in scopes {
        strip_mcp(&omp_mcp_path(ws, s), res);
        let agents_dir = omp_agents_dir(ws, s);
        rm_if_ours(
            &agents_dir.join("cairn-explorer.md"),
            &omp_agent_md("cursor/cairn-explorer.json"),
            res,
        );
        rm_if_ours(
            &agents_dir.join("knowledge-steward.md"),
            &omp_agent_md("cursor/knowledge-steward.json"),
            res,
        );
    }
}

// --- cross-tool + git hooks -----------------------------------------------------------

fn install_cross_tool(workspace: &Path, force: bool, dry_run: bool) -> InstallResult {
    let mut res = InstallResult::new("cross-tool");
    write_tree(
        &workspace.join(".agents").join("skills").join("cairn"),
        force,
        &mut res,
        dry_run,
    );
    for name in SLASH_COMMANDS {
        write_file(
            &workspace
                .join(".agents")
                .join("commands")
                .join(format!("{name}.md")),
            &template(&format!("commands/{name}.md")),
            force,
            &mut res,
            dry_run,
        );
    }
    write_file(
        &workspace
            .join(".agents")
            .join("agents")
            .join("cairn-explorer.md"),
        &claude_agent_md("cursor/cairn-explorer.json"),
        force,
        &mut res,
        dry_run,
    );
    write_file(
        &workspace
            .join(".agents")
            .join("agents")
            .join("knowledge-steward.md"),
        &claude_agent_md("cursor/knowledge-steward.json"),
        force,
        &mut res,
        dry_run,
    );
    res
}

fn uninstall_cross_tool(workspace: &Path) -> InstallResult {
    let mut res = InstallResult::new("cross-tool");
    rm_tree_if_cairn(
        &workspace.join(".agents").join("skills").join("cairn"),
        &mut res,
    );
    for name in SLASH_COMMANDS {
        rm_if_ours(
            &workspace
                .join(".agents")
                .join("commands")
                .join(format!("{name}.md")),
            &template(&format!("commands/{name}.md")),
            &mut res,
        );
    }
    rm_if_ours(
        &workspace
            .join(".agents")
            .join("agents")
            .join("cairn-explorer.md"),
        &claude_agent_md("cursor/cairn-explorer.json"),
        &mut res,
    );
    rm_if_ours(
        &workspace
            .join(".agents")
            .join("agents")
            .join("knowledge-steward.md"),
        &claude_agent_md("cursor/knowledge-steward.json"),
        &mut res,
    );
    rmdir_if_empty(&workspace.join(".agents").join("commands"), &mut res);
    rmdir_if_empty(&workspace.join(".agents").join("agents"), &mut res);
    rmdir_if_empty(&workspace.join(".agents").join("skills"), &mut res);
    rmdir_if_empty(&workspace.join(".agents"), &mut res);
    res
}

/// Top-level repo records under `workspace`: (repo_id, root); the workspace
/// root itself when it is the only repository.
pub fn discover_hook_repos(workspace: &Path) -> Vec<(String, PathBuf)> {
    const SKIP_DIRS: [&str; 15] = [
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".tox",
        "dist",
        "build",
        "target",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "site-packages",
        ".knowledge",
        ".cairn",
    ];
    let Ok(root_meta) = std::fs::metadata(workspace) else {
        return Vec::new();
    };
    if !root_meta.is_dir() {
        return Vec::new();
    }
    let Ok(entries) = std::fs::read_dir(workspace) else {
        return Vec::new();
    };
    let mut top: Vec<(String, PathBuf)> = entries
        .flatten()
        .filter(|e| e.path().is_dir())
        .filter(|e| !SKIP_DIRS.contains(&e.file_name().to_string_lossy().as_ref()))
        .filter(|e| e.path().join(".git").exists())
        .map(|e| (e.file_name().to_string_lossy().into_owned(), e.path()))
        .collect();
    top.sort();
    if !top.is_empty() {
        return top;
    }
    if workspace.join(".git").exists() {
        let name = workspace
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_else(|| workspace.to_string_lossy().into_owned());
        return vec![(name, workspace.to_path_buf())];
    }
    Vec::new()
}

fn repo_hooks_dir(repo_root: &Path) -> Option<PathBuf> {
    let git = repo_root.join(".git");
    if git.is_dir() {
        return Some(git.join("hooks"));
    }
    if !git.is_file() {
        return None;
    }
    let output = std::process::Command::new("git")
        .args(["rev-parse", "--git-path", "hooks"])
        .current_dir(repo_root)
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let resolved = PathBuf::from(String::from_utf8_lossy(&output.stdout).trim());
    if resolved.is_absolute() {
        Some(resolved)
    } else {
        Some(repo_root.join(resolved))
    }
}

/// The home path embedded in git hooks: forward slashes on Windows, where the
/// hook runs under git's sh and every native path would otherwise carry a
/// backslash the unsafe-embed check must reject.
fn hook_home() -> Result<String, String> {
    let home = crate::store_paths::current_cairn_home()
        .display()
        .to_string();
    let embed = if cfg!(windows) {
        home.replace('\\', "/")
    } else {
        home
    };
    if embed
        .chars()
        .any(|c| matches!(c, '$' | '`' | '"' | '\\' | '\r' | '\n'))
    {
        return Err(format!(
            "CAIRN_HOME ({embed}) contains characters unsafe to embed in a git hook; \
             use a path without $ ` \" \\ or newlines, or unset CAIRN_HOME"
        ));
    }
    Ok(embed)
}

fn render_hook(template: &str, repo: &str) -> String {
    let rendered = template.replace("{repo}", repo);
    let default = home_dir().join(".cairn");
    if crate::store_paths::current_cairn_home() == crate::store_paths::resolve_path(&default) {
        return rendered;
    }
    let Ok(home) = hook_home() else {
        return rendered;
    };
    let (shebang, rest) = rendered.split_once('\n').unwrap_or((&rendered, ""));
    format!("{shebang}\nexport CAIRN_HOME=\"{home}\"\n{rest}")
}

const POST_COMMIT_TEMPLATE: &str = r#"#!/bin/bash
# Auto-generated by cairn (cairn hooks install)
# Runs an incremental graph update over the committed range after every commit.
# cairn resolves the central store for this workspace from cwd (ancestor walk);
# when the store home is not the default, the export line above pins it for
# this script.
prev=$(git rev-parse -q --verify 'HEAD@{1}')
if [ -n "$prev" ]; then
  range="${prev}..HEAD"
else
  range="$(git hash-object -t tree /dev/null)..HEAD"
fi
cairn update --repo "{repo}" --diff-ref "$range" > /dev/null 2>&1 &
cairn validate-paths --mark > /dev/null 2>&1 &
"#;

const POST_CHECKOUT_TEMPLATE: &str = r#"#!/bin/bash
# Auto-generated by cairn (cairn hooks install)
# Runs an incremental graph update over the checked-out range on branch
# switches. cairn resolves the central store for this workspace from cwd
# (ancestor walk); when the store home is not the default, the export line
# above pins it for this script.
if [ "$3" = "1" ]; then
  cairn update --repo "{repo}" --diff-ref "$1..$2" > /dev/null 2>&1 &
fi
"#;

fn is_cairn_managed_hook(hook_path: &Path) -> bool {
    std::fs::read_to_string(hook_path)
        .map(|text| text.contains("# Auto-generated by cairn (cairn hooks install)"))
        .unwrap_or(false)
}

/// Guidance shown when `hooks install` finds no git repositories.
pub const NO_GIT_REPOS_GUIDANCE: &str = "No git repositories found in this workspace — cairn git hooks install into .git/hooks, so run `git init` in your repository root first.";

/// Install post-commit/post-checkout hooks; Err carries the guard reason.
pub fn install_git_hooks(repos: &[(String, PathBuf)]) -> Result<Vec<String>, String> {
    for (repo, _) in repos {
        if !repo
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-' | '/'))
        {
            return Err(format!(
                "Invalid repo name (security: shell-injection guard): {repo:?}"
            ));
        }
    }
    hook_home()?; // refuse the whole install when home cannot be embedded safely
    let mut installed = Vec::new();
    for (repo, root) in repos {
        let Some(hooks_dir) = repo_hooks_dir(root) else {
            continue;
        };
        let _ = std::fs::create_dir_all(&hooks_dir);
        let mut touched = false;
        for (hook_name, template) in [
            ("post-commit", POST_COMMIT_TEMPLATE),
            ("post-checkout", POST_CHECKOUT_TEMPLATE),
        ] {
            let hook_path = hooks_dir.join(hook_name);
            if hook_path.exists() && !is_cairn_managed_hook(&hook_path) {
                continue;
            }
            if std::fs::write(&hook_path, render_hook(template, repo)).is_ok() {
                #[cfg(unix)]
                if let Ok(meta) = std::fs::metadata(&hook_path) {
                    use std::os::unix::fs::PermissionsExt as _;
                    let mut perms = meta.permissions();
                    perms.set_mode(perms.mode() | 0o111);
                    let _ = std::fs::set_permissions(&hook_path, perms);
                }
                touched = true;
            }
        }
        if touched {
            installed.push(repo.clone());
        }
    }
    Ok(installed)
}

/// Remove cairn-managed post-commit/post-checkout hooks; user hooks stay.
pub fn uninstall_git_hooks(repos: &[(String, PathBuf)]) -> Vec<String> {
    let mut removed = Vec::new();
    for (repo, root) in repos {
        let Some(hooks_dir) = repo_hooks_dir(root) else {
            continue;
        };
        let mut touched = false;
        for hook_name in ["post-commit", "post-checkout"] {
            let hook_path = hooks_dir.join(hook_name);
            if is_cairn_managed_hook(&hook_path) && std::fs::remove_file(&hook_path).is_ok() {
                touched = true;
            }
        }
        if touched {
            removed.push(repo.clone());
        }
    }
    removed
}

// --- orchestration ---------------------------------------------------------------------

type InstallerFn = fn(&Path, bool, bool, &str) -> InstallResult;

fn installer_for(client: &str) -> Option<InstallerFn> {
    match client {
        "claude" => Some(install_claude),
        "claude-desktop" => Some(install_claude_desktop),
        "cursor" => Some(install_cursor),
        "droid" => Some(install_droid),
        "zcode" => Some(install_zcode),
        "agy" => Some(install_agy),
        "opencode" => Some(install_opencode),
        "kilo" => Some(install_kilo),
        "omp" => Some(install_omp),
        _ => None,
    }
}

fn uninstaller_for(client: &str) -> Option<fn(&Path, &mut InstallResult, &str)> {
    match client {
        "claude" => Some(uninstall_claude),
        "claude-desktop" => Some(uninstall_claude_desktop),
        "cursor" => Some(uninstall_cursor),
        "droid" => Some(uninstall_droid),
        "zcode" => Some(uninstall_zcode),
        "agy" => Some(uninstall_agy),
        "opencode" => Some(uninstall_opencode),
        "kilo" => Some(uninstall_kilo),
        "omp" => Some(uninstall_omp),
        _ => None,
    }
}

fn resolve_targets(
    clients: Option<&[String]>,
    detections: &[Detection],
) -> Result<Vec<&'static str>, String> {
    match clients {
        Some(list) if !list.is_empty() => {
            for client in list {
                if !CLIENTS.contains(&client.as_str()) && client != "all" {
                    return Err(format!(
                        "Unknown clients: [{client}]. Valid: {} all",
                        CLIENTS.join(" ")
                    ));
                }
            }
            Ok(CLIENTS
                .iter()
                .copied()
                .filter(|c| list.iter().any(|l| l == c || l == "all"))
                .collect())
        }
        _ => Ok(detections
            .iter()
            .filter(|d| d.detected)
            .map(|d| d.client)
            .collect()),
    }
}

fn verify_results(results: &mut [InstallResult]) {
    for res in results.iter_mut() {
        let mut entry = None;
        for written in &res.written {
            if let Some(found) = registration_entry(Path::new(written)) {
                entry = Some(found);
                break;
            }
        }
        let Some(entry) = entry else {
            res.notes.push(
                "verification skipped: no file-written MCP registration this run \
                 (CLI-registered or unchanged)"
                    .into(),
            );
            continue;
        };
        let Some(url) = registration_url(&entry) else {
            res.notes
                .push("verification skipped: registration carries no daemon URL to probe".into());
            continue;
        };
        let Some((host, port)) = url_host_port(&url) else {
            res.notes
                .push("verification skipped: registration carries no daemon URL to probe".into());
            continue;
        };
        if cairn_daemon::lifecycle::healthz_responds(port, &host) {
            res.verification_status = "pass".into();
            res.verification_detail = format!("healthz answered at {host}:{port}");
        } else {
            res.verification_status = "fail".into();
            res.verification_detail =
                format!("daemon at {host}:{port} unreachable — run `cairn connect` to start it");
        }
    }
}

fn url_host_port(url: &str) -> Option<(String, u16)> {
    let (scheme, rest) = url.split_once("://")?;
    let authority = rest.split(['/', '?', '#']).next()?;
    let default_port = match scheme {
        "http" => 80,
        "https" => 443,
        _ => return None,
    };
    let (host, port) = match authority.rsplit_once(':') {
        Some((h, p)) if p.chars().all(|c| c.is_ascii_digit()) && !p.is_empty() => {
            (h, p.parse::<u16>().ok()?)
        }
        _ => (authority, default_port),
    };
    if host.is_empty() {
        return None;
    }
    Some((host.trim_matches(['[', ']']).to_string(), port))
}

fn note_embedded_key(results: &mut [InstallResult]) {
    if auth_headers().as_object().is_none_or(Map::is_empty) {
        return;
    }
    for res in results.iter_mut() {
        for written in &res.written {
            if let Some(entry) = registration_entry(Path::new(written))
                && entry.get("headers").is_some_and(Value::is_object)
            {
                res.notes.push(format!(
                    "NOTE: {written} embeds the daemon API key; add it to \
                     .gitignore before committing the workspace."
                ));
                break;
            }
        }
    }
}

/// Install selected agent clients and verify their registrations.
pub fn install(
    workspace: &Path,
    clients: Option<&[String]>,
    force: bool,
    dry_run: bool,
    include_git_hooks: bool,
    scope: &str,
) -> Result<InstallReport, String> {
    let detections = detect_clients(workspace);
    let targets = resolve_targets(clients, &detections)?;

    let mut results: Vec<InstallResult> = Vec::new();
    for client in &targets {
        match installer_for(client) {
            Some(installer) => results.push(installer(workspace, force, dry_run, scope)),
            None => continue,
        }
    }
    if !dry_run {
        verify_results(&mut results);
        note_embedded_key(&mut results);
    }

    let cross = (!targets.is_empty()).then(|| install_cross_tool(workspace, force, dry_run));

    let mut git_installed = Vec::new();
    let mut git_error = None;
    if include_git_hooks && !targets.is_empty() && !dry_run {
        match install_git_hooks(&discover_hook_repos(workspace)) {
            Ok(repos) => git_installed = repos,
            Err(e) => {
                git_error = Some(format!("git hooks skipped — {e}"));
            }
        }
        if let Some(e) = &git_error {
            eprintln!("warning: {e}");
        }
    }

    Ok(InstallReport {
        detections,
        results,
        cross_tool: cross,
        git_hooks_installed: git_installed,
        git_hooks_error: git_error,
    })
}

/// Remove cairn entries for the install scope; the operation is idempotent.
pub fn uninstall(
    workspace: &Path,
    clients: Option<&[String]>,
    scope: &str,
) -> Result<InstallReport, String> {
    let detections = detect_clients(workspace);
    let targets = resolve_targets(clients, &detections)?;
    let mut results = Vec::new();
    for client in &targets {
        let mut res = InstallResult::new(client);
        if let Some(uninstaller) = uninstaller_for(client) {
            uninstaller(workspace, &mut res, scope);
        }
        results.push(res);
    }
    let cross = (!targets.is_empty()).then(|| uninstall_cross_tool(workspace));
    Ok(InstallReport {
        detections,
        results,
        cross_tool: cross,
        git_hooks_installed: Vec::new(),
        git_hooks_error: None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::agent_templates;
    use serde_json::json;

    fn tmp_dir(tag: &str) -> PathBuf {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let dir =
            std::env::temp_dir().join(format!("cairn-agents-{}-{nanos}-{tag}", std::process::id()));
        std::fs::create_dir_all(&dir).expect("temp dir");
        dir
    }

    fn write_config(path: &Path, value: &Value) {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).expect("parent");
        }
        std::fs::write(
            path,
            format!("{}\n", serde_json::to_string_pretty(value).expect("json")),
        )
        .expect("write config");
    }

    fn read_config(path: &Path) -> Value {
        serde_json::from_str(&std::fs::read_to_string(path).expect("read config"))
            .expect("parse config")
    }

    #[test]
    fn deep_merge_preserves_user_keys_and_replaces_the_cairn_server() {
        let existing = json!({
            "other": {"keep": true},
            "mcpServers": {"user-server": {"command": "x"}},
        });
        let addition = json!({"mcpServers": {"cairn": {"type": "http", "url": "u"}}});
        let merged = deep_merge(
            existing.as_object().expect("obj").clone(),
            addition.as_object().expect("obj").clone(),
            "mcpServers",
        );
        assert_eq!(merged["other"]["keep"], json!(true));
        assert_eq!(merged["mcpServers"]["user-server"]["command"], json!("x"));
        assert_eq!(merged["mcpServers"]["cairn"]["url"], json!("u"));

        // A second cairn entry replaces (does not duplicate).
        let addition2 = json!({"mcpServers": {"cairn": {"type": "http", "url": "u2"}}});
        let merged2 = deep_merge(
            Value::Object(merged.clone())
                .as_object()
                .expect("obj")
                .clone(),
            addition2.as_object().expect("obj").clone(),
            "mcpServers",
        );
        assert_eq!(merged2["mcpServers"]["cairn"]["url"], json!("u2"));
    }

    #[test]
    fn zcode_merge_moves_the_server_under_mcp_dot_servers() {
        let _env = crate::test_env::EnvScope::new("agent-install");
        let existing = json!({
            "mcpServers": {"cairn": {"url": "stale"}},
            "mcp": {"servers": {"user": {"command": "u"}}},
        });
        let addition = zcode_mcp_config_json();
        let merged = deep_merge(
            existing.as_object().expect("obj").clone(),
            addition.as_object().expect("obj").clone(),
            "zcode",
        );
        assert_eq!(
            merged["mcp"]["servers"]["cairn"]["url"],
            daemon_mcp_url_for_test()
        );
        assert!(merged["mcp"]["servers"]["user"]["command"] == json!("u"));
        // The stale top-level cairn-stamped mcpServers entry is dropped; user servers stay.
        assert!(merged.get("mcpServers").is_none());
    }

    fn daemon_mcp_url_for_test() -> Value {
        Value::String(daemon_mcp_url())
    }

    #[test]
    fn already_installed_url_comparison_detects_moved_daemons() {
        let entry = json!({"type": "http", "url": "http://127.0.0.1:9876/mcp"});
        let existing = json!({"mcpServers": {"cairn": entry}});
        let same =
            json!({"mcpServers": {"cairn": {"type": "http", "url": "http://127.0.0.1:9876/mcp"}}});
        assert!(already_installed(
            existing.as_object().expect("o"),
            same.as_object().expect("o"),
            "mcpServers"
        ));
        let moved =
            json!({"mcpServers": {"cairn": {"type": "http", "url": "http://127.0.0.1:9999/mcp"}}});
        assert!(!already_installed(
            existing.as_object().expect("o"),
            moved.as_object().expect("o"),
            "mcpServers"
        ));
        // A rotated key reads as not-installed so the reinstall rewrites it.
        let keyed_existing = json!({"mcpServers": {"cairn": {"url": "http://127.0.0.1:9876/mcp", "headers": {"Authorization": "Bearer a"}}}});
        let keyed_new = json!({"mcpServers": {"cairn": {"url": "http://127.0.0.1:9876/mcp", "headers": {"Authorization": "Bearer b"}}}});
        assert!(!already_installed(
            keyed_existing.as_object().expect("o"),
            keyed_new.as_object().expect("o"),
            "mcpServers"
        ));
    }

    #[test]
    fn merge_json_file_backs_up_malformed_configs_instead_of_clobbering() {
        let _env = crate::test_env::EnvScope::new("agent-install");
        let dir = tmp_dir("malformed");
        let path = dir.join("mcp.json");
        std::fs::write(&path, b"{not json").expect("malformed seed");
        let mut res = InstallResult::new("claude");
        merge_json_file(
            &path,
            &mcp_config_json(),
            false,
            &mut res,
            "mcpServers",
            false,
        );
        let backup = path.with_file_name("mcp.json.bak");
        assert!(backup.exists(), "malformed config must be backed up");
        assert_eq!(std::fs::read(&backup).expect("backup bytes"), b"{not json");
        let merged = read_config(&path);
        assert!(merged["mcpServers"]["cairn"]["url"].is_string());

        // A non-object JSON value is preserved the same way.
        let path2 = dir.join("array.json");
        std::fs::write(&path2, b"[1, 2]").expect("array seed");
        let mut res2 = InstallResult::new("claude");
        merge_json_file(
            &path2,
            &mcp_config_json(),
            false,
            &mut res2,
            "mcpServers",
            false,
        );
        assert_eq!(
            std::fs::read(path2.with_file_name("array.json.bak")).expect("bak"),
            b"[1, 2]"
        );
        assert!(read_config(&path2)["mcpServers"]["cairn"]["url"].is_string());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    #[cfg(unix)]
    fn install_aborts_the_client_when_the_config_backup_fails() {
        let _env = crate::test_env::EnvScope::new("agent-install");
        use std::os::unix::fs::PermissionsExt as _;
        let dir = tmp_dir("backup-fails");
        let path = dir.join("opencode.json");
        std::fs::write(&path, b"{not json").expect("seed");
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o000)).expect("chmod");
        let res = install_opencode(&dir, false, false, "workspace");
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o644)).expect("chmod");
        assert_eq!(std::fs::read(&path).expect("bytes"), b"{not json");
        assert_eq!(res.notes.len(), 1, "{:?}", res.notes);
        assert!(res.notes[0].starts_with("install failed: refusing to overwrite"));
        assert!(res.written.is_empty() && res.skipped.is_empty());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn merge_json_file_is_idempotent_without_force() {
        let _env = crate::test_env::EnvScope::new("agent-install");
        let dir = tmp_dir("idempotent");
        let path = dir.join("mcp.json");
        let mut res = InstallResult::new("claude");
        merge_json_file(
            &path,
            &mcp_config_json(),
            false,
            &mut res,
            "mcpServers",
            false,
        );
        assert!(path.exists());
        let mut res2 = InstallResult::new("claude");
        merge_json_file(
            &path,
            &mcp_config_json(),
            false,
            &mut res2,
            "mcpServers",
            false,
        );
        assert!(
            res2.skipped
                .iter()
                .any(|p| p.as_str() == path.to_string_lossy()),
            "second run must skip: {:?}",
            res2
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn install_claude_workspace_writes_the_full_asset_set() {
        let _env = crate::test_env::EnvScope::new("agent-install");
        let dir = tmp_dir("claude-ws");
        let report = install(
            &dir,
            Some(&["claude".to_string()]),
            false,
            false,
            false,
            "workspace",
        )
        .expect("install");
        let res = &report.results[0];
        assert!(res.verification_status == "pass" || res.verification_status == "fail");

        // The skill tree mirrors the vendored templates.
        let skill_md = dir.join(".claude/skills/cairn/SKILL.md");
        assert_eq!(
            std::fs::read(&skill_md).expect("skill"),
            agent_templates::read("skill/SKILL.md")
                .expect("template")
                .as_bytes()
        );
        // Slash commands carry frontmatter.
        let command = std::fs::read_to_string(dir.join(".claude/commands/cairn.md")).expect("cmd");
        assert!(command.starts_with("---\ndescription: "), "{command}");
        // Subagents and hooks.
        assert!(dir.join(".claude/agents/cairn-explorer.md").exists());
        let settings = read_config(&dir.join(".claude/settings.json"));
        assert_eq!(
            settings["hooks"]["PostToolUse"][0]["matcher"],
            json!("Edit|Write|MultiEdit")
        );
        let hook_command = settings["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
            .as_str()
            .expect("hook command");
        assert!(
            hook_command.ends_with("' hooks run post_edit"),
            "{hook_command}"
        );
        // MCP registration carries the daemon URL.
        let mcp = read_config(&dir.join(".mcp.json"));
        assert_eq!(mcp["mcpServers"]["cairn"]["url"], daemon_mcp_url_for_test());
        // CLAUDE.md is only written when missing.
        assert!(dir.join("CLAUDE.md").exists());

        // Uninstall removes everything it wrote, idempotently.
        let report =
            uninstall(&dir, Some(&["claude".to_string()]), "workspace").expect("uninstall");
        let removed: Vec<&String> = report.results[0].written.iter().collect();
        assert!(!removed.is_empty());
        assert!(!skill_md.exists());
        assert!(!dir.join(".claude/skills").exists(), "{:?}", removed);
        // .claude itself stays: the stripped settings.json remains for the user.
        assert!(dir.join(".claude/settings.json").exists(), "{:?}", removed);
        let report =
            uninstall(&dir, Some(&["claude".to_string()]), "workspace").expect("uninstall 2");
        assert!(
            report.results[0]
                .written
                .iter()
                .any(|p| p.contains("(nothing to remove)") || true)
                || report.results[0].written.is_empty()
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn install_migrates_retired_python_module_hooks_to_the_binary_command() {
        let _env = crate::test_env::EnvScope::new("agent-install");
        let dir = tmp_dir("claude-legacy-hooks");
        let legacy = "'/usr/bin/python3' -m cairn.hooks.claude_hooks post_edit";
        write_config(
            &dir.join(".claude/settings.json"),
            &json!({
                "hooks": {
                    "PostToolUse": [
                        {"matcher": "Edit|Write|MultiEdit", "hooks": [
                            {"type": "command", "command": legacy}
                        ]},
                        {"matcher": "Bash", "hooks": [
                            {"type": "command", "command": "/usr/bin/user-hook"}
                        ]},
                    ],
                },
            }),
        );
        install(
            &dir,
            Some(&["claude".to_string()]),
            false,
            false,
            false,
            "workspace",
        )
        .expect("install");
        let settings = read_config(&dir.join(".claude/settings.json"));
        let commands: Vec<String> = settings["hooks"]["PostToolUse"]
            .as_array()
            .expect("entries")
            .iter()
            .flat_map(|entry| entry["hooks"].as_array().cloned().unwrap_or_default())
            .filter_map(|h| h["command"].as_str().map(str::to_string))
            .collect();
        assert!(!commands.iter().any(|c| c == legacy), "{commands:?}");
        assert!(commands.iter().any(|c| c == "/usr/bin/user-hook"));
        let post_edit: Vec<&String> = commands
            .iter()
            .filter(|c| c.ends_with(" hooks run post_edit"))
            .collect();
        assert_eq!(post_edit.len(), 1, "{commands:?}");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn uninstall_preserves_user_hooks_and_servers() {
        let dir = tmp_dir("claude-user");
        let settings = json!({
            "model": "opus",
            "hooks": {
                "PostToolUse": [
                    {"matcher": "Edit|Write|MultiEdit", "hooks": [
                        {"type": "command", "command": claude_hook_command("post_edit")}
                    ]},
                    {"matcher": "Bash", "hooks": [
                        {"type": "command", "command": "/usr/bin/user-hook"}
                    ]},
                ],
            },
            "mcpServers": {"user-server": {"command": "u"}, "cairn": {"url": "x"}},
        });
        let path = dir.join(".claude/settings.json");
        write_config(&path, &settings);
        let mcp_path = dir.join(".mcp.json");
        write_config(
            &mcp_path,
            &json!({"mcpServers": {"user-server": {"command": "u"}, "cairn": {"url": "x"}}}),
        );

        let report =
            uninstall(&dir, Some(&["claude".to_string()]), "workspace").expect("uninstall");
        let res = &report.results[0];
        assert!(
            res.written
                .iter()
                .any(|p| p.contains("stripped cairn hooks from"))
        );
        assert!(
            res.written
                .iter()
                .any(|p| p.contains("stripped cairn from"))
        );

        let after = read_config(&path);
        assert_eq!(after["model"], json!("opus"));
        let entries = after["hooks"]["PostToolUse"].as_array().expect("entries");
        assert_eq!(entries.len(), 1, "only the cairn entry is dropped: {after}");
        assert_eq!(
            entries[0]["hooks"][0]["command"],
            json!("/usr/bin/user-hook")
        );
        let mcp_after = read_config(&mcp_path);
        assert!(mcp_after["mcpServers"].get("cairn").is_none());
        assert!(mcp_after["mcpServers"]["user-server"]["command"] == json!("u"));
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// Run with HOME pinned to a fresh dir (serialized: process env is global).
    fn with_fresh_home<T>(tag: &str, run: impl FnOnce(&Path) -> T) -> (PathBuf, T) {
        let env = crate::test_env::EnvScope::new(tag);
        let outcome = run(env.dir());
        (env.dir().to_path_buf(), outcome)
    }

    #[test]
    fn detection_table_covers_every_client() {
        let (_dir, (detections, installed)) =
            with_fresh_home("detect", |dir| (detect_clients(dir), check_installed(dir)));
        let names: Vec<&str> = detections.iter().map(|d| d.client).collect();
        // Detection order (detect.py), not the CLIENTS display order.
        assert_eq!(
            names,
            [
                "claude",
                "cursor",
                "droid",
                "zcode",
                "claude-desktop",
                "agy",
                "opencode",
                "kilo",
                "omp"
            ]
        );
        assert_eq!(installed.len(), CLIENTS.len());
        assert!(
            installed.values().all(|v| !v),
            "fresh dir: nothing installed"
        );
        let _ = std::fs::remove_dir_all(&_dir);
    }

    #[test]
    fn check_installed_reads_every_config_shape() {
        let dir = tmp_dir("installed");
        write_config(
            &dir.join(".cursor/mcp.json"),
            &json!({"mcpServers": {"cairn": {"url": "http://x/mcp"}}}),
        );
        write_config(
            &dir.join(".zcode/config.json"),
            &json!({"mcp": {"servers": {"cairn": {"url": "http://x/mcp"}}}}),
        );
        write_config(
            &dir.join("opencode.json"),
            &json!({"mcp": {"cairn": {"type": "remote", "url": "http://x/mcp", "enabled": true}}}),
        );
        let installed = with_fresh_home("installed-check", |_| check_installed(&dir)).1;
        assert!(installed["cursor"]);
        assert!(installed["zcode"]);
        assert!(installed["opencode"]);
        assert!(!installed["claude"]);
        assert!(!installed["kilo"]);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn omp_installs_flat_mcp_and_agents() {
        let _env = crate::test_env::EnvScope::new("agent-install");
        let dir = tmp_dir("omp");
        let report = install(
            &dir,
            Some(&["omp".to_string()]),
            false,
            false,
            false,
            "workspace",
        )
        .expect("install");
        let mcp = read_config(&dir.join(".omp/mcp.json"));
        assert_eq!(mcp["mcpServers"]["cairn"]["url"], daemon_mcp_url_for_test());
        assert!(dir.join(".omp/agents/cairn-explorer.md").exists());
        let agent =
            std::fs::read_to_string(dir.join(".omp/agents/cairn-explorer.md")).expect("agent");
        assert!(agent.starts_with("---\nname: "), "{agent}");
        assert_eq!(report.results[0].client, "omp");
        let report = uninstall(&dir, Some(&["omp".to_string()]), "workspace").expect("uninstall");
        assert!(
            report.results[0]
                .written
                .iter()
                .any(|p| p.contains("stripped cairn from"))
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn cross_tool_tree_installs_and_uninstalls_by_template_bytes() {
        let _env = crate::test_env::EnvScope::new("agent-install");
        let dir = tmp_dir("cross");
        let report = install(
            &dir,
            Some(&["kilo".to_string()]),
            false,
            false,
            false,
            "workspace",
        )
        .expect("install");
        assert!(report.cross_tool.is_some());
        let skill = dir.join(".agents/skills/cairn/SKILL.md");
        assert!(skill.exists());
        // A modified file is preserved on uninstall.
        std::fs::write(&skill, b"modified").expect("modify");
        let report = uninstall(&dir, Some(&["kilo".to_string()]), "workspace").expect("uninstall");
        assert!(skill.exists(), "modified template file must be kept");
        assert!(report.results[0].written.is_empty() || report.cross_tool.is_some());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn git_hooks_render_with_the_marker_and_home_pin() {
        let _env = crate::test_env::EnvScope::new("agent-install");
        let dir = tmp_dir("hooks");
        let repo = dir.join("repo");
        std::fs::create_dir_all(repo.join(".git/hooks")).expect("repo");
        let repos = discover_hook_repos(&dir);
        assert_eq!(repos.len(), 1, "top-level repo discovered: {repos:?}");
        let installed = install_git_hooks(&repos).expect("install hooks");
        assert_eq!(installed, vec!["repo".to_string()]);
        let hook = std::fs::read_to_string(repo.join(".git/hooks/post-commit")).expect("hook");
        assert!(hook.contains("# Auto-generated by cairn (cairn hooks install)"));
        assert!(hook.contains("cairn update --repo \"repo\""));
        // Reinstall over the cairn-managed hook is allowed; a user hook is not.
        let user_hook = repo.join(".git/hooks/post-checkout");
        std::fs::write(&user_hook, b"#!/bin/sh\necho user\n").expect("user hook");
        let installed = install_git_hooks(&repos).expect("reinstall");
        assert_eq!(
            installed,
            vec!["repo".to_string()],
            "post-commit reinstall touches the repo"
        );
        assert_eq!(
            std::fs::read(&user_hook).expect("user hook bytes"),
            b"#!/bin/sh\necho user\n",
            "a user-owned post-checkout must be left in place"
        );
        let removed = uninstall_git_hooks(&repos);
        assert_eq!(removed, vec!["repo".to_string()]);
        assert!(
            !repo.join(".git/hooks/post-commit").exists(),
            "cairn-managed hook must be removed"
        );
        assert_eq!(
            std::fs::read(&user_hook).expect("user hook bytes"),
            b"#!/bin/sh\necho user\n",
            "a user-owned post-checkout must survive uninstall"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn verify_results_reports_the_probe_verdict() {
        let mut results = vec![InstallResult::new("claude")];
        // A written path with no parseable registration -> the skip note.
        verify_results(&mut results);
        assert!(
            results[0]
                .notes
                .iter()
                .any(|n| n.contains("verification skipped")),
            "{:?}",
            results[0].notes
        );
    }

    #[test]
    fn instruction_files_embed_the_shared_body() {
        // CLAUDE.md / AGENTS.md composition reuses the extracted instruction body.
        let claude = claude_instructions();
        assert!(claude.starts_with("# Codebase Intelligence (Cairn)"));
        assert!(claude.contains("## Workflow: explore-first"));
        let agents = agents_instructions();
        assert!(agents.starts_with("# Codebase Intelligence System"));
        assert!(agents.contains("- Name: `cairn` (auto-connected at session start)"));
        assert!(agents.contains(instructions_body().as_str()));
    }

    #[test]
    fn url_host_port_parses_the_probe_targets() {
        assert_eq!(
            url_host_port("http://127.0.0.1:9876/mcp"),
            Some(("127.0.0.1".to_string(), 9876))
        );
        assert_eq!(
            url_host_port("https://cairn.example.com/mcp"),
            Some(("cairn.example.com".to_string(), 443))
        );
        assert_eq!(url_host_port("stdio://x"), None);
    }

    #[test]
    fn registration_entry_reads_every_container_shape() {
        let dir = tmp_dir("entry");
        let path = dir.join("config.json");
        write_config(
            &path,
            &json!({"mcpServers": {"cairn": {"url": "http://a/mcp"}}}),
        );
        assert_eq!(
            registration_url(&registration_entry(&path).expect("entry")),
            Some("http://a/mcp".to_string())
        );
        write_config(
            &path,
            &json!({"mcp": {"servers": {"cairn": {"url": "http://b/mcp"}}}}),
        );
        assert_eq!(
            registration_url(&registration_entry(&path).expect("entry")),
            Some("http://b/mcp".to_string())
        );
        write_config(&path, &json!({"mcp": {"cairn": {"url": "http://c/mcp"}}}));
        assert_eq!(
            registration_url(&registration_entry(&path).expect("entry")),
            Some("http://c/mcp".to_string())
        );
        // Non-JSON files and non-cairn configs have no entry.
        assert_eq!(registration_entry(&dir.join("missing.json")), None);
        write_config(&path, &json!({"other": 1}));
        assert_eq!(registration_entry(&path), None);
        let _ = std::fs::remove_dir_all(&dir);
    }
}
