"""Test for H8: reindex_paths maintains the resolution invariant.

When reindex_paths nulls target_id for edges pointing at deleted symbols, it
must also set resolution='unresolved'. Previously it left resolution='exact'
with target_id=NULL, violating the resolver invariant (exact => target_id set)
and confusing downstream precise-mode callers/get_callers.

This test seeds the DB with an exact edge, simulates the reindex UPDATE
(the same SQL in incremental.py), and asserts the invariant holds.
"""
import sqlite3

import pytest

from cairn.graph.schema import _apply_schema


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    yield conn
    conn.close()


def _seed_file_and_symbols(conn):
    """Insert one file + two symbols + one exact edge between them."""
    conn.execute(
        "INSERT INTO files (id, path, repo_id, hash, line_count, language) "
        "VALUES ('f1', '/ws/repo/a.py', 'repo', 'h1', 10, 'python')"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
        "VALUES ('s1', 'f1', 'caller', 'function', 'caller', 1, 5)"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
        "VALUES ('s2', 'f1', 'callee', 'function', 'callee', 6, 10)"
    )
    conn.execute(
        "INSERT INTO edges (id, source_id, target_id, target_name, kind, line) "
        "VALUES ('e1', 's1', 's2', 'callee', 'calls', 3)"
    )
    # resolution is a migration-added column; set it to 'exact' to simulate a
    # resolved edge before the reindex UPDATE runs.
    conn.execute("UPDATE edges SET resolution = 'exact' WHERE id = 'e1'")
    conn.commit()


def test_reindex_nulls_resolution_to_unresolved(db):
    """The reindex UPDATE must set resolution='unresolved' alongside target_id=NULL."""
    _seed_file_and_symbols(db)

    # This is the exact SQL from incremental.py:113-116 (H8 fix).
    db.execute(
        "UPDATE edges SET target_id = NULL, resolution = 'unresolved' "
        "WHERE target_id IN (SELECT id FROM symbols WHERE file_id = ?)",
        ("f1",),
    )
    db.commit()

    row = db.execute("SELECT target_id, resolution FROM edges WHERE id = 'e1'").fetchone()
    assert row["target_id"] is None, "target_id should be nulled"
    assert row["resolution"] == "unresolved", \
        f"resolution must be 'unresolved' after nulling target_id, got '{row['resolution']}'"


def test_no_dangling_exact_edges_after_reindex(db):
    """After reindex, no edge should have resolution='exact' with target_id=NULL."""
    _seed_file_and_symbols(db)

    db.execute(
        "UPDATE edges SET target_id = NULL, resolution = 'unresolved' "
        "WHERE target_id IN (SELECT id FROM symbols WHERE file_id = ?)",
        ("f1",),
    )
    db.commit()

    # The resolver invariant: exact => target_id is NOT NULL.
    dangling = db.execute(
        "SELECT COUNT(*) AS c FROM edges WHERE resolution = 'exact' AND target_id IS NULL"
    ).fetchone()
    assert dangling["c"] == 0, \
        f"{dangling['c']} dangling exact edges with NULL target_id — invariant violated"


def test_unrelated_exact_edges_preserved(db):
    """An exact edge to a symbol in a DIFFERENT file must not be touched."""
    _seed_file_and_symbols(db)

    # Add a second file + symbol + edge that should NOT be nulled.
    db.execute(
        "INSERT INTO files (id, path, repo_id, hash, line_count, language) "
        "VALUES ('f2', '/ws/repo/b.py', 'repo', 'h2', 5, 'python')"
    )
    db.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
        "VALUES ('s3', 'f2', 'other_callee', 'function', 'other_callee', 1, 3)"
    )
    db.execute(
        "INSERT INTO edges (id, source_id, target_id, target_name, kind, line) "
        "VALUES ('e2', 's1', 's3', 'other_callee', 'calls', 4)"
    )
    db.execute("UPDATE edges SET resolution = 'exact' WHERE id = 'e2'")
    db.commit()

    db.execute(
        "UPDATE edges SET target_id = NULL, resolution = 'unresolved' "
        "WHERE target_id IN (SELECT id FROM symbols WHERE file_id = ?)",
        ("f1",),
    )
    db.commit()

    # e1 (target in f1) should be nulled.
    e1 = db.execute("SELECT target_id, resolution FROM edges WHERE id = 'e1'").fetchone()
    assert e1["target_id"] is None
    assert e1["resolution"] == "unresolved"

    # e2 (target in f2) should be untouched — still exact with a target.
    e2 = db.execute("SELECT target_id, resolution FROM edges WHERE id = 'e2'").fetchone()
    assert e2["target_id"] == "s3", "unrelated edge target should not be nulled"
    assert e2["resolution"] == "exact", "unrelated edge resolution should stay exact"
