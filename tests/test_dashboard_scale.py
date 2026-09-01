"""Scale tests for the traffic routes over a synthesized ~10.5k-call store
(TC-006 / FR-005 / SC-1, spec ui-dashboard-traffic-scale).

Survey Q7: no 10,000-row store exists locally, so FR-005's 2-second
first-render budget is proven against a store synthesized here by direct
``tool_metrics`` inserts (no server, no sleeps, ``executemany`` for speed).

Two assertion layers, per the tech-spec pitfall note (timing on CI runners
is noisy):

* Structural bounds (always run, CI-safe): /history's first page carries
  exactly ``HISTORY_PAGE_SIZE`` rows plus the Older link; /chains renders
  at most ``CHAINS_MAX_CHAINS`` chains with at most
  ``CHAINS_CALLS_PER_CHAIN`` calls each; /tokens returns rows for every
  seeded tool.
* First-render budget: each traffic route's FIRST GET on a freshly
  started TestClient must render under a wall-clock ceiling. The strict
  2.0s ceiling (SC-1) runs only when ``CAIRN_SCALE_STRICT=1`` is set::

      CAIRN_SCALE_STRICT=1 uv run pytest tests/test_dashboard_scale.py

  The ``infra`` marker selects this module out of per-PR CI legs; it is
  a tier selection, not a strictness switch — the module-local env gate
  below is the strictness mechanism and stays. It is read at IMPORT time because
  the suite-wide ``_hermetic_env`` autouse fixture (tests/conftest.py)
  deletes every ``CAIRN_*`` env var per test -- a test-body read would
  always see it unset. Ungated runs (CI) still assert a generous 10s
  ceiling plus every structural bound, so real regressions (unbounded
  queries, whole-table rendering) stay visible.
"""
from __future__ import annotations

import os
import sqlite3
import time

import pytest

pytestmark = pytest.mark.infra

# Read at module import -- see module docstring (_hermetic_env clears
# CAIRN_* per test, so this cannot live inside a test body).
_STRICT_BUDGET = os.environ.get("CAIRN_SCALE_STRICT", "") == "1"

_STRICT_CEILING_S = 2.0  # SC-1 / FR-005: first render under 2 seconds
_CI_CEILING_S = 10.0  # generous ungated ceiling so breakage stays visible

# Store shape (~10,500 rows): a giant contiguous legacy 'unknown' session
# (the all-'unknown' legacy shape the spec calls out as FR-004/FR-005's
# first case), mid-age sessions reaching back >30 days, fresh sessions.
_LEGACY_ROWS = 6000  # contiguous 60s-apart calls, all older than 30 days
_MID_SESSIONS = 20  # x _MID_CALLS, session starts spaced 2 days apart
_MID_CALLS = 180
_RECENT_SESSIONS = 30  # x _RECENT_CALLS, all inside the last ~5 hours
_RECENT_CALLS = 30

_SCALE_TOOLS = (
    "explore",
    "ask_compass",
    "get_callers",
    "search_symbols",
    "semantic_search",
    "impact_analysis",
    "recall_memory",
    "record_memory",
)

# Varied payload sizes, including the pre-migration NULL shape (NULL
# sizes still count as calls but contribute zero tokens).
_SCALE_PAYLOADS = (
    (40, 80),
    (400, 800),
    (4000, 8000),
    (24000, 96000),
    (None, None),
)


