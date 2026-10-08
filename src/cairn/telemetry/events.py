"""Telemetry event emission helpers."""

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

# Event-name catalog shared by producers and consumers.
ANN_FALLBACK = "ann_fallback"
HASH_FALLBACK = "hash_fallback"
EMBED_SERVER_DEGRADED = "embed_server_degraded"
LOCK_CONTENTION = "lock_contention"
TRUNCATE_RESULT = "truncate_result"
EMPTY_RESULT = "empty_result"
SEMANTIC_BACKEND = "semantic_backend"
TASK_LIFECYCLE = "task_lifecycle"
STRAY_SWEPT = "stray_swept"
SEMANTIC_UNAVAILABLE = "semantic_unavailable"
EMBED_FLUSH_STALLED = "embed_flush_stalled"
RERANK_SKIPPED = "rerank_skipped"

EMBED_SERVER_REASONS = frozenset(
    {
        "server_down",
        "model_missing",
        "parity_fail",
        "fallback_session_alias",
        "fallback_local",
        "hybrid_only",
    }
)

_MAX_ATTR_CHARS = 500

_MAX_SERIALIZED_ATTRS = 4000


def _session_id() -> str:
    """Return ``CAIRN_SESSION`` for event correlation, defaulting to unknown."""
    return os.environ.get("CAIRN_SESSION", "unknown")


def _coerce_value(v: Any) -> Any:
    """Return one scrubbed, structurally capped attr value at any depth."""
    if isinstance(v, str):
        from ..memory.privacy import strip_private_data

        return strip_private_data(v)[:_MAX_ATTR_CHARS]
    if isinstance(v, dict):
        return {str(k)[:64]: _coerce_value(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_coerce_value(x) for x in v[:32]]
    return v


def _coerce_attrs(attrs: dict[str, Any]) -> Optional[str]:
    """Return scrubbed compact attrs JSON, or None when attrs are unusable."""
    if not attrs:
        return None
    from ..memory.privacy import strip_private_data

    coerced: dict[str, Any] = {}
    for k, v in attrs.items():
        if isinstance(v, str):
            v = strip_private_data(v)[:_MAX_ATTR_CHARS]
        elif not isinstance(v, (int, float, bool, type(None))):
            if isinstance(v, (dict, list, tuple)):
                v = _coerce_value(v)
            else:
                v = strip_private_data(str(v))[:_MAX_ATTR_CHARS]
        coerced[str(k)[:64]] = v
    try:
        out = json.dumps(coerced, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return None
    if len(out) > _MAX_SERIALIZED_ATTRS:
        return None
    return out


def emit(name: str, **attrs: Any) -> None:
    """Buffer one gated telemetry event and tap OTLP without raising."""
    if sink.is_telemetry_off() or sink.is_read_only():
        return
    try:
        ts = time.time()
        attrs_json = _coerce_attrs(attrs)
        session_id = _session_id()
        sink.enqueue(ts, name, session_id, attrs_json)
        otel.record(ts, name, session_id, attrs_json)
    except Exception:
        logger.debug("emit(%s) failed", name, exc_info=True)


_WARNED: set[str] = set()
_WARN_LOCK = threading.Lock()


def warn_once(key: str, warn_logger: logging.Logger, msg: str) -> None:
    """Log ``msg`` once per process and key unless telemetry is off."""
    if sink.is_telemetry_off():
        return
    with _WARN_LOCK:
        if key in _WARNED:
            return
        _WARNED.add(key)
    warn_logger.warning(msg)


_SEMANTIC_SURFACES = frozenset({"explore", "knowledge"})
_SEMANTIC_REASONS = frozenset({"unavailable", "no_embeddings", "error"})


def note_semantic_unavailable(surface: str, reason: str) -> None:
    """Record and warn once when a query surface degrades to lexical-only."""
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
