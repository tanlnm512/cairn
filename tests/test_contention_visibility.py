"""Tests for lock-contention visibility (task T03).

Every ``except sqlite3.OperationalError`` swallow site now calls
``cairn.graph.schema.note_contention(site)`` so a silently-absorbed "database is
locked" surfaces at least once per (process, site) instead of vanishing. This
mirrors the process-global one-time-warning pattern of
``warn_hash_fallback_once`` (graph/embeddings.py) and ``warn_ann_fallback_once``
(graph/ann_index.py).

Covers:
  1. ``note_contention`` -- rate-limited: warns once per site, distinct sites
     warn independently.
  2. Warning message carries the site tag + remediation context (busy_timeout,
     ``cairn serve start`` single-daemon mode).
  3. Thread-safe: concurrent callers from flusher threads warn exactly once.
  4. Integration: a simulated ``OperationalError`` at the ``ann_query`` swallow
     site produces exactly one warning across two calls and leaves the swallow
     semantics unchanged (still returns ``None``).
"""
from __future__ import annotations

import logging
import sqlite3
import threading

import pytest

from cairn.graph import schema
from cairn.graph.schema import note_contention


@pytest.fixture(autouse=True)
def _reset_contention_guard():
    """Reset the process-global one-time-warning guard around each test.

    ``_CONTENTION_WARNED`` is process-global and never reset in production
    (a contention event is sticky for the process lifetime), so tests that
    assert "fires once" must clear it to stay repeatable within the same
    process. Mirrors the ``_ANN_FALLBACK_WARNED`` reset in
    test_ann_fallback_warning.py and ``_HASH_FALLBACK_WARNED`` in
    test_embedding_backend_quality.py.
    """
    schema._CONTENTION_WARNED.clear()
    yield
    schema._CONTENTION_WARNED.clear()


def _warning_records(caplog):
    return [r for r in caplog.records if r.levelno == logging.WARNING]


# ---------------------------------------------------------------------------
# 1. note_contention -- rate-limited, once per site, independent across sites
# ---------------------------------------------------------------------------


def test_warns_once_per_site_independent_sites(caplog):
    """Same site warns once; a different site warns independently."""
    caplog.set_level(logging.WARNING, logger="cairn.graph.schema")

    note_contention("test.site_a")
    note_contention("test.site_a")  # silent -- already warned this process
    note_contention("test.site_a")  # silent
    note_contention("test.site_b")  # warns -- distinct site
    note_contention("test.site_b")  # silent

    warnings = _warning_records(caplog)
    assert len(warnings) == 2, "one warning per (process, site)"
    # record.args[0] is the site tag (the %s in the format string).
    assert [w.args[0] for w in warnings] == ["test.site_a", "test.site_b"]


def test_guard_persists_until_reset(caplog):
    """The guard is sticky: a second call after the first is a true no-op
    (not just 'no log' -- the dict records the site as already warned)."""
    caplog.set_level(logging.WARNING, logger="cairn.graph.schema")

    note_contention("test.sticky")
    assert schema._CONTENTION_WARNED.get("test.sticky") is True
    assert len(_warning_records(caplog)) == 1

    note_contention("test.sticky")  # no-op
    assert schema._CONTENTION_WARNED.get("test.sticky") is True
    assert len(_warning_records(caplog)) == 1, "second call produces no new warning"


# ---------------------------------------------------------------------------
# 2. Warning message content (site tag + remediation context)
# ---------------------------------------------------------------------------


def test_warning_message_includes_site_and_remediation(caplog):
    """The warning names the site and carries the remediation context."""
    caplog.set_level(logging.WARNING, logger="cairn.graph.schema")

    note_contention("module.function")

    warnings = _warning_records(caplog)
    assert len(warnings) == 1
    msg = warnings[0].getMessage()
    assert "module.function" in msg, "site tag present"
    assert "busy_timeout" in msg, "busy_timeout remediation context present"
    assert "another cairn process holds the DB" in msg, "contention cause named"
    assert "cairn serve start" in msg, "single-daemon-mode hint present"


