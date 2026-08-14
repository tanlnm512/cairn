"""Tests for the optional OTLP exporter (task T19, spec §3 + §7).

``cairn.telemetry.otel`` is the P2 opt-in exporter: when
``CAIRN_OTEL_ENDPOINT`` is unset (the default) it is inert -- no export, no
OpenTelemetry import, one env read per emit. When set, the emit-time tap
appends rows to the exporter's OWN side buffer (never stealing from the
SQLite-bound ``sink._BUFFER``) and a registered flusher forwards them as OTel
LogRecords on the shared 30s flush thread.

Covers:
  (a) endpoint unset -> exporter inactive, no import attempted, no flusher
      registered (default behavior unchanged).
  (b) endpoint set + SDK absent (import gate) -> ``warn_once`` fires exactly
      once, nothing raises, the exporter permanently disables and short-
      circuits, and the local SQLite telemetry keeps its rows.
  (c) endpoint set + SDK present (stubbed via ``sys.modules``) -> events are
      forwarded as LogRecords with ``body`` = event name and ``attributes`` =
      event attrs + ``session_id``; the exporter gets the endpoint URL; the
      side buffer drains on success and retains on failure, never raising.
  (d) ``CAIRN_TELEMETRY=off`` overrides the endpoint: no tap, no export,
      pending rows dropped.

No test touches a real network: the OTel SDK is either gated to ImportError
or replaced by in-process stubs. Module-global state is reset by the autouse
fixture, mirroring ``tests/test_telemetry.py`` (the shared flusher thread and
``_FLUSHER_STARTED`` are deliberately left alone; the exporter's registration
in ``sink._FLUSHERS`` is removed so it cannot leak into sibling suites).
"""

from __future__ import annotations

import builtins
import logging
import sys
import types
from typing import Any, Callable

import pytest

from cairn.telemetry import events as ev
from cairn.telemetry import otel
from cairn.telemetry import sink
from cairn.telemetry import ANN_FALLBACK, LOCK_CONTENTION, emit

_ENDPOINT = "http://collector.internal:4318/v1/logs"


# ---------------------------------------------------------------------------
# Module-global state reset (mirrors tests/test_telemetry.py + otel globals)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_otlp_state(monkeypatch):
    """Reset the sink's + exporter's process-global state around each test.

    ``otel`` keeps mutable module globals (side buffer, disable latch,
    registration flag, lazily-built OTel handles) that production never
    clears; without this reset one test's export would bleed into the next.
    The exporter's registered flusher is removed from ``sink._FLUSHERS`` so
    it cannot fire during sibling suites (the same leak-guard
    ``test_register_flusher_is_idempotent_by_identity`` applies).
    ``_FLUSHER_STARTED`` is deliberately NOT reset (one shared thread per
    process; it never ticks within a test's lifetime).
    """
    _clear_everything()
    monkeypatch.delenv("CAIRN_OTEL_ENDPOINT", raising=False)
    monkeypatch.delenv("CAIRN_TELEMETRY", raising=False)
    monkeypatch.delenv("CAIRN_READ_ONLY", raising=False)
    monkeypatch.delenv("CAIRN_SESSION", raising=False)
    yield
    _clear_everything()


def _clear_everything() -> None:
    with sink._LOCK:
        sink._BUFFER.clear()
    sink._conn_factory = None
    with ev._WARN_LOCK:
        ev._WARNED.clear()
    with otel._LOCK:
        otel._PENDING.clear()
    otel._DISABLED = False
    otel._REGISTERED = False
    otel._otlp_logger = None
    otel._otlp_tracker = None
    otel._log_record_cls = None
    with sink._LOCK:
        while otel._flush_otlp in sink._FLUSHERS:
            sink._FLUSHERS.remove(otel._flush_otlp)


# ---------------------------------------------------------------------------
# Import gate -- proves the lazy-import discipline without a real SDK
# ---------------------------------------------------------------------------


class _ImportGate:
    """``builtins.__import__`` wrapper that fails/records ``opentelemetry``.

    Raises ImportError for any import rooted at ``opentelemetry`` (simulating
    an absent SDK regardless of what is installed in the venv) and records
    every attempt, so tests can assert the default path never even tries.
    All other imports pass through to the real importer.
    """

    def __init__(self, fail: bool = True) -> None:
        self.fail = fail
        self.attempted: list[str] = []
        self._real: Callable[..., Any] = builtins.__import__

    def __call__(self, name: str, *args: Any, **kwargs: Any) -> Any:
        if name.split(".")[0] == "opentelemetry":
            self.attempted.append(name)
            if self.fail:
                raise ImportError(f"No module named {name!r} (import gate)")
        return self._real(name, *args, **kwargs)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> "_ImportGate":
        monkeypatch.setattr(builtins, "__import__", self)
        return self


