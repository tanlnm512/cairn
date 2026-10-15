"""Red-first pins for the `cairn connect` daemon lifecycle over monkeypatched lifecycle boundaries."""
from __future__ import annotations

import os
import re
import threading
import time

_STARTED = re.compile(r"start", re.IGNORECASE)
_REUSED = re.compile(r"reus|already|existing|running", re.IGNORECASE)


def _url_base() -> str:
    from cairn.mcp_server import lifecycle as lc

    return f"http://{lc.DEFAULT_HOST}:{lc.DEFAULT_PORT}"


def _patch_lifecycle(monkeypatch, *, healthy, ensure_delay=0.0, probe_barrier=None):
    """Fake every daemon lifecycle boundary `connect` may touch; returns mutable scenario state."""
    from cairn.mcp_server import lifecycle as lc

    try:
        from cairn.cli import connect as connect_cli
    except ImportError:
        connect_cli = None

    state = {"healthy": healthy, "calls": []}

    def fake_healthz(*args, **kwargs):
        if not state["healthy"] and probe_barrier is not None:
            try:
                probe_barrier.wait(timeout=5.0)
            except threading.BrokenBarrierError:
                pass
        return state["healthy"]

    def fake_healthz_json(*args, **kwargs):
        # No "db" key: store identity unknown -> reuse stays allowed.
        return {"status": "ok", "store_reachable": True} if state["healthy"] else None

    def fake_ensure(port=None, host=None, *args, **kwargs):
        state["calls"].append("ensure")
        if ensure_delay:
            time.sleep(ensure_delay)
        state["healthy"] = True
        return 9001, f"http://{lc.DEFAULT_HOST}:{lc.DEFAULT_PORT}/mcp", True

    def fake_find_strays(*args, **kwargs):
        state["calls"].append("sweep")
        # Offset from the real pid so the fake orphan can never name a live process.
        return [os.getpid() + 4101]

    def fake_sweep_strays(*args, **kwargs):
        state["calls"].append("sweep")
        return 1

    fakes = {
        "healthz_responds": fake_healthz,
        "healthz_json": fake_healthz_json,
        "ensure_daemon_running": fake_ensure,
        "find_strays": fake_find_strays,
        "sweep_strays": fake_sweep_strays,
        "terminate_pid": lambda *args, **kwargs: None,
    }
    for name, fake in fakes.items():
        # raising=False: the produced seams land with the implementation tasks.
        monkeypatch.setattr(lc, name, fake, raising=False)
        if connect_cli is not None:
            monkeypatch.setattr(connect_cli, name, fake, raising=False)
    return state


def _invoke_connect():
    from click.testing import CliRunner

    from cairn.cli import main

    return CliRunner().invoke(main, ["connect"])


def _patch_thread_echo(monkeypatch):
    """Record click.echo per thread: concurrent CliRunner invocations swap the global stdout."""
    import click

    records: dict[int, list[str]] = {}
    real_echo = click.echo

    def echo_recorder(msg=None, *args, **kwargs):
        records.setdefault(threading.get_ident(), []).append("" if msg is None else str(msg))
        return real_echo(msg, *args, **kwargs)

    monkeypatch.setattr(click, "echo", echo_recorder)
    return records


def test_cold_connect_starts_one_daemon_and_reports_started(monkeypatch):
    """Cold connect starts exactly one daemon, emits the URL, and reports started."""
    state = _patch_lifecycle(monkeypatch, healthy=False)

    result = _invoke_connect()

    assert result.exit_code == 0, result.output
    assert state["calls"].count("ensure") == 1
    assert _url_base() in result.output
    assert _STARTED.search(result.output)


def test_warm_connect_reports_reused_without_starting(monkeypatch):
    """Warm connect emits the URL, reports reuse, and starts nothing."""
    state = _patch_lifecycle(monkeypatch, healthy=True)

    result = _invoke_connect()

    assert result.exit_code == 0, result.output
    assert state["calls"].count("ensure") == 0
    assert _url_base() in result.output
    assert _REUSED.search(result.output)


def test_connect_refuses_daemon_serving_another_workspace(monkeypatch):
    """A healthy daemon pinned to another workspace's store must never be
    reused silently: connect fails loudly instead of handing out a URL that
    answers from the wrong store."""
    state = _patch_lifecycle(monkeypatch, healthy=True)
    monkeypatch.setattr(
        "cairn.mcp_server.lifecycle.healthz_json",
        lambda *a, **k: {"status": "ok", "db": "/tmp/other-ws/.kg/graph.db"},
    )

    result = _invoke_connect()

    assert result.exit_code == 1, result.output
    assert "serves store" in result.output
    assert "/tmp/other-ws/.kg/graph.db" in result.output
    assert state["calls"] == [], "no daemon may be started or swept for a foreign store"


def test_racing_connects_converge_to_one_daemon(monkeypatch):
    """Two racing connects both succeed, emit the same URL, and start exactly one daemon."""
    barrier = threading.Barrier(2)
    state = _patch_lifecycle(
        monkeypatch, healthy=False, ensure_delay=0.15, probe_barrier=barrier
    )
    records = _patch_thread_echo(monkeypatch)

    outcomes = [None, None]

    def racer(i):
        try:
            outcomes[i] = _invoke_connect()
        except BaseException as exc:
            outcomes[i] = exc

    threads = [threading.Thread(target=racer, args=(i,), daemon=True) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert all(not t.is_alive() for t in threads)
    for outcome in outcomes:
        assert not isinstance(outcome, BaseException), outcome
        assert outcome.exit_code == 0, outcome.output

    url_re = re.compile(re.escape(_url_base()) + r"\S*")
    urls = []
    reports = []
    for t in threads:
        text = "\n".join(records.get(t.ident, []))
        match = url_re.search(text)
        assert match, text
        urls.append(match.group(0))
        reports.append(
            (_STARTED.search(text) is not None, _REUSED.search(text) is not None)
        )

    assert state["calls"].count("ensure") == 1
    assert urls[0] == urls[1]
    assert sum(1 for started, _ in reports if started) == 1
    for started, reused in reports:
        if not started:
            assert reused


def test_killed_daemon_replaced_with_orphan_swept_first(monkeypatch):
    """After a crash the next connect sweeps the orphan, then starts a replacement daemon."""
    state = _patch_lifecycle(monkeypatch, healthy=True)

    state["healthy"] = False  # the running daemon is killed
    state["calls"].clear()

    result = _invoke_connect()

    assert result.exit_code == 0, result.output
    calls = state["calls"]
    assert calls.count("ensure") == 1
    assert "sweep" in calls
    assert calls.index("sweep") < calls.index("ensure")
    assert _url_base() in result.output
    assert _STARTED.search(result.output)
