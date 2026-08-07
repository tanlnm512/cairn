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


def test_protobuf_reuses_tree_sitter_file_id_when_coexisting():
    """In coexistence mode, SCIP symbols link to the existing tree-sitter file row.

    Tree-sitter creates file rows with uuid ids and a real hash; SCIP's id
    format is ``{repo}:{rel}``. Without reconciliation, SCIP's INSERT OR IGNORE
    no-ops on UNIQUE(repo_id, path) and SCIP symbols dangle off a non-existent
    file_id, silently vanishing from every JOIN. The importer must look up the
    real tree-sitter row and use ITS id.
    """
    conn = _conn()
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('demo','demo','.')")
    # Tree-sitter creates this with a uuid id (not the {repo}:{rel} format).
    ts_file_id = "a1b2c3d4e5f6"  # uuid-like, NOT "demo:src/Foo.swift"
    conn.execute(
        "INSERT INTO files (id, path, repo_id, hash, line_count, language) "
        "VALUES (?, 'src/Foo.swift', 'demo', 'ts_hash_123', 10, 'swift')",
        (ts_file_id,),
    )
    conn.commit()

    idx = _scip_pb2.Index()
    doc = idx.documents.add()
    doc.relative_path = "src/Foo.swift"
    doc.language = "swift"
    _occ(doc, "scip-swift swift demo Foo#", roles=1, syntax_kind=19,
         line=0, start=6, end=9)

    import_scip_bytes(conn, idx.SerializeToString(), repo_id="demo")

    # The SCIP symbol must reference the TREE-SITTER file id, not a shadow.
    sym = conn.execute("SELECT file_id FROM symbols").fetchone()
    assert sym["file_id"] == ts_file_id, (
        "SCIP symbol should link to the tree-sitter file row, not a shadow"
    )
    # Only ONE file row exists (no duplicate shadow).
    files = conn.execute(
        "SELECT id FROM files WHERE path = 'src/Foo.swift'"
    ).fetchall()
    assert len(files) == 1


# ---------------------------------------------------------------------------
# Coexistence merge: tree-sitter metadata + SCIP exact edges in one row
# ---------------------------------------------------------------------------
# The core feature: when both sources cover a file, the SCIP definition is
# folded into the tree-sitter symbol. One row carries tree-sitter's modifiers/
# body/parent_scope + SCIP's richer qualified_name and exact-resolution edges.

def test_merge_folds_scip_definition_into_tree_sitter_symbol():
    """A SCIP definition matching a tree-sitter symbol enriches it, not duplicates.

    After merge: one symbol row (source='merged') with tree-sitter's modifiers
    preserved and SCIP's qualified_name adopted. Tree-sitter call edges for the
    symbol are replaced by SCIP's exact edges; inheritance edges survive.
    """
    conn = _conn()
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('demo','demo','.')")
    conn.execute(
        "INSERT INTO files (id, path, repo_id, hash, line_count, language) "
        "VALUES ('ts-file-1', 'Foo.swift', 'demo', 'ts_hash', 10, 'swift')"
    )
    # Tree-sitter symbol with metadata SCIP can't provide.
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind, "
        "line_start, line_end, column_start, column_end, modifiers, source) "
        "VALUES ('ts-sym-1', 'ts-file-1', 'Greeter', 'Greeter', 'class', "
        "1, 5, 6, 13, '[\"public\",\"final\"]', 'tree_sitter')"
    )
    # Tree-sitter call edge (fuzzy resolution) + inheritance edge (SCIP can't emit).
    conn.execute(
        "INSERT INTO edges (id, source_id, target_name, kind, line, resolution) "
        "VALUES ('ts-edge-1', 'ts-sym-1', 'helper', 'calls', 3, 'ambiguous')"
    )
    conn.execute(
        "INSERT INTO edges (id, source_id, target_name, kind, line) "
        "VALUES ('ts-edge-2', 'ts-sym-1', 'Base', 'implements', 1)"
    )
    conn.commit()

    # SCIP index: same definition + an exact-resolution call edge.
    idx = _scip_pb2.Index()
    doc = idx.documents.add()
    doc.relative_path = "Foo.swift"
    doc.language = "swift"
    # Definition at line 1 (matches tree-sitter's line_start=1).
    _occ(doc, "scip-swift swift demo Greeter#", roles=1, syntax_kind=19,
         line=0, start=6, end=13)  # 0-based line 0 = 1-based line 1
    # Exact call edge inside Greeter.
    ref = _occ(doc, "scip-swift swift demo helper#", roles=0, line=2, start=4, end=10)
    ref.multi_line_enclosing_range.start_line = 0
    ref.multi_line_enclosing_range.start_character = 0
    ref.multi_line_enclosing_range.end_line = 5
    ref.multi_line_enclosing_range.end_character = 0

    stats = import_scip_bytes(conn, idx.SerializeToString(), repo_id="demo")
    assert stats["symbols_merged"] == 1

    # One symbol row, source='merged', carrying tree-sitter's modifiers.
    syms = conn.execute(
        "SELECT source, modifiers, qualified_name FROM symbols WHERE name = 'Greeter'"
    ).fetchall()
    assert len(syms) == 1, f"expected 1 merged row, got {len(syms)}"
    assert syms[0]["source"] == "merged"
    assert syms[0]["modifiers"] == '["public","final"]'  # tree-sitter preserved
    assert "scip-swift" in syms[0]["qualified_name"]  # SCIP adopted

    # Tree-sitter call edge replaced; inheritance edge survived.
    edges = conn.execute(
        "SELECT kind, resolution FROM edges WHERE source_id = 'ts-sym-1'"
    ).fetchall()
    kinds = {e["kind"] for e in edges}
    assert "implements" in kinds, "inheritance edge must survive merge"
    # The fuzzy 'calls' edge is gone; SCIP's exact 'call' edge took over.
    assert "calls" not in kinds, "tree-sitter calls edge should be replaced"
    assert "call" in kinds, "SCIP exact call edge should be present"


