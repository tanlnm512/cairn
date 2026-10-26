//! Python sidecar discovery: the interpreter whose site-packages carry an
//! optional cairn companion package (e.g. the `cairn_intel_ingest` converter).

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

/// Set by the PyPI launcher to its own interpreter before it execs the engine.
pub const SIDECAR_PYTHON_ENV: &str = "CAIRN_SIDECAR_PYTHON";

/// The sidecar interpreter: the launcher's, else one next to this binary
/// (venv bin dir), else the PATH python3.
pub fn python() -> Option<PathBuf> {
    if let Some(launcher) = std::env::var_os(SIDECAR_PYTHON_ENV) {
        let launcher = PathBuf::from(launcher);
        if launcher.is_file() {
            return Some(launcher);
        }
    }
    if let Ok(exe) = std::env::current_exe()
        && let Some(dir) = exe.parent()
    {
        for name in ["python3", "python"] {
            let candidate = dir.join(name);
            if candidate.is_file() {
                return Some(candidate);
            }
        }
    }
    std::env::var_os("PATH").and_then(|path| {
        std::env::split_paths(&path)
            .map(|dir| dir.join("python3"))
            .find(|candidate| candidate.is_file())
    })
}

/// The sidecar interpreter when `module` imports there.
pub fn python_with(module: &str) -> Option<PathBuf> {
    let python = python()?;
    imports(&python, module).then_some(python)
}

fn imports(python: &Path, module: &str) -> bool {
    Command::new(python)
        .arg("-c")
        .arg(format!("import {module}"))
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|status| status.success())
}
