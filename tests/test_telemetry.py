"""Tests for the shared telemetry sink + emission API (task T07).

``cairn.telemetry`` is the buffered-sink event pipeline that generalizes the
proven ``metric_buffering`` pattern (deque + daemon flush + atexit + read-only
skip + backlog cap). This file is the first test coverage for that path; T15
will extend it with doctor fixtures, emission-point coverage, and the
cardinality guard.

Covers (spec §6.1/§6.2 acceptance):
  1. ``emit`` -- appends a ``(ts, name, session_id, attrs_json)`` row; attrs
     are JSON-serialized, oversized values truncated, non-serializable values
     coerced (never raises); ``CAIRN_SESSION`` stamps ``session_id``.
  2. Gates -- ``CAIRN_TELEMETRY=off`` makes ``emit``/``warn_once``/
     ``note_contention`` no-ops; ``CAIRN_READ_ONLY`` truthy skips the emit
     write (a mode=ro daemon would fail every flush).
  3. ``_flush_events`` / ``flush`` -- success drains exactly the flushed
     batch (rows appended *during* the flush survive); a failed flush (factory
     raises, or executemany raises) retains the rows for retry; an empty
     buffer or a missing factory are no-ops. A sink failure NEVER raises.
  4. Retention pruning -- the flush trims the ``events`` table to the newest N
     rows inside the same transaction (bounded DB growth, spec §6.2).
  5. ``warn_once`` / ``note_contention`` -- process-global guard fires at most
     once per key/site; ``note_contention`` emits a ``lock_contention`` event
     AND warns.
  6. ``start_flusher`` is idempotent (one shared thread per process).

The ``events`` / ``build_runs`` tables are NOT in ``schema.py`` yet (T08 adds
them); this file creates them in a fixture with the exact contract DDL so the
sink is testable in isolation. Module-global sink state (``_BUFFER``,
``_conn_factory``, the warn guard) is reset by the autouse fixture so no test
poisons its siblings. ``flush()`` is called directly rather than waiting on
the daemon's 30s sleep, and no test relies on ``time.sleep``.
"""

from __future__ import annotations

import json
import logging
import sqlite3

import pytest

from cairn.telemetry import events as ev
from cairn.telemetry import sink
from cairn.telemetry import (
    emit,
    warn_once,
    note_contention,
    configure_conn,
    flush,
    ANN_FALLBACK,
    LOCK_CONTENTION,
)

# Exact DDL T08 will add to schema.py (spec §6.2). Duplicated here so the sink
# is testable before the schema lands; the sink writes only to ``events``.
_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS build_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    started_at TIMESTAMP NOT NULL,
    duration_s REAL,
    phase_timings TEXT,
    repos INTEGER, files INTEGER, symbols INTEGER, edges INTEGER,
    resolution_exact INTEGER, resolution_ambiguous INTEGER, resolution_unresolved INTEGER,
    parse_errors INTEGER, skipped INTEGER,
    workers INTEGER,
    session_id TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TIMESTAMP NOT NULL,
    name TEXT NOT NULL,
    session_id TEXT,
    attrs TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_name ON events(name);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