def test_merge_preserves_tree_sitter_docstring_when_scip_has_none():
    """If SCIP doesn't carry a docstring, tree-sitter's survives (COALESCE)."""
    conn = _conn()
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('demo','demo','.')")
    conn.execute(
        "INSERT INTO files (id, path, repo_id, hash, line_count, language) "
        "VALUES ('ts-f', 'Bar.kt', 'demo', 'h', 5, 'kotlin')"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind, "
        "line_start, modifiers, docstring, source) "
        "VALUES ('ts-b', 'ts-f', 'Bar', 'Bar', 'class', 1, '[]', 'TS doc', 'tree_sitter')"
    )
    conn.commit()

    idx = _scip_pb2.Index()
    doc = idx.documents.add()
    doc.relative_path = "Bar.kt"
    doc.language = "kotlin"
    _occ(doc, "scip-kotlin com example Bar#", roles=1, syntax_kind=19, line=0)

    import_scip_bytes(conn, idx.SerializeToString(), repo_id="demo")
    row = conn.execute("SELECT docstring FROM symbols WHERE name = 'Bar'").fetchone()
    assert row["docstring"] == "TS doc"  # tree-sitter's docstring preserved


def test_unmatched_scip_definition_left_as_standalone():
    """A SCIP def with no tree-sitter match stays source='scip' (not lost)."""
    conn = _conn()
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('demo','demo','.')")
    conn.execute(
        "INSERT INTO files (id, path, repo_id, hash, line_count, language) "
        "VALUES ('ts-f', 'Baz.swift', 'demo', 'h', 5, 'swift')"
    )
    conn.commit()
    # No tree-sitter symbol for "Baz" -- SCIP's row should stand alone.
    idx = _scip_pb2.Index()
    doc = idx.documents.add()
    doc.relative_path = "Baz.swift"
    _occ(doc, "scip-swift swift demo Baz#", roles=1, syntax_kind=19, line=0)
    import_scip_bytes(conn, idx.SerializeToString(), repo_id="demo")
    row = conn.execute("SELECT source FROM symbols WHERE name = 'Baz'").fetchone()
    assert row["source"] == "scip"  # standalone, not merged


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


# ---------------------------------------------------------------------------
# project_root resolution (multi-repo path normalization)
# ---------------------------------------------------------------------------
# These are the only tests that pass ws_root to import_scip_bytes, so they
# exercise the Metadata.project_root -> (repo_id, repo-relative) mapping that
# the build-integrated path relies on. scip-swift writes project_root as a
# file:// URL; without scheme handling the path joined garbage onto ws_root and
# every document was silently mis-attributed.

