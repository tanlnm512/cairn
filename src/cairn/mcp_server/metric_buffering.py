"""MCP tool metric buffering.

``tool_metrics`` is analytics, not correctness. Writing it on every tool call
takes a SQLite write lock that contends with ``cairn memory search`` /
``recall_memory`` (which also write) and with other ``cairn serve`` processes
holding the WAL. Buffering here + flushing on a background thread removes the
write from every tool call's hot path: one writer every 30s instead of one
per call.

Fully self-contained except for a connection factory (``_conn``) which is
injected via :func:`configure_conn` so this module doesn't need to know about
the store / schema layer.
"""
from __future__ import annotations

import atexit
import collections
import functools
import os
import threading
import time
from typing import Callable, Optional

_METRIC_BUFFER: collections.deque = collections.deque(maxlen=2000)
_METRIC_LOCK = threading.Lock()
_METRIC_FLUSHER_STARTED = False
_METRIC_FLUSH_INTERVAL = 30.0  # seconds

# Hard cap on any tool's returned string, enforced centrally so a caller never
# hits the MCP client's "exceeds maximum allowed tokens" hard failure -- a
# tool that forgets its own limit/pagination still degrades to a truncation
# notice instead of an opaque client-side rejection. ~4 chars/token, so this
# stays well under typical 25k-token MCP result ceilings.
MAX_RESULT_CHARS = int(os.environ.get("CAIRN_MAX_RESULT_CHARS", "60000"))


def _truncate_result(name: str, result: str) -> str:
    if len(result) <= MAX_RESULT_CHARS:
        return result
    head = result[:MAX_RESULT_CHARS]
    # Cut at the last newline so the truncation note doesn't land mid-line.
    cut = head.rfind("\n")
    if cut > 0:
        head = head[:cut]
    return (
        f"{head}\n\n"
        f"[TRUNCATED: '{name}' returned {len(result)} chars, over the "
        f"{MAX_RESULT_CHARS}-char cap. Narrow the query -- e.g. pass a "
        f"smaller `limit`, use fuzzy=False, a more specific pattern, or a "
        f"lower `depth` -- rather than relying on this truncated output.]"
    )

# Connection factory injected by the server core (avoids a circular import
# with src.graph.schema / src.paths). Defaults to None; configure_conn() must
# be called once at server boot before any tool is invoked.
_conn_factory: Optional[Callable[[], "object"]] = None


def configure_conn(conn_factory: Callable[[], "object"]) -> None:
    """Inject the connection factory used by _flush_metrics.

    Called once from server.run() at boot. Must come from outside so this
    module stays free of the schema/store dependency.
    """
    global _conn_factory
    _conn_factory = conn_factory


def _flush_metrics():
    """Drain the metric buffer into tool_metrics (best-effort).

    The buffer is only cleared *after* a successful commit, so a transient
    failure (e.g. "database is locked") leaves the rows in place for the next
    flush attempt instead of silently dropping them.
    """
    import logging
    logger = logging.getLogger(__name__)

    # Snapshot the buffer WITHOUT clearing it yet -- a failed flush then leaves
    # the rows queued for the next attempt. The maxlen on the deque still caps
    # unbounded growth during a long outage.
    with _METRIC_LOCK:
        if not _METRIC_BUFFER:
            return
        batch = list(_METRIC_BUFFER)
    if _conn_factory is None:
        return
    conn = None
    try:
        conn = _conn_factory()
        conn.executemany(
            "INSERT INTO tool_metrics (tool_name, session_id, invoked_at, duration_ms, status, error_message) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            batch,
        )
        conn.commit()
    except Exception:
        # Couldn't flush this batch -- leave it buffered for the next attempt.
        # Metrics are best-effort and must never block tool execution or hold a
        # lock, but log at debug so silent drops/backlog are still observable.
        logger.debug("metric flush failed; %d rows remain buffered", len(batch), exc_info=True)
        return
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    # Commit succeeded -> safe to drop these rows from the buffer. Only remove
    # the rows we actually wrote; newer rows appended during the flush stay.
    with _METRIC_LOCK:
        for _ in range(len(batch)):
            try:
                _METRIC_BUFFER.popleft()
            except IndexError:
                break


def _start_metric_flusher():
    """Start the background flush thread once (idempotent, daemon)."""
    global _METRIC_FLUSHER_STARTED
    if _METRIC_FLUSHER_STARTED:
        return
    with _METRIC_LOCK:
        if _METRIC_FLUSHER_STARTED:
            return
        _METRIC_FLUSHER_STARTED = True

    def _loop():
        while True:
            time.sleep(_METRIC_FLUSH_INTERVAL)
            _flush_metrics()

    t = threading.Thread(target=_loop, name="cairn-metric-flusher", daemon=True)
    t.start()
    atexit.register(_flush_metrics)


def _log_metric(tool_name: str, duration_ms: float, status: str = "ok",
                error_message: str = ""):
    """Record a tool invocation (buffered; flushes on a background thread)."""
    row = (
        tool_name,
        os.environ.get("CAIRN_SESSION", "unknown"),
        time.time(),
        duration_ms,
        status,
        error_message[:500] if error_message else None,
    )
    with _METRIC_LOCK:
        _METRIC_BUFFER.append(row)
    _start_metric_flusher()


def instrument(fn):
    """Decorator: wraps an MCP tool with timing + error capture + metric logging.

    Uses functools.wraps (not manual __name__/__doc__ copying) so that
    __wrapped__ is set and inspect.signature(wrapper) resolves to the
    original function's real parameters. FastMCP's @mcp.tool() introspects
    the signature of whatever it decorates to build the tool's JSON schema;
    without __wrapped__, it would only see (*args, **kwargs) and generate a
    broken schema (and broken calls) for every tool.

    Renamed from ``_instrument`` (was private in server.py) since it now
    lives in its own module and is imported by every tools_*.py.
    """
    import logging
    import traceback
    from pathlib import Path
    
    logger = logging.getLogger(__name__)
    
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        name = fn.__name__
        t0 = time.time()
        try:
            result = fn(*args, **kwargs)
            _log_metric(name, (time.time() - t0) * 1000, "ok")
            if isinstance(result, str):
                result = _truncate_result(name, result)
            return result
        except Exception as exc:
            duration_ms = (time.time() - t0) * 1000
            
            # Log full traceback server-side
            tb_str = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            logger.error(f"Error in {name}: {exc}\n{tb_str}")

            # Sanitize error message to remove internal paths
            error_msg = str(exc)
            # Remove user-specific paths like /Users/tan.le/, /home/user/, etc.
            # Also remove project root paths that might leak
            home_dir = str(Path.home())
            if home_dir in error_msg:
                error_msg = error_msg.replace(home_dir, "~")
            # Remove other common path patterns
            parts_to_remove = [
                "/Projects/", "/cairn/", "/src/", "/.knowledge/",
                "/.cairn/", "\\.knowledge\\/", "\\.cairn\\/",
            ]
            for part in parts_to_remove:
                error_msg = error_msg.replace(part, "/")
            
            # Return sanitized error string instead of raising
            sanitized_result = f"[ERROR: {name} failed - {error_msg}]"
            
            _log_metric(name, duration_ms, "error", str(exc))
            return sanitized_result

    return wrapper
