"""The parity oracle replays without the Python engine."""

import os
import pwd
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts" / "rust_migration"
SERVE_EXAMPLE = REPO_ROOT / "rust" / "target" / "release" / "examples" / "serve_search"


def _real_home() -> Path:
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def _cargo_dir() -> Path | None:
    which = shutil.which("cargo")
    cand = Path(which).parent if which else _real_home() / ".cargo" / "bin"
    return cand if (cand / "cargo").exists() else None


def _toolchain_env(env: dict) -> dict:
    """Expose the real rustup/cargo: the suite's autouse HOME pin hides it."""
    cargo_dir = _cargo_dir()
    if cargo_dir is None:
        return env
    out = dict(env)
    out["PATH"] = os.pathsep.join([str(cargo_dir), env.get("PATH", "")])
    for var, path in (("CARGO_HOME", _real_home() / ".cargo"),
                      ("RUSTUP_HOME", _real_home() / ".rustup")):
        if path.is_dir() and var not in out:
            out[var] = str(path)
    return out


pytestmark = [
    pytest.mark.infra,
    pytest.mark.skipif(
        not SERVE_EXAMPLE.exists(),
        reason="serve_search example absent (cargo build --release -p cairn-daemon --example serve_search)",
    ),
]


def _engine_free_tree(tmp_path: Path) -> Path:
    """A checkout whose src/ holds no cairn package: the oracle must still run."""
    root = tmp_path / "repo"
    dest = root / "scripts" / "rust_migration"
    dest.mkdir(parents=True)
    for script in SCRIPTS.glob("*.py"):
        shutil.copy2(script, dest / script.name)
    (root / "src").mkdir()
    os.symlink(REPO_ROOT / "rust", root / "rust", target_is_directory=True)
    return root


def test_parity_replay_needs_no_python_engine(tmp_path):
    root = _engine_free_tree(tmp_path)
    parity = root / "scripts" / "rust_migration" / "parity.py"
    env = _toolchain_env({**os.environ, "CAIRN_LIB": "/tmp/__no_such_lib__"})
    # PYTHONPATH could smuggle the engine in past -S.
    env.pop("PYTHONPATH", None)
    # -S keeps the venv's editable cairn out: no engine importable anywhere.
    proc = subprocess.run(
        [sys.executable, "-S", str(parity), "--store", "self"],
        capture_output=True,
        text=True,
        timeout=900,
        env=env,
    )
    assert proc.returncode == 0, (proc.stderr + proc.stdout)[-3000:]
    assert "self: 26/26 tools golden-equal" in proc.stdout