def _seed_scale_store(db_path: str) -> int:
    """Build the synthesized ~10,500-row store; returns the row count.

    Direct ``INSERT``s via one ``executemany`` (target: well under a few
    seconds, no sleeps): a 6,000-call legacy ``unknown`` session of
    contiguous 60s-apart calls entirely older than 30 days; 20 mid-age
    sessions (180 calls each) whose starts are spaced 2 days apart, so
    timestamps reach back ~38 days; 30 fresh sessions (30 calls each)
    inside the last few hours. Tools cycle through ``_SCALE_TOOLS``,
    payload sizes through ``_SCALE_PAYLOADS`` (~1 in 5 rows NULL), ~1 in
    17 calls is an error, durations vary 5-300 ms.
    """
    from cairn.graph.schema import _apply_schema

    now = time.time()
    rows = []
    seq = 0  # running phase so tool/payload/status mixes differ per block

    def add(session_id: str, base_ts: float, count: int, step: float):
        nonlocal seq
        for i in range(count):
            req, resp = _SCALE_PAYLOADS[seq % len(_SCALE_PAYLOADS)]
            error = seq % 17 == 0
            rows.append(
                (
                    _SCALE_TOOLS[seq % len(_SCALE_TOOLS)],
                    session_id,
                    base_ts + i * step,
                    5.0 + (seq % 200) * 1.5,
                    "error" if error else "ok",
                    "synthetic failure" if error else None,
                    req,
                    resp,
                    '{"seed": %d}' % seq,
                )
            )
            seq += 1

    # Ends ~40.8 days back: contiguous (60s apart << SESSION_GAP_S), all
    # outside a 30d window -- the giant single-chain legacy shape.
    add("unknown", now - 45 * 86400, _LEGACY_ROWS, 60.0)
    for k in range(_MID_SESSIONS):
        # Calls span 3h ending at least a minute before now (k=0); the
        # oldest session (k=19) starts ~38 days back: >30 days reached.
        add(
            f"sess-mid-{k:02d}",
            now - k * 2 * 86400 - (_MID_CALLS * 60 + 60.0),
            _MID_CALLS,
            60.0,
        )
    for k in range(_RECENT_SESSIONS):
        add(f"sess-recent-{k:02d}", now - 600.0 - k * 600.0, _RECENT_CALLS, 2.0)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    conn.executemany(
        "INSERT INTO tool_metrics (tool_name, session_id, invoked_at, "
        "duration_ms, status, error_message, req_chars, resp_chars, "
        "args_summary) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM tool_metrics").fetchone()[0]
    conn.close()
    return total


def _scale_db_file(tmp_path) -> str:
    """A graph-schema DB file carrying the synthesized scale store."""
    db_path = str(tmp_path / "scale.db")
    _seed_scale_store(db_path)
    return db_path


def _client_over(db_path: str, tmp_path):
    """A dashboard TestClient over ``db_path`` (httpx optional, like the
    other dashboard app tests)."""
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    from cairn.dashboard.app import create_app

    return TestClient(
        create_app(db_path=db_path, knowledge_dir=str(tmp_path / "missing"))
    )


# ---------------------------------------------------------------------------
# Structural bounds (always run, CI-safe)
# ---------------------------------------------------------------------------


def _assert_history_first_page_bounded(client):
    """First /history page: exactly HISTORY_PAGE_SIZE rows + Older link."""
    from cairn.dashboard.data import HISTORY_PAGE_SIZE

    resp = client.get("/history")
    assert resp.status_code == 200
    # Each history row renders exactly one status badge (history.html);
    # no other element on the page carries that class.
    assert resp.text.count('class="badge badge-') == HISTORY_PAGE_SIZE
    assert ">Older</a>" in resp.text  # a further page exists and is linked


def _assert_chains_bounded(client, db_path):
    """/chains at most CHAINS_MAX_CHAINS chains x CHAINS_CALLS_PER_CHAIN
    calls, both route-visible and via the data layer on the same store."""
    from cairn.dashboard.data import (
        CHAINS_CALLS_PER_CHAIN,
        CHAINS_MAX_CHAINS,
        get_read_only_db,
        get_session_chains,
    )

    resp = client.get("/chains")
    assert resp.status_code == 200
    # One data-session marker per rendered chain block (chains.html).
    assert resp.text.count('data-session="') <= CHAINS_MAX_CHAINS

    # Same bound via the data layer with the route's own defaults -- the
    # robust check the plan sanctioned for this wave (chains.html is
    # wave-mate-owned), and the proof the caps are actually exercised.
    conn = get_read_only_db(db_path)
    try:
        result = get_session_chains(conn)
    finally:
        conn.close()
    assert len(result["chains"]) <= CHAINS_MAX_CHAINS
    assert result["total_chains"] > CHAINS_MAX_CHAINS  # cap not vacuous
    for chain in result["chains"]:
        assert len(chain["calls"]) <= CHAINS_CALLS_PER_CHAIN
    assert any(c["truncated_calls"] for c in result["chains"])


def _assert_tokens_cover_seeded_tools(client):
    """/tokens returns a row for every seeded tool."""
    resp = client.get("/tokens")
    assert resp.status_code == 200
    for tool in _SCALE_TOOLS:
        assert tool in resp.text, tool


