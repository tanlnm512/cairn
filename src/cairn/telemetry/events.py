"""Telemetry event emission helpers (spec §6.1, §6.3, §6.4).

:func:`emit` is the public entry point: it serializes low-cardinality
attributes, gates on ``CAIRN_TELEMETRY`` / ``CAIRN_READ_ONLY``, and hands a
pre-shaped row to the shared sink (:mod:`cairn.telemetry.sink`) for buffered
flush. :func:`warn_once` generalizes the process-global one-time-warning
pattern (``graph.embeddings.warn_hash_fallback_once``,
``graph.ann_index.warn_ann_fallback_once``) so each degradation class surfaces
at most once per process. (``note_contention`` is owned by
``graph.schema`` -- see the pointer at the bottom of this module.)

Cardinality discipline (spec §6.4): attrs are enums, short fixed tags, or
bucketed values (``"0-10ms"``, ``"ann"``/``"brute"``/``"hash"``, ...). No
paths, no free text from user input. :func:`emit` enforces JSON-serializability
and truncates oversized string values defensively, and NEVER raises into a
caller -- telemetry is best-effort (spec §5.6).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Optional

from . import otel
from . import sink

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Event-name catalog (spec §6.4).
#
# Module-level constants so emitter sites (T09-T11) and consumers
# (``cairn doctor`` / ``cairn metrics --contention``) share one spelling -- a
# typo in a string literal would silently drop a signal that exists only to be
# observed. The catalog is the contract between producers and the doctor.
# ---------------------------------------------------------------------------
ANN_FALLBACK = "ann_fallback"
HASH_FALLBACK = "hash_fallback"
LOCK_CONTENTION = "lock_contention"
TRUNCATE_RESULT = "truncate_result"
EMPTY_RESULT = "empty_result"
SEMANTIC_BACKEND = "semantic_backend"
TASK_LIFECYCLE = "task_lifecycle"
STRAY_SWEPT = "stray_swept"
SEMANTIC_UNAVAILABLE = "semantic_unavailable"
EMBED_FLUSH_STALLED = "embed_flush_stalled"

# Defensive cap on any single serialized attr value so a runaway caller can't
# bloat the events row / the WAL with a huge string. Attrs are supposed to be
# short tags/enums; this is a guardrail, not a feature. Matches the 500-char
# cap metric_buffering applies to ``tool_metrics.error_message``.
_MAX_ATTR_CHARS = 500


def _session_id() -> str:
    """The correlation id stamped on every event (mirrors tool_metrics).

    ``CAIRN_SESSION`` defaults to 'unknown'; the server/CLI set it per run so
    events group into a 'session as trace' (spec §3, P1 tracing model).
    """
    return os.environ.get("CAIRN_SESSION", "unknown")


def _coerce_attrs(attrs: dict[str, Any]) -> Optional[str]:
    """JSON-serialize attrs, truncating oversized string values defensively.

    Returns ``None`` for an empty dict (NULL ``attrs`` column). Non-serializable
    values are stringified via ``default=str`` so :func:`emit` never raises --
    a caller passing an odd object is a bug, but telemetry must not propagate
    it (spec §5.6). A hard serialization failure (e.g. a cycle even ``str``
    can't handle) drops the attrs rather than the event.
    """
    if not attrs:
        return None
    coerced: dict[str, Any] = {}
    for k, v in attrs.items():
        if isinstance(v, str) and len(v) > _MAX_ATTR_CHARS:
            v = v[:_MAX_ATTR_CHARS]
        coerced[k] = v
    try:
        return json.dumps(coerced, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        # Last resort: keep the event with NULL attrs rather than raise or drop
        # the whole signal. The event name + ts are still useful on their own.
        return None


def emit(name: str, **attrs: Any) -> None:
    """Append a telemetry event to the shared sink buffer (best-effort).

    No-op under ``CAIRN_TELEMETRY=off`` (the master kill switch, spec §5.1) or
    ``CAIRN_READ_ONLY`` (a mode=ro daemon would fail every flush and buffer
    indefinitely -- same rationale as ``metric_buffering._log_metric``). Never
    raises: serialization/gating errors are swallowed at debug. The row is
    ``(ts, name, session_id, attrs_json)`` matching the ``events`` table.
    """
    # Master switch + read-only gate: cheap env reads on every call (near-zero
    # overhead when off), so toggling takes effect without a module reload.
    if sink.is_telemetry_off() or sink.is_read_only():
        return
    try:
        ts = time.time()
        attrs_json = _coerce_attrs(attrs)
        session_id = _session_id()
        sink.enqueue(ts, name, session_id, attrs_json)
        # Optional OTLP tap (T19): no-op (one env read) unless
        # CAIRN_OTEL_ENDPOINT is set. Appends to otel's own side buffer --
        # the SQLite row queued above stays authoritative and is never
        # stolen by the export path.
        otel.record(ts, name, session_id, attrs_json)
    except Exception:
        # enqueue does buffered, non-DB work; a failure here is a logic bug,
        # not a DB outage. Still must not raise into a caller.
        logger.debug("emit(%s) failed", name, exc_info=True)


# ---------------------------------------------------------------------------
# warn_once -- process-global one-time-warning (spec §6.3)
#
# Generalizes graph/embeddings.warn_hash_fallback_once and
# graph/ann_index.warn_ann_fallback_once. Each degradation class warns at most
# once per process so a repeated fallback (e.g. brute-force scan on every
# semantic query) doesn't spam the log. Distinct keys warn independently.
# ---------------------------------------------------------------------------
_WARNED: set[str] = set()
_WARN_LOCK = threading.Lock()


def warn_once(key: str, warn_logger: logging.Logger, msg: str) -> None:
    """Emit ``msg`` via ``warn_logger.warning`` at most once per (process, key).

    No-op under ``CAIRN_TELEMETRY=off`` (the whole telemetry module is a no-op
    then, spec §6.1). Thread-safe: the guard set is mutated only under
    ``_WARN_LOCK``; the log call happens after release so logging can't
    serialize concurrent callers (mirrors the contention-helper pattern in
    ``graph.schema.note_contention``).
    """
    if sink.is_telemetry_off():
        return
    with _WARN_LOCK:
        if key in _WARNED:
            return
        _WARNED.add(key)
    warn_logger.warning(msg)


# ---------------------------------------------------------------------------
# note_contention lives in graph/schema.py -- it owns the per-site once-guard,
# the unconditional operational WARNING, and emits the lock_contention event
# via emit() above (best-effort, CAIRN_TELEMETRY-gated). It is the single
# canonical helper (the two-helper split that T07 spec'd was collapsed: only
# schema's is wired at the 13 swallow sites, with the behavior we want -- the
# warning stays operational even under CAIRN_TELEMETRY=off).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# semantic_unavailable -- durable signal for a semantic-off degrade (F4)
#
# explore() and search_knowledge() both degrade to lexical-only results when
# the semantic backend can't contribute (not installed / no embeddings built /
# an unexpected error). Those degrades were completely silent, so this helper
# emits ONE ``semantic_unavailable`` event per (process, surface) plus the
# matching once-guarded WARNING. ``surface`` is a bounded enum (explore |
# knowledge), ``reason`` a bounded enum (unavailable | no_embeddings | error) --
# both declared in the cardinality guard (tests/test_cardinality_guard.py).
# ---------------------------------------------------------------------------

_SEMANTIC_SURFACES = frozenset({"explore", "knowledge"})
_SEMANTIC_REASONS = frozenset({"unavailable", "no_embeddings", "error"})


def note_semantic_unavailable(surface: str, reason: str) -> None:
    """Record + warn that a query surface degraded to lexical-only results.

    Fires at most once per (process, surface): the first degrade wins and its
    reason is the one recorded, mirroring ``warn_ann_fallback_once``'s single
    process-global guard. No-op under ``CAIRN_TELEMETRY=off`` (both the event
    and the WARNING -- unlike lock contention, this is a quality signal, not an
    operational outage). Never raises.
    """
    try:
        if sink.is_telemetry_off():
            return
        surface = surface if surface in _SEMANTIC_SURFACES else "explore"
        reason = reason if reason in _SEMANTIC_REASONS else "error"
        key = f"semantic_unavailable:{surface}"
        with _WARN_LOCK:
            if key in _WARNED:
                return
            _WARNED.add(key)
        emit(
            SEMANTIC_UNAVAILABLE,
            surface=surface,
            reason=reason,
        )
        logger.warning(
            "semantic search unavailable on the '%s' surface (%s) -- results "
            "degrade to lexical-only. Run `cairn embed` to build embeddings.",
            surface,
            reason,
        )
    except Exception:
        logger.debug("note_semantic_unavailable(%s) failed", surface, exc_info=True)
