//! `cairn serve`: the serve group and daemon lifecycle.

use std::net::SocketAddr;
use std::process::ExitCode;
use std::time::Duration;

use cairn_daemon::lifecycle as lc;
use cairn_daemon::server::{DaemonConfig, serve_config};
use cairn_query::textfmt::title_bool;

use crate::store_paths;

pub struct ServeForegroundArgs {
    pub db: Option<String>,
    pub port: Option<u16>,
    pub read_only: Option<bool>,
    pub host: Option<String>,
    pub api_key: Option<String>,
    pub stateless: bool,
}

pub struct ServeStartArgs {
    pub port: u16,
    pub host: String,
    pub api_key: Option<String>,
}

pub struct ServeStatusArgs {
    pub port: Option<u16>,
    pub host: Option<String>,
}

pub struct ServeRestartArgs {
    pub port: Option<u16>,
    pub host: Option<String>,
}

/// The daemon's boot guard: a storeless workspace still serves (healthz
/// unhealthy until the first build); a present-but-broken store fails fast.
fn boot_guard(db_path: &std::path::Path) -> Result<(), String> {
    let report = |message: String| Err(message);
    if !db_path.exists() {
        return Ok(());
    }
    let Ok(conn) = cairn_store::conn::open_read_only(db_path) else {
        return report(format!(
            "cairn: error: failed to check database: cannot open {}. \
             Fix: set CAIRN_HOME to the parent of the populated store \
             (default ~/.cairn), then run 'cairn init && cairn build' first.",
            db_path.display()
        ));
    };
    let has_symbols: bool = conn
        .query_row(
            "SELECT COUNT(*) > 0 FROM sqlite_master WHERE type='table' AND name='symbols'",
            [],
            |row| row.get(0),
        )
        .unwrap_or(false);
    if !has_symbols {
        return report(
            "cairn: error: database is missing the 'symbols' table. \
             Run 'cairn init && cairn build' first."
                .to_string(),
        );
    }
    Ok(())
}

fn local_timestamp() -> String {
    // The daemon log's "[YYYY-MM-DD HH:MM:SS]" prefix; UTC so it stays sortable.
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default();
    cairn_build::timefmt::iso_from_epoch(now.as_secs() as i64, 0)[..19].replace('T', " ")
}

/// Refuse a removed --transport value, naming the HTTP daemon URL and connect.
pub fn refuse_removed_transport(port: Option<u16>, host: Option<&str>) -> ExitCode {
    let url = lc::mcp_url(
        port.unwrap_or(lc::DEFAULT_PORT),
        host.unwrap_or(lc::DEFAULT_HOST),
    );
    println!("cairn: error: --transport is removed; cairn serves streamable HTTP at {url}.");
    println!("Run `cairn connect` to start or reuse the daemon and print its URL.");
    ExitCode::FAILURE
}

/// Run the HTTP MCP server daemon in the foreground.
pub fn run_foreground(args: ServeForegroundArgs) -> ExitCode {
    let store = store_paths::resolve_store(None);
    let db_path = args
        .db
        .map(std::path::PathBuf::from)
        .unwrap_or(store.db.clone());
    if let Err(message) = boot_guard(&db_path) {
        let ts = local_timestamp();
        eprintln!("[{ts}] {message}");
        return ExitCode::FAILURE;
    }
    let host = args.host.unwrap_or_else(|| lc::DEFAULT_HOST.to_string());
    let port = args.port.unwrap_or(lc::DEFAULT_PORT);
    let bind: SocketAddr = match format!("{host}:{port}").parse() {
        Ok(bind) => bind,
        Err(e) => {
            eprintln!("cairn: error: invalid bind address {host}:{port}: {e}");
            return ExitCode::FAILURE;
        }
    };
    let config = DaemonConfig {
        db_path,
        workspace: store.workspace,
        knowledge_path: store.knowledge,
        read_only: args.read_only.unwrap_or(false),
        api_key: cairn_daemon::auth::resolve_api_key(args.api_key.as_deref()),
    };
    let runtime = match tokio::runtime::Runtime::new() {
        Ok(runtime) => runtime,
        Err(e) => {
            eprintln!("cairn: error: {e}");
            return ExitCode::FAILURE;
        }
    };
    let served = runtime.block_on(async move {
        match serve_config(config, bind).await {
            Ok((_addr, handle)) => {
                let _ = tokio::signal::ctrl_c().await;
                handle.stop();
                true
            }
            Err(e) => {
                eprintln!("cairn: error: {e}");
                false
            }
        }
    });
    if served {
        ExitCode::SUCCESS
    } else {
        ExitCode::FAILURE
    }
}

