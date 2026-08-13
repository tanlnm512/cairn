"""MCP tool metric buffering.

``tool_metrics`` is analytics, not correctness. Writing it on every tool call
takes a SQLite write lock that contends with ``cairn memory search`` /
``recall_memory`` (which also write) and with other ``cairn serve`` processes
holding the WAL. Buffering here + flushing on a background thread removes the
write from every tool call's hot path: one writer every 30s instead of one
per call.

This module owns the ``tool_metrics`` buffer and its flush logic, but no
longer spawns its own daemon thread: it registers ``_flush_metrics`` with the
shared telemetry sink (:mod:`cairn.telemetry.sink`, spec §6.1) so events and
tool_metrics share one writer cadence + one atexit drain. Behavior and the
``tool_metrics`` row shape are unchanged. Self-contained except for a
connection factory (``_conn``) injected via :func:`configure_conn` (which also
mirrors into the sink so a single boot call wires both tables).
"""

from __future__ import annotations

import collections
import functools
import os
import threading
import time
from typing import Callable, Optional

_METRIC_BUFFER: collections.deque = collections.deque(maxlen=2000)
_METRIC_LOCK = threading.Lock()
_METRIC_FLUSHER_STARTED = False

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

    Called once from server.run() at boot. Also mirrors into the shared
    telemetry sink (spec §6.1) so the single boot call wires both
    ``tool_metrics`` (this module's table) and ``events`` (the sink's table).
    Must come from outside so this module stays free of the schema/store
    dependency.
    """
    global _conn_factory
    _conn_factory = conn_factory
    # Mirror into the shared sink so events get the same writable factory.
    # Lazy import avoids any boot-order cycle with the telemetry package.
    from cairn.telemetry import sink as _telemetry_sink

    _telemetry_sink.configure_conn(conn_factory)


def _flush_metrics():
    """Drain the metric buffer into tool_metrics (best-effort).

    The buffer is only cleared *after* a successful commit, so a transient
    failure (e.g. "database is locked") leaves the rows in place for the next
    flush attempt instead of silently dropping them.
    """
    import logging

    logger = logging.getLogger(__name__)

    # Snapshot the buffer WITHOUT clearing it yet -- a failed flush leaves the
    # rows queued for the next attempt. The deque's maxlen caps unbounded
    # growth during a long outage.
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
        logger.debug(
            "metric flush failed; %d rows remain buffered", len(batch), exc_info=True
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
    with _METRIC_LOCK:
        for _ in range(len(batch)):
            try:
                _METRIC_BUFFER.popleft()
            except IndexError:
                break


def _start_metric_flusher():
    """Ensure the shared telemetry flush thread is running and that this
    module's ``_flush_metrics`` is registered with it.

    Previously metric_buffering spawned its own daemon thread + atexit; now it
    reuses the single shared sink thread (spec §6.1) so events and tool_metrics
    share one writer cadence and one atexit drain. ``_METRIC_FLUSHER_STARTED``
    stays as this module's idempotency flag (and is what the test fixture
    resets between tests).
    """
    global _METRIC_FLUSHER_STARTED
    if _METRIC_FLUSHER_STARTED:
        return
    with _METRIC_LOCK:
        if _METRIC_FLUSHER_STARTED:
            return
        _METRIC_FLUSHER_STARTED = True

    # Lazy import avoids any boot-order cycle with the telemetry package.
    from cairn.telemetry import sink as _telemetry_sink

    _telemetry_sink.register_flusher(_flush_metrics)
    _telemetry_sink.start_flusher()


def _log_metric(
    tool_name: str, duration_ms: float, status: str = "ok", error_message: str = ""
):
    """Record a tool invocation (buffered; flushes on a background thread)."""
    # Read-only daemons open the DB with mode=ro, so INSERT INTO tool_metrics
    # would fail every flush and buffer indefinitely (capped by deque maxlen).
    # tool_metrics is analytics, not correctness -- skip the write entirely on
    # a read-only server so the table doesn't silently stay empty and the flush
    # thread doesn't spin on a guaranteed failure.
    if os.environ.get("CAIRN_READ_ONLY", "").lower() in ("1", "true", "yes"):
        return
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

    Uses functools.wraps so ``__wrapped__`` is set and
    inspect.signature(wrapper) resolves to the original function's real
    parameters. FastMCP's @mcp.tool() introspects the signature of whatever
    it decorates to build the tool's JSON schema; without ``__wrapped__`` it
    would only see (*args, **kwargs) and generate a broken schema.
    """
    import logging
    import traceback

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

            # Log full traceback server-side.
            tb_str = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )
            logger.error(f"Error in {name}: {exc}\n{tb_str}")

            _log_metric(name, duration_ms, "error", str(exc))

            # Re-raise so FastMCP's Tool.run converts the exception into a
            # proper MCP error response (isError: true) rather than a prose
            # string that looks like a successful result.
            raise

    return wrapper