# ---------------------------------------------------------------------------
# Stub OpenTelemetry SDK -- in-process, zero network
# ---------------------------------------------------------------------------


def _install_stub_sdk(
    monkeypatch: pytest.MonkeyPatch,
    export_fails_first: int = 0,
    exporter_raises: bool = False,
    export_raises: bool = False,
) -> dict[str, Any]:
    """Inject stub ``opentelemetry`` modules into ``sys.modules``.

    The stubs mirror the SYNCHRONOUS export design: ``logger.emit`` reaches
    ``exporter.export`` on the calling thread via SimpleLogRecordProcessor,
    and failures are reported the way the real SDK does -- a returned
    ``LogExportResult.FAILURE``, not a raised exception (``export_raises``
    additionally covers the raising path). Everything the exporter does
    (constructed exporter + endpoint + timeout, provider wiring, exported
    records) is recorded into the returned dict. ``export_fails_first``
    simulates a collector that rejects the first N exports then recovers
    (retry-path coverage without a network); ``exporter_raises`` simulates a
    rejected endpoint at construction.
    """
    seen: dict[str, Any] = {
        "records": [],  # every LogRecord constructed
        "exported": [],  # records actually handed to exporter.export
        "exports": 0,  # number of exporter.export calls
        "export_failures_remaining": export_fails_first,
    }

    class _Success:
        name = "SUCCESS"

    class _Failure:
        name = "FAILURE"

    class _StubResource:
        created: Any = None

        @classmethod
        def create(cls, attrs: dict) -> "_StubResource":
            seen["resource_attrs"] = dict(attrs)
            return cls()

    class _StubLogRecord:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs
            seen["records"].append(self)

    class _StubExporter:
        def __init__(self, endpoint: str = "", timeout: Any = None, **kwargs: Any) -> None:
            if exporter_raises:
                raise ValueError("simulated bad endpoint")
            seen["exporter_endpoint"] = endpoint
            seen["exporter_timeout"] = timeout

        def export(self, batch: Any) -> Any:
            seen["exports"] += 1
            seen["exported"].extend(batch)
            if seen["export_failures_remaining"] > 0:
                seen["export_failures_remaining"] -= 1
                if export_raises:
                    raise RuntimeError("simulated collector outage")
                return _Failure()
            return _Success()

    class _StubSimpleProcessor:
        # Mirrors the real SimpleLogRecordProcessor: export runs synchronously
        # in the calling thread. ``exporter`` here is cairn's _TrackingExporter
        # wrapper -- exactly the production wiring.
        def __init__(self, exporter: Any) -> None:
            self.exporter = exporter
            seen["processor_exporter"] = exporter

        def emit(self, record: Any) -> None:
            self.exporter.export((record,))

    class _StubLogger:
        def __init__(self) -> None:
            self.processor: Any = None

        def emit(self, record: Any) -> None:
            if self.processor is not None:
                self.processor.emit(record)

    _stub_logger = _StubLogger()

    class _StubProvider:
        def __init__(self, resource: Any = None, shutdown_on_exit: bool = True) -> None:
            seen["provider_resource"] = resource
            seen["shutdown_on_exit"] = shutdown_on_exit

        def add_log_record_processor(self, processor: Any) -> None:
            seen["processor"] = processor
            _stub_logger.processor = processor

        def get_logger(self, name: str) -> _StubLogger:
            seen["logger_name"] = name
            return _stub_logger

    mods = {
        "opentelemetry": types.ModuleType("opentelemetry"),
        "opentelemetry.sdk": types.ModuleType("opentelemetry.sdk"),
        "opentelemetry.sdk._logs": types.ModuleType("opentelemetry.sdk._logs"),
        "opentelemetry.sdk._logs.export": types.ModuleType(
            "opentelemetry.sdk._logs.export"
        ),
        "opentelemetry.sdk.resources": types.ModuleType(
            "opentelemetry.sdk.resources"
        ),
        "opentelemetry.exporter": types.ModuleType("opentelemetry.exporter"),
        "opentelemetry.exporter.otlp": types.ModuleType(
            "opentelemetry.exporter.otlp"
        ),
        "opentelemetry.exporter.otlp.proto": types.ModuleType(
            "opentelemetry.exporter.otlp.proto"
        ),
        "opentelemetry.exporter.otlp.proto.http": types.ModuleType(
            "opentelemetry.exporter.otlp.proto.http"
        ),
        "opentelemetry.exporter.otlp.proto.http.log_exporter": types.ModuleType(
            "opentelemetry.exporter.otlp.proto.http.log_exporter"
        ),
    }
    mods["opentelemetry.sdk._logs"].LoggerProvider = _StubProvider
    mods["opentelemetry.sdk._logs"].LogRecord = _StubLogRecord
    mods["opentelemetry.sdk._logs.export"].SimpleLogRecordProcessor = (
        _StubSimpleProcessor
    )
    mods["opentelemetry.sdk.resources"].Resource = _StubResource
    mods["opentelemetry.exporter.otlp.proto.http.log_exporter"].OTLPLogExporter = (
        _StubExporter
    )
    for name, mod in mods.items():
        monkeypatch.setitem(sys.modules, name, mod)
    return seen


