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
    """F1: a held WAL writer's committed frames must not replay over the swap:
    after os.replace, a NEW connection reads the fresh build's data, never
    the old pre-build data left in a surviving <db>-wal.
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


def _invoke(args):
    """Run the cairn CLI in-process (lazy import per C-04)."""
    from click.testing import CliRunner

    from cairn.cli import main

    return CliRunner().invoke(main, args, catch_exceptions=False)


def test_staging_build_refuses_to_swap_empty_over_existing(
    caller_callee_ws, caller_callee_db, tmp_path
):
    """A staged build that indexed zero files must never swap its empty DB
    over the live store."""
    db = tmp_path / "graph.db"
    conn = caller_callee_db(str(db))
    before = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    conn.close()
    assert before > 0
    empty_ws = tmp_path / "empty"
    empty_ws.mkdir()

    result = _invoke(["build", "--workspace", str(empty_ws), "--db", str(db), "--staging"])

    assert result.exit_code != 0
    assert "refusing to swap" in result.output
    conn = sqlite3.connect(str(db))
    try:
        assert conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0] == before
    finally:
        conn.close()


def test_checkpoint_missing_db_errors_without_creating_it(tmp_path):
    missing = tmp_path / "nope.db"

    result = _invoke(["checkpoint", "--db", str(missing)])

    assert result.exit_code != 0
    assert "no graph DB" in result.output
    assert not missing.exists()


def test_checkpoint_non_db_file_errors_cleanly(tmp_path):
    junk = tmp_path / "junk.db"
    junk.write_text("not a database")

    result = _invoke(["checkpoint", "--db", str(junk)])

    assert result.exit_code != 0
    assert "checkpoint failed" in result.output


def test_non_staging_build_refuses_all_skipped_swap_over_existing(tmp_path):
    """A full build whose scan indexes nothing (all files skipped) must not
    swap the empty in-memory graph over a populated store."""
    import pytest

    from cairn.graph.builder import build_graph

    good_ws = tmp_path / "good"
    repo = good_ws / "demo"
    repo.mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    db = str(tmp_path / "graph.db")
    build_graph(workspace=str(good_ws), db_path=db, verbose=False)
    conn = sqlite3.connect(db)
    before = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    conn.close()
    assert before > 0

    skipped_ws = tmp_path / "skipped"
    repo2 = skipped_ws / "demo"
    repo2.mkdir(parents=True)
    (repo2 / ".git").mkdir()
    (repo2 / ".gitignore").write_text("*.py\n", encoding="utf-8")
    (repo2 / "b.py").write_text("def g():\n    return 2\n", encoding="utf-8")

    with pytest.raises(ValueError, match="refusing to replace"):
        build_graph(workspace=str(skipped_ws), db_path=db, verbose=False)

    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0] == before
    finally:
        conn.close()


def test_cli_build_refuses_all_skipped_swap_over_existing(tmp_path):
    """The build command maps the builder's all-skipped refusal to a clean
    CLI error (exit != 0) and leaves the populated store intact."""
    from cairn.graph.builder import build_graph

    good_ws = tmp_path / "good"
    repo = good_ws / "demo"
    repo.mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    db = str(tmp_path / "graph.db")
    build_graph(workspace=str(good_ws), db_path=db, verbose=False)
    conn = sqlite3.connect(db)
    before = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    conn.close()
    assert before > 0

    skipped_ws = tmp_path / "skipped"
    repo2 = skipped_ws / "demo"
    repo2.mkdir(parents=True)
    (repo2 / ".git").mkdir()
    (repo2 / ".gitignore").write_text("*.py\n", encoding="utf-8")
    (repo2 / "b.py").write_text("def g():\n    return 2\n", encoding="utf-8")

    result = _invoke(["build", "--workspace", str(skipped_ws), "--db", db])

    assert result.exit_code != 0
    assert "refusing to replace" in result.output
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0] == before
    finally:
        conn.close()