def _index_with_project_root(raw_root: str):
    """Minimal index with one definition, metadata.project_root set to raw_root.

    Document.relative_path is repo-relative ("demo/Foo.swift"); project_root
    names the repo root so the importer should resolve the document under the
    correct (repo_id, repo-relative path).
    """
    idx = _scip_pb2.Index()
    idx.metadata.project_root = raw_root
    doc = idx.documents.add()
    doc.relative_path = "demo/Foo.swift"
    doc.language = "swift"
    occ = doc.occurrences.add()
    occ.symbol = "scip-swift swift demo Greeter#"
    occ.symbol_roles = 1  # Definition
    occ.syntax_kind = 19  # IdentifierType -> class
    occ.single_line_range.line = 0
    occ.single_line_range.start_character = 6
    occ.single_line_range.end_character = 13
    return idx


def test_project_root_file_url_resolves(tmp_path):
    """scip-swift's file://-prefixed project_root must resolve to the real repo.

    Regression: Path("file:///abs").is_absolute() is False, so the old inline
    expression joined the URL onto ws_root and the document landed under the
    fallback repo id with a broken path.
    """
    ws = tmp_path / "ws"
    repo = ws / "demo"
    (repo / ".git").mkdir(parents=True)

    idx = _index_with_project_root(f"file://{repo}/")
    conn = _conn()
    import_scip_bytes(
        conn, idx.SerializeToString(),
        repo_id="default", ws_root=ws.resolve(),
    )

    # The file row's repo_id is the inferred repo ("demo"), NOT "default",
    # and its path is repo-relative ("demo/Foo.swift"), proving the file://
    # URL was stripped and resolved against the workspace.
    row = conn.execute("SELECT repo_id, path FROM files").fetchone()
    assert row["repo_id"] == "demo"
    assert row["path"] == "demo/Foo.swift"


def test_project_root_file_url_localhost_resolves(tmp_path):
    """The file://localhost/ host form resolves the same way."""
    ws = tmp_path / "ws"
    repo = ws / "demo"
    (repo / ".git").mkdir(parents=True)

    idx = _index_with_project_root(f"file://localhost{repo}/")
    conn = _conn()
    import_scip_bytes(
        conn, idx.SerializeToString(),
        repo_id="default", ws_root=ws.resolve(),
    )
    row = conn.execute("SELECT repo_id, path FROM files").fetchone()
    assert row["repo_id"] == "demo"
    assert row["path"] == "demo/Foo.swift"


def test_project_root_plain_absolute_resolves(tmp_path):
    """A plain absolute path (scip-kotlin/scip-typescript convention) resolves."""
    ws = tmp_path / "ws"
    repo = ws / "demo"
    (repo / ".git").mkdir(parents=True)

    idx = _index_with_project_root(str(repo))
    conn = _conn()
    import_scip_bytes(
        conn, idx.SerializeToString(),
        repo_id="default", ws_root=ws.resolve(),
    )
    row = conn.execute("SELECT repo_id, path FROM files").fetchone()
    assert row["repo_id"] == "demo"
    assert row["path"] == "demo/Foo.swift"


def test_project_root_relative_to_ws_resolves(tmp_path):
    """A project_root relative to ws_root joins onto ws_root (single-repo case)."""
    ws = tmp_path / "ws"
    # Single-repo workspace: the repo IS the workspace root.
    (ws / ".git").mkdir(parents=True)

    idx = _index_with_project_root(".")  # relative -> ws_root
    idx.documents[0].relative_path = "TopLevel.swift"
    conn = _conn()
    import_scip_bytes(
        conn, idx.SerializeToString(),
        repo_id="default", ws_root=ws.resolve(),
    )
    row = conn.execute("SELECT path FROM files").fetchone()
    assert row["path"] == "TopLevel.swift"


# ---------------------------------------------------------------------------
# source_id NOT NULL: file-level occurrences with no enclosing definition
# ---------------------------------------------------------------------------
# Found against REAL scip-swift output: a Swift main.swift has top-level code
# (let g = Greeter(...); print(g.greet())) where references to stdlib symbols
# (print, String interpolation) appear before any enclosing definition. The
# importer used to insert these with source_id=NULL, crashing on the NOT NULL
# FK on edges.source_id. Now they're skipped (matching tree-sitter's behavior).

