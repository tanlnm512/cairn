"""Tests for the ANN fallback one-time warning (task T02).

Mirrors tests/test_embedding_backend_quality.py's pattern for the hash fallback
warning: a process-global guard fires the degradation notice at most once per
process, and ``CAIRN_ANN_BACKEND=off`` (an explicit choice) never warns.

Covers:
  1. warn_ann_fallback_once -- rate-limited, one warning per process.
  2. Explicit CAIRN_ANN_BACKEND=off stays silent (informed choice, not a
     silent degradation).
  3. try_load's failure branches surface the right reason class
     ("sqlite-vec not installed" vs "load failed").
  4. semantic_search with ANN unavailable still returns correct results
     (brute-force scan behavior unchanged) and warns exactly once.

These tests must NOT skip when sqlite-vec is installed: they monkeypatch the
import to fail so the unavailable/fallback path is exercised regardless of the
host environment.
"""
from __future__ import annotations

import logging
import sqlite3
import sys

import pytest

from cairn.graph import ann_index as ann


@pytest.fixture(autouse=True)
def _reset_ann_guard():
    """Reset the process-global one-time-warning guard around each test.

    ``_ANN_FALLBACK_WARNED`` is process-global and never reset in production
    (ANN availability doesn't change mid-process), so tests that assert "fires
    once" must reset it to stay repeatable within the same process. Mirrors the
    ``_HASH_FALLBACK_WARNED`` reset in test_embedding_backend_quality.py.
    """
    ann._ANN_FALLBACK_WARNED = False
    yield
    ann._ANN_FALLBACK_WARNED = False


def _force_sqlite_vec_unavailable(monkeypatch):
    """Make ``import sqlite_vec`` raise ImportError for the test's duration.

    A ``None`` entry in ``sys.modules`` is the documented way to force
    ImportError on import, so this works whether or not sqlite-vec is actually
    installed in the host env.
    """
    monkeypatch.setitem(sys.modules, "sqlite_vec", None)


def _seed_symbols(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO repos (id, name, path) VALUES ('test', 'test', '/tmp/test')"
    )
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES (1, 'test', '/tmp/test/Api.kt', 'kotlin')"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, docstring, line_start, line_end) "
        "VALUES (1, 1, 'safeApiCall', 'function', 'xyz.safeApiCall', 'Retries a network call with backoff.', 1, 10)"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, docstring, line_start, line_end) "
        "VALUES (2, 1, 'formatDate', 'function', 'xyz.formatDate', 'Formats a date for display.', 12, 20)"
    )
    conn.commit()


# ---------------------------------------------------------------------------
# 1. warn_ann_fallback_once -- rate-limited warning
# ---------------------------------------------------------------------------


def test_warns_once_then_silent_when_sqlite_vec_unavailable(monkeypatch, caplog):
    """First call warns once with reason + remediation; subsequent calls silent."""
    _force_sqlite_vec_unavailable(monkeypatch)
    monkeypatch.delenv("CAIRN_ANN_BACKEND", raising=False)

    logger = logging.getLogger("cairn.tests.ann_fallback_warning")
    caplog.set_level(logging.WARNING, logger="cairn.tests.ann_fallback_warning")

    ann.warn_ann_fallback_once(logger, context="semantic_search")
    ann.warn_ann_fallback_once(logger, context="semantic_search")
    ann.warn_ann_fallback_once(logger, context="explore")

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, "process-global guard: at most one warning per process"
    msg = warnings[0].getMessage()
    assert "brute-force" in msg.lower()
    assert "cairn embed --install-deps" in msg, "remediation hint present"
    assert "semantic_search" in msg, "first caller's context recorded"
    assert "sqlite-vec not installed" in msg, "reason class surfaced"


def test_silent_when_explicitly_disabled(monkeypatch, caplog):
    """CAIRN_ANN_BACKEND=off is an explicit choice -- it must never warn."""
    _force_sqlite_vec_unavailable(monkeypatch)
    monkeypatch.setenv("CAIRN_ANN_BACKEND", "off")

    logger = logging.getLogger("cairn.tests.ann_fallback_warning")
    caplog.set_level(logging.WARNING, logger="cairn.tests.ann_fallback_warning")

    ann.warn_ann_fallback_once(logger, context="semantic_search")
    ann.warn_ann_fallback_once(logger, context="semantic_search")

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == [], "explicit CAIRN_ANN_BACKEND=off must never warn"


