//! System commands: health/lifecycle surface (doctor, serve, connect, installers).

use std::process::ExitCode;

use clap::Subcommand;

use crate::cmds_agents;
use crate::cmds_connect;
use crate::cmds_dashboard;
use crate::cmds_doctor;
use crate::cmds_lifecycle;
use crate::cmds_serve;

#[derive(Subcommand)]
pub enum SystemCommand {
    /// Run system health checks; exits 0 on PASS/WARN-only.
    Doctor {
        /// SQLite DB path.
        #[arg(long)]
        db: Option<String>,
        /// Emit JSON.
        #[arg(long)]
        json: bool,
        /// Repoint stale cairn-owned stdio/SSE registrations to the daemon URL.
        #[arg(long)]
        fix: bool,
    },
    /// System status and health across all layers.
    Status {
        /// SQLite DB path.
        #[arg(long)]
        db: Option<String>,
        /// Knowledge directory path.
        #[arg(long)]
        knowledge: Option<String>,
    },
    /// Manually re-index size/mtime-drifted files.
    Sync {
        #[arg(long)]
        workspace: Option<String>,
        /// SQLite DB path.
        #[arg(long)]
        db: Option<String>,
    },
    /// Report tool metrics by default; --builds/--quality/--contention/--tasks render telemetry sections (combinable).
    Metrics {
        /// SQLite DB path.
        #[arg(long)]
        db: Option<String>,
        /// Filter by tool name.
        #[arg(long = "tool")]
        tool_name: Option<String>,
        /// Emit JSON.
        #[arg(long)]
        json: bool,
        /// Recent build-run trend with the resolution mix.
        #[arg(long)]
        builds: bool,
        /// Retrieval quality: empty-result rate, truncations, backend mix.
        #[arg(long)]
        quality: bool,
        /// Lock-contention events grouped by site.
        #[arg(long)]
        contention: bool,
        /// LLM task-queue lifecycle: events by kind and task type.
        #[arg(long)]
        tasks: bool,
    },
    /// Print a redacted diagnostic bundle (versions, doctor checks, recent errors, config); never uploads.
    Report {
        /// SQLite DB path.
        #[arg(long)]
        db: Option<String>,
        /// Emit the bundle as JSON.
        #[arg(long)]
        json: bool,
        /// Write the bundle to this file as well as printing it.
        #[arg(long)]
        out: Option<String>,
    },
    /// Start or reuse the shared HTTP daemon and print its URL.
    Connect {
        /// HTTP daemon port.
        #[arg(long, default_value_t = cairn_daemon::lifecycle::DEFAULT_PORT)]
        port: u16,
        /// HTTP daemon host (loopback).
        #[arg(long, default_value = cairn_daemon::lifecycle::DEFAULT_HOST)]
        host: String,
        /// Daemon implementation a start launches (default: rust when a Rust binary is present, else python; CAIRN_DAEMON_IMPL overrides).
        #[arg(long = "impl")]
        impl_: Option<String>,
    },
    /// Start the loopback-only read-only web dashboard.
    Dashboard {
        /// SQLite DB path (default: central store for this workspace).
        #[arg(long)]
        db: Option<String>,
        /// Bind host, loopback only.
        #[arg(long, default_value = "127.0.0.1")]
        host: String,
        /// Bind port.
        #[arg(long, default_value_t = 8501)]
        port: u16,
    },
    /// Wire cairn into AI coding clients.
    #[command(name = "install-agents")]
    InstallAgents {
        /// Override detection: install for these clients (repeatable). Skips the interactive prompt.
        #[arg(long = "client")]
        clients: Vec<String>,
        /// Workspace root (default: cwd).
        #[arg(long)]
        workspace: Option<String>,
        /// Where to write configs: 'workspace' (./.claude/) or 'global' (~/.claude/).
        #[arg(long)]
        scope: Option<String>,
        /// Overwrite existing cairn files.
        #[arg(long)]
        force: bool,
        /// Show what would be written; change nothing.
        #[arg(long = "dry-run")]
        dry_run: bool,
        /// Also install git hooks (post-commit + post-checkout) in repos.
        #[arg(long = "git-hooks")]
        git_hooks: bool,
        /// Skip the interactive prompt; install for detected clients that don't already have cairn.
        #[arg(short = 'y', long)]
        yes: bool,
    },
    /// Remove cairn entries from AI client configs.
    #[command(name = "uninstall-agents")]
    UninstallAgents {
        /// Which clients to remove from (repeatable). Default: detected.
        #[arg(long = "client")]
        clients: Vec<String>,
        /// Which install scope to remove: 'workspace', 'global', or 'all'.
        #[arg(long, default_value = "workspace")]
        scope: String,
        /// Workspace root (default: resolved).
        #[arg(long)]
        workspace: Option<String>,
    },
    /// Uninstall cairn: agent wiring, hooks, graph store, and the binary.
    Uninstall {
        /// Remove everything for ALL workspaces (the entire ~/.cairn).
        #[arg(long)]
        full: bool,
        /// Remove agent wiring only.
        #[arg(long = "agents-only")]
        agents_only: bool,
        /// Remove git hooks only.
        #[arg(long = "hooks-only")]
        hooks_only: bool,
        /// Remove graph + knowledge data only.
        #[arg(long = "graph-only")]
        graph_only: bool,
        /// Remove the cairn binary only.
        #[arg(long = "package-only")]
        package_only: bool,
        /// Limit agent removal to these clients (repeatable).
        #[arg(long = "client")]
        clients: Vec<String>,
        /// Agent-wiring scope to remove: 'workspace', 'global', or 'all'.
        #[arg(long, default_value = "workspace")]
        scope: String,
        /// Workspace root (default: resolved).
        #[arg(long)]
        workspace: Option<String>,
        /// Show what would be removed; change nothing.
        #[arg(long = "dry-run")]
        dry_run: bool,
        /// Skip confirmations (implied by --full).
        #[arg(short = 'y', long)]
        yes: bool,
    },
    /// Upgrade cairn. Detects install method and updates in place.
    Upgrade {
        /// Only check, don't upgrade.
        #[arg(long)]
        check: bool,
    },
    /// Print the installed cairn version.
    Version,
    /// Hook management: git hooks, and the agent hook entrypoints.
    Hooks {
        #[command(subcommand)]
        command: HooksGroup,
    },
    /// Download the reranker model and enable reranking persistently (CAIRN_RERANK=0 forces it off).
    #[command(name = "download-reranker")]
    DownloadReranker {
        /// CrossEncoder model id (default: BAAI/bge-reranker-base, or $CAIRN_RERANK_MODEL if set).
        #[arg(long)]
        model: Option<String>,
    },
    /// Start the HTTP MCP server daemon in the foreground, or manage the shared daemon.
    Serve {
        /// SQLite DB path (default: central store).
        #[arg(long)]
        db: Option<String>,
        /// HTTP daemon port.
        #[arg(long)]
        port: Option<u16>,
        /// Open the graph DB read-only (default: read-write).
        #[arg(long)]
        read_only: bool,
        /// Open the graph DB read-write (default).
        #[arg(long, conflicts_with = "read_only")]
        read_write: bool,
        /// Removed: cairn serves streamable HTTP only.
        #[arg(long, hide = true)]
        transport: Option<String>,
        /// Bind host for the HTTP daemon (loopback).
        #[arg(long, default_value = cairn_daemon::lifecycle::DEFAULT_HOST)]
        host: String,
        /// Bearer API key required by the HTTP daemon on non-loopback binds.
        #[arg(long = "api-key")]
        api_key: Option<String>,
        /// Run the HTTP daemon without session affinity.
        #[arg(long)]
        stateless: bool,
        #[command(subcommand)]
        command: Option<ServeGroup>,
    },
}

