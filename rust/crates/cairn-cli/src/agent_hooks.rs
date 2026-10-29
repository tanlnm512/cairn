//! Agent hook entrypoints (`cairn hooks run <entrypoint>`): the Claude Code and
//! Cursor hook protocol driven through the cairn CLI.
//!
//! stdout is reserved for machine-readable hook output (empty or JSON): the
//! aggregators parse it, so every human-readable diagnostic goes to stderr.

use std::io::Read;
use std::process::{ExitCode, Stdio};
use std::time::Duration;

use serde_json::{Map, Value, json};

use crate::agent_install::spawn_with_timeout;
use crate::cmds_knowledge::EMPTY_DIGEST;
use cairn_query::tools_km::{failure_signature, strip_private_data};

/// Transcript turns kept for `session_end` capture.
const TRANSCRIPT_TAIL: usize = 80;
/// Per-field cap on captured failure text, applied after redaction.
const FAILURE_FIELD_CHARS: usize = 4000;

/// Run one hook entrypoint over the JSON payload on stdin.
pub fn run(entrypoint: &str) -> ExitCode {
    match entrypoint {
        "post_edit" => post_edit(&read_stdin_payload()),
        "session_end" => session_end(&read_stdin_payload()),
        "post_tool_failure" => post_tool_failure(&read_stdin_payload()),
        "session_start" => session_start(),
        other => {
            eprintln!("unknown hook: {other}");
            return ExitCode::FAILURE;
        }
    }
    ExitCode::SUCCESS
}

fn read_stdin_payload() -> Map<String, Value> {
    let mut raw = String::new();
    let _ = std::io::stdin().read_to_string(&mut raw);
    payload_from(&raw)
}

/// Parsed hook payload; malformed or non-object JSON yields an empty map.
fn payload_from(raw: &str) -> Map<String, Value> {
    match serde_json::from_str::<Value>(raw) {
        Ok(Value::Object(map)) => map,
        _ => Map::new(),
    }
}

fn cairn_exe() -> String {
    std::env::current_exe()
        .map(|p| p.to_string_lossy().into_owned())
        .unwrap_or_else(|_| "cairn".to_string())
}

/// Run a cairn command: its stdout on success, else one `error: ...` line.
fn run_cairn(args: &[&str], timeout: Duration, stdin: Option<&str>) -> String {
    let mut command = std::process::Command::new(cairn_exe());
    command.args(args);
    match spawn_with_timeout(&mut command, timeout, stdin) {
        Ok(out) if out.code == Some(0) => out.stdout,
        Ok(out) => {
            let detail = match out.stderr.trim() {
                "" => out.stdout.trim().to_string(),
                err => err.to_string(),
            };
            let suffix = if detail.is_empty() {
                String::new()
            } else {
                format!(": {detail}")
            };
            format!("error: cairn exited {}{suffix}", out.code.unwrap_or(-1))
        }
        Err(e) => format!("error: {e}"),
    }
}

fn log_human(message: &str) {
    eprintln!("{}", message.trim_end_matches('\n'));
}

// --- post_edit -----------------------------------------------------------------

fn post_edit(data: &Map<String, Value>) {
    let file_path = edited_file_path(data);
    let mut args = vec!["update"];
    if let Some(path) = file_path.as_deref() {
        args.extend(["--file", path]);
    }
    let out = run_cairn(&args, Duration::from_secs(30), None);
    if file_path.is_some() {
        run_cairn(&["validate-paths", "--mark"], Duration::from_secs(30), None);
    }
    if !out.is_empty() {
        log_human(&out);
    }
}

/// The edited path from the common payload shapes.
fn edited_file_path(data: &Map<String, Value>) -> Option<String> {
    let tool_input = data.get("tool_input").and_then(Value::as_object);
    let from_input = |key: &str| tool_input.and_then(|t| t.get(key)).and_then(Value::as_str);
    from_input("file_path")
        .or_else(|| from_input("path"))
        .or_else(|| data.get("file_path").and_then(Value::as_str))
        .filter(|s| !s.is_empty())
        .map(str::to_string)
}

// --- session_end ---------------------------------------------------------------

fn session_end(data: &Map<String, Value>) {
    let path = data
        .get("transcript_path")
        .and_then(Value::as_str)
        .unwrap_or("");
    let messages = if path.is_empty() {
        Vec::new()
    } else {
        std::fs::read_to_string(path)
            .map(|text| transcript_messages(&text))
            .unwrap_or_default()
    };
    if messages.is_empty() {
        log_human("(no transcript; nothing to capture)");
        return;
    }
    let session_id = match data.get("session_id") {
        Some(Value::String(s)) if !s.is_empty() => s.clone(),
        Some(Value::Number(n)) => n.to_string(),
        _ => "claude".to_string(),
    };
    // The transcript rides stdin: a long session exceeds ARG_MAX as an argv.
    let transcript = Value::Array(messages).to_string();
    let out = run_cairn(
        &[
            "memory",
            "capture",
            "--session-transcript-stdin",
            "--session-id",
            &session_id,
        ],
        Duration::from_secs(60),
        Some(&transcript),
    );
    log_human(if out.is_empty() {
        "(no memories captured)"
    } else {
        &out
    });
}

