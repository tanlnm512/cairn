"""Tests for H2: embed_all commit failure tracking.

Verifies the two load-bearing behaviors of the embed_all summary dict:
1. On success: ``embedded`` reflects committed rows, ``failed_batches`` is 0.
2. On commit failure: ``failed_batches`` is incremented and ``embedded`` is 0.

The happy-path summary keys/counts are asserted by
test_commit_success_counts_correctly; the failure path is exercised only by
test_commit_failure_simulation, which wraps the connection to fail on commit.

Uses the hash_backend fixture to avoid the torch dependency.
"""
from __future__ import annotations

import sqlite3

import pytest

pytestmark = pytest.mark.usefixtures("hash_backend")


def _seed_symbols(conn: sqlite3.Connection, count: int = 5) -> None:
    """Seed count symbols for embedding tests."""
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('test', 'test', '/tmp/test')")
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES (1, 'test', '/tmp/test/Api.kt', 'kotlin')"
    )
    for i in range(1, count + 1):
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, qualified_name, docstring, line_start, line_end) "
            "VALUES (?, 1, ?, 'function', ?, ?, 1, 10)",
            (str(i), f'func{i}', f'test.func{i}', f'Docstring {i}'),
        )
    conn.commit()


def test_commit_success_counts_correctly(fresh_db):
    """When commit succeeds, embedded count reflects committed rows and the
    summary carries embedded/attempted/failed_batches with failed_batches=0.
    Covers the happy path + the summary-dict contract for the success branch."""
    from cairn.graph import embeddings as emb

    _seed_symbols(fresh_db, count=3)
    summary = emb.embed_all(fresh_db)

    assert summary["embedded"] == 3
    assert summary["embedded"] == summary["attempted"]
    assert summary["failed_batches"] == 0
    # contract: all three keys present and integral
    assert isinstance(summary["embedded"], int)
    assert isinstance(summary["attempted"], int)
    assert isinstance(summary["failed_batches"], int)


def test_commit_failure_simulation(fresh_db):
    """Simulate commit failure by using a connection wrapper.

    This test actually triggers the failure path by wrapping the connection
    to fail on commit calls.
    """
    from cairn.graph import embeddings as emb

    _seed_symbols(fresh_db, count=3)

    class FailingConnection:
        def __init__(self, conn):
            self._conn = conn
            self.commit_count = 0

        def execute(self, *args, **kwargs):
            return self._conn.execute(*args, **kwargs)

        def commit(self):
            self.commit_count += 1
            if self.commit_count == 1:
                import sqlite3
                raise sqlite3.OperationalError("database is locked")
            return self._conn.commit()

        def cursor(self):
            return self._conn.cursor()

        def __getattr__(self, name):
            return getattr(self._conn, name)

    failing_conn = FailingConnection(fresh_db)
    summary = emb.embed_all(failing_conn)

    # The first commit failed, so embedded should be 0
    assert summary["embedded"] == 0, "embedded should be 0 when commit fails"
    assert summary["failed_batches"] == 1, "failed_batches should be incremented"
    assert summary["attempted"] == 3, "attempted should still reflect total symbols"


def test_reap_commit_failure_does_not_crash_the_pass(fresh_db):
    """The reap and term-df flushes sit under the same contention guard as
    the batch flush: a still-locked DB degrades the summary instead of
    raising (which would roll back every buffered batch)."""
    from cairn.graph import embeddings as emb

    _seed_symbols(fresh_db, count=3)

    class LockedThenOpenConnection:
        def __init__(self, conn):
            self._conn = conn
            self.commit_count = 0

        def execute(self, *args, **kwargs):
            return self._conn.execute(*args, **kwargs)

        def commit(self):
            self.commit_count += 1
            if self.commit_count <= 2:  # batch flush, then the reap flush
                raise sqlite3.OperationalError("database is locked")
            return self._conn.commit()

        def cursor(self):
            return self._conn.cursor()

        def __getattr__(self, name):
            return getattr(self._conn, name)

    locked_conn = LockedThenOpenConnection(fresh_db)
    summary = emb.embed_all(locked_conn)

    assert summary["failed_batches"] == 1, "batch flush absorbed"
    assert summary["reaped"] == 0, "reap flush absorbed, nothing committed to count"
    # a later commit (term-df rebuild / final flush) flushes the buffered batches
    assert emb.embed_count(fresh_db) == 3


def test_summary_panel_reports_failed_batches(monkeypatch, tmp_path):
    """The CLI summary panel surfaces failed_batches: batches lost to WAL
    contention are visible instead of silently dropped."""
    from click.testing import CliRunner

    from cairn.cli import main as cli_main
    from cairn.graph import embeddings as emb

    def fake_embed_all(conn, **kwargs):
        return {
            "model": emb.current_model(),
            "embedded": 0,
            "attempted": 2,
            "failed_batches": 2,
            "skipped": 0,
            "total": 2,
            "reaped": 0,
        }

    monkeypatch.setenv("CAIRN_EMBED_BACKEND", "hash")
    monkeypatch.setenv("CAIRN_ANN_BACKEND", "off")
    emb.reset_backend_cache()
    monkeypatch.setattr(emb, "embed_all", fake_embed_all)

    result = CliRunner().invoke(
        cli_main, ["embed", "--db", str(tmp_path / "db.sqlite")],
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "failed batches 2" in " ".join(result.output.split())
