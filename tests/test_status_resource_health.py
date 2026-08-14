"""T14: ``cairn://status`` health block -- degradations, pending-sync,
last-build age, 24h tool error rate.

The status resource is a read-only text surface (not a tool), so this exercises
it end-to-end via a file-backed fixture DB and asserts the ``health:`` block is
present and reflects seeded state. No new MCP tool is registered -- a tool-count
smoke (``verify_tool_count``) pins the 27-tool contract (acceptance criterion).

Fixture DB is file-backed rather than ``:memory:`` because ``status_resource``
opens and closes ``_conn()`` twice (stats, then staleness+health); an in-memory
DB is destroyed by the first ``close()``. The backend degradation probes
(``is_hash_fallback`` / ``ann_backend_enabled``) are monkeypatched so the
degradation flags are deterministic regardless of whether
sentence-transformers / sqlite-vec happen to be installed in this environment.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest

from cairn.graph.schema import _apply_schema
from cairn.mcp_server import _server_core
from cairn.mcp_server.server import _EXPECTED_TOOL_COUNT, verify_tool_count


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_db(path, setup=None):
    """Create a file-backed DB with the full schema, optionally seed rows.

    Mirrors tests/test_doctor.py::_make_db: ``_apply_schema`` + a repos row so
    pending_sync / build_runs / tool_metrics inserts mirror production shape.
    """
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    # INSERT OR IGNORE so re-seeding an existing DB (fixture creates it, then a
    # test appends rows) is idempotent -- the schema is CREATE IF NOT EXISTS and
    # the repos seed row is ignored on the second pass.
    conn.execute(
        "INSERT OR IGNORE INTO repos (id, name, path, language, git_remote, indexed_at) "
        "VALUES ('r1', 'r1', '.', '', NULL, '2026-08-13T00:00:00')"
    )
    if setup:
        setup(conn)
    conn.commit()
    conn.close()


def _open(db_path):
    """A fresh connection to ``db_path`` with the Row factory the resource expects.

    Each ``_conn()`` call in production opens a new connection via ``get_db``;
    monkeypatching ``_server_core._conn`` to this lambda reproduces that so the
    resource's open/close/open sequence sees persistent data.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture
def status_db(tmp_path, monkeypatch):
    """File-backed DB + ``_conn`` patched so ``status_resource`` reads it."""
    db = tmp_path / "graph.db"
    _make_db(db)
    monkeypatch.setattr(_server_core, "_conn", lambda: _open(db))
    # Defensive: a stray CAIRN_ANN_BACKEND=off in the env would suppress the
    # ANN degradation case. Tests that depend on env state set it themselves.
    monkeypatch.delenv("CAIRN_ANN_BACKEND", raising=False)
    return db


def _status():
    """Invoke the status resource (the @mcp.resource function) directly."""
    return _server_core.status_resource()


# ---------------------------------------------------------------------------
# Shape + presence (acceptance: resource snapshot includes health block)
# ---------------------------------------------------------------------------


def test_health_block_present_on_clean_store(status_db, monkeypatch):
    """A fresh, empty store still emits the full health block (all fields).

    This is the acceptance snapshot: the block exists, is read-only, and
    reports healthy defaults (no degradations, 0 pending, never built, 0%
    error rate) rather than crashing on empty telemetry tables. Backends are
    forced healthy so ``degradations: none`` is deterministic regardless of
    whether sentence-transformers / sqlite-vec are installed in this env.
    """
    monkeypatch.setattr("cairn.graph.embeddings.is_hash_fallback", lambda: False)
    monkeypatch.setattr("cairn.graph.ann_index.ann_backend_enabled", lambda: True)
    out = _status()

    assert "health:" in out
    assert "  degradations: none" in out
    assert "  pending_sync: 0" in out
    assert "  last_build_age: never" in out
    assert "  error_rate_24h: 0.0% (0 errors / 0 calls)" in out

    # The pre-existing status fields are still present (block was appended, not
    # a replacement).
    assert "cairn status" in out
    assert "  pending reindex: 0 file(s)" in out