#[derive(Subcommand)]
pub enum HooksGroup {
    /// Install git hooks.
    Install {
        /// Workspace root (default: resolved workspace).
        #[arg(long)]
        workspace: Option<String>,
    },
    /// Uninstall git hooks.
    Uninstall {
        /// Workspace root (default: resolved workspace).
        #[arg(long)]
        workspace: Option<String>,
    },
    /// Run an agent hook entrypoint over the JSON payload on stdin.
    #[command(hide = true)]
    Run {
        /// post_edit, session_end, post_tool_failure, or session_start.
        entrypoint: String,
    },
}

#[derive(Subcommand)]
pub enum ServeGroup {
    /// Run the HTTP MCP server daemon in the foreground.
    Run {
        /// SQLite DB path (default: central store).
        #[arg(long)]
        db: Option<String>,
        /// HTTP daemon port.
        #[arg(long)]
        port: Option<u16>,
        /// Open the graph DB read-only (default: read-write).
        #[arg(long)]
        read_only: bool,
        /// Open the graph DB read-write (default).
        #[arg(long, conflicts_with = "read_only")]
        read_write: bool,
        /// Removed: cairn serves streamable HTTP only.
        #[arg(long, hide = true)]
        transport: Option<String>,
        /// Bind host for the HTTP daemon (loopback).
        #[arg(long, default_value = cairn_daemon::lifecycle::DEFAULT_HOST)]
        host: String,
        /// Bearer API key required by the HTTP daemon on non-loopback binds.
        #[arg(long = "api-key")]
        api_key: Option<String>,
        /// Run the HTTP daemon without session affinity.
        #[arg(long)]
        stateless: bool,
    },
    /// Start the daemon in the background.
    Start {
        /// HTTP daemon port.
        #[arg(long, default_value_t = cairn_daemon::lifecycle::DEFAULT_PORT)]
        port: u16,
        /// Bind host (loopback).
        #[arg(long, default_value = cairn_daemon::lifecycle::DEFAULT_HOST)]
        host: String,
        /// Bearer API key required by the HTTP daemon on non-loopback binds.
        #[arg(long = "api-key")]
        api_key: Option<String>,
    },
    /// Stop the daemon.
    Stop,
    /// Print daemon status.
    Status {
        /// HTTP daemon port (default: the managed daemon's configured port).
        #[arg(long)]
        port: Option<u16>,
        /// HTTP daemon host (default: the managed daemon's configured host).
        #[arg(long)]
        host: Option<String>,
    },
    /// Restart the daemon.
    Restart {
        /// HTTP daemon port (default: the managed daemon's configured port).
        #[arg(long)]
        port: Option<u16>,
        /// HTTP daemon host (default: the managed daemon's configured host).
        #[arg(long)]
        host: Option<String>,
    },
}

