"""Store-integrity regressions for the DB-file swap and read-only open.

F1: os.replace swaps only the main DB file. If the OLD db ran in WAL mode,
its "<db>-wal"/"<db>-shm" survive the swap and the next open replays the old
committed WAL frames over the NEW main file -- silently serving the pre-build
graph (or SQLITE_CORRUPT). backup_to/swap_db_file must checkpoint + remove
the old sidecars under the build lock before replacing.

F7: a failed backup_to must not leave a half-written "<db>.tmp" on disk (the
next successful build would swap it in).

F8: the read-only URI (`file:<path>?mode=ro`) must percent-encode paths with
spaces / '?' / '#' so they aren't parsed as URI separators.
"""
from __future__ import annotations

import os
import sqlite3

import pytest

from cairn.graph.schema import backup_to, get_build_db, get_db, swap_db_file


def _wal_writer_with_committed_frames(db_path: str, value: str) -> sqlite3.Connection:
    """An OPEN WAL connection holding committed-but-uncheckpointed frames.

    Mirrors the daemon flush window: insert + commit on a WAL db without
    closing (auto-checkpoint only fires at ~1000 pages, so the frames stay
    in the -wal sidecar).
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("CREATE TABLE t (v TEXT)")
    conn.execute("INSERT INTO t VALUES (?)", (value,))
    conn.commit()
    assert os.path.exists(db_path + "-wal"), "fixture bug: frames not in -wal"
    return conn


def test_backup_to_swap_with_open_wal_writer_new_conn_sees_new_data(tmp_path):
    """F1: a held WAL writer's committed frames must not replay over the swap.

    Before the fix, the old <db>-wal survived os.replace and a NEW connection
    read the OLD pre-build data instead of the fresh build.
    """
    db_path = str(tmp_path / "graph.db")
    writer = _wal_writer_with_committed_frames(db_path, "OLD-PRE-BUILD-DATA")

    mem = get_build_db()
    mem.execute("CREATE TABLE t (v TEXT)")
    mem.execute("INSERT INTO t VALUES ('NEW-BUILD-DATA')")
    mem.commit()
    backup_to(mem, db_path)
    mem.close()

    # The old sidecars must not survive the swap.
    assert not os.path.exists(db_path + "-wal")
    assert not os.path.exists(db_path + "-shm")

    fresh = sqlite3.connect(db_path)
    try:
        val = fresh.execute("SELECT v FROM t LIMIT 1").fetchone()
    finally:
        fresh.close()
        writer.close()
    assert val is not None and val[0] == "NEW-BUILD-DATA", (
        f"new connection read {val!r} -- old WAL replayed over the swapped DB"
    )


def test_swap_db_file_removes_old_wal_sidecars(tmp_path):
    """F1: the shared swap helper has the same contract as backup_to."""
    db_path = str(tmp_path / "live.db")
    writer = _wal_writer_with_committed_frames(db_path, "OLD")

    tmp_path_db = db_path + ".tmp"
    tmp = sqlite3.connect(tmp_path_db)
    tmp.execute("CREATE TABLE t (v TEXT)")
    tmp.execute("INSERT INTO t VALUES ('NEW')")
    tmp.commit()
    tmp.close()

    swap_db_file(tmp_path_db, db_path)

    assert not os.path.exists(db_path + "-wal")
    assert not os.path.exists(db_path + "-shm")
    assert not os.path.exists(tmp_path_db + "-wal")
    fresh = sqlite3.connect(db_path)
    try:
        assert fresh.execute("SELECT v FROM t").fetchone()[0] == "NEW"
    finally:
        fresh.close()
        writer.close()


def test_backup_to_failure_leaves_no_tmp(tmp_path):
    """F7: a failed backup must not leave "<db>.tmp" (or sidecars) on disk."""
    db_path = str(tmp_path / "fail.db")

    class _FailingMemConn:
        def backup(self, dest):
            raise sqlite3.OperationalError("disk I/O error (simulated)")

    with pytest.raises(sqlite3.OperationalError):
        backup_to(_FailingMemConn(), db_path)  # type: ignore[arg-type]

    assert not os.path.exists(db_path + ".tmp")
    assert not os.path.exists(db_path + ".tmp-wal")
    assert not os.path.exists(db_path + ".tmp-shm")
    # The real db (if any) was never touched.
    assert not os.path.exists(db_path)


def test_backup_to_success_after_failure_still_clean(tmp_path):
    """F7 companion: the cleanup path doesn't wedge later successful builds."""
    db_path = str(tmp_path / "retry.db")

    class _FailingMemConn:
        def backup(self, dest):
            raise sqlite3.OperationalError("simulated")

    with pytest.raises(sqlite3.OperationalError):
        backup_to(_FailingMemConn(), db_path)  # type: ignore[arg-type]

    mem = get_build_db()
    mem.execute(
        "INSERT INTO repos (id, name, path, language, git_remote, indexed_at) "
        "VALUES ('r1', 'demo', '.', 'kotlin', NULL, '2026-01-01')"
    )
    mem.commit()
    backup_to(mem, db_path)
    mem.close()
    assert not os.path.exists(db_path + ".tmp")
    assert os.path.exists(db_path)


def test_get_db_read_only_handles_special_chars_in_path(tmp_path):
    """F8: '?' / '#' / space in the path must not truncate the mode=ro URI."""
    weird_dir = tmp_path / "we ird?dir#name"
    weird_dir.mkdir()
    db_path = str(weird_dir / "graph.db")

    w = get_db(db_path)
    w.execute(
        "INSERT INTO repos (id, name, path, language, git_remote, indexed_at) "
        "VALUES ('r1', 'demo', '.', 'kotlin', NULL, '2026-01-01')"
    )
    w.commit()
    w.close()

    r = get_db(db_path, read_only=True)
    try:
        row = r.execute("SELECT name FROM repos WHERE id='r1'").fetchone()
        assert row is not None and row[0] == "demo"
        # Truly read-only: writes must be refused.
        with pytest.raises(sqlite3.OperationalError):
            r.execute("DELETE FROM repos")
    finally:
        r.close()