/// Text-only user/assistant turns of a JSONL transcript, last `TRANSCRIPT_TAIL`.
/// Partial or foreign lines are skipped: the file may be mid-write.
fn transcript_messages(text: &str) -> Vec<Value> {
    let mut out = Vec::new();
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let Ok(Value::Object(record)) = serde_json::from_str::<Value>(line) else {
            continue;
        };
        if !matches!(
            record.get("type").and_then(Value::as_str),
            Some("user" | "assistant")
        ) {
            continue;
        }
        let Some(Value::Object(message)) = record.get("message") else {
            continue;
        };
        let text = match message.get("content") {
            Some(Value::String(s)) => s.clone(),
            Some(Value::Array(blocks)) => blocks
                .iter()
                .filter_map(Value::as_object)
                .filter(|b| b.get("type").and_then(Value::as_str) == Some("text"))
                .map(|b| b.get("text").and_then(Value::as_str).unwrap_or(""))
                .collect::<Vec<_>>()
                .join("\n"),
            _ => String::new(),
        };
        if text.is_empty() {
            continue;
        }
        let role = message.get("role").and_then(Value::as_str).unwrap_or("");
        out.push(json!({"role": role, "content": text}));
    }
    let drop = out.len().saturating_sub(TRANSCRIPT_TAIL);
    out.split_off(drop)
}

// --- session_start -------------------------------------------------------------

fn session_start() {
    let out = run_cairn(
        &["memory", "digest", "--limit", "5"],
        Duration::from_secs(15),
        None,
    );
    match digest_channel(&out) {
        Channel::Context => print!("{out}"),
        Channel::Diagnostic => log_human(&out),
        Channel::Silent => {}
    }
}

#[derive(Debug, PartialEq)]
enum Channel {
    Context,
    Diagnostic,
    Silent,
}

/// Where the digest output goes: the agent's context, stderr, or nowhere.
fn digest_channel(out: &str) -> Channel {
    let text = out.trim();
    if text.is_empty() || text == EMPTY_DIGEST {
        Channel::Silent
    } else if text.starts_with("error: ") {
        Channel::Diagnostic
    } else {
        Channel::Context
    }
}

// --- post_tool_failure ---------------------------------------------------------

fn post_tool_failure(data: &Map<String, Value>) {
    let Some(args) = failure_record_args(data) else {
        return;
    };
    let mut command = std::process::Command::new(cairn_exe());
    command
        .args(&args)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    // Detached: the capture must never delay the agent's next prompt.
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW);
    }
    let _ = command.spawn();
}

/// `memory record` argv for a tool failure; None when nothing is captured.
/// Redaction runs before truncation so a split secret cannot survive.
fn failure_record_args(data: &Map<String, Value>) -> Option<Vec<String>> {
    if truthy(data.get("is_interrupt")) || truthy(data.get("isInterrupt")) {
        return None;
    }
    let tool_name = ["tool_name", "toolName"]
        .iter()
        .find_map(|key| data.get(*key).filter(|v| truthy(Some(v))))
        .map(scalar_text)
        .unwrap_or_else(|| "unknown".to_string());
    let tool_input = ["tool_input", "toolArgs"]
        .iter()
        .find_map(|key| data.get(*key).filter(|v| truthy(Some(v))))
        .cloned()
        .unwrap_or_else(|| json!({}));
    let error = data
        .get("error")
        .filter(|v| truthy(Some(v)))
        .map(scalar_text)?;

    let cap = |text: String| text.chars().take(FAILURE_FIELD_CHARS).collect::<String>();
    let safe_input = cap(strip_private_data(&crate::json_compat::dumps_compact(
        &tool_input,
    )));
    let safe_error = cap(strip_private_data(&error));
    let body = format!(
        "{tool_name} failed during use.\n\nInput: {safe_input}\n\nError: {safe_error}\n\n\
         Why: Auto-captured by post_tool_failure hook.\n\
         How to apply: Check if this error pattern is recurring before using this tool \
         the same way again."
    );
    Some(vec![
        "memory".into(),
        "record".into(),
        "mistake".into(),
        format!("Tool failure: {tool_name}"),
        "--body".into(),
        body,
        "--confidence".into(),
        "0.3".into(),
        "--recurrence-key".into(),
        failure_signature(&tool_name, &safe_error),
    ])
}

