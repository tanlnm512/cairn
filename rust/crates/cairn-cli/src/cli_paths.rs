//! Shared CLI path helpers: `--db` precedence and the platform home directory.

use std::path::PathBuf;

use crate::store_paths;

/// Effective graph DB path: explicit --db wins, else the resolved store.
pub fn db_path(db: Option<&str>) -> PathBuf {
    db.map_or_else(|| store_paths::resolve_store(None).db, PathBuf::from)
}

/// db_path as a lossy string for string-typed --db args.
pub fn db_arg(db: &Option<String>) -> String {
    db_path(db.as_deref()).to_string_lossy().into_owned()
}

/// The user's platform home directory ("/" when the OS reports none).
pub fn home_dir() -> PathBuf {
    cairn_store::home::platform_home().unwrap_or_else(|| PathBuf::from("/"))
}
