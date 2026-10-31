//! `install-agents` / `uninstall-agents` CLI flow.

use std::collections::BTreeSet;
use std::io::{BufRead, IsTerminal, Write as _};
use std::path::Path;
use std::process::ExitCode;

use cairn_daemon::lifecycle as lc;

use crate::agent_install;
use crate::display;

pub struct InstallAgentsArgs {
    pub clients: Vec<String>,
    pub workspace: Option<String>,
    pub scope: Option<String>,
    pub force: bool,
    pub dry_run: bool,
    pub git_hooks: bool,
    pub yes: bool,
}

pub struct UninstallAgentsArgs {
    pub clients: Vec<String>,
    pub scope: String,
    pub workspace: Option<String>,
}

fn client_choices() -> Vec<String> {
    agent_install::CLIENTS
        .iter()
        .map(|c| c.to_string())
        .chain(std::iter::once("all".to_string()))
        .collect()
}

/// Install-agents: wire cairn into AI coding clients; prompts unless --client/--yes.
pub fn run_install(args: InstallAgentsArgs) -> ExitCode {
    for client in &args.clients {
        if !client_choices().contains(client) {
            display::error(&format!(
                "Invalid value \"{client}\" for \"--client <CLIENTS>\": invalid selection"
            ));
            return ExitCode::FAILURE;
        }
    }
    let ws = crate::store_paths::resolve_workspace(args.workspace.as_deref().map(Path::new));
    let detections = agent_install::detect_clients(&ws);
    let installed_map = agent_install::check_installed(&ws);

    println!("Client detection:");
    for d in &detections {
        let mark = if d.detected { "✓" } else { " " };
        let inst = if installed_map.get(d.client).copied().unwrap_or(false) {
            "installed"
        } else {
            "not installed"
        };
        let inst_mark = if inst == "installed" { "✓" } else { " " };
        println!(
            "  [{mark}] {:16} {:30}  cairn: [{inst_mark}] {inst}",
            d.client, d.reason
        );
    }
    println!();

    let mut scope = args.scope.clone();
    let target_clients: Option<Vec<String>> = if !args.clients.is_empty() {
        Some(args.clients.clone())
    } else {
        let detected: Vec<&str> = detections
            .iter()
            .filter(|d| d.detected)
            .map(|d| d.client)
            .collect();
        let not_yet: Vec<String> = detected
            .iter()
            .filter(|c| !installed_map.get(**c).copied().unwrap_or(false))
            .map(|c| c.to_string())
            .collect();

        if detected.is_empty() {
            println!("No clients detected. Use --client <name> or --client all to force.");
            return ExitCode::SUCCESS;
        }
        if not_yet.is_empty() {
            println!("All detected clients already have cairn installed.");
            println!("Use --force to overwrite, or --client <name> to target a specific one.");
            return ExitCode::SUCCESS;
        }
        if args.yes || !std::io::stdin().is_terminal() {
            println!(
                "Installing for: {} (detected, not yet installed)",
                not_yet.join(", ")
            );
            Some(not_yet)
        } else {
            match prompt_clients(&detected, &not_yet) {
                PromptOutcome::Selected(list) => Some(list),
                PromptOutcome::Empty => {
                    println!("Nothing selected. Use --client <name> to target a specific client.");
                    return ExitCode::SUCCESS;
                }
                PromptOutcome::Aborted => {
                    println!("Aborted.");
                    return ExitCode::SUCCESS;
                }
            }
        }
    };

    // Interactive scope prompt only when --scope wasn't passed.
    if target_clients.is_some()
        && args.clients.is_empty()
        && scope.is_none()
        && std::io::stdin().is_terminal()
        && !args.yes
    {
        match prompt_scope() {
            Some(s) => scope = Some(s),
            None => {
                println!("\nAborted.");
                return ExitCode::SUCCESS;
            }
        }
    }
    let scope = scope.unwrap_or_else(|| "workspace".to_string());

    let include_git = args.git_hooks;
    println!("Scope: {scope}");
    let Ok(report) = agent_install::install(
        &ws,
        target_clients.as_deref(),
        args.force,
        args.dry_run,
        include_git,
        &scope,
    ) else {
        return ExitCode::FAILURE;
    };

    // Daemon gate: probe healthz so registrations are traced to a live daemon.
    let url = agent_install::daemon_mcp_url();
    let (port, host) = lc::daemon_bind();
    println!("MCP server: {url} (URL + key registrations)");
    if !lc::healthz_responds(
        port.unwrap_or(lc::DEFAULT_PORT),
        host.as_deref().unwrap_or(lc::DEFAULT_HOST),
    ) {
        println!("  note: daemon at {url} unreachable — run `cairn connect` to start it.");
    }
    println!();

    let targeted: BTreeSet<&str> = report.results.iter().map(|r| r.client.as_str()).collect();
    if targeted.is_empty() {
        println!("Nothing to install. Use --client <name> or --client all.");
        return ExitCode::SUCCESS;
    }

    println!(
        "Installing for: {}{}",
        targeted
            .iter()
            .map(|c| c.to_string())
            .collect::<Vec<_>>()
            .join(", "),
        if args.dry_run { " (dry-run)" } else { "" }
    );
    println!();
    for res in &report.results {
        println!("=== {} ===", res.client);
        if !res.written.is_empty() {
            println!("  wrote:   {}", res.written.len());
            for p in res.written.iter().take(8) {
                println!("    {p}");
            }
            if res.written.len() > 8 {
                println!("    ... and {} more", res.written.len() - 8);
            }
        }
        if !res.skipped.is_empty() {
            println!(
                "  skipped: {} (use --force to overwrite)",
                res.skipped.len()
            );
        }
        for note in &res.notes {
            println!("  note: {note}");
        }
        if res.verification_status == "pass" {
            println!("  verify: PASS ({})", res.verification_detail);
        } else if res.verification_status == "fail" {
            println!("  verify: FAIL — {}", res.verification_detail);
        }
        println!();
    }

    if let Some(cross) = &report.cross_tool {
        println!("=== cross-tool (.agents/) ===");
        println!("  wrote: {}", cross.written.len());
    }
    if !report.git_hooks_installed.is_empty() {
        println!();
        println!(
            "Git hooks installed in: {}",
            report.git_hooks_installed.join(", ")
        );
    }
    println!();
    println!(
        "Done. Registrations point at the shared daemon; run `cairn connect` if it is not running."
    );
    ExitCode::SUCCESS
}

