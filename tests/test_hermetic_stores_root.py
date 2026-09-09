"""Regression guard: ``_hermetic_env`` must re-point cairn.paths' import-time
stores-root bindings into the per-test sandbox.

``cairn/paths.py`` derives CAIRN_HOME, REGISTRY_FILE, CONFIG_FILE, and
SHARED_LIB from the environment once, at import time (under pytest that is
collection time, before any fixture runs). The autouse fixture scrubs the
CAIRN_* env vars and points CAIRN_HOME into the sandbox, but consumers that
read the module ATTRIBUTES at call time only see the sandbox when the
fixture patches the attributes themselves:

* the dashboard enumerates stores via ``paths.CAIRN_HOME`` on every page
  render (the store switcher) and on /workspaces -- an unpatched attribute
  renders the dev machine's real stores into test pages, so any test
  asserting on page data can match real workspace labels;
* ``register_workspace``/``_save_registry`` write via ``CAIRN_HOME`` and
  ``REGISTRY_FILE`` -- unpatched, a test's registry write lands in the
  real ``~/.cairn``.

``_REAL_STORE_HOME`` is captured at module import (collection time, before
any fixture patches anything): exactly what paths.py bound.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

# What paths.py bound at ITS import time (collection, pre-fixture).
_REAL_STORE_HOME = Path(
    os.environ.get("CAIRN_HOME", str(Path.home() / ".cairn"))
)

# base.html carries the switcher's valid-key set on every page:
# data-valid-stores='["<16-hex>", ...]'.
_VALID_STORES_RE = re.compile(r"data-valid-stores='([^']*)'")
# /workspaces renders each store's key inside <code>...</code>.
_ROW_KEY_RE = re.compile(r"<code>([0-9a-f]{16})</code>")

# 16 hex chars, the store_key() layout; a literal keeps the seeded store
# addressable in both extraction surfaces.
_SEED_KEY = "0f1e2d3c4b5a6978"


def _sandbox() -> Path:
    """The sandbox stores root the fixture installed (env side)."""
    return Path(os.environ["CAIRN_HOME"])


def _rendered_store_keys(html: str) -> set:
    """Every store key a rendered page carries, from both surfaces: the
    every-page switcher seed and the /workspaces rows."""
    keys: set = set()
    match = _VALID_STORES_RE.search(html)
    if match:
        keys |= set(json.loads(match.group(1)))
    keys |= set(_ROW_KEY_RE.findall(html))
    return keys


def _seed_sandbox_store(sandbox: Path, tmp_path: Path) -> Path:
    """One populated sandbox store + its registry entry, so the pages under
    test have exactly one sandbox store to enumerate."""
    from cairn.graph.schema import get_db

    (sandbox / _SEED_KEY).mkdir(parents=True, exist_ok=True)
    conn = get_db(str(sandbox / _SEED_KEY / ".kg"))
    conn.close()
    ws = tmp_path / "ws-fixture"
    ws.mkdir(exist_ok=True)
    (sandbox / "workspaces.json").write_text(
        json.dumps({str(ws): _SEED_KEY}), encoding="utf-8"
    )
    return ws


def test_fixture_repoints_paths_module_bindings_into_sandbox():
    """The fixture's env vars and paths.py's import-time attributes must
    agree: every stores-layout attribute lives in the sandbox, never at the
    pre-fixture (real machine) binding."""
    from cairn import paths

    sandbox = _sandbox()
    assert paths.CAIRN_HOME == sandbox
    assert paths.REGISTRY_FILE == sandbox / "workspaces.json"
    assert paths.CONFIG_FILE == sandbox / "config.json"
    assert paths.SHARED_LIB == sandbox / "lib"
    # shared_lib_path() derives from the patched attribute too (CAIRN_LIB
    # is scrubbed suite-wide, so no override is in play).
    assert paths.shared_lib_path() == paths.SHARED_LIB / paths._abi_tag()
    assert paths.CAIRN_HOME != _REAL_STORE_HOME


def test_dashboard_renders_only_sandbox_store_keys(tmp_path):
    """Every store key a rendered dashboard page carries -- the every-page
    switcher seed AND the /workspaces rows -- must come from the sandbox
    home: the seeded sandbox store is listed, and nothing else is."""
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    from cairn import paths
    from cairn.dashboard.app import create_app

    # The seam under test is the module attribute (what the dashboard reads
    # per request), not the env var the fixture also sets.
    assert paths.CAIRN_HOME == _sandbox()
    ws = _seed_sandbox_store(_sandbox(), tmp_path)

    client = TestClient(create_app(db_path=str(tmp_path / "launch.db")))
    for path in ("/", "/workspaces"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        rendered = _rendered_store_keys(resp.text)
        assert rendered == {_SEED_KEY}, (
            f"{path}: rendered store keys {sorted(rendered)} are not exactly "
            f"the sandbox store {{{_SEED_KEY}}}"
        )
        assert str(ws) in resp.text, path  # the sandbox store's identity
        # Belt: the pre-fixture (real machine) store home never appears.
        assert str(_REAL_STORE_HOME) not in resp.text, path


def test_workspace_registration_stays_in_sandbox(tmp_path):
    """register_workspace resolves its store under paths.CAIRN_HOME and
    writes paths.REGISTRY_FILE: both must land in the sandbox -- the real
    machine's registry is never created nor appended to."""
    from cairn import paths

    sandbox = _sandbox()
    real_registry = _REAL_STORE_HOME / "workspaces.json"
    ws = tmp_path / "registered-ws"
    ws.mkdir()
    ws_str = str(ws.resolve())
    real_had_ws = (
        real_registry.exists()
        and ws_str in real_registry.read_text(encoding="utf-8")
    )
    assert not real_had_ws  # the fixture workspace is brand new

    stored = paths.register_workspace(ws)

    assert paths.CAIRN_HOME == sandbox
    assert stored.home.parent == sandbox
    assert stored.home == sandbox / paths.store_key(ws.resolve())
    sandbox_registry = sandbox / "workspaces.json"
    assert ws_str in sandbox_registry.read_text(encoding="utf-8")
    # The real registry stays untouched: never created, and never gained
    # the test's workspace entry.
    if real_registry.exists():
        assert ws_str not in real_registry.read_text(encoding="utf-8")
