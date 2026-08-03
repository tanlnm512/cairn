"""WI-4: staleness banner from pending_sync on graph MCP tools.

The ``pending_sync`` table is populated by the file watcher when a source file
changes but before reindex runs. Before WI-4, no MCP tool read it (only
``cg stats`` did), so a long-running ``cg serve`` answered from a stale graph
with zero signal. These tests pin the new behavior: when a result's files have
pending edits, a banner is prepended; otherwise output is unchanged.
"""
from __future__ import annotations

import pytest

from cairn.mcp_server import tools_graph


def _row(conn, table, **cols):
    keys = ", ".join(cols)
    placeholders = ", ".join("?" for _ in cols)
    conn.execute(f"INSERT INTO {table} ({keys}) VALUES ({placeholders})", list(cols.values()))


def _seed_caller(conn, *, caller_file="Caller.kt", caller_name="ping", stale=False):
    """One resolved edge: caller_name -> target 'doThing'. Optionally mark the
    caller's file as pending-sync (stale)."""
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('r1', 'be-sdk', '/repo')")
    _row(conn, "files", id="f1", repo_id="r1", path=caller_file, language="kotlin")
    _row(conn, "files", id="f2", repo_id="r1", path="Target.kt", language="kotlin")
    _row(conn, "symbols", id="s1", file_id="f1", name=caller_name, qualified_name="Caller.ping",
         kind="method", line_start=10, line_end=12)
    _row(conn, "symbols", id="s2", file_id="f2", name="doThing", qualified_name="Target.doThing",
         kind="function", line_start=5, line_end=8)
    _row(conn, "edges", id="e1", source_id="s1", target_id="s2", target_name=None,
         kind="call", line=11, column=4)
    if stale:
        conn.execute(
            "INSERT INTO pending_sync (path, repo_id, changed_at) VALUES (?, 'r1', '2026-07-30T00:00:00Z')",
            (caller_file,),
        )
    conn.commit()


@pytest.fixture
def _patched_conn(fresh_db, monkeypatch):
    monkeypatch.setattr(tools_graph, "_conn", lambda: fresh_db)
    return fresh_db


def test_banner_present_when_caller_file_is_stale(_patched_conn):
    _seed_caller(_patched_conn, stale=True)

    result = tools_graph.get_callers("doThing")

    assert "Stale graph" in result
    assert "Caller.kt" in result
    # The real result is still present below the banner.
    assert "callers of 'doThing'" in result
    assert "ping" in result


def test_no_banner_when_graph_is_fresh(_patched_conn):
    """The common case: no pending_sync rows -> no banner, output unchanged."""
    _seed_caller(_patched_conn, stale=False)

    result = tools_graph.get_callers("doThing")

    assert "Stale graph" not in result
    assert "callers of 'doThing'" in result
    assert "ping" in result


def test_no_banner_when_no_callers(_patched_conn):
    """Empty result must not trigger a staleness query or a banner."""
    result = tools_graph.get_callers("TotallyUnusedSymbol")

    assert result == "No callers found for 'TotallyUnusedSymbol' (checked precise and fuzzy)."
    assert "Stale" not in result


def test_staleness_banner_helper_directly(fresh_db):
    """Unit-test the helper: empty paths -> '', stale hit -> banner, fresh -> ''."""
    from cairn.mcp_server._server_core import _staleness_banner

    assert _staleness_banner(fresh_db, []) == ""
    assert _staleness_banner(fresh_db, ["nonexistent.kt"]) == ""

    fresh_db.execute(
        "INSERT INTO pending_sync (path, repo_id, changed_at) VALUES ('A.kt', 'r1', 't')"
    )
    fresh_db.commit()
    banner = _staleness_banner(fresh_db, ["A.kt", "B.kt"])
    assert "Stale graph" in banner
    assert "1 file" in banner  # only A.kt is stale
    assert "A.kt" in banner
