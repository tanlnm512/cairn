"""Tests for the hybrid SCIP / tree-sitter build path.

Verifies that when ``cairn.json`` declares a SCIP index for a language and the
index file exists:
  - tree-sitter parsing is SKIPPED for that language's files,
  - the SCIP importer runs post-resolve and contributes source='scip' symbols,
  - a missing index file falls back to tree-sitter for that language.

Skipped when the optional ``[scip]`` extra isn't installed.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cairn.graph.builder import build_graph
from cairn.parsers.scip_importer import scip_available

# NOTE: _scip_pb2 is imported lazily inside _kotlin_index() below, not at module
# top level. A top-level import would raise ImportError during collection when
# the optional [scip] extra isn't installed, turning a clean SKIP (via the
# pytestmark below) into a hard collection ERROR -- defeating the skip guard.
pytestmark = pytest.mark.skipif(not scip_available(), reason="[scip] extra not installed")


def _make_workspace(tmp_path: Path, name: str) -> Path:
    ws = tmp_path / name
    repo = ws / "demo"
    (repo / ".git").mkdir(parents=True)
    # A Kotlin file (would be tree-sitter-parsed without SCIP) + a Python file
    # (always tree-sitter; no SCIP index declared for python).
    (repo / "Foo.kt").write_text("class Foo { fun go() {} }\n")
    (repo / "bar.py").write_text("def bar():\n    pass\n")
    return ws


def _kotlin_index() -> bytes:
    """A minimal real SCIP protobuf index with one Kotlin symbol."""
    from cairn.parsers import _scip_pb2  # deferred: see module-level note

    idx = _scip_pb2.Index()
    doc = idx.documents.add()
    doc.relative_path = "demo/Foo.kt"
    doc.language = "kotlin"
    occ = doc.occurrences.add()
    occ.symbol = "scip-kotlin com example Foo#"
    occ.symbol_roles = 1  # Definition
    occ.syntax_kind = 19  # IdentifierType -> class
    occ.single_line_range.line = 0
    occ.single_line_range.start_character = 6
    occ.single_line_range.end_character = 9
    return idx.SerializeToString()


def test_scip_language_skips_tree_sitter(tmp_path):
    """Files whose language has a present SCIP index are NOT tree-sitter-parsed."""
    ws = _make_workspace(tmp_path, "scip_skip")
    (ws / "build" / "scip").mkdir(parents=True)
    (ws / "build" / "scip" / "kotlin.scip").write_bytes(_kotlin_index())
    (ws / "cairn.json").write_text(json.dumps({"scip": {"kotlin": "build/scip/kotlin.scip"}}))

    db = str(tmp_path / "scip_skip.db")
    summary = build_graph(workspace=str(ws), db_path=db)

    # The SCIP symbol is present with source='scip'.
    import sqlite3
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    scip_syms = conn.execute(
        "SELECT name, source FROM symbols WHERE source = 'scip'"
    ).fetchall()
    assert len(scip_syms) == 1
    assert scip_syms[0]["name"] == "Foo"

    # No tree-sitter symbol named 'Foo' (the .kt file was skipped). The Kotlin
    # class would otherwise produce a tree_sitter 'Foo' symbol too.
    ts_foo = conn.execute(
        "SELECT source FROM symbols WHERE name = 'Foo' AND source != 'scip'"
    ).fetchall()
    assert ts_foo == [], "Kotlin file was tree-sitter-parsed despite SCIP index"

    # Python still went through tree-sitter (no SCIP index for it).
    ts_bar = conn.execute(
        "SELECT source FROM symbols WHERE name = 'bar'"
    ).fetchall()
    assert len(ts_bar) == 1
    assert ts_bar[0]["source"] == "tree_sitter"

    # Summary carries the SCIP import stats.
    assert "scip" in summary
    assert "kotlin" in summary["scip"]
    conn.close()


def test_missing_index_falls_back_to_tree_sitter(tmp_path):
    """An absent index file (declared but not present) falls back to tree-sitter."""
    ws = _make_workspace(tmp_path, "scip_missing")
    # Declare kotlin but DON'T create the file.
    (ws / "cairn.json").write_text(json.dumps({"scip": {"kotlin": "build/scip/absent.scip"}}))

    db = str(tmp_path / "scip_missing.db")
    summary = build_graph(workspace=str(ws), db_path=db)

    import sqlite3
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    # No SCIP data (file was absent) -> Foo came from tree-sitter.
    foo = conn.execute("SELECT source FROM symbols WHERE name = 'Foo'").fetchall()
    assert len(foo) == 1
    assert foo[0]["source"] == "tree_sitter"
    # No SCIP stats in summary (nothing imported).
    assert "scip" not in summary
    conn.close()


def test_no_config_is_pure_tree_sitter(tmp_path):
    """No cairn.json -> everything is tree-sitter, no SCIP hook runs."""
    ws = _make_workspace(tmp_path, "no_config")
    db = str(tmp_path / "no_config.db")
    summary = build_graph(workspace=str(ws), db_path=db)

    import sqlite3
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    sources = {r["source"] for r in conn.execute("SELECT DISTINCT source FROM symbols").fetchall()}
    # No 'scip'; legacy NULLs treated as tree_sitter but new builds tag it.
    assert "scip" not in sources
    assert "scip" not in summary
    conn.close()
