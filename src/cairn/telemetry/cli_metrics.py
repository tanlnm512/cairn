"""CLI invocation metrics: the ``tool_metrics`` row builder + buffered flusher."""

from __future__ import annotations

import collections
import json
import logging
import os
import threading
import time
import uuid
from typing import Callable, Optional, Sequence

from cairn.telemetry.sink import (
    configure_conn as _sink_configure_conn,
    is_read_only,
    is_telemetry_off,
    register_flusher,
    start_flusher,
)

logger = logging.getLogger(__name__)

_CLI_BUFFER: collections.deque = collections.deque(maxlen=2000)
_CLI_LOCK = threading.Lock()
_CLI_FLUSHER_STARTED = False

_FLUSH_LOCK = threading.Lock()

MAX_CLI_ARGS_SUMMARY_CHARS = 200

_conn_factory: Optional[Callable[[], "object"]] = None

_INSERT_SQL = (
    "INSERT INTO tool_metrics "
    "(tool_name, session_id, invoked_at, duration_ms, status, error_message, "
    "req_chars, resp_chars, args_summary, source) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def derive_session_id() -> str:
    """Return a terminal session id or fresh CLI id, never ``"unknown"``."""
    term = os.environ.get("TERM_SESSION_ID")
    if term:
        return f"term:{term}"
    pane = os.environ.get("TMUX_PANE")
    if pane:
        return f"tmux:{pane}"
    return f"cli:{uuid.uuid4().hex[:12]}"


def build_row(
    command_path: str,
    argv: Optional[Sequence[str]],
    duration_ms: float,
    status: str,
    error_message: str = "",
) -> tuple:
    """Return a privacy-scrubbed, bounded ``tool_metrics`` row for a CLI call."""
    req_chars: Optional[int] = None
    raw_summary: Optional[str] = None
    try:
        raw_summary = json.dumps(argv, default=str, separators=(",", ":"))
        req_chars = len(raw_summary)
    except Exception:
        raw_summary = None

    from cairn.memory.privacy import strip_private_data

    if error_message:
        error_message = strip_private_data(error_message)
    if raw_summary:
        raw_summary = strip_private_data(raw_summary)
    return (
        f"cli:{command_path}",
        derive_session_id(),
        time.time(),
        duration_ms,
        status,
        error_message[:500] if error_message else None,
        req_chars,
        None,
        raw_summary[:MAX_CLI_ARGS_SUMMARY_CHARS] if raw_summary else None,
        "cli",
    )


def record_cli_invocation(
    command_path: str,
    argv: Optional[Sequence[str]],
    duration_ms: float,
    status: str,
    error_message: str = "",
) -> None:
    """Buffer one gated CLI invocation and ensure its shared flusher."""
    try:
        if is_telemetry_off() or is_read_only():
            return
        row = build_row(command_path, argv, duration_ms, status, error_message)
        with _CLI_LOCK:
            _CLI_BUFFER.append(row)
        _start_cli_flusher()
    except Exception:
        logger.debug("record_cli_invocation failed", exc_info=True)


def _start_cli_flusher() -> None:
    """Register the CLI flusher with the shared sink exactly once."""
    global _CLI_FLUSHER_STARTED
    if _CLI_FLUSHER_STARTED:
        return
    with _CLI_LOCK:
        if _CLI_FLUSHER_STARTED:
            return
        _CLI_FLUSHER_STARTED = True
    register_flusher(_flush_cli_metrics)
    start_flusher()


def _flush_cli_metrics():
    """Drain committed CLI metric rows to ``tool_metrics`` without raising."""
    if _conn_factory is None:
        return
    with _FLUSH_LOCK:
        with _CLI_LOCK:
            if not _CLI_BUFFER:
                return
            batch = list(_CLI_BUFFER)
        conn = None
        try:
            conn = _conn_factory()
            conn.executemany(_INSERT_SQL, batch)
            conn.commit()
        except Exception:
            logger.debug(
                "cli metric flush failed; %d rows remain buffered",
                len(batch),
                exc_info=True,
            )
            return
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
        with _CLI_LOCK:
            for _ in range(len(batch)):
                try:
                    _CLI_BUFFER.popleft()
                except IndexError:
                    break


def configure_conn(conn_factory: Callable[[], "object"]) -> None:
    """Inject the writable CLI and shared-sink connection factories."""
    global _conn_factory
    _conn_factory = conn_factory
    _sink_configure_conn(conn_factory)


def _reset_for_tests() -> None:
    """Reset CLI-owned flush state while leaving shared sink state intact."""
    global _conn_factory, _CLI_FLUSHER_STARTED
    with _CLI_LOCK:
        _CLI_BUFFER.clear()
    _conn_factory = None
    _CLI_FLUSHER_STARTED = False
