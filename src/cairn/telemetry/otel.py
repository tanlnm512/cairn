"""Optional OTLP export of cairn telemetry (task T19, spec §3 + §7).

Off by default, forever optional. ``CAIRN_OTEL_ENDPOINT`` unset (the default)
means zero behavior change: no export, no OpenTelemetry import, no overhead
beyond the single env read ``events.emit`` already spends on its gates. When
set, the buffered telemetry events are forwarded to that OTLP/http endpoint as
OpenTelemetry LogRecords -- conservative mapping, documented once here:

  * one LogRecord per ``events`` row
  * ``body``   = the event name (e.g. ``ann_fallback``)
  * ``attributes`` = the event's low-cardinality attrs (re-parsed from the
    stored JSON) plus ``session_id`` for correlation
  * ``Resource`` = ``service.name="cairn"`` only -- no paths, no PII
    (spec §5.2/§7 cardinality + privacy invariants apply unchanged)

Design (why a side buffer, not a tap on ``sink._BUFFER``):
  * ``events.emit`` appends every row to ``sink._BUFFER`` (SQLite stays the
    source of truth) AND, only when the endpoint is set, to this module's own
    ``_PENDING`` deque. The two buffers are independent, so the OTLP flush can
    never steal rows from -- or add retry pressure to -- the SQLite flush.
  * ``_flush_otlp`` is registered with ``sink.register_flusher`` and therefore
    runs on the shared daemon tick + atexit drain, already exception-isolated
    by ``sink._flush_all`` (a bug here cannot kill the flush thread).
  * Draining mirrors ``sink._flush_events``: snapshot without clearing, export,
    then pop exactly the exported rows on success. A failed export retains the
    rows for the next tick (best-effort, at-least-once across process
    restarts).
  * Export is SYNCHRONOUS and failure-observing: the exporter is wrapped in
    :class:`_TrackingExporter` behind ``SimpleLogRecordProcessor``, so each
    ``logger.emit`` performs the HTTP export on the calling thread and the
    wrapper sees the exporter's own ``LogExportResult.FAILURE`` return (the
    SDK's http exporter catches network exceptions itself and NEVER raises).
    ``BatchLogRecordProcessor`` cannot be used here: its worker thread pops the
    batch before exporting, swallows exporter failures, and reports
    ``force_flush()`` success -- which would make every row pop as "exported"
    during a collector outage, silently losing exactly the data this feature
    exists to deliver.
  * The whole flush cycle runs under ``_FLUSH_LOCK`` (mirroring
    ``sink._flush_events``): the daemon tick, the server watchdog drain, and
    atexit can overlap, and two concurrent drains would double-export and
    double-pop.

Lazy-import discipline (spec §3 non-goals + §7): this module imports NOTHING
from OpenTelemetry and no network library at module scope. The ``opentelemetry``
imports live inside :func:`_get_logger`, reachable only when the endpoint is
set, telemetry is on, and there are rows to export. A missing SDK (or any
construction failure) calls ``events.warn_once`` once and permanently disables
the exporter -- never raises, never crashes the flush thread, and the local
SQLite telemetry keeps working unchanged.
"""

from __future__ import annotations

import collections
import json
import logging
import os
import threading
from typing import Any, Dict, Optional

from . import sink

logger = logging.getLogger(__name__)

# The opt-in switch. Read fresh on every emit-time tap (same posture as
# CAIRN_TELEMETRY / CAIRN_READ_ONLY) so toggling takes effect without a
# module reload.
_ENDPOINT_ENV = "CAIRN_OTEL_ENDPOINT"

# OTLP-side buffer of pending ``(ts, name, session_id, attrs_json)`` rows --
# the same shape as sink._BUFFER. Separate deque so the SQLite flush owns its
# rows exclusively; maxlen caps growth during a long collector outage
# (mirrors the shared-sink doctrine).
_PENDING: collections.deque = collections.deque(maxlen=2000)
_LOCK = threading.Lock()

# Serializes whole flush cycles (snapshot -> export -> pop), mirroring
# ``sink._FLUSH_LOCK``: the daemon tick, the server watchdog drain, ``flush()``
# callers, and atexit can overlap; two concurrent drains would double-export
# the same rows and double-pop rows never written.
_FLUSH_LOCK = threading.Lock()

# Exporter lifecycle. ``_DISABLED`` is a one-way latch: set after a missing
# SDK or a construction failure so the flusher never retries the import
# (warn_once already told the user; retrying every 30s would only spam
# debug logs). ``_REGISTERED`` mirrors sink._FLUSHER_STARTED's idempotency.
_REGISTERED = False
_DISABLED = False

# Per-export HTTP timeout (seconds). Bounds how long one dead-endpoint flush
# stalls the shared flusher thread -- and the atexit drain, where it would
# otherwise visibly hang process exit on a black-holed endpoint.
_EXPORT_TIMEOUT_S = 5.0

