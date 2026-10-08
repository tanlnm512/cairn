"""MCP tool metric buffering."""

from __future__ import annotations

import collections
import functools
import json
import os
import threading
import time
from typing import Callable, Optional

_METRIC_BUFFER: collections.deque = collections.deque(maxlen=2000)
_METRIC_LOCK = threading.Lock()
_METRIC_FLUSHER_STARTED = False

# Hard cap enforced centrally: a tool that forgets its own limit degrades to
# a truncation notice instead of the MCP client's token-limit failure.
MAX_RESULT_CHARS = int(os.environ.get("CAIRN_MAX_RESULT_CHARS", "60000"))

# Cap on the redacted kwargs summary stored per tool_metrics row: the summary
# identifies the call shape, it is not a payload replay, so anything past
# ~200 chars is noise in an analytics table.
MAX_ARGS_SUMMARY_CHARS = 200


def _chars_bucket(n: int) -> str:
    """Bucket a result length into a fixed low-cardinality set."""
    if n <= 500:
        return "<=500"
    if n <= 2000:
        return "500-2k"
    if n <= 10000:
        return "2k-10k"
    return ">10k"


def _truncate_result(name: str, result: str) -> str:
    if len(result) <= MAX_RESULT_CHARS:
        return result
    # Emit only on the truncation branch; lazy, guarded telemetry so a
    # telemetry bug can never fail the tool call.
    try:
        from cairn.telemetry import TRUNCATE_RESULT, emit as _emit

        _emit(TRUNCATE_RESULT, tool=name, chars_bucket=_chars_bucket(len(result)))
    except Exception:
        pass
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


def _kwargs_payload(kwargs: dict) -> tuple:
    """Compact JSON form of a call's kwargs -> ``(req_chars, args_summary)``; never raises."""
    try:
        summary = json.dumps(kwargs, default=str, separators=(",", ":"))
    except Exception:
        return None, None
    return len(summary), summary


def _result_chars(result: object) -> Optional[int]:
    """Char length of a tool result; None when str() fails (never raises)."""
    try:
        return len(result) if isinstance(result, str) else len(str(result))
    except Exception:
        return None


# Connection factory injected by the server core (avoids a circular import
# with the graph schema module). Defaults to None; configure_conn() must
# be called once at server boot before any tool is invoked.
_conn_factory: Optional[Callable[[], "object"]] = None


def configure_conn(conn_factory: Callable[[], "object"]) -> None:
    """Inject the connection factory and mirror it into the shared telemetry sink."""
    global _conn_factory
    _conn_factory = conn_factory
    # Mirror into the shared sink so events get the same writable factory.
    # Lazy import avoids any boot-order cycle with the telemetry package.
    from cairn.telemetry import sink as _telemetry_sink

    _telemetry_sink.configure_conn(conn_factory)


def _flush_metrics():
    """Drain the metric buffer into tool_metrics (best-effort; failed batches requeue)."""
    import logging

    logger = logging.getLogger(__name__)

    # Snapshot AND clear under one lock acquisition: the deque's maxlen could
    # otherwise evict snapshot rows while the flush runs, and a positional
    # drain afterwards would pop never-written rows instead.
    with _METRIC_LOCK:
        if not _METRIC_BUFFER:
            return
        batch = list(_METRIC_BUFFER)
        _METRIC_BUFFER.clear()
    if _conn_factory is None:
        _requeue(batch)
        return
    conn = None
    try:
        conn = _conn_factory()
        # Rows buffered from non-truncated calls omit the two trailing
        # truncation columns; pad them so one INSERT serves both shapes.
        params = [
            row if len(row) > 9 else row + (None, None) for row in batch
        ]
        conn.executemany(
            "INSERT INTO tool_metrics "
            "(tool_name, session_id, invoked_at, duration_ms, status, error_message, "
            "req_chars, resp_chars, args_summary, truncated_from_chars, "
            "truncated_to_chars) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            params,
        )
        conn.commit()
    except Exception:
        # Couldn't flush this batch -- re-queue it for the next attempt.
        # Metrics are best-effort and must never block tool execution or hold a
        # lock, but log at debug so silent drops/backlog are still observable.
        _requeue(batch)
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


