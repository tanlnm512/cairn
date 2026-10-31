//! Central-store path resolution keyed by workspace.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use sha2::{Digest, Sha256};

/// Effective CAIRN_HOME: `~`-expanded env value when set, else `~/.cairn`.
pub fn cairn_home() -> Option<PathBuf> {
    cairn_store::paths::cairn_home()
}

/// `~`-expanded, absolute CAIRN_HOME (the `_current_cairn_home` contract).
pub fn current_cairn_home() -> PathBuf {
    cairn_store::paths::current_cairn_home()
}

/// `Path.resolve()` (non-strict): absolute with symlinks resolved where possible.
pub fn resolve_path(path: &Path) -> PathBuf {
    cairn_store::paths::resolve_path(path)
}

/// Stable short key for a workspace path (sha256 prefix).
pub fn store_key(workspace: &Path) -> String {
    let digest = Sha256::digest(workspace.to_string_lossy().as_bytes());
    let hex = digest
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect::<String>();
    hex[..16].to_string()
}

/// Resolved locations for one workspace's cairn data.
#[derive(Debug, Clone)]
pub struct StorePaths {
    pub workspace: PathBuf,
    pub home: PathBuf,
    pub db: PathBuf,
    pub knowledge: PathBuf,
}

impl StorePaths {
    /// Create the store directories if missing.
    pub fn ensure(&self) -> std::io::Result<&Self> {
        std::fs::create_dir_all(&self.home)?;
        std::fs::create_dir_all(&self.knowledge)?;
        Ok(self)
    }
}

/// The {workspace_abs_path: key} registry, sorted by path; empty when absent/corrupt.
pub fn load_registry() -> BTreeMap<String, String> {
    cairn_store::paths::load_registry()
}

/// Persist the registry atomically (tmp file + rename).
pub fn save_registry(reg: &BTreeMap<String, String>) -> std::io::Result<()> {
    let home = cairn_home().ok_or_else(|| {
        std::io::Error::other("CAIRN_HOME unavailable: no env override and no HOME")
    })?;
    std::fs::create_dir_all(&home)?;
    let mut value = serde_json::Map::new();
    for (ws, key) in reg {
        value.insert(ws.clone(), serde_json::Value::String(key.clone()));
    }
    let body = serde_json::to_string_pretty(&value).map_err(std::io::Error::other)?;
    let tmp = home.join(".workspaces-rust.tmp");
    std::fs::write(&tmp, format!("{body}\n"))?;
    std::fs::rename(&tmp, home.join("workspaces.json"))
}

/// Which workspace cairn is operating on: explicit > env > registered ancestor > cwd.
pub fn resolve_workspace(explicit: Option<&Path>) -> PathBuf {
    if let Some(ws) = explicit {
        return resolve_path(ws);
    }
    if let Ok(ws) = std::env::var("CAIRN_WORKSPACE")
        && !ws.is_empty()
    {
        return resolve_path(Path::new(&ws));
    }
    let reg = load_registry();
    if !reg.is_empty()
        && let Ok(cwd) = std::env::current_dir()
    {
        let mut here = resolve_path(&cwd);
        loop {
            if reg.contains_key(&here.to_string_lossy().to_string()) {
                return here;
            }
            if !here.pop() {
                break;
            }
        }
    }
    std::env::current_dir()
        .map(|cwd| resolve_path(&cwd))
        .unwrap_or_default()
}

/// StorePaths for the context; CAIRN_DB/CAIRN_KNOWLEDGE override, no writes.
pub fn resolve_store(explicit: Option<&Path>) -> StorePaths {
    let workspace = resolve_workspace(explicit);
    let key = store_key(&workspace);
    let home = cairn_home()
        .unwrap_or_else(|| PathBuf::from(".cairn"))
        .join(&key);
    let db = std::env::var("CAIRN_DB").ok().filter(|v| !v.is_empty());
    let knowledge = std::env::var("CAIRN_KNOWLEDGE")
        .ok()
        .filter(|v| !v.is_empty());
    StorePaths {
        workspace,
        home: home.clone(),
        db: db.map(PathBuf::from).unwrap_or_else(|| home.join(".kg")),
        knowledge: knowledge
            .map(PathBuf::from)
            .unwrap_or_else(|| home.join(".knowledge")),
    }
}

/// Register a workspace (creates its store dirs + registry entry).
pub fn register_workspace(workspace: &Path) -> std::io::Result<StorePaths> {
    let ws = resolve_path(workspace);
    let key = store_key(&ws);
    let home = cairn_home()
        .ok_or_else(|| std::io::Error::other("CAIRN_HOME unavailable"))?
        .join(&key);
    let paths = StorePaths {
        workspace: ws.clone(),
        home: home.clone(),
        db: home.join(".kg"),
        knowledge: home.join(".knowledge"),
    };
    paths.ensure()?;
    let mut reg = load_registry();
    reg.insert(ws.to_string_lossy().to_string(), key);
    save_registry(&reg)?;
    Ok(paths)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn store_key_is_the_sha256_prefix_of_the_path() {
        // printf %s /tmp/ws | shasum -a 256 | cut -c1-16
        let key = store_key(Path::new("/tmp/ws"));
        assert_eq!(key, "b011ea26cc731e1a");
    }

    #[test]
    fn resolve_path_normalizes_lexically_when_missing() {
        let cwd = std::env::current_dir().expect("cwd");
        let resolved = resolve_path(Path::new("a/../b"));
        assert_eq!(resolved, resolve_path(&cwd).join("b"));
    }

    #[test]
    fn registry_round_trips_sorted() {
        let env = crate::test_env::EnvScope::new("registry");
        let dir = env.dir().join(".cairn");
        std::fs::create_dir_all(&dir).expect("dir");
        // SAFETY: EnvScope holds the env lock and restores CAIRN_HOME on drop.
        unsafe { std::env::set_var("CAIRN_HOME", &dir) };
        let key_a = store_key(Path::new("/a"));
        let key_b = store_key(Path::new("/b"));
        let mut reg = load_registry();
        reg.insert("/b".into(), key_b.clone());
        reg.insert("/a".into(), key_a.clone());
        save_registry(&reg).expect("save");
        let loaded = load_registry();
        let keys: Vec<&String> = loaded.keys().collect();
        assert_eq!(keys, ["/a", "/b"]);
        assert_eq!(loaded.get("/a").map(String::as_str), Some(key_a.as_str()));
        assert_eq!(loaded.get("/b").map(String::as_str), Some(key_b.as_str()));
    }
}