"""


# ---------------------------------------------------------------------------
# Module-global state reset (mirrors tests/test_metrics.py::_reset_metric_state)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_telemetry_state(monkeypatch):
    """Reset the sink's process-global state around each test.

    ``_BUFFER`` (deque) and ``_conn_factory`` are module-level and never
    cleared in production (the flusher only drains after a successful commit).
    The warn_once guard set (``events._WARNED``) is likewise never cleared.
    Without this reset, rows/connections/warn-keys from one test would bleed
    into the next. ``CAIRN_TELEMETRY`` / ``CAIRN_READ_ONLY`` / ``CAIRN_SESSION``
    are cleared so gating tests start from a known baseline; ``monkeypatch``
    restores the originals on teardown.

    ``_FLUSHER_STARTED`` is deliberately NOT reset: the shared daemon thread
    is started idempotently and resetting the flag would let the next emit
    spawn a second thread. The thread never ticks within a test (30s sleep)
    and a between-test tick is a no-op (factory is None here).
    """
    with sink._LOCK:
        sink._BUFFER.clear()
    sink._conn_factory = None
    with ev._WARN_LOCK:
        ev._WARNED.clear()
    monkeypatch.delenv("CAIRN_TELEMETRY", raising=False)
    monkeypatch.delenv("CAIRN_READ_ONLY", raising=False)
    monkeypatch.delenv("CAIRN_SESSION", raising=False)
    yield
    # Tear down: clear again so a stray daemon tick can't write leftover rows
    # into a connection the next test isn't expecting.
    with sink._LOCK:
        sink._BUFFER.clear()
    sink._conn_factory = None


class _UnclosableConn:
    """Wraps a sqlite connection so ``close()`` is a no-op.

    ``_flush_events`` closes the connection it opens in a ``finally`` block.
    The ``_events_db`` fixture yields a single private ``:memory:`` connection
    that is destroyed once closed, so without this wrapper the test could not
    read back the rows it just flushed. Only the methods ``_flush_events``
    touches (``executemany``, ``execute`` for prune, ``commit``, ``close``)
    are forwarded.
    """

    def __init__(self, real: sqlite3.Connection):
        self._real = real

    def executemany(self, sql, params):
        return self._real.executemany(sql, params)

    def execute(self, sql, params=()):
        return self._real.execute(sql, params)

    def commit(self):
        return self._real.commit()

    def close(self):  # keep the underlying DB alive for post-flush assertions
        pass


@pytest.fixture
def events_db():
    """A fresh in-memory DB with the events + build_runs DDL, wired to the sink.

    The connection is wrapped in ``_UnclosableConn`` so the flush's ``close()``
    doesn't destroy the ``:memory:`` DB before assertions can read it back.
    Row factory is set so readbacks use dict-style access.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_EVENTS_DDL)
    conn.commit()
    configure_conn(lambda: _UnclosableConn(conn))
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1. emit -- row shape + attr serialization
# ---------------------------------------------------------------------------


def test_emit_appends_row_with_correct_shape():
    """emit appends one (ts, name, session_id, attrs_json) tuple to the buffer.

    The 4-tuple matches the ``events(ts, name, session_id, attrs)`` columns the
    sink's executemany inserts.
    """
    emit(ANN_FALLBACK, reason="load_failed")

    rows = list(sink._BUFFER)
    assert len(rows) == 1, "exactly one event row per emit"
    ts, name, session_id, attrs_json = rows[0]
    assert name == ANN_FALLBACK
    assert session_id == "unknown", "default session when CAIRN_SESSION unset"
    assert isinstance(ts, float)
    assert json.loads(attrs_json) == {"reason": "load_failed"}


def test_emit_with_no_attrs_stores_null():
    """An emit with no attrs stores NULL (None), not the string 'null'."""
    emit(LOCK_CONTENTION)

    rows = list(sink._BUFFER)
    assert len(rows) == 1
    _ts, _name, _sid, attrs_json = rows[0]
    assert attrs_json is None, "empty attrs -> NULL column, not 'null' text"


def test_emit_serializes_nested_and_nonstring_attrs():
    """Lists/ints/bools serialize to compact JSON (bounded-cardinality values)."""
    emit(
        "semantic_backend",
        backend="ann",
        fusion=1,
        rerank=False,
        ms_bucket="10-100ms",
        tags=["a", "b"],
    )

    _ts, _name, _sid, attrs_json = sink._BUFFER[0]
    decoded = json.loads(attrs_json)
    assert decoded == {
        "backend": "ann",
        "fusion": 1,
        "rerank": False,
        "ms_bucket": "10-100ms",
        "tags": ["a", "b"],
    }


def test_emit_truncates_oversized_attr_value():
    """A single attr value over the cap is truncated (WAL-bloat guardrail)."""
    huge = "x" * (ev._MAX_ATTR_CHARS + 500)
    emit("truncate_result", tool="explore", chars=huge)

    _ts, _name, _sid, attrs_json = sink._BUFFER[0]
    decoded = json.loads(attrs_json)
    assert len(decoded["chars"]) == ev._MAX_ATTR_CHARS
    assert decoded["tool"] == "explore"


def test_emit_never_raises_on_non_serializable_attr():
    """A non-serializable attr is coerced (default=str); emit never raises.

    Telemetry is best-effort (spec §5.6): a caller passing an odd object is a
    bug, but it must not propagate as an exception into the tool call path.
    """
    emit(
        "task_lifecycle", task_kind=object(), event="claimed"
    )  # object() is not JSON-native

    # Must not raise; the row still lands with the coerced value.
    assert len(sink._BUFFER) == 1
    _ts, _name, _sid, attrs_json = sink._BUFFER[0]
    decoded = json.loads(attrs_json)
    assert decoded["event"] == "claimed"


