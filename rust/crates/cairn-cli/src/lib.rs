//! The `cairn` binary crate.

pub mod agent_hooks;
pub mod agent_install;
pub mod agent_templates;
pub mod build_graph;
pub mod canonical_dump;
pub mod cli;
pub mod cli_rows;
pub mod closure;
pub mod cmds_agents;
pub mod cmds_connect;
pub mod cmds_core;
pub mod cmds_dashboard;
pub mod cmds_doctor;
pub mod cmds_knowledge;
pub mod cmds_lifecycle;
pub mod cmds_serve;
pub mod cmds_system;
pub mod dashboard;
pub mod display;
pub mod pack;
pub mod py_json;
pub mod rail;
pub mod store_paths;
pub mod targz;

#[cfg(test)]
pub(crate) mod test_env {
    use std::ffi::OsString;
    use std::path::{Path, PathBuf};
    use std::sync::{Mutex, MutexGuard};

    static ENV_LOCK: Mutex<()> = Mutex::new(());
    const PINNED: [&str; 5] = [
        "HOME",
        "CAIRN_HOME",
        "CAIRN_DB",
        "CAIRN_KNOWLEDGE",
        "CAIRN_WORKSPACE",
    ];

    /// Serializes process-env tests and pins HOME to a fresh dir with CAIRN_HOME unset.
    pub(crate) struct EnvScope {
        dir: PathBuf,
        saved: Vec<(&'static str, Option<OsString>)>,
        _lock: MutexGuard<'static, ()>,
    }

    impl EnvScope {
        pub(crate) fn new(tag: &str) -> Self {
            let lock = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
            let nanos = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("clock")
                .as_nanos();
            let dir = std::env::temp_dir()
                .join(format!("cairn-env-{}-{nanos}-{tag}", std::process::id()));
            std::fs::create_dir_all(&dir).expect("env dir");
            let saved = PINNED
                .iter()
                .map(|key| (*key, std::env::var_os(key)))
                .collect();
            // SAFETY: every env-mutating test holds ENV_LOCK; restored on drop.
            unsafe {
                std::env::set_var("HOME", &dir);
                for key in PINNED.iter().skip(1) {
                    std::env::remove_var(key);
                }
            }
            Self {
                dir,
                saved,
                _lock: lock,
            }
        }

        pub(crate) fn dir(&self) -> &Path {
            &self.dir
        }
    }

    impl Drop for EnvScope {
        fn drop(&mut self) {
            for (key, value) in &self.saved {
                // SAFETY: still holding ENV_LOCK.
                unsafe {
                    match value {
                        Some(v) => std::env::set_var(key, v),
                        None => std::env::remove_var(key),
                    }
                }
            }
        }
    }
}

/// Returns the crate name; skeleton smoke contract.
pub fn crate_name() -> &'static str {
    env!("CARGO_PKG_NAME")
}

#[cfg(test)]
mod tests {
    #[test]
    fn links_workspace_members() {
        assert_eq!(super::crate_name(), "cairn-cli");
        assert_eq!(cairn_store::crate_name(), "cairn-store");
        assert_eq!(cairn_query::crate_name(), "cairn-query");
        assert_eq!(cairn_parse::crate_name(), "cairn-parse");
        assert_eq!(cairn_build::crate_name(), "cairn-build");
        assert_eq!(cairn_daemon::crate_name(), "cairn-daemon");
    }
}
