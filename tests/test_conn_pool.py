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