# ---------------------------------------------------------------------------
# (a) endpoint unset -> inactive, no import, no registration
# ---------------------------------------------------------------------------


def test_endpoint_unset_exporter_inactive(monkeypatch):
    """Default: is_enabled() False, the tap appends nothing, no flusher."""
    monkeypatch.delenv("CAIRN_OTEL_ENDPOINT", raising=False)
    assert otel.is_enabled() is False
    assert otel.endpoint() == ""

    emit(ANN_FALLBACK, reason="load_failed")
    emit(LOCK_CONTENTION, site="schema.get_db")

    assert len(sink._BUFFER) == 2, "SQLite buffer unaffected"
    assert len(otel._PENDING) == 0, "no rows tapped without the endpoint"
    with sink._LOCK:
        assert otel._flush_otlp not in sink._FLUSHERS, "nothing registered"


def test_endpoint_unset_never_imports_opentelemetry(monkeypatch):
    """Unset endpoint: emit + a manual flush attempt zero opentelemetry imports.

    The import gate would raise if any opentelemetry import were attempted,
    proving the lazy-import discipline (spec §3/§7) on the default path.
    """
    gate = _ImportGate(fail=True).install(monkeypatch)

    emit(ANN_FALLBACK, reason="load_failed")
    otel.flush()  # empty pending -> returns before the lazy import

    assert gate.attempted == [], "default path must not import the SDK"


# ---------------------------------------------------------------------------
# (b) endpoint set + SDK absent -> warn_once, no raise, short-circuit
# ---------------------------------------------------------------------------


def test_missing_sdk_warns_once_and_disables(monkeypatch, caplog):
    """Absent SDK: one warning naming the extra, then permanent disable.

    The rows stay in the SQLite-bound buffer (local telemetry is unaffected);
    the exporter's side buffer is cleared (those rows can never export) and
    every later attempt short-circuits without another warning.
    """
    monkeypatch.setenv("CAIRN_OTEL_ENDPOINT", _ENDPOINT)
    gate = _ImportGate(fail=True).install(monkeypatch)
    assert otel.is_enabled() is True

    emit(ANN_FALLBACK, reason="load_failed")
    emit(LOCK_CONTENTION, site="schema.get_db")
    assert len(otel._PENDING) == 2, "tap buffered rows (no import yet)"

    with caplog.at_level(logging.WARNING):
        otel.flush()  # must not raise despite the ImportError
        otel.flush()  # second attempt: short-circuits, no second warning

    warnings = [
        r for r in caplog.records if r.levelno == logging.WARNING
    ]
    assert len(warnings) == 1, "warn_once fires exactly once"
    assert "cairn-intel[otlp]" in warnings[0].getMessage()
    assert "CAIRN_OTEL_ENDPOINT" in warnings[0].getMessage()

    assert otel._DISABLED is True
    assert otel.is_enabled() is False
    assert len(otel._PENDING) == 0, "unexportable rows dropped, not hoarded"
    assert gate.attempted, "the gate saw (and blocked) the lazy import attempt"

    # Local telemetry keeps working: the SQLite rows were never stolen.
    assert len(sink._BUFFER) == 2
    emit("empty_result", query_kind="explore")
    assert len(sink._BUFFER) == 3, "emit still buffers locally after disable"
    assert len(otel._PENDING) == 0, "record() short-circuits once disabled"
    attempted_after_first = list(gate.attempted)
    otel.flush()  # still must not raise nor import
    assert gate.attempted == attempted_after_first, "no further import attempts"


