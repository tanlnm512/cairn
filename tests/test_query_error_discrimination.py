"""F2: OperationalError discrimination at the two query swallow sites.

``ann_index.ann_query`` and ``lexical.search_symbols`` previously routed EVERY
``sqlite3.OperationalError`` to ``note_contention`` ("lock contention
absorbed") -- including FTS5 syntax errors, no-such-table races, and vec0
corruption, which are *query* failures, not cross-process lock events. That
misattribution polluted the ``lock_contention`` signal ``cairn doctor`` /
``metrics --contention`` aggregate on, and the spec's ``query_error`` enum
value was never emitted.

These tests drive both branches of the discrimination at both sites:

  * "database is locked" / "database is busy" -> note_contention fires (the
    genuine case, unchanged behavior);
  * any other OperationalError -> no contention signal; ann_query emits the
    durable ``ann_fallback reason=query_error`` instead, and lexical logs a
    once-per-process WARNING (its LIKE degrade is by design).

A connection proxy raises on the MATCH query specifically so the surrounding
metadata queries (sqlite_master probes, the LIKE fallback) still run -- the
degraded result paths are exercised, not skipped.
"""
from __future__ import annotations

import logging
import sqlite3

import pytest

from cairn.graph import ann_index as ann
from cairn.graph import lexical
from cairn.graph import schema


class _MatchRaisingConn:
    """Delegate everything to a real schema'd conn except the MATCH query.

    ``execute`` inspects the SQL: queries containing ``MATCH`` raise the
    configured OperationalError; everything else (sqlite_master probes, LIKE
    scans) hits the real connection, which carries the full schema so the
    degraded result paths (the LIKE fallback) run for real.
    """

    def __init__(self, message: str):
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        schema._apply_schema(self._conn)
        self._message = message

    def execute(self, sql, params=()):
        if "MATCH" in sql:
            raise sqlite3.OperationalError(self._message)
        return self._conn.execute(sql, params)

    def cursor(self, *args, **kwargs):
        # The LIKE fallback drives a cursor directly; its SQL never contains
        # MATCH, so a plain delegation is correct.
        return self._conn.cursor(*args, **kwargs)

    def close(self):
        self._conn.close()


@pytest.fixture(autouse=True)
def _reset_guards(monkeypatch):
    """Isolate the process-global once-guards + telemetry env per test."""
    monkeypatch.delenv("CAIRN_TELEMETRY", raising=False)
    monkeypatch.delenv("CAIRN_ANN_BACKEND", raising=False)
    ann._ANN_FALLBACK_WARNED = False
    schema._CONTENTION_WARNED.clear()
    from cairn.telemetry import events, sink

    with events._WARN_LOCK:
        events._WARNED.clear()
    with sink._LOCK:
        sink._BUFFER.clear()
    yield
    ann._ANN_FALLBACK_WARNED = False
    schema._CONTENTION_WARNED.clear()
    with sink._LOCK:
        sink._BUFFER.clear()


def _buffered(name):
    import json

    from cairn.telemetry import sink

    return [
        json.loads(a) if a else {}
        for _ts, n, _sid, a in list(sink._BUFFER)
        if n == name
    ]


# ---------------------------------------------------------------------------
# ann_index.ann_query
# ---------------------------------------------------------------------------


def test_ann_query_locked_error_notes_contention(monkeypatch, caplog):
    """A genuine 'database is locked' still routes to note_contention and does
    NOT burn the ann_fallback reason on a bogus query_error."""
    monkeypatch.setattr(ann, "try_load", lambda conn: True)
    # index_exists must see the vec0 table so the MATCH query is reached.
    conn = _MatchRaisingConn("database is locked")
    try:
        # A stand-in vec0 table so index_exists passes (the real vec0 virtual
        # table needs the extension; the probe only reads sqlite_master).
        conn.execute(
            f"CREATE TABLE {ann._table_name('some-model')} "
            "(rowid INTEGER PRIMARY KEY, embedding BLOB, distance REAL)"
        )
        assert ann.ann_query(conn, "some-model", b"\x00" * 8, 5) is None
    finally:
        conn.close()

    assert schema._CONTENTION_WARNED.get("ann_index.ann_query") is True
    assert _buffered("ann_fallback") == [], "lock contention is not an ANN query error"


def test_ann_query_syntax_error_emits_query_error_not_contention(monkeypatch, caplog):
    """A vec0/FTS-style syntax/corruption error emits the spec's query_error
    reason (once) and leaves the contention signal untouched."""
    monkeypatch.setattr(ann, "try_load", lambda conn: True)
    caplog.set_level(logging.WARNING, logger="cairn.graph.ann_index")
    conn = _MatchRaisingConn("fts5: syntax error near \"k\"")
    try:
        # A stand-in vec0 table so index_exists passes (the real vec0 virtual
        # table needs the extension; the probe only reads sqlite_master).
        conn.execute(
            f"CREATE TABLE {ann._table_name('some-model')} "
            "(rowid INTEGER PRIMARY KEY, embedding BLOB, distance REAL)"
        )
        assert ann.ann_query(conn, "some-model", b"\x00" * 8, 5) is None
        assert ann.ann_query(conn, "some-model", b"\x00" * 8, 5) is None
    finally:
        conn.close()

    assert "ann_index.ann_query" not in schema._CONTENTION_WARNED
    assert _buffered("lock_contention") == []
    events = _buffered("ann_fallback")
    assert events == [{"reason": "query_error"}], "once-guarded, durable, enum-typed"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "query error" in warnings[0].getMessage()


# ---------------------------------------------------------------------------
# lexical.search_symbols
# ---------------------------------------------------------------------------


def test_lexical_locked_error_notes_contention(caplog):
    """Genuine lock at the FTS site still routes to note_contention."""
    caplog.set_level(logging.WARNING, logger="cairn.graph.schema")
    conn = _MatchRaisingConn("database is locked")
    try:
        assert lexical.search_symbols(conn, "safeApiCall", limit=5) == []
    finally:
        conn.close()

    assert schema._CONTENTION_WARNED.get("lexical.fts_search") is True


def test_lexical_non_contention_error_warns_once_without_contention(caplog):
    """FTS5 missing/corrupt (anything not locked/busy): the LIKE degrade is by
    design -- one quiet WARNING, no contention pollution, no new event."""
    caplog.set_level(logging.WARNING, logger="cairn.graph.lexical")
    conn = _MatchRaisingConn("no such table: symbols_fts")
    try:
        assert lexical.search_symbols(conn, "safeApiCall", limit=5) == []
        assert lexical.search_symbols(conn, "safeApiCall", limit=5) == []
    finally:
        conn.close()

    assert "lexical.fts_search" not in schema._CONTENTION_WARNED
    assert _buffered("lock_contention") == []
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, "once per process via warn_once"
    assert "LIKE" in warnings[0].getMessage()


def test_lexical_non_contention_warning_silent_when_telemetry_off(monkeypatch, caplog):
    """The once-warning rides the telemetry module's warn_once helper, so the
    master switch silences it (a quality signal, not an operational outage)."""
    monkeypatch.setenv("CAIRN_TELEMETRY", "off")
    caplog.set_level(logging.WARNING, logger="cairn.graph.lexical")
    conn = _MatchRaisingConn("no such table: symbols_fts")
    try:
        assert lexical.search_symbols(conn, "safeApiCall", limit=5) == []
    finally:
        conn.close()

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == []
