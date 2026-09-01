"""FRESH-1: FileWatcherService (live file watching as default serve behavior).

Hermetic tests for the watcher service in ``cairn.graph.watcher``:

* save event -> ``pending_sync`` rows (BOTH path forms) -> ``incremental_update``
  called with the rows visible -> rows cleared after the pass;
* two quick events coalesce into ONE update pass;
* read-only mode never starts the service;
* missing watchdog degrades to a logged no-op;
* ``CAIRN_WATCH=0`` hard-disables;
* a ``RuntimeError`` from ``incremental_update`` (concurrent CLI build holds
  the schema build lock) is absorbed, logged once, and the service survives
  for the next batch.

No test needs real watchdog installed: the observer class is injected via
``sys.modules``. The one real-integration test (real Observer thread, real
file write) is skipif-guarded on watchdog being importable.

Migrations note: the service's DB access goes through ``schema.get_db`` and
the fixture DBs are pre-migrated with ``_apply_schema`` so ``pending_sync``
exists from the first connect.
"""
from __future__ import annotations

import importlib.util
import logging
import sqlite3
import sys
import threading
import time
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from cairn.graph import scanner as scanner_mod
from cairn.graph.schema import _apply_schema
from cairn.graph.watcher import FileWatcherService, _DebouncingHandler

pytestmark = pytest.mark.infra


# ---------------------------------------------------------------------------
# Helpers: corpus, DB, fake watchdog, event objects
# ---------------------------------------------------------------------------


def _make_corpus(tmp_path: Path) -> tuple[Path, Path]:
    """A workspace with one git repo (a bare .git dir satisfies discover_repos)."""
    ws = tmp_path / "ws"
    repo = ws / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / ".git").mkdir()
    return ws, repo


def _make_db(tmp_path: Path) -> str:
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    _apply_schema(conn)
    conn.commit()
    conn.close()
    return str(db)


class FakeObserver:
    """Stand-in for watchdog.observers.Observer recording lifecycle calls."""

    instances: list["FakeObserver"] = []

    def __init__(self):
        self.schedules = []  # (handler, path, recursive)
        self.daemon = False
        self.started = False
        self.stopped = False
        self.joined = False
        FakeObserver.instances.append(self)

    def schedule(self, handler, path, recursive=False):
        self.schedules.append((handler, path, recursive))

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def join(self, timeout=None):
        self.joined = True


@pytest.fixture
def fake_watchdog(monkeypatch):
    """Inject a fake watchdog package so no real observer threads start."""
    FakeObserver.instances = []
    observers_mod = types.ModuleType("watchdog.observers")
    observers_mod.Observer = FakeObserver
    watchdog_mod = types.ModuleType("watchdog")
    watchdog_mod.observers = observers_mod
    # Pre-seeding sys.modules short-circuits the import machinery: the
    # service's lazy `from watchdog.observers import Observer` resolves here.
    monkeypatch.setitem(sys.modules, "watchdog", watchdog_mod)
    monkeypatch.setitem(sys.modules, "watchdog.observers", observers_mod)
    return FakeObserver


def _event(path: str, dest: str | None = None) -> SimpleNamespace:
    """A watchdog-shaped file event."""
    return SimpleNamespace(
        is_directory=False, src_path=path, dest_path=dest, event_type="modified"
    )


def _rows(db_path: str, sql: str = "SELECT path FROM pending_sync") -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        return sorted(r[0] for r in conn.execute(sql).fetchall())
    finally:
        conn.close()


