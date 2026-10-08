"""Optional OTLP export of cairn telemetry."""

from __future__ import annotations

import collections
import json
import logging
import os
import threading
from typing import Any, Dict, Optional

from . import sink

logger = logging.getLogger(__name__)

_ENDPOINT_ENV = "CAIRN_OTEL_ENDPOINT"

_PENDING: collections.deque = collections.deque(maxlen=2000)
_LOCK = threading.Lock()

_FLUSH_LOCK = threading.Lock()

_REGISTERED = False
_DISABLED = False

_EXPORT_TIMEOUT_S = 5.0

_otlp_logger: Any = None
_otlp_tracker: Any = None
_log_record_cls: Any = None


def endpoint() -> str:
    """The configured OTLP/http endpoint, '' when unset (the default)."""
    return os.environ.get(_ENDPOINT_ENV, "").strip()


def is_enabled() -> bool:
    """True when the endpoint is set and the exporter is not disabled."""
    return bool(endpoint()) and not _DISABLED


def record(
    ts: float, name: str, session_id: str, attrs_json: Optional[str]
) -> None:
    """Buffer one gated telemetry row for lazy OTLP export."""
    if _DISABLED or not endpoint():
        return
    with _LOCK:
        _PENDING.append((ts, name, session_id, attrs_json))
    _register()


def _register() -> None:
    """Register the OTLP flusher with the shared sink once (idempotent)."""
    global _REGISTERED
    if _REGISTERED:
        return
    with _LOCK:
        if _REGISTERED:
            return
        _REGISTERED = True
    sink.register_flusher(_flush_otlp)


def _attributes(attrs_json: Optional[str], session_id: str) -> Dict[str, Any]:
    """Rebuild OTel attrs, degrading malformed JSON to session id only."""
    attrs: Dict[str, Any] = {"session_id": session_id}
    if attrs_json:
        try:
            parsed = json.loads(attrs_json)
        except (TypeError, ValueError):
            logger.debug("otlp: unparsable attrs json dropped", exc_info=True)
            return attrs
        if isinstance(parsed, dict):
            attrs.update(parsed)
    return attrs


def _warn_once_and_disable(msg: str) -> None:
    """Warn once, disable the exporter, and drop unexportable rows."""
    global _DISABLED
    from .events import warn_once

    warn_once("otlp_export_disabled", logger, msg)
    _DISABLED = True
    with _LOCK:
        _PENDING.clear()


class _TrackingExporter:
    """Exporter wrapper that tracks whether any export invocation failed."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.ok = True

    def export(self, batch: Any) -> Any:
        result = self.inner.export(batch)
        if getattr(result, "name", "SUCCESS") != "SUCCESS":
            self.ok = False
        return result

    def shutdown(self) -> None:
        try:
            self.inner.shutdown()
        except Exception:
            pass

    def force_flush(self, timeout_millis: float = 0) -> bool:
        return True


def _get_logger() -> Any:
    """Lazily build a synchronous OTLP logger; None when setup fails."""
    global _otlp_logger, _otlp_tracker, _log_record_cls
    if _DISABLED:
        return None
    if _otlp_logger is not None:
        return _otlp_logger
    try:
        from opentelemetry.exporter.otlp.proto.http.log_exporter import (  # type: ignore[import-not-found]
            OTLPLogExporter,
        )
        from opentelemetry.sdk._logs import (  # type: ignore[import-not-found, attr-defined]
            LoggerProvider,
            LogRecord,
        )
        from opentelemetry.sdk._logs.export import (  # type: ignore[import-not-found]
            SimpleLogRecordProcessor,
        )
        from opentelemetry.sdk.resources import (  # type: ignore[import-not-found]
            Resource,
        )
    except ImportError:
        _warn_once_and_disable(
            "CAIRN_OTEL_ENDPOINT is set but the OpenTelemetry SDK is not "
            "installed; telemetry stays local-only. Install the optional "
            "extra (pip install 'cairn-intel[otlp]') or unset "
            "CAIRN_OTEL_ENDPOINT."
        )
        return None
    try:
        provider = LoggerProvider(
            resource=Resource.create({"service.name": "cairn"}),
            shutdown_on_exit=False,
        )
        tracker = _TrackingExporter(
            OTLPLogExporter(endpoint=endpoint(), timeout=_EXPORT_TIMEOUT_S)
        )
        provider.add_log_record_processor(SimpleLogRecordProcessor(tracker))  # type: ignore[arg-type]
        _otlp_tracker = tracker
        _otlp_logger = provider.get_logger("cairn.telemetry")
        _log_record_cls = LogRecord
    except Exception:
        logger.debug("otlp: exporter construction failed", exc_info=True)
        _warn_once_and_disable(
            "CAIRN_OTEL_ENDPOINT is set but the OTLP exporter could not be "
            "constructed (see debug logs); telemetry stays local-only."
        )
        return None
    return _otlp_logger


def _flush_otlp() -> None:
    """Export acknowledged pending rows without raising or losing failures."""
    with _FLUSH_LOCK:
        if sink.is_telemetry_off() or sink.is_read_only():
            with _LOCK:
                _PENDING.clear()
            return
        if _DISABLED:
            return
        with _LOCK:
            if not _PENDING:
                return
            batch = list(_PENDING)
        try:
            otlp_logger = _get_logger()
        except Exception:
            logger.debug("otlp: exporter setup raised", exc_info=True)
            return
        if otlp_logger is None:
            return
        tracker = _otlp_tracker
        if tracker is None:
            return
        tracker.ok = True
        try:
            for ts, name, session_id, attrs_json in batch:
                otlp_logger.emit(
                    _log_record_cls(
                        timestamp=int(ts * 1_000_000_000),
                        severity_text="INFO",
                        body=name,
                        attributes=_attributes(attrs_json, session_id),
                    )
                )
                if not tracker.ok:
                    break
        except Exception:
            logger.debug(
                "otlp export failed; %d events retained", len(batch), exc_info=True
            )
            return
        if not tracker.ok:
            logger.debug(
                "otlp export rejected %d-event batch (collector unhealthy); "
                "retained for retry", len(batch),
            )
            return
        with _LOCK:
            for _ in range(len(batch)):
                try:
                    _PENDING.popleft()
                except IndexError:
                    break


def flush() -> None:
    """Synchronously drain pending OTLP rows on a best-effort basis."""
    _flush_otlp()