/// Truthiness of a JSON value (null, false, zero, and empty are false).
fn truthy(value: Option<&Value>) -> bool {
    match value {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64().is_some_and(|f| f != 0.0),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

fn scalar_text(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn non_object_payloads_degrade_to_empty() {
        for raw in ["null", "[]", "\"text\"", "42", "{not json", ""] {
            assert!(payload_from(raw).is_empty(), "{raw}");
        }
        assert_eq!(payload_from(r#"{"a": 1}"#)["a"], json!(1));
    }

    #[test]
    fn edited_path_comes_from_the_common_payload_shapes() {
        let shapes = [
            json!({"tool_input": {"file_path": "a.py"}}),
            json!({"tool_input": {"path": "a.py"}}),
            json!({"file_path": "a.py"}),
        ];
        for shape in shapes {
            let data = shape.as_object().expect("object");
            assert_eq!(edited_file_path(data).as_deref(), Some("a.py"), "{shape}");
        }
        let empty = json!({"tool_input": {"file_path": ""}, "tool_input2": 1});
        assert_eq!(edited_file_path(empty.as_object().expect("object")), None);
        assert_eq!(edited_file_path(&Map::new()), None);
    }

    #[test]
    fn transcript_keeps_text_only_user_and_assistant_turns() {
        let records = [
            json!({"type": "user", "message": {"role": "user", "content": "fix the flaky test"}}),
            json!({"type": "attachment", "message": {"role": "user", "content": "noise"}}),
            json!({"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "thinking", "text": "private reasoning"},
                {"type": "text", "text": "root cause was init ordering"},
                {"type": "tool_use", "name": "Bash", "input": {}},
            ]}}),
            json!({"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_result", "content": "ok"}]}}),
            json!({"type": "user", "message": {"role": "user", "content": ""}}),
            json!({"type": "system"}),
            json!([1, 2, 3]),
        ];
        let mut text: String = records.iter().map(|r| format!("{r}\n")).collect();
        text.push_str("{broken json\n");
        assert_eq!(
            transcript_messages(&text),
            vec![
                json!({"role": "user", "content": "fix the flaky test"}),
                json!({"role": "assistant", "content": "root cause was init ordering"}),
            ]
        );
    }

    #[test]
    fn transcript_keeps_the_last_eighty_turns() {
        let text: String = (0..90)
            .map(|i| {
                format!(
                    "{}\n",
                    json!({"type": "user", "message": {"role": "user", "content": format!("m{i}")}})
                )
            })
            .collect();
        let kept = transcript_messages(&text);
        assert_eq!(kept.len(), 80);
        assert_eq!(kept[0]["content"], json!("m10"));
        assert_eq!(kept[79]["content"], json!("m89"));
    }

    #[test]
    fn digest_output_routes_to_context_stderr_or_nowhere() {
        assert_eq!(
            digest_channel("  [0.92, refs-verified=1.0] Always run pre-commit\n"),
            Channel::Context
        );
        for silent in ["", "\n", "No tribal memories yet.\n"] {
            assert_eq!(digest_channel(silent), Channel::Silent, "{silent:?}");
        }
        assert_eq!(digest_channel("error: timed out\n"), Channel::Diagnostic);
    }

    #[test]
    fn failure_record_carries_the_recurrence_key_of_the_redacted_error() {
        let payload = json!({
            "tool_name": "Bash",
            "tool_input": {"command": "pytest -q"},
            "error": "Traceback: /opt/venv/lib/site.py:1 boom pid 4242",
        });
        let args = failure_record_args(payload.as_object().expect("object")).expect("args");
        assert_eq!(&args[..3], ["memory", "record", "mistake"]);
        assert_eq!(args[3], "Tool failure: Bash");
        let key = args
            .iter()
            .position(|a| a == "--recurrence-key")
            .expect("key flag");
        assert_eq!(args[key + 1], "086e3a1aee6ab843");
        let body = &args[args.iter().position(|a| a == "--body").expect("body") + 1];
        assert!(
            body.contains("Input: {\"command\": \"pytest -q\"}"),
            "{body}"
        );
    }

    #[test]
    fn failure_record_redacts_before_truncating() {
        let secret = format!("sk-ant-{}", "a".repeat(30));
        let error = format!("{}{secret}", "x".repeat(3990));
        let payload = json!({"tool_name": "Bash", "error": error});
        let args = failure_record_args(payload.as_object().expect("object")).expect("args");
        let body = &args[args.iter().position(|a| a == "--body").expect("body") + 1];
        assert!(
            !body.contains("sk-ant"),
            "a split secret must not survive unredacted"
        );
    }

    #[test]
    fn failure_record_skips_interrupts_and_errorless_payloads() {
        let interrupted = json!({"tool_name": "Bash", "error": "boom", "is_interrupt": true});
        assert!(failure_record_args(interrupted.as_object().expect("object")).is_none());
        let camel = json!({"toolName": "Bash", "error": "boom", "isInterrupt": true});
        assert!(failure_record_args(camel.as_object().expect("object")).is_none());
        let silent = json!({"tool_name": "Bash", "error": ""});
        assert!(failure_record_args(silent.as_object().expect("object")).is_none());
        assert!(failure_record_args(&Map::new()).is_none());
        let camel_ok = json!({"toolName": "Read", "toolArgs": {"p": 1}, "error": "nope"});
        let args = failure_record_args(camel_ok.as_object().expect("object")).expect("args");
        assert_eq!(args[3], "Tool failure: Read");
    }

    #[test]
    fn unknown_entrypoint_fails() {
        assert_eq!(run("no_such_hook"), ExitCode::FAILURE);
    }
}
