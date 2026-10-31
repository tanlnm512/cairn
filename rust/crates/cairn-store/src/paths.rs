//! Path helpers shared across crates.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

/// Canonicalize-style paths with the Windows verbatim (`\\?\`) prefix removed,
/// matching plain `Path.resolve()`-style paths.
pub fn strip_verbatim(path: std::path::PathBuf) -> std::path::PathBuf {
    #[cfg(windows)]
    if let Some(s) = path.as_os_str().to_str() {
        if let Some(rest) = s.strip_prefix(r"\\?\UNC\") {
            return std::path::PathBuf::from(format!(r"\\{rest}"));
        }
        if let Some(rest) = s.strip_prefix(r"\\?\") {
            return std::path::PathBuf::from(rest);
        }
    }
    path
}

/// `~`-expanded form of a path string; unchanged when not `~`-prefixed.
fn expanduser(path: &str) -> String {
    if let Some(rest) = path.strip_prefix("~/")
        && let Some(home) = crate::home::platform_home()
    {
        return home.join(rest).to_string_lossy().into_owned();
    }
    path.to_string()
}

/// Effective cairn home: a non-empty, `~`-expanded CAIRN_HOME, else
/// `<platform home>/.cairn`; None when neither resolves.
pub fn cairn_home() -> Option<PathBuf> {
    if let Some(home) = std::env::var("CAIRN_HOME").ok().filter(|v| !v.is_empty()) {
        return Some(PathBuf::from(expanduser(&home)));
    }
    crate::home::platform_home().map(|h| h.join(".cairn"))
}

/// Absolute cairn home: `resolve_path` over [`cairn_home`], with a
/// cwd-resolved `.cairn` fallback when neither env nor platform home exists.
pub fn current_cairn_home() -> PathBuf {
    cairn_home()
        .map(|home| resolve_path(&home))
        .unwrap_or_else(|| resolve_path(Path::new(".cairn")))
}

/// True when `key` is the canonical store-key shape: the 16-char lowercase-hex
/// sha256 prefix the register path mints.
fn is_store_key(key: &str) -> bool {
    key.len() == 16
        && key
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// `Path.resolve()` (non-strict): absolute with symlinks resolved where possible.
pub fn resolve_path(path: &Path) -> PathBuf {
    if let Ok(resolved) = std::fs::canonicalize(path) {
        return strip_verbatim(resolved);
    }
    let joined = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()
            .map(|cwd| cwd.join(path))
            .unwrap_or_else(|_| path.to_path_buf())
    };
    let mut out = PathBuf::new();
    for component in joined.components() {
        match component {
            std::path::Component::CurDir => {}
            std::path::Component::ParentDir => {
                out.pop();
            }
            other => out.push(other.as_os_str()),
        }
    }
    out
}

/// The {workspace path: store key} registry, sorted by path; empty when absent/corrupt.
pub fn load_registry() -> BTreeMap<String, String> {
    let Some(home) = cairn_home() else {
        return BTreeMap::new();
    };
    let Ok(text) = std::fs::read_to_string(home.join("workspaces.json")) else {
        return BTreeMap::new();
    };
    let Ok(parsed) = serde_json::from_str::<serde_json::Value>(&text) else {
        return BTreeMap::new();
    };
    parsed
        .as_object()
        .map(|map| {
            map.iter()
                .filter_map(|(ws, key)| {
                    let key = key.as_str()?;
                    // Registry values are joined into store paths; keep only
                    // canonical keys so a crafted file cannot steer them.
                    is_store_key(key).then(|| (ws.clone(), key.to_string()))
                })
                .collect()
        })
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Env-mutating tests serialize here; values restore before returning.
    static ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

    fn temp_home(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "cairn-paths-{tag}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .expect("clock")
                .as_nanos()
        ));
        std::fs::create_dir_all(&dir).expect("temp home");
        dir
    }

    #[test]
    fn cairn_home_env_wins_and_empty_falls_back() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|p| p.into_inner());
        let saved = std::env::var_os("CAIRN_HOME");
        // SAFETY: ENV_LOCK held; restored before returning.
        unsafe { std::env::set_var("CAIRN_HOME", "/tmp/cairn-paths-home") };
        assert_eq!(cairn_home(), Some(PathBuf::from("/tmp/cairn-paths-home")));
        unsafe { std::env::set_var("CAIRN_HOME", "") };
        assert!(
            cairn_home().is_none_or(|h| h.ends_with(".cairn")),
            "empty CAIRN_HOME must fall back to the platform home, not the empty path"
        );
        match saved {
            // SAFETY: restoring the saved value.
            Some(v) => unsafe { std::env::set_var("CAIRN_HOME", v) },
            None => unsafe { std::env::remove_var("CAIRN_HOME") },
        }
    }

    #[test]
    fn current_cairn_home_expands_and_absolutizes() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|p| p.into_inner());
        let saved = std::env::var_os("CAIRN_HOME");
        // SAFETY: ENV_LOCK held; restored before returning.
        unsafe { std::env::set_var("CAIRN_HOME", "~/cairn-paths-data") };
        let home = current_cairn_home();
        assert!(home.is_absolute(), "{home:?}");
        assert!(
            !home.to_string_lossy().contains('~'),
            "tilde must expand: {home:?}"
        );
        unsafe { std::env::set_var("CAIRN_HOME", "reldata") };
        let home = current_cairn_home();
        assert!(
            home.is_absolute(),
            "relative CAIRN_HOME must absolutize: {home:?}"
        );
        assert!(
            home.ends_with("reldata"),
            "relative CAIRN_HOME must keep its tail: {home:?}"
        );
        match saved {
            // SAFETY: restoring the saved value.
            Some(v) => unsafe { std::env::set_var("CAIRN_HOME", v) },
            None => unsafe { std::env::remove_var("CAIRN_HOME") },
        }
    }

    #[test]
    fn registry_drops_non_canonical_store_keys() {
        let _guard = ENV_LOCK.lock().unwrap_or_else(|p| p.into_inner());
        let dir = temp_home("registry");
        std::fs::write(
            dir.join("workspaces.json"),
            r#"{
                "/proj/ok": "0123456789abcdef",
                "/proj/traversal": "../../victim",
                "/proj/short": "k1",
                "/proj/upper": "0123456789ABCDEF",
                "/proj/abs": "/abs/escape"
            }"#,
        )
        .expect("registry fixture");
        let saved = std::env::var_os("CAIRN_HOME");
        // SAFETY: ENV_LOCK held; restored before returning.
        unsafe { std::env::set_var("CAIRN_HOME", &dir) };
        let reg = load_registry();
        match saved {
            // SAFETY: restoring the saved value.
            Some(v) => unsafe { std::env::set_var("CAIRN_HOME", v) },
            None => unsafe { std::env::remove_var("CAIRN_HOME") },
        }
        assert_eq!(
            reg.keys().collect::<Vec<_>>(),
            [&"/proj/ok".to_string()],
            "only the canonical key survives: {reg:?}"
        );
        assert_eq!(
            reg.get("/proj/ok").map(String::as_str),
            Some("0123456789abcdef")
        );
        let _ = std::fs::remove_dir_all(&dir);
    }
}
