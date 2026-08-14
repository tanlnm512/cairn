"""Focused emitter coverage for T11 (spec §6.4 catalog).

One test per emitter landed in T11:
  1. ``truncate_result`` -- ``metric_buffering._truncate_result`` emits ONLY on
     the over-cap branch, carrying the tool name + an original-length bucket.
  2. ``task_lifecycle`` -- ``llm.tasks.claim_task`` emits ``event=claimed`` with
     ``task_kind`` + ``attempt`` (the complete/revise/drop sites share the
     identical emit shape, so the claimed transition validates the wiring).
  3. ``stray_swept`` -- ``mcp_server.server._run_stray_sweep`` emits ``count``
     only when a pass actually killed strays (an idle sweep is silent).

Each test reads the in-process telemetry buffer directly (no DB flush needed)
under an autouse reset so no test poisons the shared sink state.
"""

from __future__ import annotations

import json

import pytest

from cairn.telemetry import sink
from cairn.telemetry import (
    EMPTY_RESULT,
    STRAY_SWEPT,
    TASK_LIFECYCLE,
    TRUNCATE_RESULT,
)


# ---------------------------------------------------------------------------
# Module-global sink state reset (mirrors tests/test_telemetry.py)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_telemetry_state(monkeypatch):
    """Clear the shared sink buffer + warn guards + gating env around each test.

    ``_FLUSHER_STARTED`` is deliberately left alone (resetting it would let the
    next emit spawn a second daemon thread); the 30s tick never fires inside a
    test and is a no-op here regardless (no conn factory configured).
    """
    with sink._LOCK:
        sink._BUFFER.clear()
    sink._conn_factory = None
    monkeypatch.delenv("CAIRN_TELEMETRY", raising=False)
    monkeypatch.delenv("CAIRN_READ_ONLY", raising=False)
    yield
    with sink._LOCK:
        sink._BUFFER.clear()
    sink._conn_factory = None


def _buffered_events():
    """Snapshot of (name, attrs_dict) currently queued in the sink buffer."""
    return [(row[1], json.loads(row[3]) if row[3] is not None else {}) for row in sink._BUFFER]


# ---------------------------------------------------------------------------
# 1. truncate_result -- metric_buffering._truncate_result
# ---------------------------------------------------------------------------


def test_truncate_result_emits_only_on_actual_truncation(monkeypatch):
    """Over-cap result emits truncate_result(tool, chars_bucket); under-cap is silent.

    Emitting on every call would drown the signal (a healthy tool never
    truncates); the event must fire ONLY when truncation actually happens. The
    ``chars_bucket`` reflects the ORIGINAL length, not the truncated head, so a
    doctor can see whether a tool is clipping at ~2k vs ~60k.
    """
    from cairn.mcp_server import metric_buffering as mb

    # Under-cap: no emit (the guard returns early before the over-cap branch).
    monkeypatch.setattr(mb, "MAX_RESULT_CHARS", 5000)
    mb._truncate_result("search_symbols", "short result")
    assert len(sink._BUFFER) == 0, "under-cap result must not emit"

    # Over-cap: emit fires with the tool name + original-length bucket.
    monkeypatch.setattr(mb, "MAX_RESULT_CHARS", 50)
    mb._truncate_result("explore", "x" * 200)  # 200 chars -> "<=500" bucket

    events = _buffered_events()
    assert len(events) == 1, "exactly one event on the over-cap branch"
    name, attrs = events[0]
    assert name == TRUNCATE_RESULT
    assert attrs == {"tool": "explore", "chars_bucket": "<=500"}


# ---------------------------------------------------------------------------
# 2. task_lifecycle -- llm.tasks.claim_task (the claimed transition)
# ---------------------------------------------------------------------------


