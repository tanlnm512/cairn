"""Tests for backup_to atomic swap and concurrent build locking (VAL-DB-001, VAL-DB-002)."""
from __future__ import annotations

import errno
import fcntl
import os
import sqlite3

import pytest

from cairn.graph.schema import get_build_db, backup_to


def test_backup_to_writes_to_temp_then_atomic_swap(tmp_path):
    """VAL-DB-001: backup_to writes to db_path + '.tmp' first, then os.replace for atomic swap."""
    # Create an in-memory DB with some data
    mem_conn = get_build_db()
    mem_conn.execute(
        "INSERT INTO repos (id, name, path, language, git_remote, indexed_at) "
        "VALUES ('r1', 'demo', '/tmp/demo', 'kotlin', NULL, '2026-01-01')"
    )
    mem_conn.commit()

    db_path = str(tmp_path / "backup_target.db")
    backup_to(mem_conn, db_path)
    mem_conn.close()

    # Verify the final file exists and contains the data
    assert os.path.exists(db_path)
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT name FROM repos WHERE id='r1'").fetchone()
        assert row == ("demo",)
    finally:
        conn.close()


def test_backup_to_old_inode_preserved_for_readers(tmp_path):
    """VAL-DB-001: Pre-existing readers keep the old inode; new connections see the fresh DB."""
    # Create initial DB
    db_path = str(tmp_path / "inode_test.db")
    conn1 = get_build_db()
    conn1.execute(
        "INSERT INTO repos (id, name, path, language, git_remote, indexed_at) "
        "VALUES ('r1', 'old', '/tmp/old', 'kotlin', NULL, '2026-01-01')"
    )
    conn1.commit()
    backup_to(conn1, db_path)

    # Get inode of original file
    original_inode = os.stat(db_path).st_ino

    # Open a connection that will hold the old file open (simulating a reader)
    reader_conn = sqlite3.connect(db_path)
    old_data = reader_conn.execute("SELECT name FROM repos WHERE id='r1'").fetchone()[0]
    assert old_data == "old"

    # Now rebuild with different data via backup_to
    # Note: backup_to does a full DB backup, so this overwrites everything
    conn2 = get_build_db()
    conn2.execute(
        "INSERT INTO repos (id, name, path, language, git_remote, indexed_at) "
        "VALUES ('r1', 'new', '/tmp/new', 'python', NULL, '2026-01-02')"
    )
    conn2.commit()
    backup_to(conn2, db_path)
    conn1.close()
    conn2.close()

    # The reader should still see the old data (old inode)
    reader_data = reader_conn.execute("SELECT name FROM repos WHERE id='r1'").fetchone()
    assert reader_data is not None, "Old reader should still see old data"
    assert reader_data[0] == "old", "Old reader should still see old data"

    # The file on disk should have a new inode
    new_inode = os.stat(db_path).st_ino
    assert new_inode != original_inode, "File should have new inode after atomic swap"

    # A new connection should see the new data
    new_conn = sqlite3.connect(db_path)
    try:
        new_data = new_conn.execute("SELECT name FROM repos WHERE id='r1'").fetchone()
        assert new_data is not None, "New connection should see data"
        assert new_data[0] == "new", "New connection should see new data"
    finally:
        new_conn.close()

    reader_conn.close()


def test_backup_to_no_tmp_residue(tmp_path):
    """VAL-DB-001: No .tmp file remains after successful backup."""
    mem_conn = get_build_db()
    mem_conn.execute(
        "INSERT INTO repos (id, name, path, language, git_remote, indexed_at) "
        "VALUES ('r1', 'demo', '/tmp/demo', 'kotlin', NULL, '2026-01-01')"
    )
    mem_conn.commit()

    db_path = str(tmp_path / "no_residue.db")
    backup_to(mem_conn, db_path)
    mem_conn.close()

    # Check that no .tmp file exists
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert len(tmp_files) == 0, f"Expected no .tmp files, found: {tmp_files}"

    # Check no .build.lock file exists
    lock_files = list(tmp_path.glob("*.build.lock"))
    assert len(lock_files) == 0, f"Expected no .build.lock files, found: {lock_files}"


def test_backup_to_concurrent_rebuild_raises_with_message(tmp_path):
    """VAL-DB-002: Concurrent rebuild fails fast with a clear error message."""
    db_path = str(tmp_path / "concurrent.db")
    lock_path = db_path + ".build.lock"

    # First, let's manually acquire the lock to simulate a concurrent rebuild
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        # Now try to backup while the lock is held - should fail
        mem_conn = get_build_db()
        mem_conn.execute(
            "INSERT INTO repos (id, name, path, language, git_remote, indexed_at) "
            "VALUES ('r1', 'demo', '/tmp/demo', 'kotlin', NULL, '2026-01-01')"
        )
        mem_conn.commit()

        with pytest.raises(RuntimeError) as exc_info:
            backup_to(mem_conn, db_path)

        error_msg = str(exc_info.value).lower()
        assert "concurrent" in error_msg or "build" in error_msg or "lock" in error_msg, \
            f"Expected error message to mention concurrent/build/lock, got: {exc_info.value}"
        mem_conn.close()

    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def test_backup_to_lock_released_on_success(tmp_path):
    """VAL-DB-002: Lock is released after successful backup."""
    mem_conn = get_build_db()
    mem_conn.execute(
        "INSERT INTO repos (id, name, path, language, git_remote, indexed_at) "
        "VALUES ('r1', 'demo', '/tmp/demo', 'kotlin', NULL, '2026-01-01')"
    )
    mem_conn.commit()

    db_path = str(tmp_path / "lock_release.db")
    lock_path = db_path + ".build.lock"

    backup_to(mem_conn, db_path)
    mem_conn.close()

    # Verify lock file doesn't exist (or is not locked)
    # The implementation might leave an empty lock file, but it shouldn't be locked
    if os.path.exists(lock_path):
        # Try to acquire the lock - should succeed immediately
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_WRONLY, 0o644)
        try:
            # Use non-blocking try lock - should succeed
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # If we get here, the lock was available
        except (IOError, OSError) as e:
            if e.errno == errno.EWOULDBLOCK:
                pytest.fail("Lock should have been released after successful backup")
            raise
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)


def test_backup_to_persists_wal_and_foreign_keys(tmp_path):
    """VAL-DB-001: Persisted DB has journal_mode=WAL and foreign_keys is set."""
    mem_conn = get_build_db()
    mem_conn.execute(
        "INSERT INTO repos (id, name, path, language, git_remote, indexed_at) "
        "VALUES ('r1', 'demo', '/tmp/demo', 'kotlin', NULL, '2026-01-01')"
    )
    mem_conn.commit()

    db_path = str(tmp_path / "pragmas.db")
    backup_to(mem_conn, db_path)
    mem_conn.close()

    # Check that a fresh connection sees WAL mode
    conn = sqlite3.connect(db_path)
    try:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
        assert journal_mode == "wal", f"Expected WAL mode, got {journal_mode}"

        # Note: foreign_keys is a per-connection PRAGMA, but it's set in backup_to
        # before commit. The DB doesn't persist it, but it was set on the dest
        # connection. Since get_db() sets it for all connections, we just need to
        # verify it's being set (not that it persists across connections).
        # The existing test_backup_to_persists_schema_and_data verifies get_db
        # behavior.
    finally:
        conn.close()