def test_try_load_importerror_warns_not_installed(monkeypatch, caplog):
    """try_load's ImportError branch surfaces 'sqlite-vec not installed'."""
    _force_sqlite_vec_unavailable(monkeypatch)
    monkeypatch.delenv("CAIRN_ANN_BACKEND", raising=False)

    caplog.set_level(logging.WARNING, logger="cairn.graph.ann_index")

    conn = sqlite3.connect(":memory:")
    try:
        assert ann.try_load(conn) is False
    finally:
        conn.close()

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "sqlite-vec not installed" in warnings[0].getMessage()


def test_try_load_failure_warns_load_failed(monkeypatch, caplog):
    """When sqlite-vec is importable but the extension fails to load, try_load
    warns once with the 'load failed' reason."""
    sqlite_vec = pytest.importorskip(
        "sqlite_vec",
        reason="needs sqlite-vec installed to exercise the load-failure path",
    )
    monkeypatch.setenv("CAIRN_ANN_BACKEND", "sqlite-vec")

    caplog.set_level(logging.WARNING, logger="cairn.graph.ann_index")

    def _boom(_conn):
        raise RuntimeError("simulated load failure")

    monkeypatch.setattr(sqlite_vec, "load", _boom)
    conn = sqlite3.connect(":memory:")
    try:
        assert ann.try_load(conn) is False
    finally:
        conn.close()

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "load failed" in warnings[0].getMessage()


# ---------------------------------------------------------------------------
# 2. semantic_search end-to-end with ANN unavailable
# ---------------------------------------------------------------------------


def test_semantic_search_returns_correct_results_when_ann_unavailable(
    monkeypatch, fresh_db, hash_backend, caplog
):
    """Behavior unchanged when sqlite-vec is unavailable: the brute-force scan
    still returns the right symbols, and the fallback warns exactly once across
    two queries (process-global guard)."""
    _force_sqlite_vec_unavailable(monkeypatch)
    monkeypatch.delenv("CAIRN_ANN_BACKEND", raising=False)
    from cairn.graph import embeddings as emb
    from cairn.graph.queries import semantic_search

    emb.reset_backend_cache()
    _seed_symbols(fresh_db)
    emb.embed_all(fresh_db)

    caplog.set_level(logging.WARNING, logger="cairn.graph.semantic")
    first = semantic_search(fresh_db, "safeApiCall", limit=5, threshold=-1.0)
    second = semantic_search(fresh_db, "formatDate", limit=5, threshold=-1.0)

    # Results correct both times (the brute-force scan is exact, not approximate).
    assert {r["id"] for r in first} == {"1", "2"}
    assert {r["id"] for r in second} == {"1", "2"}

    # Exactly one fallback warning across both queries (process-global guard).
    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "brute-force" in r.getMessage().lower()
    ]
    assert len(warnings) == 1, "first query warns once, subsequent query silent"


def test_semantic_search_never_warns_when_ann_explicitly_off(
    monkeypatch, fresh_db, hash_backend, caplog
):
    """CAIRN_ANN_BACKEND=off end-to-end: brute-force still works, no warning."""
    _force_sqlite_vec_unavailable(monkeypatch)
    monkeypatch.setenv("CAIRN_ANN_BACKEND", "off")
    from cairn.graph import embeddings as emb
    from cairn.graph.queries import semantic_search

    emb.reset_backend_cache()
    _seed_symbols(fresh_db)
    emb.embed_all(fresh_db)

    caplog.set_level(logging.WARNING, logger="cairn.graph.semantic")
    results = semantic_search(fresh_db, "safeApiCall", limit=5, threshold=-1.0)

    assert results, "brute-force still returns results"
    ann_warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "brute-force" in r.getMessage().lower()
    ]
    assert ann_warnings == [], "explicit opt-out must not warn"