# Lazily-built OTel handles. Untyped (Any) on purpose: the OpenTelemetry SDK
# is an optional extra that is usually absent, and these are only ever
# constructed behind the env gate in _get_logger.
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
    """Emit-time tap called by ``events.emit`` after its gates.

    No-op (one env read) unless ``CAIRN_OTEL_ENDPOINT`` is set. The row is
    APPENDED to this module's side buffer -- the SQLite row is already in
    ``sink._BUFFER`` and is never touched here. Lazily registers the OTLP
    flusher so the shared daemon thread picks the export up on its next tick.
    """
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
    """Rebuild OTel attributes from the stored attrs JSON (+ session_id).

    The JSON was already cardinality-checked at emit time; re-parsing (rather
    than threading a second dict through the sink) keeps ``events.emit``'s
    change to a single line. A malformed blob (impossible via emit, possible
    only by direct enqueue) degrades to session_id-only attributes.
    """
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
    """Warn once, then permanently disable the exporter.

    Local import of ``warn_once``: ``events`` imports this module at top level
    (for the emit tap), so importing events back at module scope would be
    circular. This branch runs at most once per process, so the deferred
    import costs nothing on any hot path. Clearing ``_PENDING`` is deliberate:
    the rows can never export now, and the SQLite ``events`` table remains the
    source of truth for them.
    """
    global _DISABLED
    from .events import warn_once

    warn_once("otlp_export_disabled", logger, msg)
    _DISABLED = True
    with _LOCK:
        _PENDING.clear()


class _TrackingExporter:
    """Delegate wrapper that remembers whether an export failed.

    The SDK's http exporter catches its own network exceptions and returns
    ``LogExportResult.FAILURE`` -- it never raises -- so a dead collector is
    observable only on the return value. Duck-typed on purpose:
    ``LogExportResult`` lives in the optional SDK, while
    ``getattr(result, "name", "SUCCESS")`` matches it without an import, and
    an unrecognized result object fails OPEN (treated as success) so an SDK
    quirk can't wedge the buffer into a permanent retry loop.
    """

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
    """Build the OTLP logger on first use; None when the SDK is unusable.

    STRICTLY lazy (spec §3/§7): every ``opentelemetry`` import lives inside
    this function, which is only reachable when the endpoint is set, telemetry
    is on, and there are pending rows. The default install path never executes
    a single line of this function.

    Synchronous by design (see module docstring): the exporter is wrapped in
    :class:`_TrackingExporter` behind ``SimpleLogRecordProcessor``, so
    ``logger.emit`` performs the HTTP export on the calling thread and its
    success/failure is observable when it returns. ``shutdown_on_exit=False``
    keeps the SDK from registering its own atexit hook, whose LIFO ordering
    against this sink's atexit drain is SDK-version-dependent.
    """
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
        # _TrackingExporter is duck-typed on purpose (see its docstring); the
        # stub-visible protocol mismatch is expected, not a regression.
        provider.add_log_record_processor(SimpleLogRecordProcessor(tracker))  # type: ignore[arg-type]
        _otlp_tracker = tracker
        _otlp_logger = provider.get_logger("cairn.telemetry")
        _log_record_cls = LogRecord
    except Exception:
        # Bad endpoint URL, SDK version quirk, ... -- same posture as a
        # missing SDK: one warning, then off. Never raise into the flush
        # thread. The endpoint value is deliberately NOT echoed (it may embed
        # credentials); details are in the debug log.
        logger.debug("otlp: exporter construction failed", exc_info=True)
        _warn_once_and_disable(
            "CAIRN_OTEL_ENDPOINT is set but the OTLP exporter could not be "
            "constructed (see debug logs); telemetry stays local-only."
        )
        return None
    return _otlp_logger


def _flush_otlp() -> None:
    """Drain the OTLP side buffer (best-effort, never raises).

    Registered with ``sink.register_flusher``; invoked by the shared daemon
    tick and the atexit drain, each flusher isolated by ``sink._flush_all``.
    Mirrors ``sink._flush_events``: snapshot WITHOUT clearing, export, then
    pop exactly the exported rows on success -- a failed export (exception,
    or the tracker observing ``LogExportResult.FAILURE``) leaves the rows
    queued for the next tick. The whole cycle runs under ``_FLUSH_LOCK``.
    """
    with _FLUSH_LOCK:
        # Master switch + read-only gate re-checked on the flush path: turning
        # telemetry off mid-process must stop export too (rows already
        # buffered for OTLP are telemetry, so they are dropped with everything
        # else), and a read-only daemon must not open network egress either.
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
            # A non-ImportError SDK import failure (corrupt install, plugin
            # raising at import) must not break the never-raise contract.
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
                    # Collector rejected the record. Stop here instead of
                    # hammering a dead endpoint (each further record costs a
                    # full export timeout) and retain the whole batch --
                    # at-least-once, re-exported on the next tick.
                    break
        except Exception:
            # Collector down / timeout / SDK bug: retain the rows for the
            # next tick, log at debug, never propagate (sink._flush_all also
            # guards, but this keeps the failure scoped to this exporter).
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
        # Export acknowledged -> drop exactly these rows. Rows appended during
        # the export sit to the right of the drained ones and stay queued.
        # _FLUSH_LOCK guarantees no other drain is mid-cycle, so the leftmost
        # len(batch) rows are exactly ``batch``.
        with _LOCK:
            for _ in range(len(batch)):
                try:
                    _PENDING.popleft()
                except IndexError:
                    break


def flush() -> None:
    """Drain the OTLP side buffer synchronously now (best-effort).

    Public hook for tests, mirroring ``sink.flush`` for the SQLite buffer.
    No-op unless the endpoint is set and rows are pending.
    """
    _flush_otlp()