def _requeue(batch: list) -> None:
    """Return unflushed rows to the buffer front, FIFO; on overflow keep the newest rows."""
    with _METRIC_LOCK:
        room = (_METRIC_BUFFER.maxlen or len(batch)) - len(_METRIC_BUFFER)
        if room <= 0:
            return
        requeue = batch[-room:]
        _METRIC_BUFFER.extendleft(reversed(requeue))


def _start_metric_flusher():
    """Register ``_flush_metrics`` with the shared telemetry flusher (idempotent)."""
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
    tool_name: str,
    duration_ms: float,
    status: str = "ok",
    error_message: str = "",
    req_chars: Optional[int] = None,
    resp_chars: Optional[int] = None,
    args_summary: Optional[str] = None,
    truncated_from_chars: Optional[int] = None,
    truncated_to_chars: Optional[int] = None,
):
    """Buffer one tool invocation row; optional payload fields leave NULLs, never break."""
    # Read-only daemons cannot INSERT; skipping keeps the flush thread from
    # spinning on a guaranteed failure.
    if os.environ.get("CAIRN_READ_ONLY", "").lower() in ("1", "true", "yes"):
        return
    # CAIRN_TELEMETRY=off stops tool_metrics like events; is_telemetry_off()
    # re-reads the env every call.
    from cairn.telemetry.sink import is_telemetry_off

    if is_telemetry_off():
        return
    # Redact at the write chokepoint: exceptions routinely echo request
    # payloads (connection strings, auth headers), so scrub BEFORE the row
    # is buffered, then truncate.
    if error_message:
        from cairn.memory.privacy import strip_private_data

        error_message = strip_private_data(error_message)
    # kwargs routinely embed user code, paths, and credentials -- the JSON
    # summary is scrubbed before it is ever buffered, same chokepoint rule as
    # error_message above.
    if args_summary:
        from cairn.memory.privacy import strip_private_data

        args_summary = strip_private_data(args_summary)
    row: tuple = (
        tool_name,
        os.environ.get("CAIRN_SESSION", "unknown"),
        time.time(),
        duration_ms,
        status,
        error_message[:500] if error_message else None,
        req_chars,
        resp_chars,
        args_summary[:MAX_ARGS_SUMMARY_CHARS] if args_summary else None,
    )
    if truncated_from_chars is not None:
        # The truncation columns ride the row only when the call was actually
        # capped; _flush_metrics pads shorter rows with NULLs.
        row = row + (truncated_from_chars, truncated_to_chars)
    with _METRIC_LOCK:
        _METRIC_BUFFER.append(row)
    _start_metric_flusher()


def instrument(fn):
    """Wrap an MCP tool with timing, size capture, truncation, and metric logging."""
    import logging
    import traceback

    logger = logging.getLogger(__name__)

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        name = fn.__name__
        req_chars, args_summary = _kwargs_payload(kwargs)
        t0 = time.time()
        try:
            result = fn(*args, **kwargs)
            truncated_from = truncated_to = None
            # The cap applies to every result shape: structured results
            # degrade to their string form, so nothing bypasses the ceiling.
            original_chars = (
                len(result) if isinstance(result, str) else _result_chars(result)
            )
            if original_chars is not None and original_chars > MAX_RESULT_CHARS:
                result = _truncate_result(
                    name, result if isinstance(result, str) else str(result)
                )
                truncated_from = original_chars
                truncated_to = len(result)
            # resp_chars is measured post-truncation: the capped payload is
            # what the client's context actually receives.
            _log_metric(
                name,
                (time.time() - t0) * 1000,
                "ok",
                req_chars=req_chars,
                resp_chars=_result_chars(result),
                args_summary=args_summary,
                truncated_from_chars=truncated_from,
                truncated_to_chars=truncated_to,
            )
            return result
        except Exception as exc:
            duration_ms = (time.time() - t0) * 1000

            # Log full traceback server-side.
            tb_str = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )
            logger.error(f"Error in {name}: {exc}\n{tb_str}")

            _log_metric(
                name,
                duration_ms,
                "error",
                str(exc),
                req_chars=req_chars,
                args_summary=args_summary,
            )

            # Re-raise so FastMCP's Tool.run converts the exception into a
            # proper MCP error response (isError: true) rather than a prose
            # string that looks like a successful result.
            raise

    return wrapper
