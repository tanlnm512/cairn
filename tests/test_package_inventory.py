"""The PyPI package is the engine binary: maturin bin wheel, no Python package, no runtime extras."""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE = REPO_ROOT / "src" / "cairn"
PYPROJECT = REPO_ROOT / "pyproject.toml"


def _pyproject() -> str:
    return PYPROJECT.read_text(encoding="utf-8")


def _extras(text: str) -> set[str]:
    m = re.search(r"^\[project\.optional-dependencies\]\n(.*?)(?:\n\n|\Z)", text, re.M | re.S)
    assert m, "[project.optional-dependencies] section missing"
    return set(re.findall(r"^(\w[\w.-]*) = \[", m.group(1), re.M))


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


def test_the_python_package_tree_is_gone():
    """FR-010: nothing Python ships under src/; the wheel's only payload is the engine binary."""
    assert not PACKAGE.exists(), "src/cairn/ must not exist"
    assert not (REPO_ROOT / "src").exists() or not any(
        (REPO_ROOT / "src").iterdir()
    ), "src/ must hold nothing"


def test_only_dev_extras_remain():
    """FR-010: the runtime extras (graph-analytics, ingest) are retired; test/dev are tooling only."""
    assert _extras(_pyproject()) <= {"test", "dev"}
    text = _pyproject()
    assert "graph-analytics" not in text and "networkx" not in text


def test_wheel_declares_no_runtime_dependencies():
    assert re.search(r"^dependencies = \[\]$", _pyproject(), re.M), "the bin wheel must stay dependency-free"


def test_commitizen_version_files_do_not_reference_the_deleted_package():
    m = re.search(r"^version_files = \[(.*?)\]", _pyproject(), re.M | re.S)
    assert m, "commitizen version_files missing"
    assert "src/cairn" not in m.group(1)