enum PromptOutcome {
    Selected(Vec<String>),
    Empty,
    Aborted,
}

fn prompt_clients(detected: &[&str], not_yet: &[String]) -> PromptOutcome {
    let items: Vec<crate::picker::PickerItem> = detected
        .iter()
        .map(|client| crate::picker::PickerItem {
            label: client.to_string(),
            preselected: not_yet.iter().any(|c| c == client),
        })
        .collect();
    let hint = "(click/space toggle · a all · numbers work too · enter confirm · esc abort)";
    match crate::picker::pick("Select clients to install cairn for:", &items, hint) {
        Some(crate::picker::PickOutcome::Confirmed(indexes)) => {
            let picked: Vec<String> = indexes.iter().map(|i| detected[*i].to_string()).collect();
            if picked.is_empty() {
                return PromptOutcome::Empty;
            }
            println!("Selected: {}", picked.join(", "));
            PromptOutcome::Selected(picked)
        }
        Some(crate::picker::PickOutcome::Aborted) => PromptOutcome::Aborted,
        None => prompt_clients_line(detected, not_yet),
    }
}

/// Line-based fallback for terminals without interactive rendering.
fn prompt_clients_line(detected: &[&str], not_yet: &[String]) -> PromptOutcome {
    println!("Select clients to install cairn for:");
    for (index, client) in detected.iter().enumerate() {
        let mark = if not_yet.contains(&client.to_string()) {
            "●"
        } else {
            "○"
        };
        println!("  {mark} {}. {client}", index + 1);
    }
    print!("(numbers or names, comma-separated · 'a' all · enter confirm): ");
    let _ = std::io::stdout().flush();
    let Some(line) = read_line() else {
        return PromptOutcome::Aborted;
    };
    let text = line.trim();
    if text.is_empty() {
        return PromptOutcome::Empty;
    }
    let mut picked: Vec<String> = Vec::new();
    for token in text.split(',') {
        let token = token.trim();
        if token.eq_ignore_ascii_case("a") || token.eq_ignore_ascii_case("all") {
            return PromptOutcome::Selected(detected.iter().map(|c| c.to_string()).collect());
        }
        if let Ok(n) = token.parse::<usize>()
            && (1..=detected.len()).contains(&n)
        {
            picked.push(detected[n - 1].to_string());
        } else if detected.contains(&token) {
            picked.push(token.to_string());
        }
    }
    if picked.is_empty() {
        PromptOutcome::Empty
    } else {
        PromptOutcome::Selected(picked)
    }
}

