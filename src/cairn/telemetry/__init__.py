"""Public API for cairn telemetry (spec §6.1).

Telemetry is analytics, not correctness. Every call here is best-effort: a
sink failure never raises into a caller, never holds a user lock, and never
blocks a tool call (spec §5.4/§5.6). The shared sink (:mod:`.sink`) owns one
daemon flush thread per process; :mod:`.events` owns the emission API and the
event-name catalog (spec §6.4).

Typical use::

    from cairn.telemetry import emit, ANN_FALLBACK
    emit(ANN_FALLBACK, reason="load_failed")

Gates:
  * ``CAIRN_TELEMETRY=off`` -> :func:`emit` / :func:`warn_once` become no-ops
    (near-zero overhead; spec §6.1).
  * ``CAIRN_READ_ONLY`` truthy -> :func:`emit` skips the write (a mode=ro
    daemon would fail every flush; mirrors ``metric_buffering._log_metric``).

:func:`configure_conn` injects the writable-connection factory. At server boot
``metric_buffering.configure_conn`` mirrors into here, so the single existing
boot call wires both ``tool_metrics`` and ``events``.
"""

from __future__ import annotations

from .events import (
    ANN_FALLBACK,
    EMPTY_RESULT,
    EMBED_FLUSH_STALLED,
    EMBED_SERVER_DEGRADED,
    HASH_FALLBACK,
    LOCK_CONTENTION,
    RERANK_SKIPPED,
    SEMANTIC_BACKEND,
    SEMANTIC_UNAVAILABLE,
    STRAY_SWEPT,
    TASK_LIFECYCLE,
    TRUNCATE_RESULT,
    emit,
    note_semantic_unavailable,
    warn_once,
)
from .sink import configure_conn, flush, start_flusher

__all__ = [
    # Emission API
    "emit",
    "warn_once",
    "note_semantic_unavailable",
    # Sink wiring / flush hooks
    "configure_conn",
    "flush",
    "start_flusher",
    # Event-name catalog (spec §6.4)
    "ANN_FALLBACK",
    "HASH_FALLBACK",
    "LOCK_CONTENTION",
    "TRUNCATE_RESULT",
    "EMPTY_RESULT",
    "SEMANTIC_BACKEND",
    "TASK_LIFECYCLE",
    "STRAY_SWEPT",
    "SEMANTIC_UNAVAILABLE",
    "EMBED_FLUSH_STALLED",
    "EMBED_SERVER_DEGRADED",
    "RERANK_SKIPPED",
]