def test_session_env_stamps_session_id(monkeypatch):
    """CAIRN_SESSION stamps the session_id column for cross-call correlation."""
    monkeypatch.setenv("CAIRN_SESSION", "trace-7")
    emit(ANN_FALLBACK, reason="no_index")

    _ts, _name, session_id, _attrs = sink._BUFFER[0]
    assert session_id == "trace-7"


# ---------------------------------------------------------------------------
# 2. Gates -- CAIRN_TELEMETRY=off / CAIRN_READ_ONLY
# ---------------------------------------------------------------------------


def test_telemetry_off_makes_emit_a_noop(monkeypatch):
    """CAIRN_TELEMETRY=off -> emit appends nothing (master kill switch)."""
    monkeypatch.setenv("CAIRN_TELEMETRY", "off")
    emit(ANN_FALLBACK, reason="load_failed")
    assert len(sink._BUFFER) == 0, "telemetry off -> emit is a no-op"


def test_telemetry_off_makes_warn_once_a_noop(monkeypatch, caplog):
    """CAIRN_TELEMETRY=off -> warn_once does not log."""
    monkeypatch.setenv("CAIRN_TELEMETRY", "off")
    with caplog.at_level(logging.WARNING):
        warn_once("k", logging.getLogger("test"), "should not fire")
    assert all("should not fire" not in r.message for r in caplog.records)


def test_telemetry_off_makes_note_contention_a_noop(monkeypatch, caplog):
    """CAIRN_TELEMETRY=off -> note_contention neither emits nor logs."""
    monkeypatch.setenv("CAIRN_TELEMETRY", "off")
    with caplog.at_level(logging.WARNING):
        note_contention("schema.get_db")
    assert len(sink._BUFFER) == 0
    assert all("lock contention" not in r.message.lower() for r in caplog.records)


def test_read_only_skips_emit_write(monkeypatch):
    """CAIRN_READ_ONLY=1 -> emit skips the buffer (mode=ro would fail every flush).

    Same rationale as metric_buffering._log_metric: a read-only daemon opening
    the DB with mode=ro cannot INSERT, so buffering would just fill to the
    deque cap and the flush thread would spin on guaranteed failures.
    """
    monkeypatch.setenv("CAIRN_READ_ONLY", "1")
    emit(ANN_FALLBACK, reason="load_failed")
    assert len(sink._BUFFER) == 0


@pytest.mark.parametrize("flag", ["1", "true", "yes"])
def test_read_only_skips_for_all_truthy_values(monkeypatch, flag):
    """All three truthy spellings of CAIRN_READ_ONLY gate the emit write."""
    monkeypatch.setenv("CAIRN_READ_ONLY", flag)
    emit(ANN_FALLBACK, reason="load_failed")
    assert len(sink._BUFFER) == 0


def test_telemetry_off_value_is_case_insensitive(monkeypatch):
    """'OFF' / 'Off' also disable (strip().lower() comparison)."""
    for val in ("OFF", "Off", " off "):
        monkeypatch.setenv("CAIRN_TELEMETRY", val)
        sink._BUFFER.clear()
        emit(ANN_FALLBACK, reason="load_failed")
        assert len(sink._BUFFER) == 0, f"{val!r} should disable telemetry"


def test_telemetry_on_by_default(monkeypatch):
    """With CAIRN_TELEMETRY unset (or any non-'off' value), emit buffers."""
    monkeypatch.delenv("CAIRN_TELEMETRY", raising=False)
    emit(ANN_FALLBACK, reason="load_failed")
    assert len(sink._BUFFER) == 1


# ---------------------------------------------------------------------------
# 3. _flush_events / flush -- drain / retain / no-raise
# ---------------------------------------------------------------------------


def test_flush_empty_buffer_is_noop(events_db):
    """Flushing an empty buffer touches neither the DB nor the drain."""
    flush()  # must not raise
    count = events_db.execute("SELECT COUNT(*) AS c FROM events").fetchone()["c"]
    assert count == 0
    assert len(sink._BUFFER) == 0


def test_flush_without_factory_is_noop():
    """With no factory configured, flush returns after snapshotting.

    The buffer is snapshotted but never drained (rows stay queued) so a later
    ``configure_conn`` + flush can still land them.
    """
    emit(ANN_FALLBACK, reason="load_failed")
    assert len(sink._BUFFER) == 1

    flush()  # factory is None (reset by the autouse fixture)

    assert len(sink._BUFFER) == 1, "row retained when no factory is set"


