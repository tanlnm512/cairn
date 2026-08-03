"""Phase 9 — RAM-first build path.

Builds the same small fixture workspace two ways -- forced on-disk
(repo_filter set) and in-memory (repo_filter=None, the default full-rebuild
path) -- and asserts identical table counts and resolution histograms.
Proves backup_to() is a lossless persist and that the bulk-load pragmas /
executemany batching / O(n^2) fix / dropped periodic commits didn't change
*what* gets built, only how fast.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

from codegraph.graph.builder import build_graph
from codegraph.graph.schema import get_build_db, backup_to


FIXTURE_FILES = {
    "Profile.kt": (
        'class Profile {\n'
        '    fun displayName(): String { return "x" }\n'
        '}\n'
    ),
    "Account.kt": (
        'class Account {\n'
        '    fun displayName(): String { return "y" }\n'
        '}\n'
    ),
    "UserRepo.kt": (
        "class UserRepo {\n"
        "    val profile: Profile = Profile()\n"
        "    fun run() {\n"
        "        val local: Profile = Profile()\n"
        "        local.displayName()\n"
        "        this.profile.displayName()\n"
        "        profile.displayName()\n"
        "        val other = Account()\n"
        "        other.displayName()\n"
        "        Profile().displayName()\n"
        "        helper()\n"
        "    }\n"
        "    fun helper() {}\n"
        "}\n"
    ),
}


def _make_fixture(tmp_path, name: str) -> str:
    workspace = tmp_path / name
    repo = workspace / "demo"
    (repo / ".git").mkdir(parents=True)
    for fname, contents in FIXTURE_FILES.items():
        (repo / fname).write_text(contents)
    return str(workspace)


def _counts(db_path: str) -> tuple[dict, dict]:
    conn = sqlite3.connect(db_path)
    try:
        out = {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("files", "symbols", "edges", "imports")
        }
        hist = dict(
            conn.execute(
                "SELECT resolution, COUNT(*) FROM edges GROUP BY resolution"
            ).fetchall()
        )
        return out, hist
    finally:
        conn.close()


def test_inmemory_and_ondisk_builds_have_identical_counts(tmp_path):
    ws_mem = _make_fixture(tmp_path, "ws_mem")
    db_mem = str(tmp_path / "mem.db")
    build_graph(workspace=ws_mem, db_path=db_mem, verbose=False)  # in-memory path

    ws_disk = _make_fixture(tmp_path, "ws_disk")
    db_disk = str(tmp_path / "disk.db")
    build_graph(workspace=ws_disk, repo_filter="demo", db_path=db_disk, verbose=False)  # on-disk path

    counts_mem, hist_mem = _counts(db_mem)
    counts_disk, hist_disk = _counts(db_disk)

    assert counts_mem == counts_disk
    assert hist_mem == hist_disk
    # Sanity: the fixture actually produced resolvable edges, not an empty no-op.
    assert counts_mem["edges"] > 0
    assert hist_mem.get("exact", 0) > 0


def test_inmemory_build_persists_fts_index(tmp_path):
    ws = _make_fixture(tmp_path, "ws_fts")
    db_path = str(tmp_path / "fts.db")
    build_graph(workspace=ws, db_path=db_path, verbose=False)

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM symbols_fts WHERE symbols_fts MATCH 'displayName'"
        ).fetchall()
    finally:
        conn.close()
    assert rows, "FTS index should be queryable immediately after in-memory persist"


def test_get_build_db_uses_bulk_load_pragmas():
    conn = get_build_db()
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "memory"
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 0  # OFF
    finally:
        conn.close()


def test_backup_to_persists_schema_and_data(tmp_path):
    conn = get_build_db()
    conn.execute(
        "INSERT INTO repos (id, name, path, language, git_remote, indexed_at) "
        "VALUES ('r1', 'demo', '/tmp/demo', 'kotlin', NULL, '2026-01-01')"
    )
    conn.commit()

    db_path = str(tmp_path / "backup_target.db")
    backup_to(conn, db_path)
    conn.close()

    dest = sqlite3.connect(db_path)
    try:
        row = dest.execute("SELECT name FROM repos WHERE id='r1'").fetchone()
        assert row == ("demo",)
        # Serving mode: journal_mode persists in the file header, so a fresh
        # connection sees WAL immediately.
        assert dest.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        dest.close()

    # foreign_keys is a per-connection PRAGMA (SQLite does not persist it in
    # the file), so the real contract is "get_db() turns it on for servers",
    # not "backup_to() bakes it into the file". Verify via get_db().
    from codegraph.graph.schema import get_db

    served = get_db(db_path)
    try:
        assert served.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        served.close()