def test_scale_store_shape(tmp_path):
    """The synthesized store matches the shape FR-005's budget assumes:
    >=10,000 calls, one several-thousand-call CONTIGUOUS legacy 'unknown'
    session (a single giant chain), timestamps reaching back >30 days,
    a spread of tools, and both NULL and non-NULL payload sizes."""
    db_path = str(tmp_path / "scale.db")
    total = _seed_scale_store(db_path)
    assert total >= 10_000  # FR-005's threshold
    assert total == (
        _LEGACY_ROWS + _MID_SESSIONS * _MID_CALLS + _RECENT_SESSIONS * _RECENT_CALLS
    )

    conn = sqlite3.connect(db_path)
    try:
        legacy = conn.execute(
            "SELECT COUNT(*), MIN(invoked_at), MAX(invoked_at) "
            "FROM tool_metrics WHERE session_id = 'unknown'"
        ).fetchone()
        assert legacy[0] == _LEGACY_ROWS  # several thousand calls
        # Contiguous 60s-apart calls: the span is exactly (n-1)*60s, so
        # no gap ever exceeds SESSION_GAP_S -- one giant chain.
        assert legacy[2] - legacy[1] == pytest.approx(
            (_LEGACY_ROWS - 1) * 60.0, abs=1e-3
        )
        oldest = conn.execute(
            "SELECT MIN(invoked_at) FROM tool_metrics"
        ).fetchone()[0]
        assert oldest < time.time() - 30 * 86400  # reaches back >30 days
        tools = conn.execute(
            "SELECT COUNT(DISTINCT tool_name) FROM tool_metrics"
        ).fetchone()[0]
        assert tools == len(_SCALE_TOOLS)  # a spread of tools
        null_sizes = conn.execute(
            "SELECT COUNT(*) FROM tool_metrics WHERE req_chars IS NULL"
        ).fetchone()[0]
        assert 0 < null_sizes < total  # pre-migration shape present
    finally:
        conn.close()


def test_history_first_page_bounded_at_scale(tmp_path):
    """TC-006 structural: /history's first page is exactly one bounded
    page (HISTORY_PAGE_SIZE rows + Older link), never the whole store."""
    _assert_history_first_page_bounded(_client_over(_scale_db_file(tmp_path), tmp_path))


def test_chains_render_bounded_at_scale(tmp_path):
    """TC-006 structural: /chains stays bounded despite 51 chains in the
    store -- at most CHAINS_MAX_CHAINS rendered, at most
    CHAINS_CALLS_PER_CHAIN calls each; the giant legacy 'unknown' session
    cannot flood the page."""
    db_path = _scale_db_file(tmp_path)
    _assert_chains_bounded(_client_over(db_path, tmp_path), db_path)


def test_tokens_covers_seeded_tools_at_scale(tmp_path):
    """TC-006 structural: /tokens aggregates the whole 10.5k-row store
    and returns a row for every seeded tool."""
    _assert_tokens_cover_seeded_tools(
        _client_over(_scale_db_file(tmp_path), tmp_path)
    )


# ---------------------------------------------------------------------------
# First-render budget (TC-006 / FR-005 / SC-1)
# ---------------------------------------------------------------------------


def test_traffic_routes_first_render_budget(tmp_path):
    """Each traffic route's FIRST GET on a freshly started TestClient
    renders within budget over the synthesized ~10.5k-call store.

    Strict 2.0s wall (SC-1) only under CAIRN_SCALE_STRICT=1 (module-level
    gate -- see module docstring); ungated runs keep the structural
    bounds plus a generous 10s ceiling so a real regression still fails.
    A fresh TestClient per route makes every timed GET that client's
    first request, so template loading/compilation counts as part of
    "first render", as FR-005 intends.
    """
    db_path = _scale_db_file(tmp_path)

    elapsed = {}
    for path in ("/history", "/tokens", "/chains"):
        client = _client_over(db_path, tmp_path)
        start = time.perf_counter()
        resp = client.get(path)
        elapsed[path] = time.perf_counter() - start
        assert resp.status_code == 200, path

    ceiling = _STRICT_CEILING_S if _STRICT_BUDGET else _CI_CEILING_S
    for path, seconds in elapsed.items():
        assert seconds < ceiling, (
            f"{path} first render took {seconds:.2f}s (budget {ceiling}s)"
        )

    # Structural bounds hold regardless of the gate (CI-safe).
    client = _client_over(db_path, tmp_path)
    _assert_history_first_page_bounded(client)
    _assert_chains_bounded(client, db_path)
    _assert_tokens_cover_seeded_tools(client)
