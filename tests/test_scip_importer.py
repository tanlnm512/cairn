"""Tests for the rewritten SCIP importer (protobuf + JSON, two-pass resolution).

These construct real ``scip_pb2.Index`` messages programmatically (no sample
index files checked in) and assert the importer produces correct symbols,
edges, resolution, provenance, and range data. The legacy JSON path is covered
too so ``test_big_tech_improvements.py``'s regression contract holds.

Skipped when the optional ``[scip]`` extra (protobuf runtime) isn't installed.
"""
from __future__ import annotations

import sqlite3

import pytest

from cairn.graph.schema import _apply_schema
from cairn.parsers.scip_importer import (
    import_scip_bytes,
    import_scip_data,
    import_scip_file,
    scip_available,
)

# The whole module needs protobuf. Skip cleanly if the runtime isn't there.
pytestmark = pytest.mark.skipif(not scip_available(), reason="[scip] extra not installed")
_PROTO = pytest.importorskip("google.protobuf")  # belt-and-suspenders
_scip_pb2 = pytest.importorskip("cairn.parsers._scip_pb2")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    _apply_schema(c)
    return c


def _occ(doc, symbol, roles=0, syntax_kind=0, **range_kw):
    """Helper to add an Occurrence with a typed single-line range."""
    occ = doc.occurrences.add()
    occ.symbol = symbol
    occ.symbol_roles = roles
    occ.syntax_kind = syntax_kind
    sl = range_kw.setdefault("line", 0)
    occ.single_line_range.line = sl
    occ.single_line_range.start_character = range_kw.setdefault("start", 0)
    occ.single_line_range.end_character = range_kw.setdefault("end", 5)
    return occ


# ---------------------------------------------------------------------------
# Protobuf: cross-file resolution is real, not a placeholder
# ---------------------------------------------------------------------------

def test_protobuf_cross_file_resolution_is_exact():
    """A reference whose target is a definition in the same index resolves exact."""
    idx = _scip_pb2.Index()
    doc = idx.documents.add()
    doc.relative_path = "src/main.py"
    doc.language = "python"
    _occ(doc, "scip-python python main main_func#", roles=1, syntax_kind=16,
         line=0, start=4, end=13)
    _occ(doc, "scip-python python main helper#", roles=1, syntax_kind=16,
         line=5, start=4, end=10)
    ref = _occ(doc, "scip-python python main helper#", roles=0, syntax_kind=15,
               line=2, start=4, end=10)
    ref.multi_line_enclosing_range.start_line = 0
    ref.multi_line_enclosing_range.start_character = 0
    ref.multi_line_enclosing_range.end_line = 10
    ref.multi_line_enclosing_range.end_character = 0

    conn = _conn()
    stats = import_scip_bytes(conn, idx.SerializeToString(), repo_id="demo")
    assert stats["symbols_added"] == 2
    assert stats["edges_added"] == 1

    edge = conn.execute(
        "SELECT target_name, resolution, source_id, target_id FROM edges"
    ).fetchone()
    assert edge["target_name"] == "helper"
    assert edge["resolution"] == "exact"
    assert edge["target_id"] is not None
    assert "helper" in edge["target_id"]
    # source_id points to the enclosing definition (main_func).
    assert "main_func" in edge["source_id"]


def test_protobuf_external_reference_is_unresolved():
    """A reference with no in-index definition is tagged 'unresolved', not 'exact'."""
    idx = _scip_pb2.Index()
    doc = idx.documents.add()
    doc.relative_path = "src/main.py"
    _occ(doc, "scip-python python main main_func#", roles=1, syntax_kind=16,
         line=0, start=4, end=13)
    # Reference to an external symbol (stdlib println, never defined here).
    # The descriptor's short name is 'println()' (SCIP keeps the parens for
    # callable symbols), so the edge's target_name is 'println()'.
    _occ(doc, "scip-python python . println()", roles=0, line=2, start=0, end=8)

    conn = _conn()
    import_scip_bytes(conn, idx.SerializeToString(), repo_id="demo")
    edge = conn.execute(
        "SELECT target_name, resolution FROM edges"
    ).fetchone()
    assert edge is not None
    assert edge["target_name"] == "println()"
    assert edge["resolution"] == "unresolved"


def test_protobuf_symbols_tagged_source_scip():
    """Every imported symbol carries source='scip' provenance."""
    idx = _scip_pb2.Index()
    doc = idx.documents.add()
    doc.relative_path = "a.kt"
    _occ(doc, "scip-kotlin com example Foo#", roles=1, syntax_kind=19, line=0)
    conn = _conn()
    import_scip_bytes(conn, idx.SerializeToString(), repo_id="r")
    row = conn.execute("SELECT source FROM symbols").fetchone()
    assert row["source"] == "scip"


def test_protobuf_typed_range_preferred_over_deprecated():
    """When both typed_range and the deprecated range are set, typed wins."""
    idx = _scip_pb2.Index()
    doc = idx.documents.add()
    doc.relative_path = "a.py"
    occ = doc.occurrences.add()
    occ.symbol = "scip-python python foo#"
    occ.symbol_roles = 1
    occ.syntax_kind = 16
    # Set BOTH; typed must win (line 5, not line 99).
    occ.single_line_range.line = 5
    occ.single_line_range.start_character = 2
    occ.single_line_range.end_character = 5
    occ.range.extend([99, 0, 3])

    conn = _conn()
    import_scip_bytes(conn, idx.SerializeToString(), repo_id="r")
    sym = conn.execute("SELECT line_start, column_start FROM symbols").fetchone()
    assert sym["line_start"] == 6  # 0-based 5 -> 1-based 6
    assert sym["column_start"] == 2


