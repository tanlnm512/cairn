"""get_callers / get_callees: precise-empty auto-fallback to fuzzy.

Regression test for a real miss caught in a live A/B benchmark (2026-07-28,
see agent_integration/skill/evals/rule08b-empty-precise-gave-up.md): an agent
asked for callers of a symbol defined outside the indexed workspace got an
empty precise result and reported "no callers" without retrying fuzzy=True,
even though a real caller existed and fuzzy=True found it in one call.

These tools now retry fuzzy themselves when fuzzy=False and precise is empty,
so the agent doesn't have to remember to.
"""
from __future__ import annotations

import pytest

from cairn.mcp_server import tools_graph


def _row(conn, table, **cols):
    keys = ", ".join(cols)
    placeholders = ", ".join("?" for _ in cols)
    conn.execute(f"INSERT INTO {table} ({keys}) VALUES ({placeholders})", list(cols.values()))


def _seed_external_call_edge(conn):
    """One in-repo caller with an unresolved edge to an externally-defined symbol.

    Mirrors BEAPIConnector.startLoadURL: the target is defined in a
    non-vendored sibling pod, so no `symbols` row exists for it and
    `target_id` is NULL -- precise get_callers/get_callees can never resolve
    to it, only fuzzy (name-only) matching can.
    """
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('r1', 'customer-ios-new', '/repo')")
    _row(conn, "files", id="f1", repo_id="r1", path="Caller.swift", language="swift")
    _row(
        conn,
        "symbols",
        id="s1",
        file_id="f1",
        name="pingGoogle",
        qualified_name="BEInternetConnectionManager.pingGoogle",
        kind="method",
        line_start=87,
        line_end=90,
    )
    _row(
        conn,
        "edges",
        id="e1",
        source_id="s1",
        target_id=None,
        target_name="startLoadURL",
        kind="call",
        line=87,
        column=8,
    )
    conn.commit()


@pytest.fixture
def _patched_conn(fresh_db, monkeypatch):
    """Route the tool's _conn() to the test's in-memory fixture DB."""
    monkeypatch.setattr(tools_graph, "_conn", lambda: fresh_db)
    return fresh_db


def test_get_callers_falls_back_to_fuzzy_when_precise_empty(_patched_conn):
    _seed_external_call_edge(_patched_conn)

    result = tools_graph.get_callers("startLoadURL")

    assert "0 precise callers for 'startLoadURL'" in result
    assert "fuzzy candidates" in result
    assert "pingGoogle" in result
    assert "Caller.swift:87" in result


def test_get_callers_explicit_fuzzy_unaffected(_patched_conn):
    """fuzzy=True explicitly should behave exactly as before -- no fallback framing."""
    _seed_external_call_edge(_patched_conn)

    result = tools_graph.get_callers("startLoadURL", fuzzy=True)

    assert "0 precise callers" not in result
    assert "callers of 'startLoadURL':" in result
    assert "pingGoogle" in result


def test_get_callers_still_reports_no_callers_when_truly_absent(_patched_conn):
    """Precise and fuzzy both empty -> unchanged plain message, not a false fallback claim."""
    result = tools_graph.get_callers("TotallyUnusedSymbol")

    assert result == "No callers found for 'TotallyUnusedSymbol' (checked precise and fuzzy)."


def test_get_callees_falls_back_to_fuzzy_when_precise_empty(_patched_conn):
    conn = _patched_conn
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('r1', 'customer-ios-new', '/repo')")
    _row(conn, "files", id="f1", repo_id="r1", path="Caller.swift", language="swift")
    _row(conn, "symbols", id="s1", file_id="f1", name="pingGoogle", kind="method", line_start=87, line_end=90)
    _row(conn, "edges", id="e1", source_id="s1", target_id=None, target_name="startLoadURL", kind="call", line=87, column=8)
    conn.commit()

    result = tools_graph.get_callees("pingGoogle")

    assert "0 precise callees for 'pingGoogle'" in result
    assert "startLoadURL (unresolved)" in result


def test_get_callees_still_reports_no_callees_when_truly_absent(_patched_conn):
    result = tools_graph.get_callees("TotallyUnusedSymbol")

    assert result == "No callees found for 'TotallyUnusedSymbol' (checked precise and fuzzy)."
