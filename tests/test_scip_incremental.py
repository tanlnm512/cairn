"""Tests for SCIP-aware incremental reindexing under the coexistence model.

In coexistence, tree-sitter always runs (providing modifiers/body/inheritance),
and SCIP merges exact-resolution edges onto the tree-sitter rows at build time
(source='merged'). When a file is edited, ``reindex_paths`` re-parses just that
file with tree-sitter (source='tree_sitter') -- the SCIP merge doesn't run for
a single-file incremental update, so the symbol temporarily loses its merged
status. The next full ``cairn build`` re-imports SCIP and re-merges, restoring
source='merged' -- a bounded, self-healing staleness window.

Skipped when the optional ``[scip]`` extra isn't installed.
"""
from __future__ import annotations

import json

import pytest

from cairn.graph.builder import build_graph
from cairn.graph.incremental import reindex_paths
from cairn.parsers.scip_importer import scip_available

# NOTE: _scip_pb2 is imported lazily inside _kotlin_index() below, not at module
# top level. A top-level import would raise ImportError during collection when
# the optional [scip] extra isn't installed, turning a clean SKIP (via the
# pytestmark below) into a hard collection ERROR -- defeating the skip guard.
pytestmark = pytest.mark.skipif(not scip_available(), reason="[scip] extra not installed")


def _kotlin_index() -> bytes:
    from cairn.parsers import _scip_pb2  # deferred: see module-level note

    idx = _scip_pb2.Index()
    doc = idx.documents.add()
    doc.relative_path = "demo/Foo.kt"
    doc.language = "kotlin"
    occ = doc.occurrences.add()
    occ.symbol = "scip-kotlin com example Foo#"
    occ.symbol_roles = 1
    occ.syntax_kind = 19
    occ.single_line_range.line = 0
    occ.single_line_range.start_character = 6
    occ.single_line_range.end_character = 9
    return idx.SerializeToString()


def test_reindex_of_merged_file_falls_back_to_tree_sitter(tmp_path):
    """Editing a coexistence file re-parses it as source='tree_sitter'.

    The incremental path re-parses only with tree-sitter (the SCIP merge runs
    at full-build time, not per-file). So after reindex the symbol is
    temporarily tree_sitter-only -- the merged status is restored on the next
    full build.
    """
    ws = tmp_path / "ws"
    repo = ws / "demo"
    (repo / ".git").mkdir(parents=True)
    (ws / "build" / "scip").mkdir(parents=True)
    (ws / "build" / "scip" / "kotlin.scip").write_bytes(_kotlin_index())
    (ws / "cairn.json").write_text(json.dumps({"scip": {"kotlin": "build/scip/kotlin.scip"}}))
    foo = repo / "Foo.kt"
    foo.write_text("class Foo {}\n")

    db = str(tmp_path / "inc.db")
    build_graph(workspace=str(ws), db_path=db)

    from cairn.graph.schema import get_db
    conn = get_db(db)
    try:
        # Initially Foo is merged (tree-sitter + SCIP).
        before = conn.execute(
            "SELECT source FROM symbols WHERE name = 'Foo'"
        ).fetchone()
        assert before["source"] == "merged"

        # Edit the file and reindex just it.
        foo.write_text("class Foo { fun go() {} }\n")
        reindex_paths(conn, str(ws), [str(foo)])

        # After reindex: Foo is tree_sitter (incremental doesn't re-run SCIP merge).
        after = conn.execute(
            "SELECT source FROM symbols WHERE name = 'Foo'"
        ).fetchone()
        assert after is not None
        assert after["source"] == "tree_sitter", (
            f"expected tree_sitter after reindex, got {after['source']!r}"
        )
    finally:
        conn.close()


def test_full_build_restores_merged_after_incremental(tmp_path):
    """A full rebuild re-imports SCIP and re-merges, restoring source='merged'."""
    ws = tmp_path / "ws"
    repo = ws / "demo"
    (repo / ".git").mkdir(parents=True)
    (ws / "build" / "scip").mkdir(parents=True)
    (ws / "build" / "scip" / "kotlin.scip").write_bytes(_kotlin_index())
    (ws / "cairn.json").write_text(json.dumps({"scip": {"kotlin": "build/scip/kotlin.scip"}}))
    foo = repo / "Foo.kt"
    foo.write_text("class Foo {}\n")

    db = str(tmp_path / "heal.db")
    build_graph(workspace=str(ws), db_path=db)

    from cairn.graph.schema import get_db
    conn = get_db(db)
    try:
        foo.write_text("class Foo { fun updated() {} }\n")
        reindex_paths(conn, str(ws), [str(foo)])
        mid = conn.execute("SELECT source FROM symbols WHERE name = 'Foo'").fetchone()
        assert mid["source"] == "tree_sitter"
    finally:
        conn.close()

    # Full rebuild re-imports SCIP and re-merges -> source flips back to 'merged'.
    build_graph(workspace=str(ws), db_path=db)
    conn = get_db(db)
    try:
        final = conn.execute("SELECT source FROM symbols WHERE name = 'Foo'").fetchone()
        assert final["source"] == "merged", (
            f"full build should restore source='merged', got {final['source']!r}"
        )
    finally:
        conn.close()
