"""Telemetry event emission helpers (spec §6.1, §6.3, §6.4).

:func:`emit` is the public entry point: it serializes low-cardinality
attributes, gates on ``CAIRN_TELEMETRY`` / ``CAIRN_READ_ONLY``, and hands a
pre-shaped row to the shared sink (:mod:`cairn.telemetry.sink`) for buffered
flush. :func:`warn_once` generalizes the process-global one-time-warning
pattern (``graph.embeddings.warn_hash_fallback_once``,
``graph.ann_index.warn_ann_fallback_once``) so each degradation class surfaces
at most once per process. :func:`note_contention` is the P1 event+log upgrade
of ``graph.schema.note_contention`` (the P0 log-only helper); T11 will swap the
13 call sites over -- this helper just needs to exist and be ready.

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
        sink.enqueue(time.time(), name, _session_id(), _coerce_attrs(attrs))
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
# note_contention -- P1 event+log upgrade of schema.note_contention (spec §6.3)
#
# The P0 helper (graph/schema.py::note_contention) only logs. This upgraded
# version ALSO emits a durable ``lock_contention`` event so ``cairn doctor``
# and ``cairn metrics --contention`` can surface contention trends, not just a
# one-time log line. T11 swaps the 13 call sites from the P0 helper to this
# one; until then it just needs to exist and be ready (it is NOT wired at any
# site in this task -- schema.py and its callers are owned by T11).
# ---------------------------------------------------------------------------
def note_contention(site: str) -> None:
    """Emit a ``lock_contention`` event AND warn once per (process, site).

    Combines a durable telemetry signal (for doctor / metrics aggregation) with
    the P0 behavior of one WARNING per site. ``site`` is a stable, low-
    cardinality ``module.function`` tag (no line numbers -- they drift). The
    emit is gated by ``CAIRN_READ_ONLY`` internally; the whole call is a no-op
    under ``CAIRN_TELEMETRY=off``.
    """
    if sink.is_telemetry_off():
        return
    emit(LOCK_CONTENTION, site=site)
    warn_once(
        f"lock_contention:{site}",
        logger,
        "SQLite lock contention absorbed at %s -- another cairn process holds "
        "the DB; busy_timeout retried/absorbed this so the operation completed "
        "(or degraded gracefully). Repeated contention is diagnosable via "
        "`cairn serve start` (single-daemon SSE mode serializes access and "
        "avoids cross-process lock waits)." % site,
    )
