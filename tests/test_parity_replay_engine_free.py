"""The parity and CLI golden oracles replay without the Python engine."""

import os
import pwd
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts" / "rust_migration"
SERVE_EXAMPLE = REPO_ROOT / "rust" / "target" / "release" / "examples" / "serve_search"
RUST_BIN = REPO_ROOT / "rust" / "target" / "release" / "cairn"


def _real_home() -> Path:
    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except KeyError:
        # uid without a passwd entry (mapped-uid containers): fall back to HOME
        return Path(os.environ.get("HOME", "/root"))


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


pytestmark = pytest.mark.infra


def _engine_free_env() -> dict:
    env = _toolchain_env({**os.environ, "CAIRN_LIB": "/tmp/__no_such_lib__"})
    # PYTHONPATH could smuggle a package in past -S.
    env.pop("PYTHONPATH", None)
    return env


def _engine_free_tree(tmp_path: Path) -> Path:
    """A checkout holding only the oracle scripts and the rust tree."""
    root = tmp_path / "repo"
    dest = root / "scripts" / "rust_migration"
    dest.mkdir(parents=True)
    for script in SCRIPTS.glob("*.py"):
        shutil.copy2(script, dest / script.name)
    os.symlink(REPO_ROOT / "rust", root / "rust", target_is_directory=True)
    return root


@pytest.mark.skipif(
    not SERVE_EXAMPLE.exists(),
    reason="serve_search example absent (cargo build --release -p cairn-daemon --example serve_search)",
)
def test_parity_replay_needs_no_python_engine(tmp_path):
    root = _engine_free_tree(tmp_path)
    parity = root / "scripts" / "rust_migration" / "parity.py"
    # -S keeps the venv's packages out: nothing python-side to import.
    proc = subprocess.run(
        [sys.executable, "-S", str(parity), "--store", "self"],
        capture_output=True,
        text=True,
        timeout=900,
        env=_engine_free_env(),
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "self: 26/26 tools golden-equal" in proc.stdout


@pytest.mark.skipif(
    not RUST_BIN.exists(),
    reason="release binary absent (cargo build --release -p cairn-cli)",
)
def test_cli_golden_replay_needs_no_python_engine(tmp_path):
    root = _engine_free_tree(tmp_path)
    harness = root / "scripts" / "rust_migration" / "cli_golden.py"
    # No --rust-bin pin: the harness builds the release binary itself, so a
    # stale rust/target artifact can never be replayed against the goldens.
    proc = subprocess.run(
        [sys.executable, "-S", str(harness)],
        capture_output=True,
        text=True,
        timeout=900,
        env=_engine_free_env(),
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    verdict = re.search(r"(\d+)/(\d+) commands golden-equal", proc.stdout)
    assert verdict and verdict.group(1) == verdict.group(2), proc.stdout[-3000:]
