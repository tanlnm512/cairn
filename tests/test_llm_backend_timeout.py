"""Bounded-wait contracts for the LLM file-queue and subprocess backends."""

from __future__ import annotations

import inspect
import subprocess
import time
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from cairn.llm import client as client_mod
from cairn.llm.client import FileQueueBackend, LLMClient, SubprocessBackend


class _NoDiskBundle:
    """Enough state for backend methods whose task I/O is stubbed."""


class _RecordingFallback:
    """FileQueueBackend substitute that records each propagated bound."""

    def __init__(self) -> None:
        self.calls: List[float] = []

    def synthesize(
        self, prompt_id: str, facts: Dict[str, Any], timeout: float
    ) -> str:
        self.calls.append(timeout)
        return "synthesized"

    def revise(
        self,
        prompt_id: str,
        draft: str,
        errors: List[str],
        facts: Dict[str, Any],
        timeout: float,
    ) -> str:
        self.calls.append(timeout)
        return "revised"

    def judge(
        self, prompt_id: str, draft: str, facts: Dict[str, Any], timeout: float
    ) -> Dict[str, Any]:
        self.calls.append(timeout)
        return {"score": 1.0}

    def extract(self, transcript: str, timeout: float) -> List[Dict[str, Any]]:
        self.calls.append(timeout)
        return [{"value": transcript}]


@pytest.mark.parametrize("method_name", ["synthesize", "revise", "judge", "extract"])
def test_protocol_declares_backward_compatible_timeout(method_name: str) -> None:
    signature = inspect.signature(getattr(LLMClient, method_name))

    assert signature.parameters["timeout"].default == 120


@pytest.mark.parametrize(
    ("timeout", "max_wait", "poll_interval", "effective_bound"),
    [
        (0.2, 600.0, 0.04, 0.2),
        (120.0, 0.1, 0.02, 0.1),
    ],
)
def test_file_queue_stops_at_constructor_and_call_bounds(
    monkeypatch: pytest.MonkeyPatch,
    timeout: float,
    max_wait: float,
    poll_interval: float,
    effective_bound: float,
) -> None:
    sleep_durations: List[float] = []
    now = 1_000.0

    def _monotonic() -> float:
        return now

    def _sleep(duration: float) -> None:
        nonlocal now
        assert duration >= 0
        sleep_durations.append(duration)
        now += duration

    monkeypatch.setattr(
        client_mod,
        "time",
        SimpleNamespace(monotonic=_monotonic, sleep=_sleep),
    )

    task = SimpleNamespace(id="pending-task")

    monkeypatch.setattr(
        client_mod.task_mod,
        "create_task",
        lambda bundle, kind, resource, facts: task,
    )
    monkeypatch.setattr(
        client_mod.task_mod,
        "get_task",
        lambda bundle, task_id: None,
    )
    monkeypatch.setattr(
        client_mod.task_mod,
        "read_result",
        lambda bundle, task_id: "unused",
    )

    backend = FileQueueBackend(
        _NoDiskBundle(), poll_interval=poll_interval, max_wait=max_wait
    )

    result = backend._run_task_call("kind", "resource", {}, timeout)

    assert result == ""
    assert sum(sleep_durations) <= effective_bound + 1e-9
    assert max(sleep_durations) <= effective_bound


def test_file_queue_methods_propagate_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = FileQueueBackend(_NoDiskBundle())
    calls: List[tuple[str, float]] = []

    def _run_task_call(
        kind: str, resource: str, facts: Dict[str, Any], timeout: float
    ) -> str:
        calls.append((kind, timeout))
        return ""

    monkeypatch.setattr(backend, "_run_task_call", _run_task_call)

    assert backend.synthesize("prompt", {}, timeout=0.2) == ""
    assert backend.revise("prompt", "draft", [], {}, timeout=0.2) == ""
    assert backend.judge("prompt", "draft", {}, timeout=0.2) == {"score": 0.5}
    assert backend.extract("transcript", timeout=0.2) == []

    assert calls == [
        ("prompt", 0.2),
        ("prompt-revise", 0.2),
        ("prompt-judge", 0.2),
        ("memory-extract", 0.2),
    ]


def test_subprocess_execution_stops_at_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_timeouts: List[float] = []

    def _run(*args: Any, timeout: float, **kwargs: Any) -> Any:
        requested_timeouts.append(timeout)
        time.sleep(0.05)
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=timeout)

    monkeypatch.setattr(client_mod.subprocess, "run", _run)
    backend = SubprocessBackend(_NoDiskBundle(), cli="droid")

    started = time.perf_counter()
    result = backend._exec("prompt", timeout=0.2)
    elapsed = time.perf_counter() - started

    assert result == ""
    assert requested_timeouts == [0.2]
    assert elapsed < 0.7


def test_subprocess_fallback_methods_propagate_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = SubprocessBackend(_NoDiskBundle(), cli="droid")
    fallback = _RecordingFallback()
    execution_timeouts: List[float] = []

    def _exec(prompt: str, timeout: float) -> str:
        execution_timeouts.append(timeout)
        return ""

    monkeypatch.setattr(backend, "_exec", _exec)
    monkeypatch.setattr(backend, "_fallback", fallback)

    assert backend.synthesize("prompt", {}, timeout=0.2) == "synthesized"
    assert backend.revise("prompt", "draft", [], {}, timeout=0.2) == "revised"
    assert backend.judge("prompt", "draft", {}, timeout=0.2) == {"score": 1.0}
    assert backend.extract("transcript", timeout=0.2) == [{"value": "transcript"}]

    assert execution_timeouts == [0.2, 0.2, 0.2, 0.2]
    assert fallback.calls == [0.2, 0.2, 0.2, 0.2]
