"""Tests for the wiki_generate MCP tool (28th tool).

The tool's registration and behavior are checked without booting the server or
calling ``run()``: the ``_server_core`` helpers the tool body uses (``_conn``/
``_bundle``) are monkeypatched onto the tool module (pattern:
tests/test_mcp_phase3.py), the tool function is invoked directly, and the
count/boot guard comes from the already-imported ``cairn.mcp_server.server``
submodule. All ``cairn.mcp_server.*`` imports are function-level and never
touch the package root (C-04).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from cairn.llm.tasks import list_tasks
from cairn.okf.bundle import OKFBundle


def _seed_graph(conn: sqlite3.Connection) -> None:
    """Seed two modules (alpha, beta) so the planner yields 3 pages."""
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('r1', 'r1', '/tmp/r1')")
    for fid, path in ((1, "alpha/one.py"), (2, "alpha/two.py"), (3, "beta/one.py")):
        conn.execute(
            "INSERT INTO files (id, repo_id, path, language) VALUES (?, 'r1', ?, 'python')",
            (fid, path),
        )
    for sid, fid, name in ((1, 1, "one"), (2, 2, "two"), (3, 3, "three")):
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, qualified_name, "
            "line_start, line_end) VALUES (?, ?, ?, 'function', ?, 1, 10)",
            (sid, fid, name, name),
        )
    conn.commit()


class _KeepOpenConn:
    """Delegates to the fixture conn; ``close()`` is a no-op so the tool's
    finally-close cannot kill the shared in-memory DB across calls."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def __getattr__(self, name: str):
        return getattr(self._conn, name)

    def close(self) -> None:
        pass


@pytest.fixture
def wiki_env(tmp_path: Path, fresh_db, monkeypatch):
    """Seeded graph conn + tmp OKFBundle, stubbed into the tool module."""
    _seed_graph(fresh_db)
    knowledge_dir = tmp_path / ".knowledge"
    (knowledge_dir / "_tasks").mkdir(parents=True)
    bundle = OKFBundle(knowledge_dir)

    import cairn.mcp_server.tools_wiki as tw

    monkeypatch.setattr(tw, "_conn", lambda: _KeepOpenConn(fresh_db))
    monkeypatch.setattr(tw, "_bundle", lambda: bundle)
    return bundle


def test_wiki_generate_is_registered_on_the_22_tool_surface():
    import cairn.mcp_server.server as server_mod
    from cairn.mcp_server._server_core import mcp

    tools = {t.name: t for t in mcp._tool_manager.list_tools()}
    assert "wiki_generate" in tools
    assert server_mod._EXPECTED_TOOL_COUNT == 22
    # The same boot guard run() calls: raises on any registration drift.
    server_mod.verify_tool_count()

    annotations = tools["wiki_generate"].annotations
    assert annotations is not None
    # Queues tasks + writes a manifest: not read-only, not destructive
    # (no data is removed), not idempotent (re-runs queue fresh attempts).
    assert annotations.readOnlyHint is False
    assert annotations.destructiveHint is False
    assert annotations.idempotentHint is False


def test_wiki_generate_returns_plan_and_queued_task_ids(wiki_env):
    from cairn.mcp_server.tools_wiki import wiki_generate

    out = wiki_generate(repo="r1")
    assert isinstance(out, str)
    for page_id in ("overview", "alpha", "beta"):
        assert page_id in out

    queued = list_tasks(wiki_env, kind="wiki-page")
    assert len(queued) == 3
    for task in queued:
        assert task.id in out


def test_wiki_generate_pages_option_caps_the_plan(wiki_env):
    from cairn.mcp_server.tools_wiki import wiki_generate

    out = wiki_generate(repo="r1", pages=2)
    assert "overview" in out
    assert "alpha" in out
    assert "beta" not in out
    assert len(list_tasks(wiki_env, kind="wiki-page")) == 2


def test_wiki_generate_clamps_pages_lower_bound(wiki_env):
    from cairn.mcp_server.tools_wiki import wiki_generate

    out = wiki_generate(repo="r1", pages=0)
    assert "overview" in out
    assert len(list_tasks(wiki_env, kind="wiki-page")) == 1


def test_wiki_generate_refine_catalog_queues_catalog_task(wiki_env):
    from cairn.mcp_server.tools_wiki import wiki_generate

    out = wiki_generate(repo="r1", refine_catalog=True)
    assert isinstance(out, str)
    catalog = list_tasks(wiki_env, kind="wiki-catalog")
    assert len(catalog) == 1
    assert catalog[0].id in out
    assert "re-run" in out.lower()
    assert list_tasks(wiki_env, kind="wiki-page") == []

    # Second call while the catalog chain is still pending: same catalog task
    # surfaced, nothing new queued.
    out2 = wiki_generate(repo="r1", refine_catalog=True)
    assert catalog[0].id in out2
    assert "pending" in out2.lower()
    assert len(list_tasks(wiki_env, kind="wiki-catalog")) == 1
    assert list_tasks(wiki_env, kind="wiki-page") == []


def test_wiki_generate_unknown_repo_hints_at_build(wiki_env):
    from cairn.mcp_server.tools_wiki import wiki_generate

    out = wiki_generate(repo="missing")
    assert "no indexed files" in out
    assert "cairn build" in out
    assert list_tasks(wiki_env, kind="wiki-page") == []