/// Start the persistent shared HTTP daemon; idempotent.
pub fn run_start(args: ServeStartArgs) -> ExitCode {
    let api_key = cairn_daemon::auth::resolve_api_key(args.api_key.as_deref());
    let store = store_paths::resolve_store(None);
    match lc::ensure_daemon_running(
        args.port,
        &args.host,
        api_key.as_deref(),
        &lc::StoreBinding {
            workspace: store.workspace,
            db: store.db,
            knowledge: store.knowledge,
        },
    ) {
        Ok((pid, url, started)) => {
            if started {
                println!("cairn daemon started (pid {}) at {url}", pid_display(pid));
            } else {
                println!(
                    "cairn daemon already running (pid {}) at {url}",
                    pid_display(pid)
                );
            }
            println!("  logs: {}", lc::daemon_log_path().display());
            if lc::is_macos() {
                println!(
                    "  auto-restarts on crash; starts at login. Stop with `cairn serve stop`."
                );
            } else {
                println!("  Stop with `cairn serve stop`.");
            }
            ExitCode::SUCCESS
        }
        Err(e) => {
            println!(
                "ERROR: {e}. Check the daemon log: {}",
                lc::daemon_log_path().display()
            );
            ExitCode::FAILURE
        }
    }
}

fn pid_display(pid: Option<u32>) -> String {
    pid.map(|p| p.to_string())
        .unwrap_or_else(|| "None".to_string())
}

/// Stop the shared HTTP daemon and kill stray `cairn serve` processes.
pub fn run_stop() -> ExitCode {
    let store = store_paths::resolve_store(None);
    let mut unload_error: Option<String> = None;

    if lc::is_macos() {
        if lc::is_loaded() {
            match lc::unload() {
                Ok(()) => println!("unloaded LaunchAgent (launchd sent SIGTERM)"),
                Err(e) => {
                    // A launchctl timeout must not skip the pidfile/stray cleanup below.
                    unload_error = Some(e.clone());
                    println!("ERROR: {e}");
                }
            }
        } else {
            println!("LaunchAgent not loaded");
        }
    }

    if let Some(pid) = lc::pidfile_pid() {
        lc::terminate_pid(
            pid,
            Duration::from_secs(5),
            Some(&lc::is_cairn_serve_cmdline),
        );
        let _ = std::fs::remove_file(lc::pidfile_path());
        println!("stopped daemonized daemon (pid {pid})");
    }

    let strays = lc::find_strays(&store.db);
    if !strays.is_empty() {
        println!(
            "killing {} stray cairn serve process(es): {:?}",
            strays.len(),
            strays
        );
        lc::sweep_strays(&store.db, false);
    }

    std::thread::sleep(Duration::from_millis(500));
    if unload_error.is_some() {
        return ExitCode::FAILURE;
    }
    if lc::is_loaded() && lc::running_pid().is_some() {
        println!("ERROR: the daemon is still running after stop; check `cairn serve status`.");
        return ExitCode::FAILURE;
    }
    println!("stopped.");
    ExitCode::SUCCESS
}

/// Show daemon health: managed state, pid, healthz, strays, DB lock holders.
pub fn run_status(args: ServeStatusArgs) -> ExitCode {
    let store = store_paths::resolve_store(None);

    // An unpassed option defers to the port/host the daemon was started with,
    // so a custom-port daemon is not probed (and reported UNHEALTHY) on the
    // default port.
    let (d_port, d_host) = lc::daemon_bind();
    let port = args.port.or(d_port).unwrap_or(lc::DEFAULT_PORT);
    let host = args
        .host
        .or(d_host)
        .unwrap_or_else(|| lc::DEFAULT_HOST.to_string());

    let loaded = lc::is_loaded();
    let pid = lc::running_pid().or_else(lc::pidfile_pid);
    let responds = lc::healthz_responds(port, &host);
    let strays = lc::find_strays(&store.db);
    let db_holders = lc::db_holder_identities(&store.db);

    for line in status_lines(StatusInputs {
        loaded,
        pid,
        responds,
        port,
        host: &host,
        strays: &strays,
        db_holders: &db_holders,
    }) {
        println!("{line}");
    }
    ExitCode::SUCCESS
}

pub struct StatusInputs<'a> {
    pub loaded: bool,
    pub pid: Option<u32>,
    pub responds: bool,
    pub port: u16,
    pub host: &'a str,
    pub strays: &'a [u32],
    pub db_holders: &'a [String],
}

/// The exact status report lines, factored for the contract test.
pub fn status_lines(inputs: StatusInputs<'_>) -> Vec<String> {
    let health = if inputs.responds && inputs.strays.is_empty() {
        "HEALTHY"
    } else if inputs.pid.is_none() && !inputs.loaded && !inputs.responds {
        "not running"
    } else {
        "UNHEALTHY"
    };
    let mut lines = vec![
        format!("cairn daemon: {health}"),
        format!("  LaunchAgent loaded : {}", title_bool(inputs.loaded)),
        format!("  daemon pid         : {}", pid_display(inputs.pid)),
        format!(
            "  healthz responds   : {}  ({})",
            title_bool(inputs.responds),
            lc::mcp_url(inputs.port, inputs.host)
        ),
        format!(
            "  stray cairn serve pids: {}",
            if inputs.strays.is_empty() {
                "none".to_string()
            } else {
                format!("{:?}", inputs.strays)
            }
        ),
        format!(
            "  DB lock holders    : {}",
            if inputs.db_holders.is_empty() {
                "none".to_string()
            } else {
                format!("{:?}", inputs.db_holders)
            }
        ),
    ];
    if lc::is_macos() {
        lines.push(format!(
            "  plist              : {}",
            lc::plist_path().display()
        ));
    }
    lines.push(format!(
        "  log                : {}",
        lc::daemon_log_path().display()
    ));
    if !inputs.strays.is_empty() {
        lines.push("  -> run `cairn serve stop` to kill strays".to_string());
    }
    lines
}