def test_protobuf_file_level_occurrence_without_enclosing_def_is_skipped():
    """An occurrence with no enclosing definition must not crash (NOT NULL FK).

    Regression: a reference at the top of a file (before any definition, or in
    a file with only top-level code) has no source symbol. edges.source_id is
    NOT NULL, so inserting it crashed the whole import. Now skipped, matching
    the tree-sitter path's "file-level call with no owning symbol" handling.
    """
    idx = _scip_pb2.Index()
    doc = idx.documents.add()
    doc.relative_path = "main.swift"
    doc.language = "swift"
    # A definition on line 5...
    _occ(doc, "scip-swift swift main Greeter#", roles=1, syntax_kind=19,
         line=4, start=7, end=14)
    # ...and a reference on line 0 (BEFORE the definition, so no preceding def).
    # No enclosing_range is set, so source_id stays None.
    _occ(doc, "scip-swift swift . print()", roles=0, line=0, start=0, end=5)
    # (deliberately leave enclosing_range unset)

    conn = _conn()
    # Must not raise IntegrityError (NOT NULL constraint).
    stats = import_scip_bytes(conn, idx.SerializeToString(), repo_id="demo")
    # The definition symbol is imported; the orphan reference is skipped.
    assert stats["symbols_added"] == 1
    # The print() reference had no enclosing def -> skipped, not stored.
    orphan = conn.execute(
        "SELECT COUNT(*) AS n FROM edges WHERE target_name = 'print()'"
    ).fetchone()
    assert orphan["n"] == 0


def test_json_file_level_occurrence_without_enclosing_def_is_skipped():
    """Same regression guard for the JSON path."""
    payload = {
        "documents": [{
            "relative_path": "main.swift", "language": "swift",
            "occurrences": [
                # Definition on line 3 (0-based 2).
                {"symbol": "scip-swift swift main Foo#", "symbol_roles": 1,
                 "range": [2, 0, 3]},
                # Reference on line 1 (0-based 0) -- before any definition.
                {"symbol": "scip-swift swift . bar()", "symbol_roles": 0,
                 "range": [0, 0, 3]},
            ],
        }]
    }
    conn = _conn()
    # Must not raise.
    import_scip_data(conn, payload, repo_id="demo")
    orphan = conn.execute(
        "SELECT COUNT(*) AS n FROM edges WHERE target_name = 'bar()'"
    ).fetchone()
    assert orphan["n"] == 0


# ---------------------------------------------------------------------------
# Language inference: empty Document.language falls back to file extension
# ---------------------------------------------------------------------------
# Found against REAL scip-java 0.10.4 output: it emits language='' for both
# Java and Kotlin documents. Without extension-based fallback the importer
# stored 'scip', breaking the hybrid skip logic (which keys off files.language).

def test_language_inferred_from_extension_when_document_omits_it():
    """An empty Document.language is derived from the file extension.

    scip-java 0.10.4 leaves language blank; the importer must still tag Java
    files 'java' and Kotlin files 'kotlin' so the hybrid skip + downstream
    tooling work.
    """
    idx = _scip_pb2.Index()
    for rel, lang in [("src/Main.java", ""), ("src/Foo.kt", "")]:
        doc = idx.documents.add()
        doc.relative_path = rel
        doc.language = lang  # empty -- the bug case
        _occ(doc, f"semanticdb maven . . {rel}#Bar", roles=1, syntax_kind=19,
             line=0)
    conn = _conn()
    import_scip_bytes(conn, idx.SerializeToString(), repo_id="demo")
    langs = {r["path"]: r["language"] for r in conn.execute(
        "SELECT path, language FROM files").fetchall()}
    assert langs["src/Main.java"] == "java"
    assert langs["src/Foo.kt"] == "kotlin"


def test_language_normalized_to_lowercase():
    """scip-swift emits 'Swift' (capitalized); scanner keys are lowercase."""
    idx = _scip_pb2.Index()
    doc = idx.documents.add()
    doc.relative_path = "Sources/App/main.swift"
    doc.language = "Swift"  # capitalized, as scip-swift actually emits
    _occ(doc, "scip-swift swift main App#", roles=1, syntax_kind=19, line=0)
    conn = _conn()
    import_scip_bytes(conn, idx.SerializeToString(), repo_id="demo")
    row = conn.execute("SELECT language FROM files").fetchone()
    assert row["language"] == "swift"


def test_language_falls_back_to_scip_for_unknown_extension():
    """An unrecognized extension with no declared language stays 'scip'."""
    idx = _scip_pb2.Index()
    doc = idx.documents.add()
    doc.relative_path = "weird.xyz"
    doc.language = ""
    _occ(doc, "scip-unknown unknown main Foo#", roles=1, syntax_kind=19, line=0)
    conn = _conn()
    import_scip_bytes(conn, idx.SerializeToString(), repo_id="demo")
    row = conn.execute("SELECT language FROM files").fetchone()
    assert row["language"] == "scip"

