//! `cairn connect`: start or reuse the shared HTTP daemon and print its MCP URL.

use std::process::ExitCode;
use std::time::Duration;

use cairn_daemon::lifecycle as lc;
use serde_json::Value;

use crate::store_paths;

// Covers the daemon start lock (120s) plus the unhealthy-wait window.
const LOCK_WAIT: Duration = Duration::from_secs(150);
const LOCK_POLL: Duration = Duration::from_millis(50);

pub struct ConnectArgs {
    pub port: u16,
    pub host: String,
    pub impl_: Option<String>,
}

enum ReuseDecision {
    Reuse,
    /// Same store, stale binary: stop it and start this one.
    VersionStale,
    Mismatch,
    NotRunning,
}

/// The reuse-guard verdict for one healthz payload against this workspace's
/// store. Store identity compares `store_id` (the keyed hash) with the `db`
/// path as the old-daemon fallback; a missing or differing `version` restarts.
fn reuse_decision(
    payload: Option<&Value>,
    my_store_id: &str,
    my_db: &str,
    daemon_url: &str,
) -> ReuseDecision {
    let Some(payload) = payload else {
        return ReuseDecision::NotRunning;
    };
    let same_store = payload
        .get("store_id")
        .and_then(Value::as_str)
        .is_some_and(|served| served == my_store_id)
        || payload
            .get("db")
            .and_then(Value::as_str)
            .is_some_and(|served| served == my_db);
    if !same_store {
        let shown = payload
            .get("db")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
            .map(str::to_string)
            .unwrap_or_else(|| "an unidentified store".to_string());
        eprintln!(
            "ERROR: the daemon at {daemon_url} serves {shown}, not this \
             workspace's ({my_db}). Stop it with `cairn serve stop`, or pass \
             --port to start a dedicated daemon."
        );
        return ReuseDecision::Mismatch;
    }
    let version = payload.get("version").and_then(Value::as_str);
    if version != Some(env!("CARGO_PKG_VERSION")) {
        return ReuseDecision::VersionStale;
    }
    ReuseDecision::Reuse
}

fn try_reuse(port: u16, host: &str) -> ReuseDecision {
    let store = store_paths::resolve_store(None);
    let mine = store.db.to_string_lossy().into_owned();
    let my_store_id = match cairn_store::swap::store_id(&store.db) {
        Some(id) => format!("{id:?}"),
        None => "unknown".to_string(),
    };
    let daemon_url = lc::mcp_url(port, host);
    reuse_decision(
        lc::healthz_json(port, host).as_ref(),
        &my_store_id,
        &mine,
        &daemon_url,
    )
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
    // A mismatch aborts: never fall through to a start sequence against a
    // foreign daemon. A stale binary is stopped here so the start sequence
    // below launches this one.
    match try_reuse(args.port, &args.host) {
        ReuseDecision::Reuse => {
            report(&lc::mcp_url(args.port, &args.host), false, None);
            return ExitCode::SUCCESS;
        }
        ReuseDecision::VersionStale => {
            eprintln!("Daemon binary is stale; restarting it.");
            let _ = crate::cmds_serve::run_stop();
        }
        ReuseDecision::Mismatch => return ExitCode::FAILURE,
        ReuseDecision::NotRunning => {}
    }
    let lockfile = lc::daemon_home().join("mcp-connect.lock");
    let lock = match lc::lockfile_guard(&lockfile, LOCK_WAIT, LOCK_POLL) {
        Ok(lock) => lock,
        Err(_) => {
            eprintln!(
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
        ReuseDecision::VersionStale => {
            eprintln!("Daemon binary is stale; restarting it.");
            let _ = crate::cmds_serve::run_stop();
        }
        ReuseDecision::Mismatch => return ExitCode::FAILURE,
        ReuseDecision::NotRunning => {}
    }
    let store = store_paths::resolve_store(None);
    lc::sweep_strays(&store.db, true);
    let api_key = cairn_daemon::auth::client_key();
    match lc::ensure_daemon_running(
        args.port,
        &args.host,
        api_key.as_deref(),
        true,
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
        let my_id = "StoreId(1, 2)";
        let version = env!("CARGO_PKG_VERSION");
        // No daemon -> start path.
        assert!(matches!(
            reuse_decision(None, my_id, mine, url),
            ReuseDecision::NotRunning
        ));
        // Healthy daemon serving this store by id, matching version -> reuse.
        assert!(matches!(
            reuse_decision(
                Some(&json!({"store_id": my_id, "version": version})),
                my_id,
                mine,
                url
            ),
            ReuseDecision::Reuse
        ));
        // Old daemon: db-path identity, no version -> restart.
        assert!(matches!(
            reuse_decision(Some(&json!({"db": mine})), my_id, mine, url),
            ReuseDecision::VersionStale
        ));
        // Matching store by legacy db path with the current version -> reuse.
        assert!(matches!(
            reuse_decision(
                Some(&json!({"db": mine, "version": version})),
                my_id,
                mine,
                url
            ),
            ReuseDecision::Reuse
        ));
        // Version drift on the same store -> restart, not mismatch.
        assert!(matches!(
            reuse_decision(
                Some(&json!({"store_id": my_id, "version": "0.0.1"})),
                my_id,
                mine,
                url
            ),
            ReuseDecision::VersionStale
        ));
        // Foreign store -> mismatch.
        assert!(matches!(
            reuse_decision(
                Some(&json!({"store_id": "StoreId(9, 9)", "version": version})),
                my_id,
                mine,
                url
            ),
            ReuseDecision::Mismatch
        ));
    }
}
