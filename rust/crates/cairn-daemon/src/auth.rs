//! Bearer API-key resolution and constant-time credential checks (the `auth.py` contract).

use std::env;
use std::fs;
use std::path::PathBuf;

/// Effective CAIRN_HOME: the env value (absolute) when set, else `~/.cairn`.
pub fn cairn_home() -> PathBuf {
    let home = match env::var("CAIRN_HOME") {
        Ok(v) if !v.trim().is_empty() => Some(PathBuf::from(v)),
        _ => cairn_store::home::platform_home().map(|h| h.join(".cairn")),
    };
    match home {
        Some(p) if p.is_absolute() => p,
        Some(p) => env::current_dir().map(|cwd| cwd.join(&p)).unwrap_or(p),
        None => PathBuf::from(".cairn"),
    }
}

/// Return the explicit key when non-empty, else CAIRN_MCP_API_KEY, else None.
pub fn resolve_api_key(explicit: Option<&str>) -> Option<String> {
    if let Some(key) = explicit
        && !key.is_empty()
    {
        return Some(key.to_string());
    }
    env::var("CAIRN_MCP_API_KEY").ok().filter(|k| !k.is_empty())
}

/// Return the init-persisted key from CAIRN_HOME/mcp-api-key, or None when absent or empty.
pub fn persisted_api_key() -> Option<String> {
    let value = fs::read_to_string(cairn_home().join("mcp-api-key")).ok()?;
    let value = value.trim();
    if value.is_empty() {
        None
    } else {
        Some(value.to_string())
    }
}

/// Resolution chain for consumers of the daemon's key: env, then the persisted
/// init file (`resolve_api_key(None) or persisted_api_key()` semantics).
pub fn client_key() -> Option<String> {
    resolve_api_key(None).or_else(persisted_api_key)
}

/// Length-safe constant-time byte equality; false when lengths differ.
fn constant_time_eq(a: &str, b: &str) -> bool {
    if a.len() != b.len() {
        return false;
    }
    let mut diff = 0u8;
    for (x, y) in a.bytes().zip(b.bytes()) {
        diff |= x ^ y;
    }
    diff == 0
}

/// Return true when provided carries the expected Bearer credential (raw header
/// value or bare token); anything missing or malformed is false.
pub fn bearer_key_ok(provided: Option<&str>, expected: &str) -> bool {
    let Some(provided) = provided.filter(|p| !p.is_empty()) else {
        return false;
    };
    if expected.is_empty() {
        return false;
    }
    let parts: Vec<&str> = provided.split_whitespace().collect();
    let credential = match parts.as_slice() {
        [scheme, token] if scheme.eq_ignore_ascii_case("bearer") => *token,
        [token] => *token,
        _ => return false,
    };
    constant_time_eq(credential, expected)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resolve_prefers_explicit_then_env() {
        // The env matrix lives in tests/auth_lifecycle.rs (process-global env).
        assert_eq!(resolve_api_key(Some("k")), Some("k".to_string()));
        assert_eq!(resolve_api_key(Some("")), None);
        assert_eq!(resolve_api_key(None), None);
    }

    #[test]
    fn bearer_matrix() {
        assert!(bearer_key_ok(Some("Bearer tok"), "tok"));
        assert!(bearer_key_ok(Some("bearer tok"), "tok"));
        assert!(bearer_key_ok(Some("BEARER tok"), "tok"));
        assert!(bearer_key_ok(Some("tok"), "tok"));
        assert!(!bearer_key_ok(Some("Bearer tok"), "other"));
        assert!(!bearer_key_ok(Some("tok"), "other"));
        assert!(!bearer_key_ok(Some("Basic tok"), "tok"));
        assert!(!bearer_key_ok(Some("Bearer a b"), "tok"));
        assert!(!bearer_key_ok(Some(""), "tok"));
        assert!(!bearer_key_ok(None, "tok"));
        assert!(!bearer_key_ok(Some("tok"), ""));
    }

    #[test]
    fn constant_time_eq_basics() {
        assert!(constant_time_eq("abc", "abc"));
        assert!(!constant_time_eq("abc", "abd"));
        assert!(!constant_time_eq("abc", "abcd"));
        assert!(!constant_time_eq("", "a"));
        assert!(constant_time_eq("", ""));
    }
}
