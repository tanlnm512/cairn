//! `cairn connect`: start or reuse the shared HTTP daemon and print its MCP URL.

use std::process::ExitCode;
use std::time::Duration;

use cairn_daemon::lifecycle as lc;
use serde_json::Value;

use crate::store_paths;

const LOCK_WAIT: Duration = Duration::from_secs(30);
const LOCK_POLL: Duration = Duration::from_millis(50);

pub struct ConnectArgs {
    pub port: u16,
    pub host: String,
    pub impl_: Option<String>,
}

enum ReuseDecision {
    Reuse,
    Mismatch,
    NotRunning,
}

/// The reuse-guard verdict for one healthz payload against this workspace's store.
fn reuse_decision(payload: Option<&Value>, my_db: &str, daemon_url: &str) -> ReuseDecision {
    let Some(payload) = payload else {
        return ReuseDecision::NotRunning;
    };
    let served = payload
        .get("db")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty());
    if let Some(served) = served
        && served != my_db
    {
        println!(
            "ERROR: the daemon at {daemon_url} serves store {served}, not this \
             workspace's ({my_db}). Stop it with `cairn serve stop`, or pass \
             --port to start a dedicated daemon."
        );
        return ReuseDecision::Mismatch;
    }
    ReuseDecision::Reuse
}

fn try_reuse(port: u16, host: &str) -> ReuseDecision {
    let store = store_paths::resolve_store(None);
    let mine = store.db.to_string_lossy().into_owned();
    let daemon_url = lc::mcp_url(port, host);
    reuse_decision(lc::healthz_json(port, host).as_ref(), &mine, &daemon_url)
}

fn report(url: &str, started: bool, pid: Option<u32>) {
    if started {
        let pid_part = pid
            .filter(|p| *p != 0)
            .map(|p| format!(" (pid {p})"))
            .unwrap_or_default();
        println!("cairn daemon started{pid_part} at {url}");
    } else {
        println!("cairn daemon already running at {url}");
    }
}

/// Start or reuse the shared HTTP daemon and print its MCP URL.
pub fn run(args: ConnectArgs) -> ExitCode {
    if let Some(impl_name) = &args.impl_ {
        // SAFETY: single-threaded CLI dispatch before any runtime threads spawn.
        unsafe { std::env::set_var(lc::DAEMON_IMPL_ENV, impl_name) };
    }
    // A mismatch aborts like the Python sys.exit(1): never fall through to a
    // start sequence against a foreign daemon.
    match try_reuse(args.port, &args.host) {
        ReuseDecision::Reuse => {
            report(&lc::mcp_url(args.port, &args.host), false, None);
            return ExitCode::SUCCESS;
        }
        ReuseDecision::Mismatch => return ExitCode::FAILURE,
        ReuseDecision::NotRunning => {}
    }
    let lockfile = lc::daemon_home().join("mcp-connect.lock");
    let lock = match lc::lockfile_guard(&lockfile, LOCK_WAIT, LOCK_POLL) {
        Ok(lock) => lock,
        Err(_) => {
            println!(
                "ERROR: timed out after {}s waiting for the connect start lock \
                 ({}); retry after the other connect finishes.",
                LOCK_WAIT.as_secs(),
                lockfile.display()
            );
            return ExitCode::FAILURE;
        }
    };
    let _guard = lock;
    match try_reuse(args.port, &args.host) {
        ReuseDecision::Reuse => {
            report(&lc::mcp_url(args.port, &args.host), false, None);
            return ExitCode::SUCCESS;
        }
        ReuseDecision::Mismatch => return ExitCode::FAILURE,
        ReuseDecision::NotRunning => {}
    }
    let store = store_paths::resolve_store(None);
    lc::sweep_strays(&store.db, true);
    match lc::ensure_daemon_running(
        args.port,
        &args.host,
        None,
        &lc::StoreBinding {
            workspace: store.workspace,
            db: store.db,
            knowledge: store.knowledge,
        },
    ) {
        Ok((pid, url, started)) => {
            report(&url, started, pid);
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

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn reuse_guard_matrix() {
        let mine = "/home/.cairn/abc/.kg";
        let url = "http://127.0.0.1:9876/mcp";
        // No daemon -> start path.
        assert!(matches!(
            reuse_decision(None, mine, url),
            ReuseDecision::NotRunning
        ));
        // Healthy daemon serving this store -> reuse.
        assert!(matches!(
            reuse_decision(Some(&json!({"db": mine})), mine, url),
            ReuseDecision::Reuse
        ));
        // Payload without a db field -> reuse (the Python truthiness contract).
        assert!(matches!(
            reuse_decision(Some(&json!({"status": "ok"})), mine, url),
            ReuseDecision::Reuse
        ));
    }
}
