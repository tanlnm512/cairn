"""F6: `embed_flush_stalled` -- chronic memory-embed flush failures made durable.

``embed_buffering._flush`` retries failed batches indefinitely; the escalation
to WARNING (>= 4 consecutive failures) was log-only, so a broken embed model
churning every 15s was invisible to ``cairn metrics`` / doctor. Now the
escalation also records ONE ``embed_flush_stalled`` event per failure streak,
with the failure count collapsed to a bounded bucket (4-10 / 11-100 / >100),
and the streak guard resets on the first successful flush so a later,
separate outage emits again.

The flusher thread is deliberately never started here: rows go straight into
``_QUEUE`` and ``_flush()`` is invoked synchronously, keeping the tests
hermetic and fast.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from cairn.mcp_server import embed_buffering as eb


@pytest.fixture(autouse=True)
def _reset_buffer_state(monkeypatch):
    """Isolate the flusher's module state + telemetry env around each test."""
    from cairn.telemetry import sink

    monkeypatch.delenv("CAIRN_TELEMETRY", raising=False)
    monkeypatch.delenv("CAIRN_READ_ONLY", raising=False)
    with sink._LOCK:
        sink._BUFFER.clear()
    with eb._LOCK:
        eb._QUEUE.clear()
    eb._FAILURES = 0
    eb._STALL_EVENT_SENT = False
    yield
    with sink._LOCK:
        sink._BUFFER.clear()
    with eb._LOCK:
        eb._QUEUE.clear()
    eb._FAILURES = 0
    eb._STALL_EVENT_SENT = False


def _queue_one():
    with eb._LOCK:
        eb._QUEUE.clear()
        eb._QUEUE.append("knowledge/test/concept")


def _failing_factory():
    def _boom():
        raise RuntimeError("simulated chronic flush failure")

    return _boom


def _stalled_events():
    from cairn.telemetry import sink

    return [
        json.loads(a) if a else {}
        for _ts, n, _sid, a in list(sink._BUFFER)
        if n == "embed_flush_stalled"
    ]


def test_below_threshold_no_event(monkeypatch):
    """Transient failures (< _WARN_AFTER) stay log-only: retries are expected,
    the durable signal is reserved for the chronic case."""
    _queue_one()
    monkeypatch.setattr(eb, "_conn_factory", _failing_factory())
    monkeypatch.setattr(eb, "_bundle_factory", lambda: object())

    for _ in range(eb._WARN_AFTER - 1):
        eb._flush()

    assert eb._FAILURES == eb._WARN_AFTER - 1
    assert _stalled_events() == []


def test_escalation_emits_event_once_per_streak(monkeypatch, caplog):
    """Past the threshold: WARNING every failing flush, but exactly one
    embed_flush_stalled event per streak, with a bounded failures bucket."""
    import logging

    caplog.set_level(logging.WARNING, logger="cairn.mcp_server.embed_buffering")
    _queue_one()
    monkeypatch.setattr(eb, "_conn_factory", _failing_factory())
    monkeypatch.setattr(eb, "_bundle_factory", lambda: object())

    for _ in range(eb._WARN_AFTER + 3):  # a few ticks past the threshold
        eb._flush()

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 4, "the WARNING itself repeats every failing flush"
    events = _stalled_events()
    assert len(events) == 1, "the durable event fires once per streak"
    assert events[0] == {"failures": "4-10"}, "bounded bucket, never a raw count"
    assert eb._STALL_EVENT_SENT is True


def test_streak_resets_on_success_so_next_outage_emits_again(monkeypatch):
    """A successful flush clears the streak guard -- a second, separate outage
    records a second event (each streak is its own signal)."""
    _queue_one()
    monkeypatch.setattr(eb, "_conn_factory", _failing_factory())
    monkeypatch.setattr(eb, "_bundle_factory", lambda: object())
    for _ in range(eb._WARN_AFTER):
        eb._flush()
    assert len(_stalled_events()) == 1

    # Recover: a working writable conn + a no-op embedder drain the batch.
    real = sqlite3.connect(":memory:")

    def _ok_factory():
        return real

    monkeypatch.setattr(eb, "_conn_factory", _ok_factory)
    from cairn.graph import embeddings as emb

    monkeypatch.setattr(emb, "embed_memory_concepts", lambda *a, **k: None)
    eb._flush()
    assert eb._FAILURES == 0
    assert eb._STALL_EVENT_SENT is False
    with eb._LOCK:
        assert not list(eb._QUEUE), "batch drained on success"
    real.close()

    # Fail again past the threshold -> the second streak emits again.
    _queue_one()
    monkeypatch.setattr(eb, "_conn_factory", _failing_factory())
    for _ in range(eb._WARN_AFTER):
        eb._flush()
    assert len(_stalled_events()) == 2


def test_telemetry_off_silences_the_event(monkeypatch, caplog):
    """The master switch silences the durable signal (the operational WARNING
    still fires -- same posture as the pre-existing escalation)."""
    import logging

    monkeypatch.setenv("CAIRN_TELEMETRY", "off")
    caplog.set_level(logging.WARNING, logger="cairn.mcp_server.embed_buffering")
    _queue_one()
    monkeypatch.setattr(eb, "_conn_factory", _failing_factory())
    monkeypatch.setattr(eb, "_bundle_factory", lambda: object())

    for _ in range(eb._WARN_AFTER):
        eb._flush()

    assert _stalled_events() == []
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_no_attr_value_contains_a_path_separator(monkeypatch):
    """Universal guard: the failures attr is a bucket tag, never free text."""
    _queue_one()
    monkeypatch.setattr(eb, "_conn_factory", _failing_factory())
    monkeypatch.setattr(eb, "_bundle_factory", lambda: object())
    for _ in range(eb._WARN_AFTER):
        eb._flush()

    for attrs in _stalled_events():
        for value in attrs.values():
            assert "/" not in str(value) and "\\" not in str(value)