fn prompt_scope() -> Option<String> {
    println!();
    println!("Config scope:");
    println!("  workspace  — write to ./.claude/, ./.cursor/ etc. (per-project)");
    println!("  global     — write to ~/.claude/, ~/.cursor/ etc. (all projects inherit)");
    loop {
        print!("Scope [workspace]: ");
        let _ = std::io::stdout().flush();
        let line = read_line()?;
        match line.trim() {
            "" => return Some("workspace".to_string()),
            "workspace" => return Some("workspace".to_string()),
            "global" => return Some("global".to_string()),
            _ => println!(
                "Error: '{0}' is not one of 'workspace', 'global'.",
                line.trim()
            ),
        }
    }
}

fn read_line() -> Option<String> {
    let mut line = String::new();
    match std::io::stdin().lock().read_line(&mut line) {
        Ok(0) | Err(_) => None,
        Ok(_) => Some(line),
    }
}

/// Uninstall-agents: remove cairn entries from AI client configs, idempotently.
pub fn run_uninstall(args: UninstallAgentsArgs) -> ExitCode {
    for client in &args.clients {
        if !client_choices().contains(client) {
            display::error(&format!(
                "Invalid value \"{client}\" for \"--client <CLIENTS>\": invalid selection"
            ));
            return ExitCode::FAILURE;
        }
    }
    let ws = crate::store_paths::resolve_workspace(args.workspace.as_deref().map(Path::new));
    let Ok(report) = agent_install::uninstall(
        &ws,
        if args.clients.is_empty() {
            None
        } else {
            Some(&args.clients)
        },
        &args.scope,
    ) else {
        return ExitCode::FAILURE;
    };

    let targeted: BTreeSet<&str> = report.results.iter().map(|r| r.client.as_str()).collect();
    if targeted.is_empty() {
        println!("No clients detected to uninstall from. Use --client <name> or --client all.");
        return ExitCode::SUCCESS;
    }

    for res in &report.results {
        println!("=== {} ===", res.client);
        for p in &res.written {
            println!("  {p}");
        }
        for p in &res.skipped {
            println!("  skipped: {p}");
        }
        for note in &res.notes {
            println!("  note: {note}");
        }
        if res.written.is_empty() && res.skipped.is_empty() && res.notes.is_empty() {
            println!("  (nothing to remove)");
        }
        println!();
    }

    if let Some(cross) = &report.cross_tool {
        println!("=== cross-tool (.agents/) ===");
        for p in &cross.written {
            println!("  {p}");
        }
        for p in &cross.skipped {
            println!("  skipped: {p}");
        }
    }

    println!();
    println!("Done. Git hooks: run `cairn hooks uninstall` separately if needed.");
    ExitCode::SUCCESS
}