/// Run a system command.
pub fn run(command: SystemCommand) -> ExitCode {
    match command {
        SystemCommand::Doctor { db, json, fix } => cmds_doctor::run(cmds_doctor::DoctorArgs {
            db,
            as_json: json,
            fix,
        }),
        SystemCommand::Status { db, knowledge } => {
            cmds_lifecycle::run_status(cmds_lifecycle::StatusArgs { db, knowledge })
        }
        SystemCommand::Sync { workspace, db } => {
            cmds_lifecycle::run_sync(cmds_lifecycle::SyncArgs { workspace, db })
        }
        SystemCommand::Metrics {
            db,
            tool_name,
            json,
            builds,
            quality,
            contention,
            tasks,
        } => cmds_lifecycle::run_metrics(cmds_lifecycle::MetricsArgs {
            db,
            tool: tool_name,
            as_json: json,
            builds,
            quality,
            contention,
            tasks,
        }),
        SystemCommand::Report { db, json, out } => {
            cmds_lifecycle::run_report(cmds_lifecycle::ReportArgs {
                db,
                as_json: json,
                out,
            })
        }
        SystemCommand::Uninstall {
            full,
            agents_only,
            hooks_only,
            graph_only,
            package_only,
            clients,
            scope,
            workspace,
            dry_run,
            yes,
        } => cmds_lifecycle::run_uninstall(cmds_lifecycle::UninstallArgs {
            full,
            agents_only,
            hooks_only,
            graph_only,
            package_only,
            clients,
            scope,
            workspace,
            dry_run,
            yes,
        }),
        SystemCommand::Upgrade { check } => {
            cmds_lifecycle::run_upgrade(cmds_lifecycle::UpgradeArgs { check })
        }
        SystemCommand::Version => cmds_lifecycle::run_version(),
        SystemCommand::DownloadReranker { model } => {
            cmds_lifecycle::run_download_reranker(cmds_lifecycle::DownloadRerankerArgs { model })
        }
        SystemCommand::Connect { port, host, impl_ } => {
            cmds_connect::run(cmds_connect::ConnectArgs { port, host, impl_ })
        }
        SystemCommand::Dashboard { db, host, port } => {
            cmds_dashboard::run(cmds_dashboard::DashboardArgs { db, host, port })
        }
        SystemCommand::InstallAgents {
            clients,
            workspace,
            scope,
            force,
            dry_run,
            git_hooks,
            yes,
        } => cmds_agents::run_install(cmds_agents::InstallAgentsArgs {
            clients,
            workspace,
            scope,
            force,
            dry_run,
            git_hooks,
            yes,
        }),
        SystemCommand::UninstallAgents {
            clients,
            scope,
            workspace,
        } => cmds_agents::run_uninstall(cmds_agents::UninstallAgentsArgs {
            clients,
            scope,
            workspace,
        }),
        SystemCommand::Serve {
            db,
            port,
            read_only,
            read_write,
            transport,
            host,
            api_key,
            stateless,
            command,
        } => {
            let read_only_state = if read_only {
                Some(true)
            } else if read_write {
                Some(false)
            } else {
                None
            };
            if transport.is_some() {
                return cmds_serve::refuse_removed_transport(port, Some(&host));
            }
            match command {
                None => cmds_serve::run_foreground(cmds_serve::ServeForegroundArgs {
                    db,
                    port,
                    read_only: read_only_state,
                    host: Some(host),
                    api_key,
                    stateless,
                }),
                Some(ServeGroup::Run {
                    db,
                    port,
                    read_only,
                    read_write,
                    transport,
                    host,
                    api_key,
                    stateless,
                }) => {
                    if transport.is_some() {
                        return cmds_serve::refuse_removed_transport(port, Some(&host));
                    }
                    let _ = stateless;
                    cmds_serve::run_foreground(cmds_serve::ServeForegroundArgs {
                        db,
                        port,
                        read_only: if read_only {
                            Some(true)
                        } else if read_write {
                            Some(false)
                        } else {
                            None
                        },
                        host: Some(host),
                        api_key,
                        stateless: false,
                    })
                }
                Some(ServeGroup::Start {
                    port,
                    host,
                    api_key,
                }) => cmds_serve::run_start(cmds_serve::ServeStartArgs {
                    port,
                    host,
                    api_key,
                }),
                Some(ServeGroup::Stop) => cmds_serve::run_stop(),
                Some(ServeGroup::Status { port, host }) => {
                    cmds_serve::run_status(cmds_serve::ServeStatusArgs { port, host })
                }
                Some(ServeGroup::Restart { port, host }) => {
                    cmds_serve::run_restart(cmds_serve::ServeRestartArgs { port, host })
                }
            }
        }
        SystemCommand::Hooks { command } => match command {
            HooksGroup::Install { workspace } => run_hooks_install(workspace),
            HooksGroup::Uninstall { workspace } => run_hooks_uninstall(workspace),
            HooksGroup::Run { entrypoint } => crate::agent_hooks::run(&entrypoint),
        },
    }
}