def test_protobuf_deprecated_range_fallback():
    """An indexer that only sets the deprecated repeated-int32 range still works."""
    idx = _scip_pb2.Index()
    doc = idx.documents.add()
    doc.relative_path = "a.py"
    occ = doc.occurrences.add()
    occ.symbol = "scip-python python foo#"
    occ.symbol_roles = 1
    occ.syntax_kind = 16
    occ.range.extend([3, 0, 8, 0])  # multi-line: startLine 3, startChar 0, endLine 8, endChar 0

    conn = _conn()
    import_scip_bytes(conn, idx.SerializeToString(), repo_id="r")
    sym = conn.execute("SELECT line_start, line_end, column_start FROM symbols").fetchone()
    assert sym["line_start"] == 4
    assert sym["line_end"] == 9
    assert sym["column_start"] == 0


def test_protobuf_does_not_clobber_existing_tree_sitter_rows():
    """import_scip on an already-built DB must not overwrite tree-sitter metadata."""
    conn = _conn()
    # Pre-existing tree-sitter file row with real metadata.
    conn.execute(
        "INSERT INTO repos (id, name, path) VALUES ('r','r','.')"
    )
    conn.execute(
        "INSERT INTO files (id, path, repo_id, hash, line_count, language) "
        "VALUES ('r:a.py', 'a.py', 'r', 'abc123', 42, 'python')"
    )
    conn.commit()

    idx = _scip_pb2.Index()
    doc = idx.documents.add()
    doc.relative_path = "a.py"
    _occ(doc, "scip-python python foo#", roles=1, syntax_kind=16, line=0)
    import_scip_bytes(conn, idx.SerializeToString(), repo_id="r")

    row = conn.execute("SELECT hash, line_count FROM files WHERE id = 'r:a.py'").fetchone()
    # INSERT OR IGNORE: original tree-sitter metadata survives.
    assert row["hash"] == "abc123"
    assert row["line_count"] == 42


def test_import_scip_file_proto_and_json(tmp_path):
    """import_scip_file reads both formats via the fmt flag."""
    # Proto
    idx = _scip_pb2.Index()
    doc = idx.documents.add()
    doc.relative_path = "a.py"
    _occ(doc, "scip-python python foo#", roles=1, syntax_kind=16, line=0)
    proto_path = tmp_path / "a.scip"
    proto_path.write_bytes(idx.SerializeToString())

    conn = _conn()
    stats = import_scip_file(conn, str(proto_path), repo_id="r", fmt="proto")
    assert stats["symbols_added"] == 1

    # JSON (legacy shape)
    import json
    json_path = tmp_path / "a.json"
    json_path.write_text(json.dumps({
        "documents": [{
            "relative_path": "b.py", "language": "python",
            "occurrences": [
                {"symbol": "scip-python python bar#", "symbol_roles": 1, "range": [0, 0, 3]},
            ],
        }]
    }))
    conn2 = _conn()
    stats2 = import_scip_file(conn2, str(json_path), repo_id="r", fmt="json")
    assert stats2["symbols_added"] == 1


# ---------------------------------------------------------------------------
# JSON path (legacy) -- keeps test_big_tech_improvements.py's contract
# ---------------------------------------------------------------------------

def test_json_unresolved_when_no_definition():
    """The legacy JSON fixture (ref with no def) now correctly resolves unresolved.

    Previously the importer hardcoded resolution='exact' for every edge; the
    rewrite resolves against the in-index definition map, so a reference whose
    target isn't defined in the payload is 'unresolved' (real, not placeholder).
    """
    payload = {
        "documents": [{
            "relative_path": "src/main.py", "language": "python",
            "occurrences": [
                {"symbol": "scip-python python main main_func#", "symbol_roles": 1, "range": [0, 0, 0, 9]},
                {"symbol": "scip-python python main helper#", "symbol_roles": 0, "range": [5, 4, 5, 10]},
            ],
        }]
    }
    conn = _conn()
    stats = import_scip_data(conn, payload, repo_id="default")
    assert stats["files_added"] == 1
    assert stats["symbols_added"] == 1  # only main_func is a definition
    assert stats["edges_added"] == 1
    edge = conn.execute("SELECT resolution FROM edges WHERE target_name = 'helper'").fetchone()
    assert edge["resolution"] == "unresolved"


def test_json_exact_when_definition_present():
    payload = {
        "documents": [{
            "relative_path": "src/main.py", "language": "python",
            "occurrences": [
                {"symbol": "scip-python python main helper#", "symbol_roles": 1, "range": [0, 0, 0, 6]},
                {"symbol": "scip-python python main helper#", "symbol_roles": 0, "range": [5, 4, 5, 10]},
            ],
        }]
    }
    conn = _conn()
    import_scip_data(conn, payload, repo_id="default")
    edge = conn.execute("SELECT resolution FROM edges WHERE target_name = 'helper'").fetchone()
    assert edge["resolution"] == "exact"