def test_missing_sdk_does_not_raise_from_flush_all(monkeypatch):
    """A missing SDK cannot kill the shared flush thread.

    ``sink._flush_all`` isolates registered flushers, but the exporter must
    also swallow internally -- running the full registered-flusher path with
    the SDK gated off proves the daemon tick survives a broken export config.
    """
    monkeypatch.setenv("CAIRN_OTEL_ENDPOINT", _ENDPOINT)
    _ImportGate(fail=True).install(monkeypatch)
    emit(ANN_FALLBACK, reason="load_failed")

    sink._flush_all()  # must not raise

    assert otel._DISABLED is True
    assert len(sink._BUFFER) == 1, "SQLite-bound row retained"


# ---------------------------------------------------------------------------
# (c) endpoint set + SDK present -> LogRecords forwarded with name + attrs
# ---------------------------------------------------------------------------


def test_sdk_present_exports_records_with_name_and_attrs(monkeypatch):
    """Happy path: each event becomes a LogRecord (body=name, attrs mapped).

    Also pins the wiring contract: the exporter is built with the env
    endpoint URL and a bounded timeout, the resource is service.name=cairn
    (no paths/PII), the provider registers no atexit hook of its own, export
    is synchronous (records reach the exporter within the flush call), the
    side buffer drains, and the SQLite-bound rows are untouched (DB stays the
    source of truth).
    """
    monkeypatch.setenv("CAIRN_OTEL_ENDPOINT", _ENDPOINT)
    monkeypatch.setenv("CAIRN_SESSION", "trace-19")
    seen = _install_stub_sdk(monkeypatch)

    emit(ANN_FALLBACK, reason="load_failed")
    emit(LOCK_CONTENTION)  # no attrs -> attributes carry session_id only

    assert len(otel._PENDING) == 2
    with sink._LOCK:
        assert sink._FLUSHERS.count(otel._flush_otlp) == 1, "flusher registered"

    otel.flush()

    assert seen["exporter_endpoint"] == _ENDPOINT
    assert seen["exporter_timeout"] == otel._EXPORT_TIMEOUT_S
    assert seen["resource_attrs"] == {"service.name": "cairn"}
    assert seen["logger_name"] == "cairn.telemetry"
    assert seen["shutdown_on_exit"] is False, "no SDK atexit hook (drain is ours)"
    assert seen["exports"] == 2, "synchronous export: one export per record"
    assert len(seen["exported"]) == 2, "both events forwarded"

    first, second = seen["records"]
    assert first.kwargs["severity_text"] == "INFO"
    assert first.kwargs["body"] == ANN_FALLBACK
    assert first.kwargs["attributes"] == {
        "reason": "load_failed",
        "session_id": "trace-19",
    }
    assert second.kwargs["body"] == LOCK_CONTENTION
    assert second.kwargs["attributes"] == {"session_id": "trace-19"}
    assert isinstance(first.kwargs["timestamp"], int), "OTel ns timestamp"

    assert len(otel._PENDING) == 0, "side buffer drained after success"
    assert len(sink._BUFFER) == 2, "SQLite rows never stolen by the export"


def test_sdk_present_export_failure_retains_then_recovers(monkeypatch):
    """A dead collector keeps the rows queued for retry; nothing raises.

    Mirrors sink._flush_events: snapshot-without-clear semantics -- the failed
    batch stays in the side buffer and the next successful flush drains it.
    Failure is reported the way the real SDK reports it: a returned
    LogExportResult.FAILURE, not a raised exception.
    """
    monkeypatch.setenv("CAIRN_OTEL_ENDPOINT", _ENDPOINT)
    seen = _install_stub_sdk(monkeypatch, export_fails_first=1)

    emit(ANN_FALLBACK, reason="load_failed")
    assert len(otel._PENDING) == 1

    otel.flush()  # collector rejects the export -- must not raise

    assert len(seen["records"]) == 1, "record was constructed and export attempted"
    assert seen["exports"] == 1
    assert len(seen["exported"]) == 1
    assert len(otel._PENDING) == 1, "failed batch retained for retry"

    otel.flush()  # collector recovered -> backlog drains

    assert len(seen["records"]) == 2, "the retained row was re-exported"
    assert len(seen["exported"]) == 2
    assert len(otel._PENDING) == 0


def test_sdk_present_export_exception_retains(monkeypatch):
    """An exporter that RAISES (instead of returning FAILURE) also retains.

    The SDK's http exporter normally catches its own network errors, but an
    exception must not escape the flush contract either.
    """
    monkeypatch.setenv("CAIRN_OTEL_ENDPOINT", _ENDPOINT)
    seen = _install_stub_sdk(monkeypatch, export_fails_first=1, export_raises=True)

    emit(ANN_FALLBACK, reason="load_failed")
    otel.flush()  # must not raise

    assert len(seen["exported"]) == 1, "export was attempted"
    assert len(otel._PENDING) == 1, "failed batch retained for retry"

    otel.flush()  # recovered
    assert len(otel._PENDING) == 0