/// Stop then start the daemon.
pub fn run_restart(args: ServeRestartArgs) -> ExitCode {
    // Defer to the daemon's configured bind so a restart never rebinds a
    // custom-port daemon to the defaults.
    let (d_port, d_host) = lc::daemon_bind();
    let port = args.port.or(d_port).unwrap_or(lc::DEFAULT_PORT);
    let host = args
        .host
        .or(d_host)
        .unwrap_or_else(|| lc::DEFAULT_HOST.to_string());
    // A failed stop must not leave the daemon down: start reuses a healthy
    // daemon or cycles a wedged one.
    let _ = run_stop();
    run_start(ServeStartArgs {
        port,
        host,
        api_key: None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn inputs<'a>(
        loaded: bool,
        pid: Option<u32>,
        responds: bool,
        strays: &'a [u32],
        holders: &'a [String],
    ) -> StatusInputs<'a> {
        StatusInputs {
            loaded,
            pid,
            responds,
            port: 9876,
            host: "127.0.0.1",
            strays,
            db_holders: holders,
        }
    }

    #[test]
    fn status_health_classification() {
        // HEALTHY: responding, no strays.
        let lines = status_lines(inputs(true, Some(7), true, &[], &[]));
        assert_eq!(lines[0], "cairn daemon: HEALTHY");
        // not running: no pid, not loaded, not responding.
        let lines = status_lines(inputs(false, None, false, &[], &[]));
        assert_eq!(lines[0], "cairn daemon: not running");
        // UNHEALTHY: loaded but not responding.
        let lines = status_lines(inputs(true, None, false, &[], &[]));
        assert_eq!(lines[0], "cairn daemon: UNHEALTHY");
        // UNHEALTHY: responding but strays present.
        let lines = status_lines(inputs(true, Some(7), true, &[42], &[]));
        assert_eq!(lines[0], "cairn daemon: UNHEALTHY");
    }

    #[test]
    fn status_lines_match_the_report_shape() {
        let lines = status_lines(inputs(
            false,
            None,
            false,
            &[42, 43],
            &["cairn(7)".to_string()],
        ));
        assert_eq!(lines[1], "  LaunchAgent loaded : False");
        assert_eq!(lines[2], "  daemon pid         : None");
        assert_eq!(
            lines[3],
            "  healthz responds   : False  (http://127.0.0.1:9876/mcp)"
        );
        assert_eq!(lines[4], "  stray cairn serve pids: [42, 43]");
        assert_eq!(lines[5], "  DB lock holders    : [\"cairn(7)\"]");
        assert!(
            lines
                .last()
                .is_some_and(|l| l.contains("cairn serve stop` to kill strays"))
        );
    }

    #[test]
    fn boot_guard_requires_a_symbols_table() {
        let dir = std::env::temp_dir().join(format!(
            "cairn-serve-guard-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("clock")
                .as_nanos()
        ));
        std::fs::create_dir_all(&dir).expect("dir");
        // A storeless workspace still serves (healthz unhealthy until the
        // first build), so the guard must pass a missing store through.
        let missing = dir.join("missing.kg");
        assert!(
            boot_guard(&missing).is_ok(),
            "missing store must not block serve"
        );
        // A non-store file: fail fast with the remediation line (SQLite
        // opens lazily, so garbage surfaces as the symbols-table refusal).
        let garbage = dir.join("garbage.kg");
        std::fs::write(&garbage, b"not sqlite").expect("garbage");
        let error = boot_guard(&garbage).expect_err("garbage store must fail");
        assert!(error.contains("cairn init && cairn build"), "{error}");
        // A store without the symbols table is refused.
        let empty = dir.join("empty.kg");
        let conn = cairn_store::conn::open_writable(&empty).expect("open");
        conn.execute_batch("CREATE TABLE user_data (x INTEGER);")
            .expect("table");
        drop(conn);
        let error = boot_guard(&empty).expect_err("no symbols table");
        assert_eq!(
            error,
            "cairn: error: database is missing the 'symbols' table. Run 'cairn init && cairn build' first."
        );
        // The spike fixture store passes the guard.
        let spike = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../../rust/golden/spike/search-store.kg");
        if spike.exists() {
            assert!(
                boot_guard(&spike).is_ok(),
                "populated store passes the guard"
            );
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn local_timestamp_has_the_log_shape() {
        let ts = local_timestamp();
        assert_eq!(ts.len(), 19, "{ts}");
        assert_eq!(ts.as_bytes()[10], b' ', "{ts}");
        assert_eq!(ts.as_bytes()[4], b'-', "{ts}");
    }
}
