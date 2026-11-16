//! `install-agents` / `uninstall-agents` CLI flow.

use std::collections::{BTreeMap, BTreeSet};
use std::io::{BufRead, IsTerminal, Write as _};
use std::path::Path;
use std::process::ExitCode;

use cairn_daemon::lifecycle as lc;

use crate::agent_install::{self, Detection};
use crate::display;
use crate::picker::{self, PickOneOutcome, PickOutcome, PickerItem};

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
    pub scope: Option<String>,
    pub workspace: Option<String>,
}

fn client_choices() -> Vec<String> {
    agent_install::CLIENTS
        .iter()
        .map(|c| c.to_string())
        .chain(std::iter::once("all".to_string()))
        .collect()
}

fn validate_clients(clients: &[String]) -> bool {
    for client in clients {
        if !client_choices().contains(client) {
            display::error(&format!(
                "Invalid value \"{client}\" for \"--client <CLIENTS>\": invalid selection"
            ));
            return false;
        }
    }
    true
}

/// Install-agents: wire cairn into AI coding clients; prompts unless --client/--yes.
pub fn run_install(args: InstallAgentsArgs) -> ExitCode {
    if !validate_clients(&args.clients) {
        return ExitCode::FAILURE;
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

    let interactive = !args.yes && std::io::stdin().is_terminal();
    let mut scope = args.scope.clone();
    let target_clients: Option<Vec<String>> = if !args.clients.is_empty() {
        Some(args.clients.clone())
    } else {
        let detected_rows: Vec<&Detection> = detections.iter().filter(|d| d.detected).collect();
        let detected: Vec<&str> = detected_rows.iter().map(|d| d.client).collect();
        let not_yet: Vec<String> = detected
            .iter()
            .filter(|c| !installed_map.get(**c).copied().unwrap_or(false))
            .map(|c| c.to_string())
            .collect();

        if detected.is_empty() {
            println!("No clients detected. Use --client <name> or --client all to force.");
            return ExitCode::SUCCESS;
        }
        if !interactive {
            if not_yet.is_empty() {
                println!("All detected clients already have cairn installed.");
                println!("Use --force to overwrite, or --client <name> to target a specific one.");
                return ExitCode::SUCCESS;
            }
            println!(
                "Installing for: {} (detected, not yet installed)",
                not_yet.join(", ")
            );
            Some(not_yet)
        } else {
            match prompt_clients(&detected_rows, &installed_map, &not_yet) {
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

    if target_clients.is_some() && args.clients.is_empty() && scope.is_none() && interactive {
        match prompt_scope() {
            Some(s) => scope = Some(s),
            None => {
                println!("Aborted.");
                return ExitCode::SUCCESS;
            }
        }
    }
    let scope = scope.unwrap_or_else(|| "workspace".to_string());

    let mut force = args.force;
    let mut include_git = args.git_hooks;
    if let Some(targets) = &target_clients
        && args.clients.is_empty()
        && interactive
        && !args.force
        && !args.git_hooks
    {
        let any_installed = targets
            .iter()
            .any(|c| installed_map.get(c).copied().unwrap_or(false));
        if !prompt_install_options(&mut force, &mut include_git, any_installed) {
            println!("Aborted.");
            return ExitCode::SUCCESS;
        }
    }

    println!("Scope: {scope}");
    let Ok(report) = agent_install::install(
        &ws,
        target_clients.as_deref(),
        force,
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
        let loc = if scope == "global" {
            "~/.agents/"
        } else {
            ".agents/"
        };
        println!("=== cross-tool ({loc}) ===");
        println!("  wrote: {}", cross.written.len());
        if !cross.skipped.is_empty() {
            println!(
                "  skipped: {} (use --force to overwrite)",
                cross.skipped.len()
            );
        }
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

fn prompt_clients(
    detected: &[&Detection],
    installed_map: &BTreeMap<String, bool>,
    not_yet: &[String],
) -> PromptOutcome {
    let default_all = not_yet.is_empty();
    let items: Vec<PickerItem> = detected
        .iter()
        .map(|d| {
            let inst = if installed_map.get(d.client).copied().unwrap_or(false) {
                "installed"
            } else {
                "not installed"
            };
            let preselected = default_all || not_yet.iter().any(|c| c == d.client);
            PickerItem::new(
                d.client,
                format!("{} · cairn: {inst}", d.reason),
                preselected,
            )
        })
        .collect();
    let hint = "(↑/↓ move · space/click toggle · a all · 1-9 toggle · enter confirm · esc abort)";
    match picker::pick("Select clients to install cairn for:", &items, hint) {
        Some(PickOutcome::Confirmed(indexes)) => {
            let picked: Vec<String> = indexes
                .iter()
                .map(|i| detected[*i].client.to_string())
                .collect();
            if picked.is_empty() {
                return PromptOutcome::Empty;
            }
            println!("Selected: {}", picked.join(", "));
            PromptOutcome::Selected(picked)
        }
        Some(PickOutcome::Aborted) => PromptOutcome::Aborted,
        None => {
            let names: Vec<&str> = detected.iter().map(|d| d.client).collect();
            let fallback_defaults = if default_all {
                names.iter().map(|c| c.to_string()).collect::<Vec<_>>()
            } else {
                not_yet.to_vec()
            };
            prompt_clients_line(
                "Select clients to install cairn for:",
                &names,
                &fallback_defaults,
            )
        }
    }
}

/// Line-based fallback for terminals without interactive rendering.
fn prompt_clients_line(header: &str, detected: &[&str], preselected: &[String]) -> PromptOutcome {
    println!("{header}");
    for (index, client) in detected.iter().enumerate() {
        let mark = if preselected.contains(&client.to_string()) {
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
    let items = [
        PickerItem::new(
            "workspace",
            "write to ./.agents/, ./.claude/, ./.cursor/ etc. (per-project)",
            true,
        ),
        PickerItem::new(
            "global",
            "write to ~/.agents/, ~/.claude/, ~/.cursor/ etc. (all projects inherit)",
            false,
        ),
    ];
    let hint = "(↑/↓ move · 1-2 or enter confirm · esc abort)";
    match picker::pick_one("Select config scope:", &items, 0, hint) {
        Some(PickOneOutcome::Selected(idx)) => Some(items[idx].label.clone()),
        Some(PickOneOutcome::Aborted) => None,
        None => prompt_scope_line(),
    }
}

fn prompt_scope_line() -> Option<String> {
    println!();
    println!("Config scope:");
    println!("  1. workspace  — write to ./.agents/, ./.claude/, ./.cursor/ etc. (per-project)");
    println!(
        "  2. global     — write to ~/.agents/, ~/.claude/, ~/.cursor/ etc. (all projects inherit)"
    );
    loop {
        print!("Scope [workspace]: ");
        let _ = std::io::stdout().flush();
        let line = read_line()?;
        match line.trim() {
            "" | "1" | "workspace" => return Some("workspace".to_string()),
            "2" | "global" => return Some("global".to_string()),
            other => println!("Error: '{other}' is not one of 'workspace', 'global'."),
        }
    }
}

fn prompt_install_options(force: &mut bool, git_hooks: &mut bool, any_installed: bool) -> bool {
    let items = [
        PickerItem::new(
            "Overwrite existing cairn files (--force)",
            "refresh existing configs, skills, and hooks",
            any_installed,
        ),
        PickerItem::new(
            "Install git hooks",
            "post-commit + post-checkout incremental graph updates",
            false,
        ),
    ];
    let hint = "(↑/↓ move · space/click toggle · a all · 1-2 toggle · enter confirm · esc abort)";
    match picker::pick("Install options:", &items, hint) {
        Some(PickOutcome::Confirmed(indexes)) => {
            *force = indexes.contains(&0);
            *git_hooks = indexes.contains(&1);
            let mut chosen = Vec::new();
            if *force {
                chosen.push("force");
            }
            if *git_hooks {
                chosen.push("git-hooks");
            }
            if !chosen.is_empty() {
                println!("Options: {}", chosen.join(", "));
            }
            true
        }
        Some(PickOutcome::Aborted) => false,
        None => {
            if any_installed {
                *force = true;
            }
            true
        }
    }
}

fn prompt_uninstall_clients(
    detected: &[&Detection],
    installed_map: &BTreeMap<String, bool>,
) -> PromptOutcome {
    let items: Vec<PickerItem> = detected
        .iter()
        .map(|d| {
            let installed = installed_map.get(d.client).copied().unwrap_or(false);
            let inst = if installed {
                "installed"
            } else {
                "not installed"
            };
            PickerItem::new(d.client, format!("{} · cairn: {inst}", d.reason), installed)
        })
        .collect();
    let hint = "(↑/↓ move · space/click toggle · a all · 1-9 toggle · enter confirm · esc abort)";
    match picker::pick("Select clients to uninstall cairn from:", &items, hint) {
        Some(PickOutcome::Confirmed(indexes)) => {
            let picked: Vec<String> = indexes
                .iter()
                .map(|i| detected[*i].client.to_string())
                .collect();
            if picked.is_empty() {
                return PromptOutcome::Empty;
            }
            println!("Selected: {}", picked.join(", "));
            PromptOutcome::Selected(picked)
        }
        Some(PickOutcome::Aborted) => PromptOutcome::Aborted,
        None => {
            let names: Vec<&str> = detected.iter().map(|d| d.client).collect();
            let preselected: Vec<String> = names
                .iter()
                .filter(|c| installed_map.get(**c).copied().unwrap_or(false))
                .map(|c| c.to_string())
                .collect();
            prompt_clients_line(
                "Select clients to uninstall cairn from:",
                &names,
                &preselected,
            )
        }
    }
}

fn prompt_uninstall_scope() -> Option<String> {
    let items = [
        PickerItem::new(
            "workspace",
            "remove from ./.agents/, ./.claude/, ./.cursor/ etc.",
            true,
        ),
        PickerItem::new(
            "global",
            "remove from ~/.agents/, ~/.claude/, ~/.cursor/ etc.",
            false,
        ),
        PickerItem::new(
            "all",
            "remove from both workspace and global configs",
            false,
        ),
    ];
    let hint = "(↑/↓ move · 1-3 or enter confirm · esc abort)";
    match picker::pick_one("Select uninstall scope:", &items, 0, hint) {
        Some(PickOneOutcome::Selected(idx)) => Some(items[idx].label.clone()),
        Some(PickOneOutcome::Aborted) => None,
        None => Some("workspace".to_string()),
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
    if !validate_clients(&args.clients) {
        return ExitCode::FAILURE;
    }
    if let Some(scope) = &args.scope
        && !["workspace", "global", "all"].contains(&scope.as_str())
    {
        display::error(&format!(
            "Invalid value \"{scope}\" for \"--scope <SCOPE>\": expected workspace, global, or all"
        ));
        return ExitCode::FAILURE;
    }
    let ws = crate::store_paths::resolve_workspace(args.workspace.as_deref().map(Path::new));
    let interactive = args.clients.is_empty() && std::io::stdin().is_terminal();
    let target_clients: Option<Vec<String>> = if !args.clients.is_empty() {
        Some(args.clients.clone())
    } else if interactive {
        let detections = agent_install::detect_clients(&ws);
        let installed_map = agent_install::check_installed(&ws);
        let detected_rows: Vec<&Detection> = detections
            .iter()
            .filter(|d| d.detected || installed_map.get(d.client).copied().unwrap_or(false))
            .collect();
        if detected_rows.is_empty() {
            None
        } else {
            match prompt_uninstall_clients(&detected_rows, &installed_map) {
                PromptOutcome::Selected(list) => Some(list),
                PromptOutcome::Empty => {
                    println!("Nothing selected.");
                    return ExitCode::SUCCESS;
                }
                PromptOutcome::Aborted => {
                    println!("Aborted.");
                    return ExitCode::SUCCESS;
                }
            }
        }
    } else {
        None
    };

    let scope = if let Some(scope) = args.scope.clone() {
        scope
    } else if interactive {
        let Some(chosen) = prompt_uninstall_scope() else {
            println!("Aborted.");
            return ExitCode::SUCCESS;
        };
        println!("Scope: {chosen}");
        chosen
    } else {
        "workspace".to_string()
    };

    let Ok(report) = agent_install::uninstall(&ws, target_clients.as_deref(), &scope) else {
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
        let loc = match scope.as_str() {
            "global" => "~/.agents/",
            "all" => ".agents/ + ~/.agents/",
            _ => ".agents/",
        };
        println!("=== cross-tool ({loc}) ===");
        for p in &cross.written {
            println!("  {p}");
        }
        for p in &cross.skipped {
            println!("  skipped: {p}");
        }
        for note in &cross.notes {
            println!("  note: {note}");
        }
    }

    println!();
    println!("Done. Git hooks: run `cairn hooks uninstall` separately if needed.");
    ExitCode::SUCCESS
}