def test_sdk_present_outage_short_circuits_batch(monkeypatch):
    """A rejected export stops the batch instead of hammering the endpoint.

    With a backlog and a dead collector, only ONE export timeout is paid per
    flush cycle; the remaining rows stay queued whole for the next tick
    (at-least-once).
    """
    monkeypatch.setenv("CAIRN_OTEL_ENDPOINT", _ENDPOINT)
    seen = _install_stub_sdk(monkeypatch, export_fails_first=1)

    for _ in range(5):
        emit(ANN_FALLBACK, reason="load_failed")
    assert len(otel._PENDING) == 5

    otel.flush()

    assert seen["exports"] == 1, "stopped after the first rejected record"
    assert len(otel._PENDING) == 5, "whole batch retained"

    otel.flush()  # collector back: the full backlog exports and drains

    assert len(otel._PENDING) == 0
    assert len(seen["exported"]) == 6  # 1 rejected + all 5 on the retry


def test_sdk_present_bad_construction_warns_and_disables(monkeypatch, caplog):
    """An exporter that fails to construct (e.g. rejected URL) disables once.

    Same posture as a missing SDK: one warning, permanent disable, no raise
    into the flush thread, local telemetry unaffected.
    """
    monkeypatch.setenv("CAIRN_OTEL_ENDPOINT", _ENDPOINT)
    _install_stub_sdk(monkeypatch, exporter_raises=True)
    emit(ANN_FALLBACK, reason="load_failed")

    with caplog.at_level(logging.WARNING):
        otel.flush()
        otel.flush()

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "could not be constructed" in warnings[0].getMessage()
    assert _ENDPOINT not in warnings[0].getMessage(), "endpoint never echoed"
    assert otel._DISABLED is True
    assert len(sink._BUFFER) == 1, "local row retained"


def test_registration_is_idempotent_across_emits(monkeypatch):
    """Many emits register the OTLP flusher exactly once (identity guard)."""
    monkeypatch.setenv("CAIRN_OTEL_ENDPOINT", _ENDPOINT)
    _install_stub_sdk(monkeypatch)

    for _ in range(3):
        emit(ANN_FALLBACK, reason="load_failed")

    with sink._LOCK:
        assert sink._FLUSHERS.count(otel._flush_otlp) == 1


# ---------------------------------------------------------------------------
# (d) master switch overrides the endpoint
# ---------------------------------------------------------------------------


def test_telemetry_off_overrides_endpoint(monkeypatch):
    """CAIRN_TELEMETRY=off beats CAIRN_OTEL_ENDPOINT: no tap, no export."""
    monkeypatch.setenv("CAIRN_OTEL_ENDPOINT", _ENDPOINT)
    monkeypatch.setenv("CAIRN_TELEMETRY", "off")
    gate = _ImportGate(fail=True).install(monkeypatch)

    emit(ANN_FALLBACK, reason="load_failed")

    assert len(sink._BUFFER) == 0, "master switch silences emit (existing gate)"
    assert len(otel._PENDING) == 0, "tap never reached"
    assert gate.attempted == [], "no SDK import when telemetry is off"


def test_telemetry_off_midflight_clears_pending_without_export(monkeypatch):
    """Rows already queued for OTLP are dropped when telemetry goes off.

    The off-branch runs before the lazy import, so no SDK load and no export
    happen even with pending rows and the endpoint set.
    """
    monkeypatch.setenv("CAIRN_OTEL_ENDPOINT", _ENDPOINT)
    gate = _ImportGate(fail=True).install(monkeypatch)
    with otel._LOCK:
        otel._PENDING.append((1.0, ANN_FALLBACK, "s", None))

    monkeypatch.setenv("CAIRN_TELEMETRY", "off")
    otel.flush()  # must not raise

    assert len(otel._PENDING) == 0, "pending OTLP rows dropped when off"
    assert gate.attempted == [], "no SDK import when telemetry is off"


def test_read_only_daemon_taps_and_exports_nothing(monkeypatch):
    """CAIRN_READ_ONLY=1: the emit gate precedes the tap (no export either).

    A read-only daemon writes no telemetry rows; it must not open network
    egress for them either (spec §5.4 -- the SSE daemon is the safe mode).
    """
    monkeypatch.setenv("CAIRN_OTEL_ENDPOINT", _ENDPOINT)
    monkeypatch.setenv("CAIRN_READ_ONLY", "1")

    emit(ANN_FALLBACK, reason="load_failed")

    assert len(sink._BUFFER) == 0
    assert len(otel._PENDING) == 0