def test_health_block_reflects_seeded_state(status_db, monkeypatch):
    """Seed pending_sync, a recent build, and tool errors -> the block mirrors them."""
    now = datetime.now(timezone.utc)
    started = (now - timedelta(hours=2)).isoformat()
    recent = time.time() - 600  # 10m ago, inside the 24h window
    old = time.time() - 2 * 24 * 3600  # 2d ago, OUTSIDE the 24h window

    def setup(conn):
        conn.executemany(
            "INSERT INTO pending_sync (path, repo_id, changed_at) VALUES (?, 'r1', ?)",
            [("a.py", started), ("b.py", started)],
        )
        conn.execute(
            "INSERT INTO build_runs (kind, started_at) VALUES ('sync', ?)",
            (started,),
        )
        # 4 recent calls, 1 error -> 25% error rate; plus 1 ancient error that
        # must NOT count (outside the 24h window).
        conn.executemany(
            "INSERT INTO tool_metrics (tool_name, invoked_at, duration_ms, status) "
            "VALUES ('get_callers', ?, 10, 'ok')",
            [(recent,), (recent,), (recent,)],
        )
        conn.execute(
            "INSERT INTO tool_metrics (tool_name, invoked_at, duration_ms, status, error_message) "
            "VALUES ('get_callers', ?, 10, 'error', 'boom')",
            (recent,),
        )
        conn.execute(
            "INSERT INTO tool_metrics (tool_name, invoked_at, duration_ms, status, error_message) "
            "VALUES ('get_callers', ?, 10, 'error', 'ancient')",
            (old,),
        )

    _make_db(status_db, setup=setup)

    # Healthy backends so the degradations line is deterministic (this test is
    # about the seeded counts/rate, not backend state).
    monkeypatch.setattr("cairn.graph.embeddings.is_hash_fallback", lambda: False)
    monkeypatch.setattr("cairn.graph.ann_index.ann_backend_enabled", lambda: True)
    out = _status()
    assert "  pending_sync: 2" in out
    assert "  last_build_age: 2h old" in out
    # 1 error / 4 calls = 25.0%; the 2d-old error is excluded.
    assert "  error_rate_24h: 25.0% (1 errors / 4 calls)" in out
    assert "  degradations: none" in out


# ---------------------------------------------------------------------------
# Backend degradations (deterministic via monkeypatch)
# ---------------------------------------------------------------------------


def test_health_block_flags_hash_and_ann_degradations(status_db, monkeypatch):
    """Both silent backend fallbacks appear in degradations when active.

    ``is_hash_fallback`` True + sqlite-vec expected but unavailable -> the block
    lists both, so an agent reading the resource sees the degraded retrieval
    posture without running ``cairn doctor``.
    """
    monkeypatch.setattr("cairn.graph.embeddings.is_hash_fallback", lambda: True)
    monkeypatch.setattr("cairn.graph.ann_index.ann_backend_enabled", lambda: False)
    # CAIRN_ANN_BACKEND unset -> sqlite-vec *expected*, so its absence is a
    # degradation (the fixture already deleted the env var).
    out = _status()
    assert "  degradations: embeddings=hash_fallback, ann=unavailable" in out


def test_ann_off_by_config_is_not_a_degradation(status_db, monkeypatch):
    """An explicit CAIRN_ANN_BACKEND=off is an informed choice, not a fallback.

    Mirrors ann_index.ann_backend_enabled() and the doctor's _check_ann: when
    the user opted out, ANN must NOT appear in degradations even though
    ann_backend_enabled() returns False.
    """
    monkeypatch.setenv("CAIRN_ANN_BACKEND", "off")
    monkeypatch.setattr("cairn.graph.ann_index.ann_backend_enabled", lambda: False)
    # This test is about ANN policy, not embeddings -- hold the embed backend
    # healthy so it can't pollute the degradations line.
    monkeypatch.setattr("cairn.graph.embeddings.is_hash_fallback", lambda: False)
    out = _status()
    assert "  degradations: none" in out
    assert "ann=unavailable" not in out


