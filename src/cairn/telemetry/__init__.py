"""Public API for cairn telemetry."""

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
