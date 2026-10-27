//! `cli-smoke`: replays a checked-in CLI command list against a pinned fixture and DB.

use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode, Stdio};

use clap::Parser;
use serde_json::{Value, json};

use crate::audit_status::py_json_pretty;

#[derive(Parser)]
#[command(
    name = "cli-smoke",
    about = "Replay a checked-in CLI command list against a pinned fixture and DB."
)]
struct Args {
    #[arg(help = "command-list file, one command per line")]
    list: PathBuf,
    #[arg(long, required = true, help = "cwd for every command")]
    fixture: PathBuf,
    #[arg(long, required = true, help = "CAIRN_DB pinned for every command")]
    db: PathBuf,
    #[arg(long, help = "emit a command/result table as JSON")]
    json: bool,
}

fn load_commands(text: &str) -> Vec<String> {
    text.lines()
        .map(str::trim)
        .filter(|l| !l.is_empty() && !l.starts_with('#'))
        .map(str::to_owned)
        .collect()
}

fn pinned_env(db: &Path) -> Vec<(std::ffi::OsString, std::ffi::OsString)> {
    let mut env: Vec<_> = std::env::vars_os()
        .filter(|(k, _)| !k.to_string_lossy().starts_with("CAIRN_"))
        .collect();
    env.push(("CAIRN_DB".into(), db.as_os_str().to_owned()));
    env.push(("CAIRN_TELEMETRY".into(), "off".into()));
    env
}

/// POSIX shell word splitting; `None` on an unterminated quote or trailing backslash.
fn shlex_split(line: &str) -> Option<Vec<String>> {
    let mut words = Vec::new();
    let mut cur = String::new();
    let mut in_word = false;
    let mut chars = line.chars();
    while let Some(c) = chars.next() {
        match c {
            c if c.is_whitespace() => {
                if in_word {
                    words.push(std::mem::take(&mut cur));
                    in_word = false;
                }
            }
            '\'' => {
                in_word = true;
                loop {
                    match chars.next()? {
                        '\'' => break,
                        c => cur.push(c),
                    }
                }
            }
            '"' => {
                in_word = true;
                loop {
                    match chars.next()? {
                        '"' => break,
                        '\\' => match chars.next()? {
                            c @ ('"' | '\\' | '$' | '`') => cur.push(c),
                            '\n' => {}
                            c => {
                                cur.push('\\');
                                cur.push(c);
                            }
                        },
                        c => cur.push(c),
                    }
                }
            }
            '\\' => {
                in_word = true;
                cur.push(chars.next()?);
            }
            c => {
                in_word = true;
                cur.push(c);
            }
        }
    }
    if in_word {
        words.push(cur);
    }
    Some(words)
}

fn exit_code_of(status: std::process::ExitStatus) -> i32 {
    #[cfg(unix)]
    {
        use std::os::unix::process::ExitStatusExt;
        if let Some(signal) = status.signal() {
            return -signal;
        }
    }
    status.code().unwrap_or(-1)
}

fn run_smoke(commands: &[String], fixture: &Path, db: &Path, as_json: bool) -> Result<u8, String> {
    let env = pinned_env(db);
    let mut rows: Vec<Value> = Vec::new();
    for command in commands {
        if !as_json {
            println!("::group::{command}");
        }
        let argv = shlex_split(command)
            .filter(|a| !a.is_empty())
            .ok_or_else(|| format!("cannot parse command: {command}"))?;
        let mut cmd = Command::new(&argv[0]);
        cmd.args(&argv[1..])
            .current_dir(fixture)
            .env_clear()
            .envs(env.iter().cloned());
        let (code, stdout, stderr) = if as_json {
            let output = cmd
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                .output()
                .map_err(|e| format!("{command}: {e}"))?;
            (exit_code_of(output.status), output.stdout, output.stderr)
        } else {
            let status = cmd.status().map_err(|e| format!("{command}: {e}"))?;
            (exit_code_of(status), Vec::new(), Vec::new())
        };
        let mut row = json!({"command": command, "exit_code": code});
        if code != 0 {
            if as_json {
                row["stdout"] = String::from_utf8_lossy(&stdout).into_owned().into();
                row["stderr"] = String::from_utf8_lossy(&stderr).into_owned().into();
            }
            rows.push(row);
            if as_json {
                let report = json!({"ok": false, "failed": command, "commands": rows});
                println!("{}", py_json_pretty(&report, false));
            } else {
                println!("::endgroup::");
                println!("::error::smoke command failed (exit {code}): {command}");
            }
            return Ok(1);
        }
        rows.push(row);
        if !as_json {
            println!("::endgroup::");
        }
    }
    if as_json {
        let report = json!({"ok": true, "failed": Value::Null, "commands": rows});
        println!("{}", py_json_pretty(&report, false));
    } else {
        println!("smoke: {0}/{0} commands passed", rows.len());
    }
    Ok(0)
}

pub fn run(args: &[String]) -> ExitCode {
    let parsed = match Args::try_parse_from(
        std::iter::once("cli-smoke".to_string()).chain(args.iter().cloned()),
    ) {
        Ok(parsed) => parsed,
        Err(e) => {
            let _ = e.print();
            return ExitCode::from(e.exit_code() as u8);
        }
    };
    if !parsed.list.is_file() {
        eprintln!("error: command list not found: {}", parsed.list.display());
        return ExitCode::from(2);
    }
    if !parsed.fixture.is_dir() {
        eprintln!(
            "error: fixture directory not found: {}",
            parsed.fixture.display()
        );
        return ExitCode::from(2);
    }
    let text = std::fs::read(&parsed.list)
        .map(|b| String::from_utf8_lossy(&b).into_owned())
        .unwrap_or_default();
    let commands = load_commands(&text);
    if commands.is_empty() {
        eprintln!("error: no commands found in {}", parsed.list.display());
        return ExitCode::from(2);
    }
    match run_smoke(&commands, &parsed.fixture, &parsed.db, parsed.json) {
        Ok(code) => ExitCode::from(code),
        Err(e) => {
            eprintln!("error: {e}");
            ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn command_list_skips_blanks_and_comments() {
        assert_eq!(
            load_commands("# c\n\n  cairn stats \n  # x\ncairn def X\n"),
            ["cairn stats", "cairn def X"]
        );
    }

    #[test]
    fn shlex_split_honors_quotes_and_escapes() {
        assert_eq!(
            shlex_split(r#"cairn search 'URL *' "a b" c\ d"#).unwrap(),
            ["cairn", "search", "URL *", "a b", "c d"]
        );
        assert!(shlex_split("cairn 'oops").is_none());
    }

    #[test]
    fn stops_at_first_failure_with_pinned_env() {
        let dir = std::env::temp_dir();
        let commands = ["true".to_string(), "false".to_string(), "true".to_string()];
        assert_eq!(
            run_smoke(&commands, &dir, &dir.join("x.kg"), true).unwrap(),
            1
        );
        assert_eq!(
            run_smoke(&commands[..1], &dir, &dir.join("x.kg"), true).unwrap(),
            0
        );
    }
}