# ---------------------------------------------------------------------------
# 3. Thread-safety -- concurrent callers warn exactly once
# ---------------------------------------------------------------------------


def test_thread_safe_concurrent_callers_warn_once(caplog):
    """Swallow sites are reachable from flusher daemon threads; the lock-guarded
    dict must serialize them so a burst of concurrent hits yields one warning."""
    caplog.set_level(logging.WARNING, logger="cairn.graph.schema")

    site = "test.concurrent"
    n = 32
    barrier = threading.Barrier(n)

    def worker():
        barrier.wait()
        note_contention(site)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    warnings = _warning_records(caplog)
    assert len(warnings) == 1, f"exactly one warning under {n} concurrent callers"
    assert warnings[0].args[0] == site
    assert schema._CONTENTION_WARNED.get(site) is True


# ---------------------------------------------------------------------------
# 4. Integration -- simulated OperationalError at the ann_query swallow site
#    (the representative site named in the task). Verifies the real call path
#    warns exactly once across two calls and leaves semantics unchanged.
# ---------------------------------------------------------------------------


def test_ann_query_contention_warns_once_semantics_unchanged(monkeypatch, caplog):
    """A locked vec0 query is swallowed (ann_query returns None) and surfaces
    exactly one contention warning across two calls -- the second is silent."""
    from cairn.graph import ann_index as ann

    caplog.set_level(logging.WARNING, logger="cairn.graph.schema")

    # Reach the ``conn.execute(...)`` that the except wraps by making the two
    # preflight checks pass without needing sqlite-vec or a built index.
    monkeypatch.setattr(ann, "try_load", lambda conn: True)
    monkeypatch.setattr(ann, "index_exists", lambda conn, model: True)

    class _LockedConn:
        """Stand-in connection whose query raises exactly as a locked vec0
        MATCH would. ``enable_load_extension`` is never reached because
        ``try_load`` is patched, so this only needs ``execute``."""

        def execute(self, *args, **kwargs):
            raise sqlite3.OperationalError("database is locked")

    conn = _LockedConn()
    first = ann.ann_query(conn, "all-MiniLM-L6-v2", b"\x00" * 16, 5)
    second = ann.ann_query(conn, "all-MiniLM-L6-v2", b"\x00" * 16, 5)

    # Swallow semantics unchanged: ANN unavailable -> callers fall back, not
    # "zero results". Both calls must return None (not raise).
    assert first is None, "ann_query swallows OperationalError -> None"
    assert second is None, "second call still returns None (no raise)"

    warnings = _warning_records(caplog)
    assert len(warnings) == 1, "exactly one contention warning per site per process"
    assert "ann_index.ann_query" in warnings[0].getMessage()


def test_second_swallow_site_is_independent_of_ann(monkeypatch, caplog):
    """A different swallow site warns independently of the ann_query site,
    proving the guard is keyed per-site across modules (the note_contention
    imported into ann_index shares schema._CONTENTION_WARNED)."""
    from cairn.graph import ann_index as ann

    caplog.set_level(logging.WARNING, logger="cairn.graph.schema")

    monkeypatch.setattr(ann, "try_load", lambda conn: True)
    monkeypatch.setattr(ann, "index_exists", lambda conn, model: True)

    class _LockedConn:
        def execute(self, *args, **kwargs):
            raise sqlite3.OperationalError("database is locked")

    # First: ann_query site warns.
    assert ann.ann_query(_LockedConn(), "m", b"\x00" * 16, 5) is None
    # A distinct site then warns independently.
    note_contention("other.site")

    warnings = _warning_records(caplog)
    assert len(warnings) == 2
    assert {w.args[0] for w in warnings} == {"ann_index.ann_query", "other.site"}
