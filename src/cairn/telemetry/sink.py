"""Shared buffered sink for cairn telemetry (spec §6.1).

Generalizes the proven ``mcp_server/metric_buffering`` buffered-sink pattern
into a single per-process writer so all telemetry -- events (this module),
future counters, and ``tool_metrics`` (via ``metric_buffering``'s registered
flusher) -- share ONE daemon flush thread + ONE atexit handler instead of each
subsystem spawning its own.

Doctrine (mirrors ``mcp_server/metric_buffering.py``):
  * Telemetry is analytics, not correctness. A sink failure must NEVER raise
    into a caller, never hold a user lock, and never block a tool call
    (spec §5.4/§5.6).
  * Buffer then flush on a 30s daemon thread; ``atexit`` drains at process end.
  * The buffer is snapshotted WITHOUT clearing on flush -- a transient failure
    ("database is locked") leaves the rows queued for the next tick instead of
    dropping them silently. The deque ``maxlen`` caps unbounded growth during a
    long outage.
  * Retention pruning keeps the shared DB file bounded (spec §6.2): newest
    ~5000 ``events`` / ~500 ``build_runs`` rows, inside the flush transaction.

Thread model: one daemon thread per process, started idempotently by
:func:`start_flusher`. Each tick it drains this module's own event buffer via
:func:`_flush_events` and then every flusher registered with
:func:`register_flusher` (e.g. ``metric_buffering._flush_metrics``). Subsystems
own their own buffer + table; the sink owns only the thread, the event buffer,
the connection factory, and the gates.
"""

from __future__ import annotations

import atexit
import collections
import logging
import os
import threading
import time
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

# The buffer of pending events: ``(ts, name, session_id, attrs_json)`` tuples,
# shaped to match the ``events(ts, name, session_id, attrs)`` columns. maxlen
# caps unbounded growth during a long flush outage (mirrors metric_buffering's
# deque(maxlen=2000)).
_BUFFER: collections.deque = collections.deque(maxlen=2000)
_LOCK = threading.Lock()

# Flush-thread lifecycle. ``_FLUSHER_STARTED`` is set under ``_LOCK``
# (double-checked locking) so :func:`start_flusher` is process-idempotent. It
# is NOT reset between tests -- doing so would let the next emit spawn a second
# shared thread. Tests reset the mutable state (``_BUFFER`` /
# ``_conn_factory`` / the warn guards) but leave this flag alone.
_FLUSHER_STARTED = False
_FLUSH_INTERVAL = 30.0  # seconds

# External flush callables registered by subsystems (e.g.
# ``metric_buffering._flush_metrics``). The daemon invokes each on every tick,
# in addition to this module's own :func:`_flush_events`. Idempotent by
# identity so a subsystem that resets its own ``_STARTED`` flag and re-emits
# cannot double-register.
_FLUSHERS: List[Callable[[], None]] = []

# Connection factory injected once at boot. The sink opens a fresh connection
# per flush and closes it in ``finally`` (mirrors metric_buffering). Kept
# injectable so this module stays free of the schema/store dependency.
_conn_factory: Optional[Callable[[], "object"]] = None

# Retention caps (spec §6.2). Opportunistic pruning inside the flush thread
# keeps the shared DB file bounded; rows past the cap are DELETEd by id.
_MAX_EVENTS_ROWS = 5000
_MAX_BUILD_RUNS_ROWS = 500


def is_telemetry_off() -> bool:
    """True when CAIRN_TELEMETRY=off -- the master kill switch (spec §5.1).

    Read on every emit() so a test/process toggling the env takes effect
    without a module reload (mirrors metric_buffering reading CAIRN_READ_ONLY
    per call). Default is on-but-local.
    """
    return os.environ.get("CAIRN_TELEMETRY", "on").strip().lower() == "off"


