//! Executable lookup on PATH, shared by daemon launch and client detection.

use std::path::{Path, PathBuf};

/// Executable candidate paths for `name` inside `dir`: PATHEXT extensions on
/// Windows (the plain name last), the plain name only elsewhere.
pub fn exec_candidates(dir: &Path, name: &str) -> Vec<PathBuf> {
    #[cfg(windows)]
    {
        let mut out: Vec<PathBuf> = std::env::var("PATHEXT")
            .unwrap_or_else(|_| ".COM;.EXE;.BAT;.CMD".to_string())
            .split(';')
            .filter(|ext| !ext.is_empty())
            .map(|ext| dir.join(format!("{name}{ext}")))
            .collect();
        out.push(dir.join(name));
        out
    }
    #[cfg(not(windows))]
    {
        vec![dir.join(name)]
    }
}

/// First executable-looking `name` on `PATH`; None when absent.
pub fn which(name: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    for dir in std::env::split_paths(&path) {
        if dir.as_os_str().is_empty() {
            continue;
        }
        for cand in exec_candidates(&dir, name) {
            if cand.is_file() {
                return Some(cand);
            }
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn exec_candidates_cover_the_plain_name_on_this_platform() {
        let dir = Path::new("/bin");
        let cands = exec_candidates(dir, "cairn");
        assert!(cands.contains(&dir.join("cairn")));
        if cfg!(windows) {
            assert!(cands.contains(&dir.join("cairn.exe")));
        } else {
            assert_eq!(cands.len(), 1);
        }
    }

    #[test]
    fn which_finds_a_file_created_on_the_path() {
        let dir = std::env::temp_dir().join(format!("cairn-which-{}", std::process::id()));
        fs::create_dir_all(&dir).expect("dir");
        let name = format!("cairn-which-probe-{}", std::process::id());
        let plain = dir.join(&name);
        fs::write(&plain, b"probe").expect("probe");
        let old = std::env::var_os("PATH");
        // SAFETY: restored before the test returns.
        unsafe {
            std::env::set_var("PATH", &dir);
        }
        let found = which(&name);
        match old {
            // SAFETY: restoring the saved value.
            Some(v) => unsafe { std::env::set_var("PATH", v) },
            None => unsafe { std::env::remove_var("PATH") },
        }
        assert_eq!(found, Some(plain));
        let _ = fs::remove_dir_all(&dir);
    }
}
