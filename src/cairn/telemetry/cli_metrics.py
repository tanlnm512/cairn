"""CLI invocation metrics: the ``tool_metrics`` row builder + buffered flusher.

CLI commands get the same observability as MCP tools (spec
``cli-usage-recording`` FR-001/FR-003): every ``cairn`` invocation becomes one
``tool_metrics`` row with ``tool_name = "cli:" + command_path``, timing,
status, and a redacted argv summary. This module is the CLI-side sibling of
:mod:`cairn.mcp_server.metric_buffering` -- it owns a buffer and its flush
logic but no thread of its own: it registers ``_flush_cli_metrics`` with the
shared telemetry sink (:mod:`cairn.telemetry.sink`, spec §6.1) so events,
tool_metrics, and CLI rows share one 30s flush cadence + one atexit drain
(FR-003's flush-on-clean-exit is the sink's atexit handler).

Doctrine (mirrors ``metric_buffering`` / the shared sink):
  * Telemetry is analytics, not correctness: recording never raises into the
    caller, never holds a user lock, never blocks the command (FR-003).
  * Buffer then flush; the buffer is snapshotted WITHOUT clearing, so a
    transient failure ("database is locked") leaves rows queued for the next
    attempt -- no silent drops. ``deque(maxlen=2000)`` caps growth during a
    long outage.
  * Redact at the write chokepoint: argv routinely embeds paths, tokens, and
    user text -- ``strip_private_data`` runs BEFORE a row is ever buffered,
    then the summary is truncated to ``MAX_CLI_ARGS_SUMMARY_CHARS``.
    ``error_message`` gets the same chokepoint plus a 500-char cap.
  * Gates mirror ``metric_buffering._log_metric``: skip entirely when
    ``CAIRN_TELEMETRY=off`` (master kill switch) or the process is read-only
    (a mode=ro store would fail every flush and buffer indefinitely).

Source stamping (phase 2, FR-002/D-002): every CLI row states ``source =
'cli'`` explicitly -- the only two places in this module that know the
column list are ``_INSERT_SQL`` and :func:`build_row`. MCP rows ride the
table's ``DEFAULT 'mcp'`` (their INSERT names no source column), so no other
writer of ``tool_metrics`` changes (FR-005).

Deliberately CLI-agnostic: no click imports, no MCP-server imports. The
writable connection factory is injected via :func:`configure_conn` (CLI boot
wires it against the resolved store); without a factory rows stay buffered --
never an error, never a drop.
"""

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

# Pending rows, each a tuple shaped to the tool_metrics columns named in
# _INSERT_SQL. maxlen caps unbounded growth during a flush outage (mirrors
# metric_buffering's deque(maxlen=2000)).
_CLI_BUFFER: collections.deque = collections.deque(maxlen=2000)
_CLI_LOCK = threading.Lock()
_CLI_FLUSHER_STARTED = False

# Cap on the redacted argv summary stored per row: the summary identifies the
# invocation shape, it is not a payload replay -- same value/rationale as
# metric_buffering.MAX_ARGS_SUMMARY_CHARS, re-declared locally so neither
# module imports the other just for a constant.
MAX_CLI_ARGS_SUMMARY_CHARS = 200

# Connection factory injected once at CLI boot; None until then, in which
# case rows stay buffered. Injectable (mirrors metric_buffering) so this
# module stays free of the schema/store dependency.
_conn_factory: Optional[Callable[[], "object"]] = None

# Explicit column list keeps this INSERT stable against future additive
# migrations to the table. `source` is stated explicitly ('cli') per FR-002;
# MCP rows ride the table-side DEFAULT 'mcp'.
_INSERT_SQL = (
    "INSERT INTO tool_metrics "
    "(tool_name, session_id, invoked_at, duration_ms, status, error_message, "
    "req_chars, resp_chars, args_summary, source) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def derive_session_id() -> str:
    """Session identity for a CLI record; never ``"unknown"`` (FR-006).

    Terminal-provided ids win so one shell's commands group together:
    ``TERM_SESSION_ID`` -> ``term:<value>``, ``TMUX_PANE`` -> ``tmux:<value>``.
    Otherwise each invocation is its own session: ``cli:<uuid4 hex[:12]>`` --
    a fresh value per call, never the table's ``unknown`` default (a CLI row
    stamped ``unknown`` would disappear into the legacy mega-session that
    ui-dashboard-traffic-scale exists to bound).
    """
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
    """Build one ``tool_metrics`` row tuple for a CLI invocation (FR-001).

    Contract (positional order = ``_INSERT_SQL``'s column order):
      ``(tool_name="cli:" + command_path, session_id=derive_session_id(),
         invoked_at=time.time(), duration_ms, status ("ok"|"error"),
         error_message (strip_private_data'd, [:500], else None),
         req_chars=len(raw argv JSON) or None, resp_chars=None,
         args_summary (strip_private_data'd, truncated to
         MAX_CLI_ARGS_SUMMARY_CHARS, else None), source='cli')``.

    Redaction happens HERE -- the write chokepoint -- so no unredacted bytes
    are ever buffered or persisted. Never raises on pathological argv: the
    JSON dump degrades to NULL columns, mirroring
    ``metric_buffering._kwargs_payload`` (``default=str`` covers
    non-serializable values; anything still pathological is a missing size,
    not an error).
    """
    req_chars: Optional[int] = None
    raw_summary: Optional[str] = None
    try:
        raw_summary = json.dumps(argv, default=str, separators=(",", ":"))
        # Measured on the raw (pre-redaction, pre-truncation) JSON, mirroring
        # how _kwargs_payload sizes the request before _log_metric scrubs it.
        req_chars = len(raw_summary)
    except Exception:
        raw_summary = None

    # Redact at the chokepoint, BEFORE the row is buffered (audit F4 rule):
    # argv echoes user paths/flags/tokens, exceptions echo request payloads.
    # Lazy import mirrors metric_buffering (avoids any boot-order cycle with
    # the memory package; negligible cost -- the module is cached after the
    # first CLI record).
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
        None,  # resp_chars: a CLI invocation has no response payload
        raw_summary[:MAX_CLI_ARGS_SUMMARY_CHARS] if raw_summary else None,
        "cli",  # source (FR-002): explicit here; MCP rows ride DEFAULT 'mcp'
    )


