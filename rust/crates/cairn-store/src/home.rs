//! Platform home-directory resolution shared across the crates.

use std::path::PathBuf;

fn non_empty_var(key: &str) -> Option<String> {
    std::env::var(key)
        .ok()
        .map(|v| v.trim().to_string())
        .filter(|v| !v.is_empty())
}

/// The user's home directory: `HOME` when set ( POSIX and POSIX-like shells on
/// Windows), else the platform default (`USERPROFILE`, then
/// `HOMEDRIVE`+`HOMEPATH`); None when no source resolves.
pub fn platform_home() -> Option<PathBuf> {
    if let Some(home) = non_empty_var("HOME") {
        return Some(PathBuf::from(home));
    }
    #[cfg(windows)]
    {
        if let Some(profile) = non_empty_var("USERPROFILE") {
            return Some(PathBuf::from(profile));
        }
        let drive = non_empty_var("HOMEDRIVE")?;
        let path = non_empty_var("HOMEPATH")?;
        Some(PathBuf::from(format!("{drive}{path}")))
    }
    #[cfg(not(windows))]
    {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn home_env_wins_and_missing_home_is_none() {
        let key = if cfg!(windows) { "USERPROFILE" } else { "HOME" };
        let saved = std::env::var_os(key);
        // SAFETY: single-threaded env test; restored before returning.
        unsafe {
            std::env::set_var(key, "/tmp/cairn-home-test");
        }
        assert_eq!(platform_home(), Some(PathBuf::from("/tmp/cairn-home-test")));
        unsafe {
            std::env::remove_var(key);
        }
        if !cfg!(windows) {
            assert_eq!(platform_home(), None);
        }
        match saved {
            // SAFETY: restoring the saved value.
            Some(v) => unsafe { std::env::set_var(key, v) },
            None => unsafe { std::env::remove_var(key) },
        }
    }
}