/// click.ClickException rendering: "Error: <msg>" on stderr, exit 1.
fn cli_err(msg: &str) -> ExitCode {
    eprintln!("Error: {msg}");
    ExitCode::FAILURE
}

fn hook_workspace(arg: Option<&str>) -> std::path::PathBuf {
    crate::store_paths::resolve_workspace(arg.map(std::path::Path::new))
}

fn run_hooks_install(workspace: Option<String>) -> ExitCode {
    let ws = hook_workspace(workspace.as_deref());
    let repos = crate::agent_install::discover_hook_repos(&ws);
    if repos.is_empty() {
        return cli_err(crate::agent_install::NO_GIT_REPOS_GUIDANCE);
    }
    match crate::agent_install::install_git_hooks(&repos) {
        Ok(installed) if installed.is_empty() => {
            println!("No hooks installed; existing non-cairn hooks were left untouched");
            ExitCode::SUCCESS
        }
        Ok(installed) => {
            println!(
                "Installed post-commit and post-checkout hooks in {} repos: {}",
                installed.len(),
                installed.join(", ")
            );
            ExitCode::SUCCESS
        }
        Err(msg) => cli_err(&msg),
    }
}

fn run_hooks_uninstall(workspace: Option<String>) -> ExitCode {
    let ws = hook_workspace(workspace.as_deref());
    let repos = crate::agent_install::discover_hook_repos(&ws);
    let removed = crate::agent_install::uninstall_git_hooks(&repos);
    println!(
        "Removed hooks from {} repos: {}",
        removed.len(),
        removed.join(", ")
    );
    ExitCode::SUCCESS
}