def record_cli_invocation(
    command_path: str,
    argv: Optional[Sequence[str]],
    duration_ms: float,
    status: str,
    error_message: str = "",
) -> None:
    """Record one CLI invocation: gate, build, buffer, ensure the flusher.

    Gates mirror ``metric_buffering._log_metric``: ``CAIRN_TELEMETRY=off``
    skips everything (master kill switch), and a read-only process skips the
    write entirely rather than buffer rows no flush could ever land. Never
    raises -- a metrics bug must not fail a CLI command that succeeded.
    """
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
    """Register ``_flush_cli_metrics`` with the shared sink and start it.

    Reuses the single shared flush thread + atexit drain (spec §6.1) instead
    of spawning a CLI-specific thread. ``_CLI_FLUSHER_STARTED`` is this
    module's idempotency flag (double-checked under ``_CLI_LOCK``) and the
    piece ``_reset_for_tests`` clears so suites can re-drive registration;
    the sink's ``register_flusher`` is itself idempotent by identity, so a
    reset + re-record cannot double-register or double-fire.
    """
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
    """Drain the CLI buffer into ``tool_metrics`` (best-effort, never raises).

    Snapshot WITHOUT clearing -- a failed flush (locked DB, missing factory,
    missing table) leaves rows queued for the next attempt; the deque maxlen
    caps growth meanwhile. Rows are popped only after a successful commit,
    and only the rows actually written -- newer rows appended during the
    flush stay buffered.

    Untyped (no annotations) deliberately, mirroring
    ``metric_buffering._flush_metrics`` and ``sink._flush_events``: the
    injected connection is an opaque duck-typed handle (this module stays
    free of the sqlite3 dependency), so its attribute access is left for
    runtime and mypy does not check this body.
    """
    with _CLI_LOCK:
        if not _CLI_BUFFER:
            return
        batch = list(_CLI_BUFFER)
    if _conn_factory is None:
        return
    conn = None
    try:
        conn = _conn_factory()
        conn.executemany(_INSERT_SQL, batch)
        conn.commit()
    except Exception:
        # Couldn't flush this batch -- leave it buffered for the next
        # attempt. Telemetry is best-effort, but log at debug so silent
        # backlog stays observable.
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
    # Commit succeeded -> safe to drop these rows. Only remove the rows we
    # actually wrote; newer rows appended during the flush stay.
    with _CLI_LOCK:
        for _ in range(len(batch)):
            try:
                _CLI_BUFFER.popleft()
            except IndexError:
                break


def configure_conn(conn_factory: Callable[[], "object"]) -> None:
    """Inject the writable-connection factory used by ``_flush_cli_metrics``.

    Called once at CLI boot against the resolved store. Mirrors into the
    shared sink so the single boot call wires both ``tool_metrics`` CLI rows
    and ``events`` for the CLI process -- the same one-call-wires-both
    pattern as ``metric_buffering.configure_conn`` on the MCP side. Must
    come from outside so this module stays free of the schema/store
    dependency.
    """
    global _conn_factory
    _conn_factory = conn_factory
    _sink_configure_conn(conn_factory)


def _reset_for_tests() -> None:
    """Reset module-global state between tests; never call in production.

    Mirrors how the metric suites reset ``metric_buffering`` (see
    ``tests/test_metrics.py::_reset_metric_state``): clears the buffer, drops
    the injected factory, and clears the started flag so a suite can re-drive
    flusher registration. The sink's own ``_FLUSHER_STARTED`` and flusher
    list are deliberately NOT touched -- resetting those would double-start
    the shared thread, and re-registration after this reset is a no-op
    because ``register_flusher`` is idempotent by identity.
    """
    global _conn_factory, _CLI_FLUSHER_STARTED
    with _CLI_LOCK:
        _CLI_BUFFER.clear()
    _conn_factory = None
    _CLI_FLUSHER_STARTED = False
