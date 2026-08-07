"""Tests for the SCIP / tree-sitter coexistence build path.

Verifies that when ``cairn.json`` declares a SCIP index for a language and the
index file exists:
  - tree-sitter STILL parses those files (providing modifiers, body, inheritance),
  - the SCIP importer runs post-resolve and merges exact-resolution edges onto
    the tree-sitter symbol rows (source='merged'),
  - a missing index file falls back to pure tree-sitter for that language,
  - the auto-generation orchestrator fires before the existence gate.

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
    # A Kotlin file (tree-sitter-parsed AND SCIP-merged) + a Python file
    # (always tree-sitter only; no SCIP index declared for python).
    (repo / "Foo.kt").write_text("class Foo { fun go() {} }\n")
    (repo / "bar.py").write_text("def bar():\n    pass\n")
    return ws


def _kotlin_index() -> bytes:
    """A minimal real SCIP protobuf index with one Kotlin symbol (Foo)."""
    from cairn.parsers import _scip_pb2  # deferred: see module-level note

    idx = _scip_pb2.Index()
    doc = idx.documents.add()
    doc.relative_path = "demo/Foo.kt"
    doc.language = "kotlin"
    occ = doc.occurrences.add()
    occ.symbol = "scip-kotlin com example Foo#"
    occ.symbol_roles = 1  # Definition
    occ.syntax_kind = 19  # IdentifierType -> class
    occ.single_line_range.line = 0  # 0-based -> 1-based line 1 (matches TS)
    occ.single_line_range.start_character = 6
    occ.single_line_range.end_character = 9
    return idx.SerializeToString()


def test_scip_coexists_with_tree_sitter(tmp_path):
    """Both sources run: tree-sitter provides structure, SCIP merges exact edges.

    The Foo symbol ends up source='merged' -- one row carrying tree-sitter's
    kind ('class') AND SCIP's richer qualified_name. Python (no SCIP index)
    stays pure tree-sitter.
    """
    ws = _make_workspace(tmp_path, "scip_coexist")
    (ws / "build" / "scip").mkdir(parents=True)
    (ws / "build" / "scip" / "kotlin.scip").write_bytes(_kotlin_index())
    (ws / "cairn.json").write_text(json.dumps({"scip": {"kotlin": "build/scip/kotlin.scip"}}))

    db = str(tmp_path / "scip_coexist.db")
    summary = build_graph(workspace=str(ws), db_path=db)

    import sqlite3
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row

    # Foo is merged: one row, source='merged', tree-sitter kind preserved.
    foo_rows = conn.execute(
        "SELECT name, source, kind FROM symbols WHERE name = 'Foo'"
    ).fetchall()
    assert len(foo_rows) == 1, f"expected 1 merged Foo, got {len(foo_rows)}"
    assert foo_rows[0]["source"] == "merged"
    assert foo_rows[0]["kind"] == "class"  # tree-sitter kind preserved

    # Python still went through tree-sitter (no SCIP index for it).
    bar = conn.execute(
        "SELECT source FROM symbols WHERE name = 'bar'"
    ).fetchone()
    assert bar is not None
    assert bar["source"] == "tree_sitter"

    # Summary carries the SCIP import + merge stats.
    assert "scip" in summary
    assert "kotlin" in summary["scip"]
    assert summary["scip"]["kotlin"]["symbols_merged"] >= 1
    conn.close()


def test_missing_index_falls_back_to_pure_tree_sitter(tmp_path):
    """An absent index file (declared but not present) -> pure tree-sitter."""
    ws = _make_workspace(tmp_path, "scip_missing")
    (ws / "cairn.json").write_text(json.dumps({"scip": {"kotlin": "build/scip/absent.scip"}}))

    db = str(tmp_path / "scip_missing.db")
    summary = build_graph(workspace=str(ws), db_path=db)

    import sqlite3
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    foo = conn.execute("SELECT source FROM symbols WHERE name = 'Foo'").fetchone()
    assert foo["source"] == "tree_sitter"
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
    assert "scip" not in sources
    assert "merged" not in sources
    assert "scip" not in summary
    conn.close()


def test_build_auto_generates_missing_index(tmp_path, monkeypatch):
    """A declared-but-absent index is auto-generated, then merged with tree-sitter.

    The orchestrator fires before the existence gate, produces the index, and
    the coexistence merge folds it into the tree-sitter row (source='merged').
    """
    ws = _make_workspace(tmp_path, "scip_autogen")
    (ws / "cairn.json").write_text(
        json.dumps({"scip": {"kotlin": "build/scip/kotlin.scip"}})
    )

    from cairn.parsers import scip_indexers

    def fake_generate(language, output_path, repo_path, log=lambda *a, **k: None):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(_kotlin_index())
        return True

    monkeypatch.setattr(scip_indexers, "try_generate_index", fake_generate)

    db = str(tmp_path / "scip_autogen.db")
    summary = build_graph(workspace=str(ws), db_path=db)

    import sqlite3
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    foo = conn.execute("SELECT source FROM symbols WHERE name = 'Foo'").fetchone()
    assert foo is not None
    assert foo["source"] == "merged", "auto-generated index should merge with tree-sitter"
    assert "scip" in summary and "kotlin" in summary["scip"]
    conn.close()


def test_build_generation_failure_falls_back_to_tree_sitter(tmp_path, monkeypatch):
    """If the orchestrator fails to produce the index, pure tree-sitter is used."""
    ws = _make_workspace(tmp_path, "scip_autogen_fail")
    (ws / "cairn.json").write_text(
        json.dumps({"scip": {"kotlin": "build/scip/kotlin.scip"}})
    )

    from cairn.parsers import scip_indexers

    def failing_generate(language, output_path, repo_path, log=lambda *a, **k: None):
        return False

    monkeypatch.setattr(scip_indexers, "try_generate_index", failing_generate)

    db = str(tmp_path / "scip_autogen_fail.db")
    summary = build_graph(workspace=str(ws), db_path=db)

    import sqlite3
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    foo = conn.execute("SELECT source FROM symbols WHERE name = 'Foo'").fetchone()
    assert foo["source"] == "tree_sitter"
    assert "scip" not in summary
    conn.close()