#[cfg(test)]
mod tests {
    use clap::{Command, Subcommand};

    use super::*;

    fn tree<T: Subcommand>() -> Command {
        T::augment_subcommands(Command::new("cairn"))
    }

    #[test]
    fn command_tree_parses() {
        tree::<SystemCommand>().debug_assert();
        tree::<HooksGroup>().debug_assert();
        tree::<ServeGroup>().debug_assert();
    }

    #[test]
    fn version_prints_the_package_version() {
        assert_eq!(run(SystemCommand::Version), ExitCode::SUCCESS);
    }

    #[test]
    fn hooks_install_and_uninstall_manage_repo_hooks() {
        let _env = crate::test_env::EnvScope::new("hooks-cli");
        let dir = std::env::temp_dir().join(format!(
            "cairn-hooks-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("clock")
                .as_nanos()
        ));
        let ws = dir.join("ws");
        let repo = ws.join("repo");
        std::fs::create_dir_all(repo.join(".git/hooks")).expect("repo");
        std::fs::create_dir_all(dir.join("empty")).expect("empty dir");
        let ws_arg = ws.to_string_lossy().to_string();

        let code = run(SystemCommand::Hooks {
            command: HooksGroup::Install {
                workspace: Some(ws_arg.clone()),
            },
        });
        assert_eq!(code, ExitCode::SUCCESS);
        let post_commit = repo.join(".git/hooks/post-commit");
        let post_checkout = repo.join(".git/hooks/post-checkout");
        assert!(post_commit.exists() && post_checkout.exists());

        let code = run(SystemCommand::Hooks {
            command: HooksGroup::Uninstall {
                workspace: Some(ws_arg),
            },
        });
        assert_eq!(code, ExitCode::SUCCESS);
        assert!(!post_commit.exists() && !post_checkout.exists());

        let code = run(SystemCommand::Hooks {
            command: HooksGroup::Install {
                workspace: Some(dir.join("empty").to_string_lossy().to_string()),
            },
        });
        assert_eq!(code, ExitCode::FAILURE, "no-repos guidance is an error");
        let _ = std::fs::remove_dir_all(&dir);
    }
}
