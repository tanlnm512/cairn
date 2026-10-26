"""The PyPI package is the engine binary: maturin bin wheel, no launcher, no setuptools glue."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE = REPO_ROOT / "src" / "cairn"
PYPROJECT = REPO_ROOT / "pyproject.toml"
# Modules that outlive the launcher until the Python retirement phase.
KEPT_MODULES = {"__init__.py", "communities.py"}


def _pyproject() -> str:
    return PYPROJECT.read_text(encoding="utf-8")


def test_build_backend_is_maturin_bin_over_the_cli_crate():
    """FR-004: maturin installs the cairn-cli binary as the `cairn` executable."""
    text = _pyproject()
    assert re.search(r'^build-backend = "maturin"$', text, re.M)
    m = re.search(r"^\[tool\.maturin\]\n(.*?)(?:\n\n|\Z)", text, re.M | re.S)
    assert m, "[tool.maturin] section missing"
    assert re.search(r'^bindings = "bin"$', m.group(1), re.M)
    assert re.search(r'^manifest-path = "rust/crates/cairn-cli/Cargo\.toml"$', m.group(1), re.M)


def test_launcher_and_setuptools_glue_are_gone():
    """No console-script shim, package-data injection, setup.py or MANIFEST rule remains."""
    text = _pyproject()
    assert "[project.scripts]" not in text
    assert "[tool.setuptools" not in text
    for gone in ("setup.py", "MANIFEST.in", "src/cairn/_bin", "src/cairn/console.py", "src/cairn/paths.py"):
        assert not (REPO_ROOT / gone).exists(), gone


def test_package_holds_only_the_kept_modules():
    assert {p.name for p in PACKAGE.glob("*.py")} == KEPT_MODULES
    stray = sorted(p.name for p in PACKAGE.iterdir() if p.name != "__pycache__" and p.name not in KEPT_MODULES)
    assert stray == [], stray


def test_package_imports_on_a_bare_interpreter():
    """Every kept module imports with the stdlib alone: the sidecar extra is lazy."""
    env = {**os.environ, "PYTHONPATH": str(PACKAGE.parent)}
    proc = subprocess.run(
        [sys.executable, "-S", "-c", "import cairn, cairn.communities"],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, proc.stderr


def test_wheel_declares_no_runtime_dependencies():
    assert re.search(r"^dependencies = \[\]$", _pyproject(), re.M), "the bin wheel must stay dependency-free"
