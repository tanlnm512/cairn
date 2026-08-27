"""Shared pytest fixtures for the cairn test suite.

Consolidates the duplicated setup that previously appeared per-test-file:

* ``fresh_db``  -- an in-memory SQLite connection with the full schema
  (``_apply_schema``) already applied, Row factory enabled, FKs ON. Each
  test gets its own isolated DB; nothing is shared between tests.

* ``hash_backend`` -- forces the dep-free hash embedder
  (``CAIRN_EMBED_BACKEND=hash``) and resets the cached backend before
  and after the test, so semantic-stack tests don't need torch / a model
  download. Apply with ``@pytest.fixture(autouse=True)`` per-test, or just
  request the fixture by name where needed.

Tests that need specific symbol/file rows still seed them locally -- the
fixture only removes the boilerplate of creating the connection and running
schema setup, which was duplicated 6x.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

import pytest

from cairn.graph.schema import _apply_schema

# Names agent-client detection probes via shutil.which (agent_install/detect.py
# + clients/*). Blocked suite-wide so a developer machine with real CLIs
# installed behaves like a clean CI runner.
_AGENT_CLIS = ("claude", "cursor", "droid", "agy", "opencode", "kilo", "omp")


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch, tmp_path):
    """Every test runs as if on a clean machine (suite-wide default).

    Two CI failures on one branch (2026-08-14) came from tests that were green
    locally only because of the dev machine's surroundings: an uninstall
    dry-run test that relied on agent CLIs being DETECTED (this machine has
    real claude/droid; a clean runner detects nothing), and a CLI test parsing
    click's interleaved stdout+stderr. This fixture makes the clean-runner
    environment the DEFAULT so such tests fail locally, at write time:

    * HOME/CAIRN_HOME point into the test's tmp sandbox (Path.home patched).
    * No CAIRN_* env leaks between tests (all cleared each run).
    * Agent CLIs are invisible to shutil.which (detection then depends only on
      what the test explicitly creates).
    * The macOS /Applications/Cursor.app probe (agent_install.detect) resolves
      inside the sandbox instead of the real machine.

    Tests that genuinely need the real environment can opt out with
    @pytest.mark.real_env -- justify it in a comment when you do.
    """
    home = tmp_path / "_home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda *a, **k: home)
    monkeypatch.setenv("HOME", str(home))
    sandbox_cairn = tmp_path / "_cairn_home"
    monkeypatch.setenv("CAIRN_HOME", str(sandbox_cairn))
    for var in [v for v in os.environ if v.startswith("CAIRN_")]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CAIRN_HOME", str(sandbox_cairn))

    # The config-file layer binds its path at import (the CAIRN_HOME
    # import-time binding pit): re-point it into the sandbox so a real
    # ~/.cairn/config.json on a dev machine cannot leak into suites.
    from cairn import paths as _paths

    monkeypatch.setattr(_paths, "CONFIG_FILE", sandbox_cairn / "config.json")
    _paths.reset_config_cache()

    real_which = shutil.which

    def _blocked_which(name, *a, **k):
        if name in _AGENT_CLIS:
            return None
        return real_which(name, *a, **k)

    monkeypatch.setattr(shutil, "which", _blocked_which)

    # detect.py's macOS probe: Path("/Applications/Cursor.app").exists().
    # Remap the module-local Path so absolute /Applications paths land in the
    # sandbox; every other Path behavior is unchanged.
    from cairn.agent_install import detect as _detect

    def _sandboxed_path(*args, **kw):
        # A plain factory, not a Path subclass: modern pathlib's flavour
        # dispatch silently bypasses a subclass __new__, but detect.py only
        # needs Path(...) as a constructor and Path.home -- both satisfied.
        # .home LATE-BINDS through the class attribute (Path.home at call
        # time) so a test's own Path.home re-patch still wins over this
        # fixture's -- stacking order must not change detection results.
        if len(args) == 1 and isinstance(args[0], str) and args[0].startswith("/Applications/"):
            return Path.home() / "_no_applications" / args[0].lstrip("/")
        return Path(*args, **kw)

    _sandboxed_path.home = lambda *a, **k: Path.home(*a, **k)
    monkeypatch.setattr(_detect, "Path", _sandboxed_path)
    yield


@pytest.fixture
def fresh_db() -> sqlite3.Connection:
    """A fresh in-memory SQLite connection with the full graph schema applied.

    Row factory is set. Foreign keys are LEFT OFF -- this matches what every
    per-file fixture did before consolidation (``_apply_schema`` alone does
    not enable FK; only ``schema.get_db()`` does). Some tests delete parent
    rows that have child references (e.g. embeddings referencing a symbol
    they then DELETE) and rely on FK being off to assert reap behavior;
    turning it on here would silently break those.

    Callers that need FK on can set it themselves via
    ``conn.execute("PRAGMA foreign_keys = ON")``.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def hash_backend(monkeypatch):
    """Force the dep-free hash embedder and reset the cached backend.

    Use as ``autouse=True`` in a test module that exercises the semantic
    stack but doesn't want to depend on sentence-transformers/torch. The
    cache reset on entry AND exit is what makes consecutive tests see a
    consistent backend even if an earlier test changed the env var.
    """
    monkeypatch.setenv("CAIRN_EMBED_BACKEND", "hash")
    from cairn.graph import embeddings as emb

    emb.reset_backend_cache()
    yield
    emb.reset_backend_cache()
