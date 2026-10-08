"""Shared buffered sink for cairn telemetry."""

from __future__ import annotations

import atexit
import collections
import logging
import os
import threading
import time
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

_BUFFER: collections.deque = collections.deque(maxlen=2000)
_LOCK = threading.Lock()

_FLUSHER_STARTED = False
_FLUSH_INTERVAL = 30.0  # seconds

_FLUSH_LOCK = threading.Lock()

_FLUSHERS: List[Callable[[], None]] = []

_conn_factory: Optional[Callable[[], "object"]] = None

_MAX_EVENTS_ROWS = 5000
_MAX_BUILD_RUNS_ROWS = 500

_DEFAULT_TOOL_METRICS_ROWS = 50_000


def _tool_metrics_max_rows() -> int:
    """Return the effective tool-metrics row cap, falling back safely."""
    raw = os.environ.get("CAIRN_TOOL_METRICS_MAX_ROWS", "")
    if raw:
        try:
            val = int(raw)
        except ValueError:
            val = -1
        if val >= 0:
            return val
        logger.debug("unparseable CAIRN_TOOL_METRICS_MAX_ROWS=%r, using default", raw)
    return _DEFAULT_TOOL_METRICS_ROWS


def _tool_metrics_max_age() -> Optional[float]:
    """Return the tool-metrics age bound in seconds, or None if disabled."""
    raw = os.environ.get("CAIRN_TOOL_METRICS_MAX_AGE_SECONDS", "")
    if not raw:
        return None
    try:
        val = float(raw)
    except ValueError:
        logger.debug("unparseable CAIRN_TOOL_METRICS_MAX_AGE_SECONDS=%r, ignoring", raw)
        return None
    return val if val >= 0 else None


def is_telemetry_off() -> bool:
    """Return whether ``CAIRN_TELEMETRY`` disables telemetry."""
    return os.environ.get("CAIRN_TELEMETRY", "on").strip().lower() == "off"


def is_read_only() -> bool:
    """Return whether ``CAIRN_READ_ONLY`` marks a read-only process."""
    return os.environ.get("CAIRN_READ_ONLY", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def retention_policy() -> dict:
    """Return the effective telemetry retention policy as a plain dict."""
    return {
        "events_max_rows": _MAX_EVENTS_ROWS,
        "build_runs_max_rows": _MAX_BUILD_RUNS_ROWS,
        "tool_metrics_max_rows": _tool_metrics_max_rows(),
        "tool_metrics_max_age_seconds": _tool_metrics_max_age(),
    }


def configure_conn(conn_factory: Callable[[], "object"]) -> None:
    """Inject the writable connection factory used to flush events."""
    global _conn_factory
    _conn_factory = conn_factory


def register_flusher(fn: Callable[[], None]) -> None:
    """Register an idempotent-by-identity callable for each flush tick."""
    with _LOCK:
        if fn not in _FLUSHERS:
            _FLUSHERS.append(fn)


def enqueue(ts: float, name: str, session_id: str, attrs_json: Optional[str]) -> None:
    """Append a serialized event and lazily start the shared flusher."""
    with _LOCK:
        _BUFFER.append((ts, name, session_id, attrs_json))
    start_flusher()


def _prune(conn):
    """Prune telemetry tables by recency without failing the insert."""
    try:
        conn.execute(
            "DELETE FROM events WHERE id NOT IN "
            "(SELECT id FROM events ORDER BY ts DESC, id DESC LIMIT ?)",
            (_MAX_EVENTS_ROWS,),
        )
    except Exception:
        pass
    try:
        conn.execute(
            "DELETE FROM build_runs WHERE id NOT IN "
            "(SELECT id FROM build_runs ORDER BY started_at DESC, id DESC LIMIT ?)",
            (_MAX_BUILD_RUNS_ROWS,),
        )
    except Exception:
        pass
    try:
        conn.execute(
            "DELETE FROM tool_metrics WHERE id NOT IN "
            "(SELECT id FROM tool_metrics ORDER BY invoked_at DESC, id DESC LIMIT ?)",
            (_tool_metrics_max_rows(),),
        )
        max_age = _tool_metrics_max_age()
        if max_age is not None:
            conn.execute(
                "DELETE FROM tool_metrics WHERE invoked_at < ?",
                (time.time() - max_age,),
            )
    except Exception:
        pass


def _flush_events():
    """Drain committed event rows to storage without raising."""
    if _conn_factory is None:
        return
    with _FLUSH_LOCK:
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
        with _LOCK:
            for _ in range(len(batch)):
                try:
                    _BUFFER.popleft()
                except IndexError:
                    break


def _flush_all() -> None:
    """Run the event flush and each registered flusher in isolation."""
    try:
        _flush_events()
    except Exception:
        logger.debug("_flush_events raised", exc_info=True)
    with _LOCK:
        flushers = list(_FLUSHERS)
    for fn in flushers:
        try:
            fn()
        except Exception:
            logger.debug("registered flusher %r raised", fn, exc_info=True)


def start_flusher() -> None:
    """Start the shared daemon and atexit drain exactly once."""
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
    """Synchronously drain buffered events on a best-effort basis."""
    _flush_events()