def test_flush_drains_buffer_on_success(events_db):
    """A successful executemany+commit drains the buffer into events."""
    emit(ANN_FALLBACK, reason="load_failed")
    emit(LOCK_CONTENTION, site="schema.get_db")
    assert len(sink._BUFFER) == 2

    flush()

    assert len(sink._BUFFER) == 0, "buffer drained after successful commit"
    rows = events_db.execute(
        "SELECT name, session_id, attrs FROM events ORDER BY id"
    ).fetchall()
    assert [r["name"] for r in rows] == [ANN_FALLBACK, LOCK_CONTENTION]
    assert json.loads(rows[0]["attrs"]) == {"reason": "load_failed"}
    assert json.loads(rows[1]["attrs"]) == {"site": "schema.get_db"}


def test_flush_keeps_rows_appended_during_flush(events_db):
    """Rows appended mid-flush survive: the drain pops exactly len(snapshot).

    ``_flush_events`` snapshots the buffer, writes the snapshot, then
    ``popleft``s exactly ``len(batch)`` rows. A row appended after the
    snapshot sits to the right of the drained rows and is left queued.
    """
    concurrent = (9999.0, "concurrent", "unknown", None)

    class _LateAppendConn(_UnclosableConn):
        def executemany(self, sql, params):
            # Simulate a concurrent emit landing while the flush is mid-write.
            with sink._LOCK:
                sink._BUFFER.append(concurrent)
            return self._real.executemany(sql, params)

    configure_conn(lambda: _LateAppendConn(events_db))
    emit(ANN_FALLBACK, reason="load_failed")
    emit(LOCK_CONTENTION, site="a")
    assert len(sink._BUFFER) == 2

    flush()

    # The late-appended row survived the drain.
    assert list(sink._BUFFER) == [concurrent]
    # Only the two pre-flush rows were written.
    rows = events_db.execute("SELECT name FROM events ORDER BY id").fetchall()
    assert [r["name"] for r in rows] == [ANN_FALLBACK, LOCK_CONTENTION]


def test_flush_factory_failure_never_raises_and_retains():
    """When the conn factory raises, flush does not raise and rows stay queued.

    Telemetry is best-effort: a transient failure (e.g. 'database is locked')
    must never raise into the caller and must never drop rows silently -- they
    remain queued so the next flush tick retries them.
    """

    def _boom_factory():
        raise RuntimeError("database is locked")

    configure_conn(_boom_factory)
    emit(ANN_FALLBACK, reason="load_failed")
    emit(LOCK_CONTENTION, site="a")
    assert len(sink._BUFFER) == 2

    flush()  # must not raise

    assert len(sink._BUFFER) == 2, "rows retained for retry on flush failure"


def test_flush_executemany_failure_never_raises_and_retains(events_db):
    """A failure inside executemany (not the factory) also retains the buffer.

    This is the more common real failure mode -- the connection opens fine but
    the INSERT itself hits 'database is locked'. The same try/except guard
    catches it and leaves the rows queued.
    """

    class _LockedWriteConn(_UnclosableConn):
        def executemany(self, sql, params):
            raise sqlite3.OperationalError("database is locked")

    configure_conn(lambda: _LockedWriteConn(events_db))
    emit(ANN_FALLBACK, reason="load_failed")
    assert len(sink._BUFFER) == 1

    flush()  # must not raise

    assert len(sink._BUFFER) == 1, "row retained when executemany raises"
    count = events_db.execute("SELECT COUNT(*) AS c FROM events").fetchone()["c"]
    assert count == 0


def test_emit_never_raises_when_sink_enqueue_fails(monkeypatch):
    """emit swallows any internal failure (best-effort doctrine, spec §5.6).

    Even if the sink's enqueue path were to raise, emit must not propagate it
    into the caller (a tool call / build path).
    """

    def _boom_enqueue(*args, **kwargs):
        raise RuntimeError("simulated sink failure")

    monkeypatch.setattr(sink, "enqueue", _boom_enqueue)
    emit(ANN_FALLBACK, reason="load_failed")  # must not raise
    assert len(sink._BUFFER) == 0


# ---------------------------------------------------------------------------
# 4. Retention pruning (spec §6.2)
# ---------------------------------------------------------------------------


