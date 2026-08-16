"""Thread-local read-connection pooling in the MCP server (perf phase P5).

Covers the three correctness guards: reuse per (thread, db path), reopen on
atomic file swap (full builds ``os.replace`` the DB), repoint on CAIRN_DB
change, and the CAIRN_CONN_POOL=0 kill switch.
"""
from __future__ import annotations

import os
import sqlite3

import pytest

from cairn.graph import schema
from cairn.mcp_server import _server_core
from cairn.mcp_server._server_core import _conn, _reset_conn_pool


@pytest.fixture(autouse=True)
def pool_env(monkeypatch):
    """Count underlying opens; always start and end with an empty pool."""
    _reset_conn_pool()
    counter = {"opens": 0}
    real_get_db = schema.get_db

    def counting_get_db(db_path=None, *args, **kwargs):
        counter["opens"] += 1
        return real_get_db(db_path, *args, **kwargs)

    monkeypatch.setattr(_server_core, "get_db", counting_get_db)
    yield counter
    _reset_conn_pool()


def _touch(db_path) -> None:
    """One query through the wrapper proves the connection is usable."""
    c = _conn()
    try:
        c.execute("SELECT 1").fetchone()
    finally:
        c.close()


def test_pool_reuses_connection(tmp_path, monkeypatch, pool_env):
    db = tmp_path / "a.kg"
    monkeypatch.setenv("CAIRN_DB", str(db))
    _touch(db)
    _touch(db)
    _touch(db)
    assert pool_env["opens"] == 1


def test_wrapper_close_releases_without_closing(tmp_path, monkeypatch, pool_env):
    db = tmp_path / "a.kg"
    monkeypatch.setenv("CAIRN_DB", str(db))
    c1 = _conn()
    c1.close()  # release -- must NOT close the underlying connection
    c2 = _conn()
    try:
        assert c2 is not c1
        c2.execute("SELECT 1").fetchone()  # same live underlying connection
    finally:
        c2.close()
    assert pool_env["opens"] == 1


def test_pool_reopens_after_atomic_swap(tmp_path, monkeypatch, pool_env):
    db = tmp_path / "a.kg"
    monkeypatch.setenv("CAIRN_DB", str(db))
    _touch(db)

    # A full build's swap_db_file: build elsewhere, os.replace over the path.
    other = tmp_path / "b.kg"
    conn = sqlite3.connect(str(other))
    conn.execute("CREATE TABLE swap_marker (x)")
    conn.commit()
    conn.close()
    os.replace(other, db)

    _touch(db)
    assert pool_env["opens"] == 2  # inode changed -> reopened


def test_pool_repoints_when_db_env_changes(tmp_path, monkeypatch, pool_env):
    a = tmp_path / "a.kg"
    b = tmp_path / "b.kg"
    monkeypatch.setenv("CAIRN_DB", str(a))
    _touch(a)
    monkeypatch.setenv("CAIRN_DB", str(b))
    _touch(b)
    assert pool_env["opens"] == 2
    # One entry per thread: the evicted store's connection is gone.
    cache = getattr(_server_core._conn_tls, "by_path", {})
    assert list(cache.keys()) == [str(b)]


def test_kill_switch_disables_pooling(tmp_path, monkeypatch, pool_env):
    db = tmp_path / "a.kg"
    monkeypatch.setenv("CAIRN_DB", str(db))
    monkeypatch.setenv("CAIRN_CONN_POOL", "0")
    c1 = _conn()
    assert isinstance(c1, sqlite3.Connection)  # raw connection, not a wrapper
    c1.close()
    _touch(db)
    _touch(db)
    assert pool_env["opens"] == 3  # every call opened fresh


def test_pooled_read_only_conn_rejects_writes(tmp_path, monkeypatch, pool_env):
    """P5.2: a pooled read connection under CAIRN_READ_ONLY stays read-only.

    The pool must not silently upgrade a read-only server's connection to
    writable just because it is long-lived; write tools keep using _rw_conn()
    per call by design.
    """
    db = tmp_path / "ro.kg"
    # mode=ro cannot create the file; materialize schema once, writable.
    from cairn.graph.schema import get_db as _rw_open

    _rw_open(str(db)).close()
    monkeypatch.setenv("CAIRN_DB", str(db))
    monkeypatch.setenv("CAIRN_READ_ONLY", "1")
    _reset_conn_pool()
    c = _conn()
    try:
        c.execute("SELECT 1").fetchone()  # reads work
        with pytest.raises(sqlite3.OperationalError):
            c.execute("CREATE TABLE _should_fail (x)")
        # Same underlying connection on the next call -- still read-only.
        c.close()
        c2 = _conn()
        try:
            with pytest.raises(sqlite3.OperationalError):
                c2.execute("CREATE TABLE _should_fail2 (x)")
        finally:
            c2.close()
    finally:
        _reset_conn_pool()


def test_concurrent_update_with_pooled_reads_zero_contention(
    tmp_path, monkeypatch
):
    """P5.2 scenario: a real incremental_update running beside pooled reads.

    The deployment shape the pool changed: one long-lived server process
    issuing tool reads through pooled connections while a CLI-side
    `cairn update` writes under the build lock. WAL lets readers proceed
    without blocking; the assertion is that NO lock_contention event is
    recorded (busy_timeout absorbed everything) and every pooled read
    still returned rows throughout the update window.
    """
    import threading
    import time

    from cairn.graph.builder import build_graph
    from cairn.graph.incremental import incremental_update
    from cairn.graph.queries import find_definition

    repo = tmp_path / "wrepo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "m_a.py").write_text("def alpha():\n    return 1\n")
    (repo / "m_b.py").write_text("def beta():\n    return 2\n")
    db = tmp_path / "c.kg"
    build_graph(workspace=str(repo), db_path=str(db))
    monkeypatch.setenv("CAIRN_DB", str(db))
    _reset_conn_pool()

    errors: list[Exception] = []
    empty_reads = 0
    reads = 0
    stop = threading.Event()

    def reader():
        nonlocal empty_reads, reads
        while not stop.is_set():
            try:
                c = _conn()
                try:
                    rows = find_definition(c, "alpha", limit=5)
                finally:
                    c.close()
                reads += 1
                if not rows:
                    empty_reads += 1
            except Exception as e:  # noqa: BLE001 - recorded, asserted below
                errors.append(e)
            time.sleep(0.005)

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    try:
        time.sleep(0.05)  # let the pooled reader warm up
        (repo / "m_b.py").write_text(
            "def beta():\n    return 2\n\n\ndef gamma_live():\n    return 3\n"
        )
        summary = incremental_update(workspace=str(repo), db_path=str(db))
        time.sleep(0.05)  # reader keeps reading past the update window
    finally:
        stop.set()
        t.join(timeout=5)

    assert not errors, errors
    assert reads > 10, f"reader barely ran ({reads} reads)"
    assert empty_reads == 0, "alpha vanished mid-update -- WAL read isolation broke"

    conn = sqlite3.connect(str(db))
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM events WHERE name = 'lock_contention'"
        ).fetchone()
    finally:
        conn.close()
    assert row["c"] == 0, f"{row['c']} lock_contention events during the update"

    # The update actually did its job while reads were live.
    conn = sqlite3.connect(str(db))
    try:
        conn.row_factory = sqlite3.Row
        gamma = conn.execute(
            "SELECT COUNT(*) AS c FROM symbols WHERE name = 'gamma_live'"
        ).fetchone()["c"]
    finally:
        conn.close()
    assert gamma == 1
    assert summary.get("files_reindexed") == 1
