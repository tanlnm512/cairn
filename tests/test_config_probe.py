"""Failing tests for the machine-readable resolution probe (FR-005; TC-009).

`cairn config --json` must exit 0 emitting a single JSON object with the keys
``cairn_home``, ``workspace``, ``db`` and ``knowledge`` matching the
environment in effect — the probe FR-006 spawns with a registration's exact
binary+env (tech-spec D-004/D-005) and that install-time verification and the
doctor environment audit compare resolved stores against.

Survey gap (FR-005 PARTIAL): `cairn config` prints all four values text-only
(core.py:186-191) and `--db` covers one field (core.py:150-170); no JSON probe
emitting all four exists. Every test here is RED until the `--json` flag
lands, failing on click's "No such option: --json" (exit 2) — never on
anything else.

Hermeticity (CONSTITUTION C-04): no eager `cairn.cli` import (lazy inside the
tests, mirroring the test_install_uninstall_fidelity house pattern), no
subprocess patching, and the paths.py import-time ``CAIRN_HOME`` binding pit
is handled the way tests/conftest.py handles CONFIG_FILE — the module globals
are re-pointed into the tmp sandbox so nothing here can touch the real
~/.cairn. The probe must also be read-only: running it must NOT auto-register
the cwd workspace (the resolve_store side effect its paths.py docstring
describes), because verifiers spawn it from arbitrary cwds.
"""
from __future__ import annotations

import json
from pathlib import Path

EXPECTED_KEYS = {"cairn_home", "workspace", "db", "knowledge"}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _repoint_bindings(monkeypatch, home: Path) -> None:
    """Re-point paths.py's import-time CAIRN_HOME bindings into the sandbox.

    paths.py binds CAIRN_HOME / REGISTRY_FILE from os.environ at import time,
    which under pytest is collection time (before the hermetic fixture runs) —
    the same pit conftest.py already fixes for CONFIG_FILE. resolve_store and
    _load_registry read these module globals at call time, so re-pointing them
    keeps every read (and any accidental write) inside the tmp sandbox.
    """
    from cairn import paths

    monkeypatch.setattr(paths, "CAIRN_HOME", home)
    monkeypatch.setattr(paths, "REGISTRY_FILE", home / "workspaces.json")


def _expected_store(home: Path, ws: Path) -> dict[str, str]:
    """The four values FR-005 requires, derived from the environment in effect."""
    from cairn.paths import store_key

    key = store_key(ws)
    return {
        "cairn_home": str(home),
        "workspace": str(ws),
        "db": str(home / key / ".kg"),
        "knowledge": str(home / key / ".knowledge"),
    }


def _invoke_config_json() -> object:
    """Invoke `cairn config --json` through the CLI test runner (lazy import)."""
    from click.testing import CliRunner

    from cairn.cli import main

    return CliRunner().invoke(main, ["config", "--json"], catch_exceptions=False)


# --------------------------------------------------------------------------
# The probe (TC-009)
# --------------------------------------------------------------------------

def test_config_json_custom_home_reports_all_four_keys(tmp_path, monkeypatch):
    """Given CAIRN_HOME at a custom folder and the workspace resolved from the
    current directory, the probe emits one JSON object whose four keys match
    the environment in effect (FR-005; TC-009)."""
    home = tmp_path / "custom_home"
    ws = tmp_path / "ws"
    ws.mkdir()
    _repoint_bindings(monkeypatch, home)
    monkeypatch.setenv("CAIRN_HOME", str(home))
    monkeypatch.chdir(ws)

    result = _invoke_config_json()

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert set(payload) == EXPECTED_KEYS
    assert payload == _expected_store(home, ws.resolve()), (
        "probe output must match the environment in effect"
    )


def test_config_json_default_home_reports_default_paths(tmp_path, monkeypatch):
    """Repeating the run with CAIRN_HOME unset reports the default home
    (~/.cairn of the sandboxed HOME), not some stale or custom location
    (TC-009)."""
    default_home = Path.home() / ".cairn"  # Path.home is patched into the tmp sandbox by conftest
    ws = tmp_path / "ws"
    ws.mkdir()
    _repoint_bindings(monkeypatch, default_home)
    monkeypatch.delenv("CAIRN_HOME", raising=False)
    monkeypatch.chdir(ws)

    result = _invoke_config_json()

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert set(payload) == EXPECTED_KEYS
    assert payload == _expected_store(default_home, ws.resolve()), (
        "default-home run must report the default home and its derived paths"
    )


def test_config_json_exits_zero_with_single_json_document(tmp_path, monkeypatch):
    """The probe is a scripting surface (sibling of the machine-readable --db
    flag): exit 0, and stdout is exactly one JSON document."""
    home = tmp_path / "custom_home"
    ws = tmp_path / "ws"
    ws.mkdir()
    _repoint_bindings(monkeypatch, home)
    monkeypatch.setenv("CAIRN_HOME", str(home))
    monkeypatch.chdir(ws)

    result = _invoke_config_json()

    assert result.exit_code == 0, result.output
    # json.loads on the whole stdout only succeeds for a single JSON document.
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)


def test_config_json_is_read_only_no_workspace_registration(tmp_path, monkeypatch):
    """Running the probe must NOT auto-register the cwd workspace (the
    resolve_store side effect described in its paths.py docstring): the
    workspaces.json registry gains no entry — FR-006 spawns the probe from
    arbitrary cwds and verification must not mutate the registry."""
    from cairn.paths import is_registered

    home = tmp_path / "custom_home"
    ws = tmp_path / "ws"
    ws.mkdir()
    _repoint_bindings(monkeypatch, home)
    monkeypatch.setenv("CAIRN_HOME", str(home))
    monkeypatch.chdir(ws)

    registry = home / "workspaces.json"
    assert not registry.exists(), "precondition: sandbox registry starts empty"

    result = _invoke_config_json()

    assert result.exit_code == 0, result.output
    assert not registry.exists(), (
        "the probe must not create/write workspaces.json — it is read-only"
    )
    assert not is_registered(ws.resolve()), (
        "the probe must not register the cwd workspace"
    )