def test_flush_prunes_events_to_newest_n(events_db, monkeypatch):
    """The flush trims the events table to the newest N rows, in-transaction.

    Bounded DB growth: opportunistic pruning keeps the shared DB file from
    growing unbounded. Uses a small cap (via monkeypatch) so the test is fast;
    production default is 5000.
    """
    monkeypatch.setattr(sink, "_MAX_EVENTS_ROWS", 3)
    for i in range(5):
        emit(ANN_FALLBACK, idx=i)
    assert len(sink._BUFFER) == 5

    flush()

    assert len(sink._BUFFER) == 0, "buffer drained"
    rows = events_db.execute("SELECT attrs FROM events ORDER BY id").fetchall()
    assert len(rows) == 3, "pruned to newest 3"
    # The newest 3 (idx 2,3,4) survive; the oldest 2 (idx 0,1) are pruned.
    surviving_idx = {json.loads(r["attrs"])["idx"] for r in rows}
    assert surviving_idx == {2, 3, 4}, "keeps the newest rows by id"


def test_prune_tolerates_missing_build_runs_table(events_db, monkeypatch):
    """Prune does not raise if build_runs is absent (pre-T08 / partial DB).

    ``_prune`` guards each DELETE so a missing table or read-only connection
    is absorbed rather than aborting the insert.
    """
    events_db.execute("DROP TABLE build_runs")
    monkeypatch.setattr(sink, "_MAX_EVENTS_ROWS", 5000)
    emit(ANN_FALLBACK, reason="load_failed")

    flush()  # must not raise despite build_runs being gone

    assert len(sink._BUFFER) == 0
    count = events_db.execute("SELECT COUNT(*) AS c FROM events").fetchone()["c"]
    assert count == 1


# ---------------------------------------------------------------------------
# 5. warn_once / note_contention
# ---------------------------------------------------------------------------


def test_warn_once_fires_only_once_per_key(caplog):
    """Each key warns at most once per process; distinct keys warn independently."""
    log = logging.getLogger("test_warn_once")
    with caplog.at_level(logging.WARNING, logger="test_warn_once"):
        warn_once("ann", log, "ann degraded")
        warn_once("ann", log, "ann degraded")
        warn_once("hash", log, "hash degraded")
        warn_once("ann", log, "ann degraded")

    msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert msgs.count("ann degraded") == 1, "same key warns once"
    assert msgs.count("hash degraded") == 1, "distinct key warns independently"


def test_note_contention_emits_event_and_warns_once_per_site(caplog):
    """note_contention emits a lock_contention event + warns once per site.

    This is the P1 upgrade of schema.note_contention (P0: log-only). The event
    makes contention observable by doctor / metrics; the warn_once keeps the
    log from spamming on repeated contention at the same site.
    """
    with caplog.at_level(logging.WARNING, logger="cairn.telemetry.events"):
        note_contention("schema.get_db")
        note_contention("schema.get_db")  # same site -> second call is a no-op
        note_contention("lexical.search")  # distinct site -> independent

    # Exactly one event per call (3 emits); events are not rate-limited.
    assert len(sink._BUFFER) == 3
    names = [row[1] for row in sink._BUFFER]
    assert names == [LOCK_CONTENTION, LOCK_CONTENTION, LOCK_CONTENTION]
    sites = [json.loads(row[3])["site"] for row in sink._BUFFER]
    assert sites == ["schema.get_db", "schema.get_db", "lexical.search"]

    # But the WARNING fires once per site (2 distinct sites -> 2 warnings).
    contention_msgs = [
        r.message for r in caplog.records if "lock contention" in r.message.lower()
    ]
    assert len(contention_msgs) == 2, "one warning per distinct site"


# ---------------------------------------------------------------------------
# 6. start_flusher idempotency + shared-thread registration
# ---------------------------------------------------------------------------


def test_start_flusher_is_idempotent():
    """start_flusher can be called many times; only one thread is started.

    The shared thread is a process singleton -- every subsystem (events,
    metric_buffering) calls start_flusher on its first emit, and they must all
    land on the same single thread, not one each.
    """
    sink.start_flusher()
    assert sink._FLUSHER_STARTED is True
    sink.start_flusher()  # idempotent: no second thread, no error
    sink.start_flusher()
    assert sink._FLUSHER_STARTED is True


def test_register_flusher_is_idempotent_by_identity():
    """Registering the same callable twice is a no-op (no double-fire)."""

    def _flusher():
        pass

    sink.register_flusher(_flusher)
    sink.register_flusher(_flusher)  # same object -> not added again

    with sink._LOCK:
        count = sink._FLUSHERS.count(_flusher)
        # Cleanup: remove the flusher we added so it doesn't leak into other
        # tests via the module-level _FLUSHERS list (the autouse fixture doesn't
        # clear it because metric_buffering's real registration must persist).
        sink._FLUSHERS.remove(_flusher)

    assert count == 1, "same callable registered exactly once"