def test_task_lifecycle_emits_on_claim(tmp_path):
    """claim_task emits task_lifecycle(event=claimed, task_kind, attempt).

    The complete/revise/drop transitions in complete_task use the identical
    ``_emit(TASK_LIFECYCLE, task_kind=..., event=..., attempt=...)`` call shape,
    differing only in the ``event`` tag -- so exercising the claimed path proves
    the import, the firing, and the attribute contract. ``attempt`` is the task's
    revise-cycle number (1 for a freshly-created task).
    """
    from cairn.llm.tasks import claim_task, create_task
    from cairn.okf.bundle import OKFBundle

    knowledge = tmp_path / ".knowledge"
    (knowledge / "_tasks").mkdir(parents=True)
    bundle = OKFBundle(str(knowledge))

    task = create_task(bundle, task_kind="compass-synthesize", resource="mod/foo")
    claimed = claim_task(bundle, task.id)

    assert claimed is not None, "sanity: a fresh pending task is claimable"

    events = _buffered_events()
    lifecycle = [e for e in events if e[0] == TASK_LIFECYCLE]
    assert len(lifecycle) == 1, "claim emits exactly one task_lifecycle event"
    name, attrs = lifecycle[0]
    assert name == TASK_LIFECYCLE
    assert attrs == {
        "task_kind": "compass-synthesize",
        "event": "claimed",
        "attempt": 1,
    }


# ---------------------------------------------------------------------------
# 3. stray_swept -- mcp_server.server._run_stray_sweep
# ---------------------------------------------------------------------------


def test_stray_sweeper_emits_count_only_when_strays_killed(monkeypatch):
    """_run_stray_sweep emits stray_swept(count) only when a pass killed strays.

    An idle sweep (no strays) must stay silent so a healthy daemon doesn't write
    a stray_swept row every 60s. ``count`` is the int returned by
    ``lifecycle.sweep_strays`` (mocked here; no real process is touched).
    """
    from cairn.mcp_server import lifecycle, server

    # Idle pass: 0 killed -> no emit.
    monkeypatch.setattr(lifecycle, "sweep_strays", lambda db_path, log=False: 0)
    killed = server._run_stray_sweep("/fake/db.sqlite")
    assert killed == 0
    assert len(sink._BUFFER) == 0, "an idle sweep (0 killed) must not emit"

    # Active pass: 2 killed -> one stray_swept(count=2) event.
    monkeypatch.setattr(lifecycle, "sweep_strays", lambda db_path, log=False: 2)
    killed = server._run_stray_sweep("/fake/db.sqlite")
    assert killed == 2

    events = _buffered_events()
    assert len(events) == 1, "exactly one event per active sweep pass"
    name, attrs = events[0]
    assert name == STRAY_SWEPT
    assert attrs == {"count": 2}


# ---------------------------------------------------------------------------
# 4. empty_result -- explore() and the search_symbols MCP tool (F1 expansion)
#    Spec §6.4 names the engine query layer (explore, search_symbols,
#    semantic_search). semantic_search's emit is covered in
#    test_semantic_events.py; these two pin the other query kinds.
# ---------------------------------------------------------------------------


def test_explore_emits_empty_result_when_no_seeds(fresh_db):
    """explore() with no matching symbols -> empty_result(query_kind='explore').

    explore() has a single non-bench caller (the MCP tool) and the semantic
    branch is skipped on an embedding-less DB (embed_count == 0), so exactly
    one empty_result fires -- no double-count against the search_symbols
    primitive, which is not instrumented.
    """
    from cairn.graph.explore import explore

    result = explore(fresh_db, "does-not-exist-anywhere", max_nodes=5)
    assert result["seeds"] == []
    empties = [a for n, a in _buffered_events() if n == EMPTY_RESULT]
    assert len(empties) == 1
    assert empties[0] == {"query_kind": "explore"}


def test_search_symbols_tool_emits_empty_result_when_no_match(tmp_path, monkeypatch):
    """search_symbols_data (the MCP tool wrapper) with zero matches ->
    empty_result(query_kind='search_symbols').

    Emitted at the tool boundary, not the shared ``search_symbols`` primitive in
    lexical.py (which explore/semantic call transitively and would double-count).
    """
    import sqlite3

    from cairn.graph.schema import _apply_schema
    from cairn.mcp_server import tools_graph

    db = tmp_path / "graph.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    conn.commit()
    conn.close()

    # _conn() must return a fresh open connection each call: the wrapper closes
    # the one it receives in its `finally` block.
    monkeypatch.setattr(tools_graph, "_conn", lambda: sqlite3.connect(str(db)))

    data = tools_graph.search_symbols_data("does-not-exist")
    assert data["total_count"] == 0
    empties = [a for n, a in _buffered_events() if n == EMPTY_RESULT]
    assert len(empties) == 1
    assert empties[0] == {"query_kind": "search_symbols"}
