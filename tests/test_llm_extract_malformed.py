"""LLM extraction tolerates malformed and non-JSON records."""

from __future__ import annotations

from typing import Any, Dict

import pytest

from cairn.llm.client import FileQueueBackend, SubprocessBackend


class _NoDiskBundle:
    """Enough state for extraction methods whose task I/O is stubbed."""


def _backend(name: str, monkeypatch: pytest.MonkeyPatch, raw: str):
    if name == "subprocess":
        backend = SubprocessBackend(_NoDiskBundle(), cli="droid")
        monkeypatch.setattr(
            backend, "_exec", lambda prompt, timeout=120: raw
        )
        monkeypatch.setattr(
            backend._fallback,
            "_run_task",
            lambda kind, resource, facts: raw,
        )
    else:
        backend = FileQueueBackend(_NoDiskBundle())
        monkeypatch.setattr(
            backend,
            "_run_task",
            lambda kind, resource, facts: raw,
        )
    return backend


@pytest.mark.parametrize("backend_name", ["file_queue", "subprocess"])
def test_malformed_record_is_skipped(backend_name, monkeypatch):
    raw = '{"value": 1}\n{"value":\n{"value": 2}\nnot-json\n'

    records = _backend(backend_name, monkeypatch, raw).extract("transcript")

    assert records == [{"value": 1}, {"value": 2}]


@pytest.mark.parametrize(
    ("backend_name", "raw"),
    [
        ("file_queue", ""),
        ("file_queue", "plain response without records"),
        ("subprocess", ""),
        ("subprocess", "plain response without records"),
    ],
)
def test_empty_output_returns_no_records(
    backend_name: str, raw: str, monkeypatch: pytest.MonkeyPatch
):
    records: list[Dict[str, Any]] = _backend(
        backend_name, monkeypatch, raw
    ).extract("transcript")

    assert records == []