def test_health_block_flags_missing_vec0_index(status_db, monkeypatch):
    """F1b: sqlite-vec expected and enabled, embeddings present for the current
    model, but no vec0 index -> ``ann=no_index`` degradation (semantic queries
    silently run the brute-force scan; the load probe alone can't see this)."""
    import cairn.graph.embeddings as emb

    monkeypatch.setattr("cairn.graph.embeddings.is_hash_fallback", lambda: False)
    monkeypatch.setattr("cairn.graph.ann_index.ann_backend_enabled", lambda: True)

    def setup(conn):
        for i in range(3):
            conn.execute(
                "INSERT INTO embeddings (symbol_id, model, dim, vec, chunk) "
                "VALUES (?, ?, 8, ?, ?)",
                (f"s{i}", emb.current_model(), b"\x00" * 32, "chunk"),
            )

    _make_db(status_db, setup=setup)
    out = _status()
    assert "ann=no_index" in out


def test_health_block_no_index_degradation_absent_without_embeddings(
    status_db, monkeypatch
):
    """No embeddings stored -> no vec0 table is legitimate (nothing to index):
    the index probe must not flag it (mirrors the doctor's PASS-when-empty)."""
    monkeypatch.setattr("cairn.graph.embeddings.is_hash_fallback", lambda: False)
    monkeypatch.setattr("cairn.graph.ann_index.ann_backend_enabled", lambda: True)
    out = _status()
    assert "  degradations: none" in out
    assert "ann=no_index" not in out


def test_health_block_survives_unmigrated_db(tmp_path, monkeypatch):
    """A DB without the telemetry tables does not crash the resource.

    The block degrades to zeros/'never' on a pre-instrumentation DB: this is the
    defensive-reads contract (spec §6.5 -- never fatal, read-only). Built by
    hand (not _apply_schema) so pending_sync/build_runs/tool_metrics are absent.
    """
    db = tmp_path / "bare.db"
    # Minimal schema: just the tables get_stats needs, so status_resource gets
    # past the stats block and reaches the health probes against missing tables.
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE repos (id TEXT, name TEXT, path TEXT, language TEXT, "
        "git_remote TEXT, indexed_at TEXT);"
        "CREATE TABLE files (id TEXT, repo_id TEXT, path TEXT, language TEXT, "
        "mtime REAL, size INTEGER);"
        "CREATE TABLE symbols (id TEXT, file_id TEXT, name TEXT, "
        "qualified_name TEXT, kind TEXT, line_start INTEGER, line_end INTEGER);"
        "CREATE TABLE edges (id TEXT, source_id TEXT, target_id TEXT, "
        "target_name TEXT, kind TEXT, line INTEGER, column INTEGER);"
        "CREATE TABLE imports (id TEXT, file_id TEXT, path TEXT, kind TEXT);"
        "CREATE TABLE skipped_files (id TEXT, repo_id TEXT, path TEXT, reason TEXT);"
    )
    conn.close()
    monkeypatch.setattr(_server_core, "_conn", lambda: _open(db))

    out = _status()
    assert "health:" in out
    assert "  pending_sync: 0" in out
    assert "  last_build_age: never" in out
    assert "  error_rate_24h: 0.0% (0 errors / 0 calls)" in out


# ---------------------------------------------------------------------------
# Tool-count gate (acceptance: no new MCP tool, count still 27)
# ---------------------------------------------------------------------------


def test_tool_count_unchanged_at_27():
    """T14 adds no MCP tool. The 27-tool contract (server.py) still holds.

    ``verify_tool_count`` is the boot guard; calling it here proves the status
    resource (a @mcp.resource, not @mcp.tool) did not register a new tool.
    """
    verify_tool_count()  # raises AssertionError on drift
    assert _EXPECTED_TOOL_COUNT == 27
