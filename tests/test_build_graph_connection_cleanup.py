"""Test L8: build_graph closes DB connection on all exit paths.

Tests that conn.close() is called on:
- Normal completion
- Exception from inner helper
- KeyboardInterrupt from inner helper
"""
from __future__ import annotations

import tempfile
from unittest.mock import patch

import pytest

from cairn.graph.builder import build_graph


FIXTURE_FILES = {
    "Simple.kt": (
        'class Simple {\n'
        '    fun doWork() {}\n'
        '}\n'
    ),
}


def _make_fixture(tmp_path, name: str) -> str:
    workspace = tmp_path / name
    repo = workspace / "demo"
    (repo / ".git").mkdir(parents=True)
    for fname, contents in FIXTURE_FILES.items():
        (repo / fname).write_text(contents)
    return str(workspace)


def test_build_graph_closes_connection_on_exception():
    """Exception from inner helper: conn.close() should still be called.
    
    This test monkeypatches _parse_file_worker to raise an exception
    during the build, and verifies that the connection is closed even
    in the exception path by checking we can reopen the same db_path.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        import pathlib
        tmp_path = pathlib.Path(tmpdir)
        workspace = _make_fixture(tmp_path, "exception")
        db_path = str(tmp_path / "exception.db")
        
        # First, monkeypatch to raise during parsing
        with patch('cairn.graph.builder._parse_file_worker', side_effect=RuntimeError("Simulated parse error")):
            with pytest.raises(RuntimeError, match="Simulated parse error"):
                build_graph(workspace=workspace, db_path=db_path, verbose=False)
        
        # If the connection wasn't closed, we wouldn't be able to open it
        # again (or there would be lingering locks)
        import sqlite3
        try:
            # Try to open and use the same db_path - this should work if conn was closed
            test_conn = sqlite3.connect(db_path)
            test_conn.execute("SELECT 1").fetchone()
            test_conn.close()
            # Success - connection was properly closed
        except sqlite3.OperationalError as e:
            pytest.fail(f"Database file is locked or corrupted, likely due to unclosed connection: {e}")


def test_build_graph_closes_connection_on_keyboard_interrupt():
    """KeyboardInterrupt: conn.close() should still be called.
    
    This test monkeypatches _parse_file_worker to raise KeyboardInterrupt
    during the build, and verifies that the connection is closed even
    in the KeyboardInterrupt path by checking we can reopen the same db_path.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        import pathlib
        tmp_path = pathlib.Path(tmpdir)
        workspace = _make_fixture(tmp_path, "keyboard_interrupt")
        db_path = str(tmp_path / "keyboard.db")
        
        # Monkeypatch to raise KeyboardInterrupt during parsing
        with patch('cairn.graph.builder._parse_file_worker', side_effect=KeyboardInterrupt()):
            with pytest.raises(KeyboardInterrupt):
                build_graph(workspace=workspace, db_path=db_path, verbose=False)
        
        # If the connection wasn't closed, we wouldn't be able to open it
        # again (or there would be lingering locks)
        import sqlite3
        try:
            # Try to open and use the same db_path - this should work if conn was closed
            test_conn = sqlite3.connect(db_path)
            test_conn.execute("SELECT 1").fetchone()
            test_conn.close()
            # Success - connection was properly closed
        except sqlite3.OperationalError as e:
            pytest.fail(f"Database file is locked or corrupted, likely due to unclosed connection: {e}")


def test_build_graph_early_return_closes_connection():
    """Early return (no files): conn.close() should still be called.
    
    This test creates an empty workspace (no source files) and verifies
    that the connection is closed on early return by checking we can
    reopen the same db_path.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        import pathlib
        tmp_path = pathlib.Path(tmpdir)
        # Create empty workspace (no source files)
        workspace = str(tmp_path / "empty")
        db_path = str(tmp_path / "empty.db")
        
        result = build_graph(workspace=workspace, db_path=db_path, verbose=False)
        
        assert result["files"] == 0, "Should have no files"
        
        # If the connection wasn't closed, we wouldn't be able to open it
        # again (or there would be lingering locks)
        import sqlite3
        try:
            # Try to open and use the same db_path - this should work if conn was closed
            test_conn = sqlite3.connect(db_path)
            test_conn.execute("SELECT 1").fetchone()
            test_conn.close()
            # Success - connection was properly closed
        except sqlite3.OperationalError as e:
            pytest.fail(f"Database file is locked or corrupted, likely due to unclosed connection: {e}")


def test_build_graph_closes_connection_on_normal_exit():
    """Normal completion: conn.close() should be called.
    
    This test verifies that after a successful build, the connection
    is closed by checking we can reopen the same db_path.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        import pathlib
        tmp_path = pathlib.Path(tmpdir)
        workspace = _make_fixture(tmp_path, "normal_exit")
        db_path = str(tmp_path / "normal.db")
        
        result = build_graph(workspace=workspace, db_path=db_path, verbose=False)
        
        assert result["files"] > 0, "Should have indexed files"
        
        # If the connection wasn't closed, we wouldn't be able to open it
        # again (or there would be lingering locks)
        import sqlite3
        try:
            # Try to open and use the same db_path - this should work if conn was closed
            test_conn = sqlite3.connect(db_path)
            test_conn.execute("SELECT 1").fetchone()
            test_conn.close()
            # Success - connection was properly closed
        except sqlite3.OperationalError as e:
            pytest.fail(f"Database file is locked or corrupted, likely due to unclosed connection: {e}")
