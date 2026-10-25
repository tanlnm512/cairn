"""G4e: the PyPI package holds only the console shim, binary discovery, and the sidecars."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE = REPO_ROOT / "src" / "cairn"
KEPT_MODULES = {
    "__init__.py",
    "console.py",
    "paths.py",
    "communities.py",
    "ingest_convert.py",
}


def test_package_holds_only_the_shim_discovery_and_sidecars():
    assert {p.name for p in PACKAGE.glob("*.py")} == KEPT_MODULES
    stray = sorted(
        p.name for p in PACKAGE.iterdir()
        if p.name not in {"_bin", "__pycache__"} and p.name not in KEPT_MODULES
    )
    assert stray == [], stray


def test_package_imports_on_a_bare_interpreter():
    """Every kept module imports with the stdlib alone: the sidecar extras are lazy."""
    env = {**os.environ, "PYTHONPATH": str(PACKAGE.parent)}
    proc = subprocess.run(
        [sys.executable, "-S", "-c",
         "import cairn, cairn.console, cairn.paths, cairn.communities, cairn.ingest_convert"],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, proc.stderr


def test_wheel_declares_no_runtime_dependencies():
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r"^dependencies = \[\]$", text, re.M), "the shim must stay dependency-free"