def is_read_only() -> bool:
    """True when the process is a read-only daemon (CAIRN_READ_ONLY truthy).

    Read-only daemons open the DB with mode=ro, so INSERT into ``events`` would
    fail every flush and buffer indefinitely (capped by deque maxlen).
    Telemetry is analytics -- skip the write entirely on a read-only server so
    the table doesn't silently stay empty and the flush thread doesn't spin on
    a guaranteed failure. Same rationale as
    ``metric_buffering._log_metric``.
    """
    return os.environ.get("CAIRN_READ_ONLY", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def configure_conn(conn_factory: Callable[[], "object"]) -> None:
    """Inject the writable-connection factory used to flush events.

    Called once at server boot. ``metric_buffering.configure_conn`` mirrors
    into here (spec §6.1) so the single existing boot call wires both
    ``tool_metrics`` and ``events``. Must come from outside the sink so this
    module stays free of the schema/store dependency -- mirrors
    ``metric_buffering.configure_conn``.
    """
    global _conn_factory
    _conn_factory = conn_factory


def register_flusher(fn: Callable[[], None]) -> None:
    """Register a flush callable the daemon thread invokes each tick.

    Used by ``metric_buffering`` to share this sink's single thread instead of
    spawning its own (spec §6.1). Idempotent by identity: registering the same
    ``fn`` twice is a no-op, so a test that resets a subsystem's ``_STARTED``
    flag and re-emits cannot double-fire the flusher.
    """
    with _LOCK:
        if fn not in _FLUSHERS:
            _FLUSHERS.append(fn)


def enqueue(ts: float, name: str, session_id: str, attrs_json: Optional[str]) -> None:
    """Append a pre-serialized event row to the buffer (thread-safe).

    Called by :func:`cairn.telemetry.events.emit` after gating + serialization.
    Centralizing the append here keeps the buffer/lock in one module while
    ``events.py`` owns the public API + attribute policy. Triggers a lazy
    :func:`start_flusher` so the first emit in a process boots the thread.
    """
    with _LOCK:
        _BUFFER.append((ts, name, session_id, attrs_json))
    start_flusher()


def _prune(conn):
    """Bounded growth: keep the newest N rows per table (spec §6.2).

    Runs inside the flush transaction so the prune + the insert are atomic.
    Guarded so a missing ``build_runs`` table (pre-T08 DB) or a read-only
    connection doesn't raise -- prune is best-effort, and a failure here must
    not abort the insert (the rows are already committed-worthy on their own).

    Untyped (no annotations) deliberately, mirroring
    ``metric_buffering._flush_metrics``: the injected connection is an opaque
    duck-typed handle (the sink stays free of the sqlite3 dependency), so its
    attribute access is left for runtime. mypy does not check untyped function
    bodies, which keeps this consistent with the sibling buffered sinks.
    """
    try:
        conn.execute(
            "DELETE FROM events WHERE id NOT IN "
            "(SELECT id FROM events ORDER BY id DESC LIMIT ?)",
            (_MAX_EVENTS_ROWS,),
        )
    except Exception:
        # Table missing (pre-T08 DB) or read-only -- prune is best-effort.
        pass
    try:
        conn.execute(
            "DELETE FROM build_runs WHERE id NOT IN "
            "(SELECT id FROM build_runs ORDER BY id DESC LIMIT ?)",
            (_MAX_BUILD_RUNS_ROWS,),
        )
    except Exception:
        pass


def _flush_events():
    """Drain the event buffer into the ``events`` table (best-effort).

    Mirrors ``metric_buffering._flush_metrics`` exactly: snapshot WITHOUT
    clearing (a failed flush leaves rows queued for retry), write via
    ``executemany``, prune, commit, then ``popleft`` exactly ``len(batch)``
    rows on success. A failure at any point logs at debug and returns without
    draining -- telemetry must never raise or hold a user lock.

    Untyped (no annotations) deliberately, mirroring
    ``metric_buffering._flush_metrics``: the injected connection is an opaque
    duck-typed handle, so its attribute access is left for runtime and mypy
    does not check this body.
    """
    if _conn_factory is None:
        return
    with _LOCK:
        if not _BUFFER:
            return
        batch = list(_BUFFER)
    conn = None
    try:
        conn = _conn_factory()
        conn.executemany(
            "INSERT INTO events (ts, name, session_id, attrs) VALUES (?, ?, ?, ?)",
            batch,
        )
        _prune(conn)
        conn.commit()
    except Exception:
        # Couldn't flush this batch -- leave it buffered for the next attempt.
        # Telemetry is best-effort and must never raise into a caller or hold a
        # lock, but log at debug so silent drops/backlog are still observable.
        logger.debug(
            "event flush failed; %d rows remain buffered", len(batch), exc_info=True
        )
        return
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    # Commit succeeded -> safe to drop these rows from the buffer. Only remove
    # the rows we actually wrote; newer rows appended during the flush stay.
    with _LOCK:
        for _ in range(len(batch)):
            try:
                _BUFFER.popleft()
            except IndexError:
                break


def _flush_all() -> None:
    """Invoke this sink's event flush + every registered flusher.

    Drives both the daemon tick and the atexit drain. Each flusher is isolated:
    a bug/exception in one must not abort the others or kill the thread.
    """
    try:
        _flush_events()
    except Exception:
        # _flush_events already swallows internally; this guards against a
        # logic bug so the thread never dies.
        logger.debug("_flush_events raised", exc_info=True)
    with _LOCK:
        flushers = list(_FLUSHERS)
    for fn in flushers:
        try:
            fn()
        except Exception:
            logger.debug("registered flusher %r raised", fn, exc_info=True)


def start_flusher() -> None:
    """Start the shared daemon flush thread once (idempotent).

    Double-checked under ``_LOCK``. Registers :func:`_flush_all` with
    ``atexit`` so the final tick drains every subsystem (events +
    tool_metrics) at process end. Subsystems call this lazily on their first
    emit (mirrors ``metric_buffering._start_metric_flusher``).
    """
    global _FLUSHER_STARTED
    if _FLUSHER_STARTED:
        return
    with _LOCK:
        if _FLUSHER_STARTED:
            return
        _FLUSHER_STARTED = True

    def _loop():
        while True:
            time.sleep(_FLUSH_INTERVAL)
            _flush_all()

    t = threading.Thread(target=_loop, name="cairn-telemetry-flusher", daemon=True)
    t.start()
    atexit.register(_flush_all)


def flush() -> None:
    """Drain the event buffer synchronously now (best-effort, never raises).

    Public hook for tests and any caller that wants an immediate drain without
    waiting on the 30s daemon tick. Only flushes this sink's own events buffer;
    subsystems (metric_buffering) expose their own ``_flush_*`` for their
    tables. Safe to call before ``configure_conn`` (no-op when factory is None).
    """
    _flush_events()