def _wait_until(cond, timeout: float = 5.0, interval: float = 0.02) -> bool:
    """Poll `cond` until truthy or timeout (post-pass cleanup races the
    `done` event, which fires mid-pass inside incremental_update)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(interval)
    return cond()


# ---------------------------------------------------------------------------
# Core flow: event -> pending_sync (both forms) -> incremental_update -> clear
# ---------------------------------------------------------------------------


class TestUpdatePass:
    def test_event_marks_pending_sync_then_clears(
        self, tmp_path, monkeypatch, fake_watchdog
    ):
        """One save: rows for BOTH path forms exist DURING incremental_update
        (the staleness-banner window), and are gone after the pass."""
        ws, repo = _make_corpus(tmp_path)
        db = _make_db(tmp_path)
        src = repo / "src" / "a.py"
        src.write_text("def hello():\n    return 1\n")

        done = threading.Event()
        seen: dict = {}

        def fake_incremental_update(**kwargs):
            seen["kwargs"] = kwargs
            seen["rows"] = _rows(db)
            done.set()
            return {"files_reindexed": 1}

        monkeypatch.setattr(
            "cairn.graph.incremental.incremental_update", fake_incremental_update
        )

        svc = FileWatcherService(str(ws), db, debounce_s=0.05)
        assert svc.start() is True
        try:
            handler = _DebouncingHandler(svc)
            handler.dispatch(_event(str(src)))
            assert done.wait(timeout=5), "update pass never ran"
            assert seen["kwargs"] == {"workspace": str(ws), "db_path": db}
            # Both the repo-relative form (what files.path / the staleness
            # banner use) and the absolute form (the exact string
            # reindex_paths reconstructs) were visible to concurrent readers
            # while the pass ran.
            assert seen["rows"] == sorted([str(src), "src/a.py"]), seen["rows"]
            # After the pass completes, no rows linger. (done fires mid-pass,
            # inside incremental_update -- the cleanup runs right after.)
            assert _wait_until(lambda: _rows(db) == [])
        finally:
            svc.stop()

    def test_two_quick_events_coalesce_into_one_pass(
        self, tmp_path, monkeypatch, fake_watchdog
    ):
        ws, repo = _make_corpus(tmp_path)
        db = _make_db(tmp_path)
        a = repo / "src" / "a.py"
        a.write_text("def a():\n    return 1\n")
        b = repo / "src" / "b.py"
        b.write_text("def b():\n    return 2\n")

        done = threading.Event()
        calls: list[list[str]] = []

        def fake_incremental_update(**kwargs):
            calls.append(_rows(db))
            done.set()
            return {"files_reindexed": 2}

        monkeypatch.setattr(
            "cairn.graph.incremental.incremental_update", fake_incremental_update
        )

        svc = FileWatcherService(str(ws), db, debounce_s=0.2)
        assert svc.start() is True
        try:
            handler = _DebouncingHandler(svc)
            handler.dispatch(_event(str(a)))
            time.sleep(0.03)  # well inside the window
            handler.dispatch(_event(str(b)))
            assert done.wait(timeout=5), "coalesced pass never ran"
            time.sleep(0.45)  # beyond the window: a second pass must NOT fire
            assert len(calls) == 1, f"expected ONE coalesced pass, got {len(calls)}"
            assert calls[0] == sorted([str(a), str(b), "src/a.py", "src/b.py"])
            assert _wait_until(lambda: _rows(db) == [])
        finally:
            svc.stop()

    def test_non_source_event_never_touches_db(
        self, tmp_path, monkeypatch, fake_watchdog
    ):
        """A .md save produces no pending_sync row and no update pass."""
        ws, repo = _make_corpus(tmp_path)
        db = _make_db(tmp_path)

        def boom(**kwargs):  # pragma: no cover - must not be reached
            raise AssertionError("incremental_update must not run for non-source")

        monkeypatch.setattr("cairn.graph.incremental.incremental_update", boom)

        svc = FileWatcherService(str(ws), db, debounce_s=0.05)
        assert svc.start() is True
        try:
            svc._on_event(_event(str(repo / "notes.md")))
            time.sleep(0.35)  # past the window
            assert _rows(db) == []
        finally:
            svc.stop()

    def test_gitignore_event_invalidates_scanner_cache(
        self, tmp_path, monkeypatch, fake_watchdog
    ):
        ws, repo = _make_corpus(tmp_path)
        db = _make_db(tmp_path)

        monkeypatch.setattr(
            "cairn.graph.incremental.incremental_update", lambda **kw: {"files_reindexed": 0}
        )
        # Seed the scanner cache as if a previous scan compiled the repo's
        # gitignore rules; the event must drop the entry.
        scanner_mod._gitignore_cache[str(repo)] = [("sentinel", None)]
        try:
            svc = FileWatcherService(str(ws), db, debounce_s=0.05)
            assert svc.start() is True
            try:
                svc._on_event(_event(str(repo / ".gitignore")))
                time.sleep(0.35)
                assert str(repo) not in scanner_mod._gitignore_cache
                assert _rows(db) == []  # .gitignore itself is never marked pending
            finally:
                svc.stop()
        finally:
            scanner_mod._gitignore_cache.pop(str(repo), None)


# ---------------------------------------------------------------------------
# Kill switches and degradation
# ---------------------------------------------------------------------------


class TestDisabling:
    def test_read_only_mode_never_starts(self, tmp_path, monkeypatch, fake_watchdog):
        """CAIRN_READ_ONLY: start() refuses; no observer, no writes ever."""
        ws, repo = _make_corpus(tmp_path)
        db = _make_db(tmp_path)
        monkeypatch.setenv("CAIRN_READ_ONLY", "1")

        svc = FileWatcherService(str(ws), db)
        assert svc.start() is False
        assert FakeObserver.instances == []
        # Even a direct event offer goes nowhere: the service is not running.
        svc._on_event(_event(str(repo / "src" / "a.py")))
        time.sleep(0.15)
        assert _rows(db) == []

    def test_missing_watchdog_is_logged_noop(self, tmp_path, monkeypatch, caplog):
        """No watchdog importable: no exception, one info line, no observer."""
        ws, repo = _make_corpus(tmp_path)
        db = _make_db(tmp_path)
        # None in sys.modules makes `from watchdog... import` raise ImportError.
        monkeypatch.setitem(sys.modules, "watchdog", None)
        monkeypatch.setitem(sys.modules, "watchdog.observers", None)

        svc = FileWatcherService(str(ws), db)
        with caplog.at_level(logging.INFO, logger="cairn"):
            assert svc.start() is False
        infos = [r for r in caplog.records if r.levelno == logging.INFO]
        assert len(infos) == 1, "exactly one info line, not a stack trace"
        assert "watch" in infos[0].message.lower()

    def test_cairn_watch_zero_hard_disables(self, tmp_path, monkeypatch, fake_watchdog):
        ws, repo = _make_corpus(tmp_path)
        db = _make_db(tmp_path)
        monkeypatch.setenv("CAIRN_WATCH", "0")

        svc = FileWatcherService(str(ws), db)
        assert svc.start() is False
        assert FakeObserver.instances == []


# ---------------------------------------------------------------------------
# Lock contention absorption
# ---------------------------------------------------------------------------


class TestLockContention:
    def test_runtime_error_absorbed_logged_once_service_survives(
        self, tmp_path, monkeypatch, fake_watchdog, caplog
    ):
        """A concurrent CLI build holds the schema build lock -> the update
        raises RuntimeError. It must be absorbed, warned about exactly once
        (per contiguous failure run), the pending rows kept (banner stays
        hot), and the service alive for the next batch."""
        ws, repo = _make_corpus(tmp_path)
        db = _make_db(tmp_path)
        src = repo / "src" / "a.py"
        src.write_text("def hello():\n    return 1\n")

        done = threading.Event()
        mode = {"fail": True}
        calls = {"n": 0}
        seen_rows: list[list[str]] = []

        def fake_incremental_update(**kwargs):
            calls["n"] += 1
            seen_rows.append(_rows(db))
            done.set()
            if mode["fail"]:
                raise RuntimeError(
                    "Cannot acquire build lock: another build or update is "
                    "already in progress. Wait for it to finish and retry."
                )
            return {"files_reindexed": 1}

        monkeypatch.setattr(
            "cairn.graph.incremental.incremental_update", fake_incremental_update
        )

        caplog.set_level(logging.WARNING, logger="cairn")
        svc = FileWatcherService(str(ws), db, debounce_s=0.05)
        assert svc.start() is True
        try:
            # Batch 1: lock held -> absorbed.
            svc._on_event(_event(str(src)))
            assert done.wait(timeout=5)
            assert calls["n"] == 1
            # Rows stay: the staleness banner keeps firing until a retry lands.
            assert seen_rows[0] == sorted([str(src), "src/a.py"])
            assert _rows(db) == sorted([str(src), "src/a.py"])
            warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
            assert len(warnings) == 1, "first failure logs exactly one warning"

            # Batch 2: still failing -> latched, no second warning, service alive.
            done.clear()
            mode["fail"] = True
            svc._on_event(_event(str(src)))
            assert done.wait(timeout=5)
            assert calls["n"] == 2
            warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
            assert len(warnings) == 1, "contiguous failures do not re-warn"

            # Batch 3: lock released -> pass succeeds, rows cleared, latch reset.
            done.clear()
            mode["fail"] = False
            svc._on_event(_event(str(src)))
            assert done.wait(timeout=5)
            assert calls["n"] == 3
            assert _wait_until(lambda: _rows(db) == [])
        finally:
            svc.stop()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_start_is_idempotent_and_stop_joins(self, tmp_path, fake_watchdog):
        ws, repo = _make_corpus(tmp_path)
        db = _make_db(tmp_path)

        svc = FileWatcherService(str(ws), db)
        assert svc.start() is True
        assert svc.start() is True  # idempotent: no second observer
        assert len(FakeObserver.instances) == 1

        observer = FakeObserver.instances[0]
        assert observer.started
        assert observer.daemon is True, "observer thread must be daemonized"
        assert observer.schedules and observer.schedules[0][1] == str(repo)
        assert observer.schedules[0][2] is True  # recursive

        svc.stop()
        assert observer.stopped and observer.joined
        svc.stop()  # idempotent

    def test_stop_cancels_pending_flush(self, tmp_path, monkeypatch, fake_watchdog):
        """An event inside the debounce window is dropped on stop (boot
        catch-up covers it next serve), with no DB churn."""
        ws, repo = _make_corpus(tmp_path)
        db = _make_db(tmp_path)

        def boom(**kwargs):  # pragma: no cover - must not be reached
            raise AssertionError("flush must not run after stop()")

        monkeypatch.setattr("cairn.graph.incremental.incremental_update", boom)

        svc = FileWatcherService(str(ws), db, debounce_s=0.1)
        assert svc.start() is True
        svc._on_event(_event(str(repo / "src" / "a.py")))
        svc.stop()
        time.sleep(0.4)
        assert _rows(db) == []


# ---------------------------------------------------------------------------
# Real-watchdog integration (only when the [watch] extra is installed)
# ---------------------------------------------------------------------------

_watchdog_available = importlib.util.find_spec("watchdog") is not None


@pytest.mark.skipif(not _watchdog_available, reason="watchdog not installed")
class TestRealObserverIntegration:
    def test_real_save_reindexes_within_window(self, tmp_path):
        """End-to-end shape: a real Observer thread watches the repo, a real
        file save lands, the real incremental_update parses it, and the graph
        + staleness state converge."""
        ws, repo = _make_corpus(tmp_path)
        db = _make_db(tmp_path)
        # A real deployment has run `cairn build` first: the repos row must
        # exist or reindex_paths' files insert fails the repo FK.
        conn = sqlite3.connect(db)
        conn.execute(
            "INSERT INTO repos (id, name, path) VALUES (?, ?, ?)",
            ("repo", "repo", str(repo)),
        )
        conn.commit()
        conn.close()

        svc = FileWatcherService(str(ws), db, debounce_s=0.2)
        assert svc.start() is True
        try:
            new_file = repo / "src" / "new_module.py"
            new_file.write_text("def world():\n    return 42\n")

            deadline = time.monotonic() + 15.0  # FSEvents latency headroom
            while time.monotonic() < deadline:
                conn = sqlite3.connect(db)
                try:
                    row = conn.execute(
                        "SELECT path FROM files WHERE path = ?", ("src/new_module.py",)
                    ).fetchone()
                    pending = conn.execute("SELECT COUNT(*) FROM pending_sync").fetchone()[0]
                finally:
                    conn.close()
                if row and pending == 0:
                    break
                time.sleep(0.25)
            else:
                pytest.fail("watched save was not reindexed within 15s")

            conn = sqlite3.connect(db)
            try:
                syms = conn.execute(
                    "SELECT name FROM symbols WHERE name = 'world'"
                ).fetchall()
            finally:
                conn.close()
            assert syms, "parsed symbol from the watched file must be in the graph"
        finally:
            svc.stop()
