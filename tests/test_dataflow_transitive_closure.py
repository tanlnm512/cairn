"""Tests for M5: Dataflow transitive closure uses resolved IDs not bare names.

Bug: build_transitive_closure joins on bare symbols.name, so name collisions
(e.g., two unrelated "init" symbols) produce spurious edges in the transitive
closure matrix.

Fix: Join on resolved target_id (resolution='exact') instead of bare
symbols.name. Widen transitive_edges schema to carry target_id.
"""
from __future__ import annotations

import sqlite3

from codegraph.graph.schema import _apply_schema
from codegraph.graph.dataflow import build_transitive_closure


def test_name_collision_no_spurious_edges(fresh_db):
    """Two unrelated init symbols should not merge in transitive closure.

    Setup:
    - Symbol A: "init" (id: sid_a) in file1
    - Symbol B: "init" (id: sid_b) in file2 (different class, unrelated)
    - Edge from caller1 to sid_a (resolved, target_id = sid_a)
    - Edge from caller2 to sid_b (resolved, target_id = sid_b)
    - Edge from sid_a to callee1 (sid_a calls callee1)
    - Edge from sid_b to callee2 (sid_b calls callee2)

    Expected:
    - Transitive closure should have caller1 -> callee1 (via sid_a)
    - Transitive closure should have caller2 -> callee2 (via sid_b)
    - NO spurious edges like caller1 -> callee2 or caller2 -> callee1

    Bug manifestation:
    - Current implementation joins on symbols.name = "init"
    - Both sid_a and sid_b have name "init"
    - This creates spurious cross-pollination between the two graphs
    """
    conn = fresh_db

    # Insert files
    conn.execute("""
        INSERT INTO files (id, repo_id, path, language) VALUES
        ('f1', 'r1', 'File1.kt', 'kotlin'),
        ('f2', 'r1', 'File2.kt', 'kotlin'),
        ('f3', 'r1', 'File3.kt', 'kotlin'),
        ('f4', 'r1', 'File4.kt', 'kotlin')
    """)

    # Insert symbols: two unrelated "init" methods in different classes
    conn.execute("""
        INSERT INTO symbols (id, file_id, name, qualified_name, kind) VALUES
        ('sid_a', 'f1', 'init', 'ClassA.init', 'method'),
        ('sid_b', 'f2', 'init', 'ClassB.init', 'method'),
        ('caller1', 'f3', 'caller1', 'caller1', 'function'),
        ('caller2', 'f4', 'caller2', 'caller2', 'function'),
        ('callee1', 'f1', 'callee1', 'ClassA.callee1', 'method'),
        ('callee2', 'f2', 'callee2', 'ClassB.callee2', 'method')
    """)

    # Insert edges with resolved target_id
    conn.execute("""
        INSERT INTO edges (id, source_id, target_id, kind) VALUES
        ('e1', 'caller1', 'sid_a', 'calls'),
        ('e2', 'caller2', 'sid_b', 'calls'),
        ('e3', 'sid_a', 'callee1', 'calls'),
        ('e4', 'sid_b', 'callee2', 'calls')
    """)

    conn.commit()

    # Build transitive closure
    build_transitive_closure(conn, max_depth=2)

    # Verify correct edges exist
    rows = conn.execute("""
        SELECT te.source_id, te.distance, s.name AS target_name
        FROM transitive_edges te
        JOIN symbols s ON s.name = te.target_name
        ORDER BY te.source_id, te.distance
    """).fetchall()

    # Debug: print all rows
    print("All transitive_edges rows:")
    for row in rows:
        print(f"  source_id={row['source_id']}, distance={row['distance']}, target_name={row['target_name']}")

    # Expected edges:
    # caller1 -> init (sid_a) [distance=1]
    # caller1 -> callee1 [distance=2]
    # caller2 -> init (sid_b) [distance=1]
    # caller2 -> callee2 [distance=2]
    # sid_a -> callee1 [distance=1]
    # sid_b -> callee2 [distance=1]

    # Check that caller1 can reach its own callee (callee1)
    caller1_to_callee1 = conn.execute("""
        SELECT te.distance
        FROM transitive_edges te
        WHERE te.source_id = 'caller1' AND te.target_name = 'callee1'
    """).fetchall()
    assert len(caller1_to_callee1) == 1, f"Expected 1 edge caller1->callee1, got {len(caller1_to_callee1)}"

    # Check that caller2 can reach its own callee (callee2)
    caller2_to_callee2 = conn.execute("""
        SELECT te.distance
        FROM transitive_edges te
        WHERE te.source_id = 'caller2' AND te.target_name = 'callee2'
    """).fetchall()
    assert len(caller2_to_callee2) == 1, f"Expected 1 edge caller2->callee2, got {len(caller2_to_callee2)}"

    # BUG: Check for spurious edges (caller1 -> callee2 and caller2 -> callee1)
    # These should NOT exist because the two "init" symbols are unrelated
    spurious_1 = conn.execute("""
        SELECT te.distance
        FROM transitive_edges te
        WHERE te.source_id = 'caller1' AND te.target_name = 'callee2'
    """).fetchall()

    spurious_2 = conn.execute("""
        SELECT te.distance
        FROM transitive_edges te
        WHERE te.source_id = 'caller2' AND te.target_name = 'callee1'
    """).fetchall()

    print(f"Spurious edges caller1->callee2: {len(spurious_1)}")
    print(f"Spurious edges caller2->callee1: {len(spurious_2)}")

    # These should be 0 - the bug causes them to be > 0
    assert len(spurious_1) == 0, f"Expected 0 spurious edges caller1->callee2, got {len(spurious_1)}"
    assert len(spurious_2) == 0, f"Expected 0 spurious edges caller2->callee1, got {len(spurious_2)}"


def test_transitive_closure_respects_resolution(fresh_db):
    """Transitive closure should only follow resolved edges (target_id != NULL)."""
    conn = fresh_db

    # Insert files and symbols
    conn.execute("""
        INSERT INTO files (id, repo_id, path, language) VALUES
        ('f1', 'r1', 'File1.kt', 'kotlin'),
        ('f2', 'r1', 'File2.kt', 'kotlin')
    """)

    conn.execute("""
        INSERT INTO symbols (id, file_id, name, qualified_name, kind) VALUES
        ('caller', 'f1', 'caller', 'caller', 'function'),
        ('ambiguous_init', 'f1', 'init', 'init', 'function'),
        ('resolved_init', 'f2', 'init', 'Class.init', 'method'),
        ('callee', 'f2', 'callee', 'callee', 'function')
    """)

    # Insert edges:
    # - caller -> resolved_init (fully resolved)
    # - caller -> ambiguous_init (not resolved - only target_name)
    # - resolved_init -> callee (fully resolved)
    conn.execute("""
        INSERT INTO edges (id, source_id, target_id, target_name, kind) VALUES
        ('e1', 'caller', 'resolved_init', NULL, 'calls'),
        ('e2', 'caller', NULL, 'init', 'calls'),
        ('e3', 'resolved_init', 'callee', NULL, 'calls')
    """)

    conn.commit()

    # Build transitive closure
    build_transitive_closure(conn, max_depth=2)

    # Caller should reach callee via resolved_init (distance=2)
    caller_to_callee = conn.execute("""
        SELECT te.distance
        FROM transitive_edges te
        WHERE te.source_id = 'caller' AND te.target_name = 'callee'
    """).fetchall()

    assert len(caller_to_callee) == 1, f"Expected 1 edge caller->callee, got {len(caller_to_callee)}"
    assert caller_to_callee[0]['distance'] == 2
