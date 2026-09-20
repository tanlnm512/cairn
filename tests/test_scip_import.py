"""SCIP importer core: availability guard, position join, occurrence
classification, resolution labeling, and validate-then-write edges-only
overlay (FR-001, FR-003, FR-006).

Hermeticity (CONSTITUTION C-04): no cairn.cli imports; fixture workspaces
are copied into tmp_path with a bare .git marker and built into tmp DBs --
never the real ~/.cairn. Index-parsing tests skip when the optional [scip]
extra is absent; the availability-guard tests run unconditionally.
"""
from __future__ import annotations

import importlib
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from cairn.graph.builder import build_graph
from cairn.parsers.scip_importer import import_scip_file, scip_available

FIXTURES = Path(__file__).parent / "fixtures" / "scip-indexing"

needs_scip = pytest.mark.skipif(not scip_available(), reason="[scip] extra not installed")


@pytest.fixture(autouse=True)
def _hash_embedder(hash_backend):
    """Pin the dep-free hash embedder so builds stay deterministic and offline."""


def _materialize(name: str, tmp_path: Path) -> Path:
    """Copy a fixture workspace into tmp with a bare .git marker (the scanner's
    repo key); git is unusable in the copy, matching the incremental suite."""
    ws = tmp_path / name
    shutil.copytree(FIXTURES / name, ws, ignore=shutil.ignore_patterns(".git"))
    (ws / ".git").mkdir()
    return ws


def _build(name: str, tmp_path: Path) -> tuple[Path, str]:
    ws = _materialize(name, tmp_path)
    db = str(tmp_path / f"{name}.db")
    build_graph(workspace=str(ws), db_path=db)
    return ws, db


def _build_tree_sitter_only(name: str, tmp_path: Path) -> tuple[Path, str]:
    """Build with the fixture's scip config removed, so importer-level tests
    exercise the overlay from a pure tree-sitter state."""
    ws = _materialize(name, tmp_path)
    (ws / "cairn.json").unlink()
    db = str(tmp_path / f"{name}.ts.db")
    build_graph(workspace=str(ws), db_path=db)
    return ws, db


def _count(db: str, sql: str) -> int:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql).fetchone()[0]
    finally:
        conn.close()


def _scip_edge_count(db: str, where: str) -> int:
    return _count(
        db,
        "select count(*) from edges e join symbols s on e.source_id=s.id "
        "join files f on s.file_id=f.id where e.source='scip' and " + where,
    )


# ---------------------------------------------------------------------------
# Availability guard (FR-006)
# ---------------------------------------------------------------------------
def test_importer_imports_without_protobuf_runtime(tmp_path):
    """A broken google.protobuf import degrades scip_available() to False instead
    of breaking the import (the ImportError arm; the build-side TC-008 shape)."""
    poison = tmp_path / "noscip" / "google" / "protobuf"
    poison.mkdir(parents=True)
    (tmp_path / "noscip" / "google" / "__init__.py").write_text("")
    (poison / "__init__.py").write_text("raise ImportError('protobuf unavailable')\n")
    code = (
        "from cairn.parsers.scip_importer import import_scip_file, scip_available\n"
        "assert scip_available() is False\n"
        "assert callable(import_scip_file)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, "PYTHONPATH": str(tmp_path / "noscip")},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr


def test_version_mismatched_runtime_degrades_to_unavailable(monkeypatch):
    """scip_available() also catches protobuf's VersionError (runtime older than
    the vendored stub's gencode), not just ImportError (FR-006)."""
    from google.protobuf import runtime_version

    from cairn.parsers import scip_importer

    def boom(*_args, **_kwargs):
        raise runtime_version.VersionError("runtime older than gencode")

    monkeypatch.setattr(runtime_version, "ValidateProtobufRuntimeVersion", boom)
    pkg = importlib.import_module("cairn.parsers")
    original_importer = scip_importer
    original_stub = importlib.import_module("cairn.parsers._scip_pb2")

    def _evict():
        for name in ("cairn.parsers.scip_importer", "cairn.parsers._scip_pb2"):
            sys.modules.pop(name, None)
        for attr in ("scip_importer", "_scip_pb2"):
            if hasattr(pkg, attr):
                delattr(pkg, attr)

    try:
        _evict()
        with pytest.raises(runtime_version.VersionError):
            importlib.import_module("cairn.parsers._scip_pb2")
        reloaded = importlib.import_module("cairn.parsers.scip_importer")
        assert reloaded.scip_available() is False
        assert "[scip]" in reloaded._INSTALL_HINT
    finally:
        _evict()
        monkeypatch.undo()
        sys.modules["cairn.parsers._scip_pb2"] = original_stub
        pkg._scip_pb2 = original_stub
        sys.modules["cairn.parsers.scip_importer"] = original_importer
        pkg.scip_importer = original_importer


def test_unavailable_runtime_raises_import_error_with_hint(fresh_db, tmp_path, monkeypatch):
    """import_scip_file refuses to run without the runtime, carrying the [scip]
    install hint on the ImportError; no rows are written."""
    from cairn.parsers import scip_importer

    idx = tmp_path / "index.scip"
    idx.write_bytes(b"")
    monkeypatch.setattr(scip_importer, "scip_available", lambda: False)

    with pytest.raises(ImportError, match="\\[scip\\]"):
        import_scip_file(fresh_db, str(idx), str(tmp_path))
    assert fresh_db.execute("select count(*) from edges").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Fixture-driven overlay behavior (TC-001/002/003/004/005 intent, importer level)
# ---------------------------------------------------------------------------
@needs_scip
def test_covered_file_gets_exact_and_unresolved_scip_edges(tmp_path):
    """The covered file's occurrences land as scip edges: in-workspace targets
    exact, standard-library targets unresolved with no target id."""
    ws, db = _build("covered", tmp_path)
    conn = sqlite3.connect(db)
    try:
        record = import_scip_file(conn, str(ws / "index.scip"), str(ws))
    finally:
        conn.close()

    in_index = "f.path like '%in_index.py'"
    exact = _scip_edge_count(db, f"{in_index} and e.resolution='exact'")
    unresolved = _count(
        db,
        "select count(*) from edges e where e.source='scip' "
        "and e.resolution='unresolved' and e.target_id is null",
    )
    calls = _count(
        db, "select count(*) from edges e where e.source='scip' and e.kind='calls'"
    )
    total = _count(db, "select count(*) from edges e where e.source='scip'")

    assert exact > 0
    assert unresolved > 0
    assert calls > 0
    assert total == record["edges"] == record["calls"] + record["references"]
    assert record["exact"] == exact
    assert record["unresolved"] == unresolved
    assert record["skipped_documents"] == 0
    assert record["disagreements"] == 0


@needs_scip
def test_import_writes_no_symbols_or_structure(tmp_path):
    """The overlay is edges-only: symbol population and contains edges are
    untouched and every symbol stays tree-sitter-sourced (TC-002 intent)."""
    ws, db = _build("covered", tmp_path)
    before_symbols = _count(db, "select count(*) from symbols")
    before_contains = _count(db, "select count(*) from edges where kind='contains'")
    before_non_ts = _count(
        db, "select count(*) from symbols where coalesce(source,'') != 'tree_sitter'"
    )

    conn = sqlite3.connect(db)
    try:
        import_scip_file(conn, str(ws / "index.scip"), str(ws))
    finally:
        conn.close()

    assert _count(db, "select count(*) from symbols") == before_symbols
    assert _count(db, "select count(*) from edges where kind='contains'") == before_contains
    assert (
        _count(db, "select count(*) from symbols where coalesce(source,'') != 'tree_sitter'")
        == before_non_ts
        == 0
    )


@needs_scip
def test_index_on_off_symbol_populations_identical(tmp_path):
    """Index-on and index-off builds of the twin workspaces carry identical
    symbol populations, on/off alike (TC-002 standing guard)."""
    _, db_on = _build("covered", tmp_path)
    _, db_off = _build("covered-off", tmp_path)

    assert _count(db_on, "select count(*) from symbols") == _count(
        db_off, "select count(*) from symbols"
    )

    ws = tmp_path / "covered"
    conn = sqlite3.connect(db_on)
    try:
        import_scip_file(conn, str(ws / "index.scip"), str(ws))
    finally:
        conn.close()

    assert _count(db_on, "select count(*) from symbols") == _count(
        db_off, "select count(*) from symbols"
    )


@needs_scip
def test_uncovered_file_keeps_tree_sitter_edges(tmp_path):
    """A file covered by no index document gains no scip edges and keeps its
    tree-sitter calls/references (TC-003 boundary)."""
    ws, db = _build("covered", tmp_path)
    conn = sqlite3.connect(db)
    try:
        import_scip_file(conn, str(ws / "index.scip"), str(ws))
    finally:
        conn.close()

    outside = "f.path like '%outside_index.py'"
    assert _scip_edge_count(db, outside) == 0
    assert (
        _count(
            db,
            "select count(*) from edges e join symbols s on e.source_id=s.id "
            "join files f on s.file_id=f.id where "
            f"{outside} and e.kind in ('calls','references')",
        )
        > 0
    )


@needs_scip
def test_opaque_usr_joins_by_position(tmp_path):
    """An opaque-USR index attaches exact edges to the existing tree-sitter
    symbols without adding any (TC-004; the historical disconnect cannot recur)."""
    ws, db = _build("opaque", tmp_path)
    before_symbols = _count(db, "select count(*) from symbols")

    conn = sqlite3.connect(db)
    try:
        import_scip_file(conn, str(ws / "index.scip"), str(ws))
    finally:
        conn.close()

    assert _scip_edge_count(db, "e.resolution='exact'") > 0
    assert _scip_edge_count(db, "e.kind='calls'") > 0
    assert _count(db, "select count(*) from symbols") == before_symbols


@needs_scip
def test_opaque_on_off_symbol_populations_identical(tmp_path):
    _, db_on = _build("opaque", tmp_path)
    _, db_off = _build("opaque-off", tmp_path)
    assert _count(db_on, "select count(*) from symbols") == _count(
        db_off, "select count(*) from symbols"
    )


@needs_scip
def test_document_without_matching_file_is_counted_not_written(fresh_db, tmp_path):
    """A document whose relative_path matches no files row is skipped and
    counted, writing nothing."""
    idx = tmp_path / "ghost.scip"
    idx.write_bytes(_index_bytes(_doc(relative_path="ghost.py")))
    record = import_scip_file(fresh_db, str(idx), str(tmp_path))
    assert record["skipped_documents"] == 1
    assert record["edges"] == 0
    assert fresh_db.execute("select count(*) from edges").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Position join / classification / write contract (synthetic in-memory indexes)
# ---------------------------------------------------------------------------
def _seed_graph(conn, tmp_path):
    """One file with a class (lines 1-10), a method (2-3), and a function (6-8)."""
    repo = tmp_path / "r1"
    (repo / ".git").mkdir(parents=True, exist_ok=True)
    conn.execute(
        "INSERT INTO repos (id, name, path) VALUES ('r1', 'r1', ?)", (str(repo),)
    )
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES ('f1', 'r1', 'a.py', 'python')"
    )
    for sid, name, kind, ls, le in (
        ("s_cls", "Cls", "class", 1, 10),
        ("s_m", "m", "method", 2, 3),
        ("s_f", "f", "function", 6, 8),
    ):
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end, "
            "column_start, column_end) VALUES (?, 'f1', ?, ?, ?, ?, 0, 0)",
            (sid, name, kind, ls, le),
        )
    conn.commit()


def _sym(symbol, kind):
    from cairn.parsers import _scip_pb2 as pb

    return pb.SymbolInformation(symbol=symbol, kind=kind)


def _occ(rng, symbol, role=0):
    from cairn.parsers import _scip_pb2 as pb

    return pb.Occurrence(range=list(rng), symbol=symbol, symbol_roles=role)


def _doc(relative_path="a.py", symbols=(), occurrences=()):
    from cairn.parsers import _scip_pb2 as pb

    return pb.Document(
        relative_path=relative_path, language="Python",
        symbols=list(symbols), occurrences=list(occurrences),
    )


def _index_bytes(doc):
    from cairn.parsers import _scip_pb2 as pb

    return pb.Index(
        metadata=pb.Metadata(project_root="ws"), documents=[doc]
    ).SerializeToString()


K_FUNCTION = 17
K_METHOD = 26
K_CLASS = 7
K_VARIABLE = 61


@needs_scip
def test_innermost_containing_symbol_wins(fresh_db, tmp_path):
    """An occurrence inside nested symbols binds to the tightest span, never
    the outer class; an occurrence only the class covers binds to the class."""
    _seed_graph(fresh_db, tmp_path)
    x = "python a.py a.py/x()."
    idx = tmp_path / "i.scip"
    idx.write_bytes(
        _index_bytes(
            _doc(
                symbols=[_sym(x, K_VARIABLE)],
                occurrences=[
                    _occ([0, 6, 12], x, 1),   # def x on the method's signature line
                    _occ([1, 0, 5], x),       # ref inside the method (lines 2-3)
                    _occ([8, 0, 5], x),       # ref inside only the class (lines 1-10)
                ],
            )
        )
    )
    import_scip_file(fresh_db, str(idx), str(tmp_path))

    rows = fresh_db.execute(
        "select source_id, kind from edges where source='scip' order by line"
    ).fetchall()
    assert [r[0] for r in rows] == ["s_m", "s_cls"]


@needs_scip
def test_multiline_repeated_range_joins(fresh_db, tmp_path):
    """A four-element (multi-line) repeated range joins by its line span."""
    _seed_graph(fresh_db, tmp_path)
    x = "python a.py a.py/x()."
    idx = tmp_path / "i.scip"
    idx.write_bytes(
        _index_bytes(
            _doc(
                symbols=[_sym(x, K_VARIABLE)],
                occurrences=[
                    _occ([1, 0, 2, 20], x, 1),  # def x spanning the method body
                    _occ([2, 4, 3, 9], x),      # ref spanning lines 3-4 (1-based)
                ],
            )
        )
    )
    import_scip_file(fresh_db, str(idx), str(tmp_path))
    assert fresh_db.execute("select count(*) from edges where source='scip'").fetchone()[0] == 1


@needs_scip
def test_target_kind_function_method_makes_calls_else_references(fresh_db, tmp_path):
    """Targets whose index kind is Function/Method produce calls edges; every
    other kind (and unknown, info-less symbols) produces references."""
    _seed_graph(fresh_db, tmp_path)
    fn = "python a.py a.py/fn()."
    meth = "python a.py a.py/mthd()."
    cls = "python a.py a.py/Cls."
    unknown = "python . ext()."
    idx = tmp_path / "i.scip"
    idx.write_bytes(
        _index_bytes(
            _doc(
                symbols=[_sym(fn, K_FUNCTION), _sym(meth, K_METHOD), _sym(cls, K_CLASS)],
                occurrences=[
                    _occ([1, 0, 3], fn, 1),
                    _occ([1, 4, 7], meth, 1),
                    _occ([0, 0, 3], cls, 1),
                    _occ([6, 0, 2], fn),
                    _occ([6, 4, 6], meth),
                    _occ([6, 8, 10], cls),
                    _occ([6, 12, 14], unknown),
                ],
            )
        )
    )
    import_scip_file(fresh_db, str(idx), str(tmp_path))

    kinds = dict(
        fresh_db.execute(
            "select target_name, kind from edges where source='scip'"
        ).fetchall()
    )
    assert kinds == {"fn": "calls", "mthd": "calls", "Cls": "references", "ext": "references"}


@needs_scip
def test_definition_occurrences_emit_no_edges(fresh_db, tmp_path):
    """Role-bit 0x1 occurrences are skipped entirely."""
    _seed_graph(fresh_db, tmp_path)
    x = "python a.py a.py/x()."
    idx = tmp_path / "i.scip"
    idx.write_bytes(
        _index_bytes(
            _doc(symbols=[_sym(x, K_FUNCTION)], occurrences=[_occ([1, 0, 5], x, 1)])
        )
    )
    record = import_scip_file(fresh_db, str(idx), str(tmp_path))
    assert record["edges"] == 0
    assert fresh_db.execute("select count(*) from edges where source='scip'").fetchone()[0] == 0


@needs_scip
def test_cross_document_target_resolves_exact(fresh_db, tmp_path):
    """A reference in one document resolves through the index's own symbol
    table to the defining occurrence's position join in another document."""
    conn = fresh_db
    repo = tmp_path / "r1"
    (repo / ".git").mkdir(parents=True, exist_ok=True)
    conn.execute(
        "INSERT INTO repos (id, name, path) VALUES ('r1', 'r1', ?)", (str(repo),)
    )
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES ('f1', 'r1', 'a.py', 'python')"
    )
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES ('f2', 'r1', 'b.py', 'python')"
    )
    for sid, fid, name, ls, le in (
        ("s_def", "f1", "provider", 2, 3),
        ("s_use", "f2", "consumer", 1, 2),
    ):
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, line_start, line_end, "
            "column_start, column_end) VALUES (?, ?, ?, 'function', ?, ?, 0, 0)",
            (sid, fid, name, ls, le),
        )
    conn.commit()

    from cairn.parsers import _scip_pb2 as pb

    target = "python a.py a.py/provider()."
    idx = pb.Index(
        metadata=pb.Metadata(project_root="ws"),
        documents=[
            _doc(
                relative_path="a.py",
                symbols=[_sym(target, K_FUNCTION)],
                occurrences=[_occ([1, 4, 12], target, 1)],
            ),
            _doc(
                relative_path="b.py",
                occurrences=[_occ([1, 11, 19], target)],
            ),
        ],
    )
    idx_path = tmp_path / "cross.scip"
    idx_path.write_bytes(idx.SerializeToString())
    record = import_scip_file(conn, str(idx_path), str(tmp_path))

    rows = [
        tuple(r)
        for r in conn.execute(
            "select source_id, target_id, resolution from edges where source='scip'"
        ).fetchall()
    ]
    assert rows == [("s_use", "s_def", "exact")]
    assert record["exact"] == 1


@needs_scip
def test_out_of_workspace_target_is_unresolved_with_null_id(fresh_db, tmp_path):
    """A target with no in-workspace definition site stays unresolved with a
    NULL target id and a readable target name (never a false binding)."""
    _seed_graph(fresh_db, tmp_path)
    idx = tmp_path / "i.scip"
    idx.write_bytes(
        _index_bytes(_doc(occurrences=[_occ([1, 0, 5], "python . print().")]))
    )
    record = import_scip_file(fresh_db, str(idx), str(tmp_path))

    row = fresh_db.execute(
        "select target_id, target_name, resolution from edges where source='scip'"
    ).fetchone()
    assert tuple(row) == (None, "print", "unresolved")
    assert record["unresolved"] == 1


@needs_scip
def test_in_workspace_target_that_fails_join_is_dropped(fresh_db, tmp_path):
    """A definition site whose position joins no tree-sitter symbol drops the
    edge and counts the miss (feeding the anomaly surface), never mis-binds."""
    _seed_graph(fresh_db, tmp_path)
    x = "python a.py a.py/x()."
    idx = tmp_path / "i.scip"
    idx.write_bytes(
        _index_bytes(
            _doc(
                symbols=[_sym(x, K_FUNCTION)],
                occurrences=[
                    _occ([50, 0, 5], x, 1),  # def x on a line no symbol covers
                    _occ([1, 0, 5], x),      # ref inside the method
                ],
            )
        )
    )
    record = import_scip_file(fresh_db, str(idx), str(tmp_path))
    assert record["edges"] == 0
    assert record["joined_occurrences"] == 1
    assert record["unjoined_occurrences"] == 1
    assert fresh_db.execute("select count(*) from edges where source='scip'").fetchone()[0] == 0


@needs_scip
def test_occurrence_without_range_is_counted_unjoined(fresh_db, tmp_path):
    _seed_graph(fresh_db, tmp_path)
    from cairn.parsers import _scip_pb2 as pb

    idx = tmp_path / "i.scip"
    idx.write_bytes(
        _index_bytes(
            _doc(occurrences=[pb.Occurrence(symbol="python . print().")])
        )
    )
    record = import_scip_file(fresh_db, str(idx), str(tmp_path))
    assert record["edges"] == 0
    assert record["unjoined_occurrences"] == 1


@needs_scip
def test_corrupt_index_aborts_before_any_write(fresh_db, tmp_path):
    """A corrupt index aborts with no edge written and a scip_parse_error skip
    row recorded -- never a partial overlay, never a rollback on the caller."""
    _seed_graph(fresh_db, tmp_path)
    before = fresh_db.execute("select count(*) from edges").fetchone()[0]
    idx = tmp_path / "corrupt.scip"
    idx.write_bytes(b"\x12\x05ab")

    with pytest.raises(ValueError, match="corrupt SCIP index"):
        import_scip_file(fresh_db, str(idx), str(tmp_path))

    assert fresh_db.execute("select count(*) from edges").fetchone()[0] == before
    reasons = [
        tuple(r)
        for r in fresh_db.execute(
            "select reason from skipped_files where reason like 'scip%'"
        ).fetchall()
    ]
    assert reasons == [("scip_parse_error",)]


# ---------------------------------------------------------------------------
# Per-file authority and per-document repo attribution (TC-003/TC-006)
# ---------------------------------------------------------------------------
def _materialize_multirepo(tmp_path: Path) -> Path:
    """Copy the multirepo workspace into tmp; markers on the repos, none on the
    workspace root (the scanner discovers the repos by their own markers)."""
    ws = tmp_path / "multirepo"
    shutil.copytree(FIXTURES / "multirepo", ws, ignore=shutil.ignore_patterns(".git"))
    for repo in ("alpha", "beta"):
        (ws / repo / ".git").mkdir()
    return ws


def _build_multirepo(tmp_path: Path) -> tuple[Path, str]:
    ws = _materialize_multirepo(tmp_path)
    db = str(tmp_path / "multirepo.db")
    build_graph(workspace=str(ws), db_path=db)
    return ws, db


def _import(db: str, ws: Path):
    conn = sqlite3.connect(db)
    try:
        return import_scip_file(conn, str(ws / "index.scip"), str(ws))
    finally:
        conn.close()


@needs_scip
def test_covered_file_edges_fully_index_sourced_after_import(tmp_path):
    """Per-file authority is replacement, not union: after import the covered
    file's calls/references rows carry no non-scip source (TC-003)."""
    ws, db = _build_tree_sitter_only("covered", tmp_path)
    covered = "f.path like '%in_index.py'"
    before_ts = _count(
        db,
        "select count(*) from edges e join symbols s on e.source_id=s.id "
        "join files f on s.file_id=f.id where "
        f"{covered} and e.kind in ('calls','references') "
        "and coalesce(e.source,'') != 'scip'",
    )
    before_contains = _count(db, "select count(*) from edges where kind='contains'")
    assert before_ts > 0

    _import(db, ws)

    assert (
        _count(
            db,
            "select count(*) from edges e join symbols s on e.source_id=s.id "
            "join files f on s.file_id=f.id where "
            f"{covered} and e.kind in ('calls','references') "
            "and coalesce(e.source,'') != 'scip'",
        )
        == 0
    )
    assert _scip_edge_count(db, covered) > 0
    assert _count(db, "select count(*) from edges where kind='contains'") == before_contains


@needs_scip
def test_uncovered_file_rows_byte_identical_across_import(tmp_path):
    """Every edge row of a file no index document covers survives the import
    byte-identical, ids included (TC-003 boundary)."""
    ws, db = _build("covered", tmp_path)
    q = (
        "select e.id, e.source_id, e.target_id, e.target_name, e.kind, e.line, "
        "e.column, e.resolution, coalesce(e.source,'') from edges e "
        "join symbols s on e.source_id=s.id join files f on s.file_id=f.id "
        "where f.path like '%outside_index.py' order by e.id"
    )
    conn = sqlite3.connect(db)
    before = conn.execute(q).fetchall()
    conn.close()
    assert before and all(row[-1] != "scip" for row in before)

    _import(db, ws)

    conn = sqlite3.connect(db)
    after = conn.execute(q).fetchall()
    conn.close()
    assert after == before


@needs_scip
def test_multirepo_documents_attribute_to_their_own_repos(tmp_path):
    """Each document's edges attach through its own repo's files rows -- alpha's
    document to alpha, beta's to beta -- with targets resolving across repos
    (TC-006)."""
    ws, db = _build_multirepo(tmp_path)
    record = _import(db, ws)

    assert record["matched_documents"] == 2
    assert record["skipped_documents"] == 0
    assert record["edges"] == 3

    conn = sqlite3.connect(db)
    try:
        by_source_repo = dict(
            conn.execute(
                "select r.id, count(*) from edges e "
                "join symbols s on e.source_id=s.id "
                "join files f on s.file_id=f.id join repos r on f.repo_id=r.id "
                "where e.source='scip' group by r.id"
            ).fetchall()
        )
        by_target_repo = dict(
            conn.execute(
                "select r.id, count(*) from edges e "
                "join symbols t on e.target_id=t.id "
                "join files tf on t.file_id=tf.id join repos r on tf.repo_id=r.id "
                "where e.source='scip' group by r.id"
            ).fetchall()
        )
    finally:
        conn.close()
    assert by_source_repo == {"alpha": 2, "beta": 1}
    assert by_target_repo == {"alpha": 1, "beta": 2}


@needs_scip
def test_import_never_assigns_one_repo_to_all_documents(tmp_path):
    """Index-sourced edges land under both repos' identities: no single repo id
    ever carries the whole index's attribution."""
    ws, db = _build_multirepo(tmp_path)
    _import(db, ws)

    conn = sqlite3.connect(db)
    try:
        repos_with_scip = [
            r[0]
            for r in conn.execute(
                "select distinct f.repo_id from edges e "
                "join symbols s on e.source_id=s.id join files f on s.file_id=f.id "
                "where e.source='scip' order by f.repo_id"
            ).fetchall()
        ]
    finally:
        conn.close()
    assert repos_with_scip == ["alpha", "beta"]


@needs_scip
def test_rel_path_in_two_repos_is_skipped_not_guessed(fresh_db, tmp_path):
    """A relative path matching files rows in several repos attributes to
    neither: the document is skipped and counted, never a guessed repo."""
    for name in ("alpha", "beta"):
        (tmp_path / name / ".git").mkdir(parents=True)
    conn = fresh_db
    conn.execute(
        "INSERT INTO repos (id, name, path) VALUES ('alpha', 'alpha', ?)",
        (str(tmp_path / "alpha"),),
    )
    conn.execute(
        "INSERT INTO repos (id, name, path) VALUES ('beta', 'beta', ?)",
        (str(tmp_path / "beta"),),
    )
    for fid, rid in (("fa", "alpha"), ("fb", "beta")):
        conn.execute(
            "INSERT INTO files (id, repo_id, path, language) "
            "VALUES (?, ?, 'dup.py', 'python')",
            (fid, rid),
        )
    conn.commit()

    idx = tmp_path / "dup.scip"
    idx.write_bytes(_index_bytes(_doc(relative_path="dup.py")))
    record = import_scip_file(fresh_db, str(idx), str(tmp_path))
    assert record["matched_documents"] == 0
    assert record["skipped_documents"] == 1
    assert record["edges"] == 0


# ---------------------------------------------------------------------------
# Anomaly gate (FR-005/TC-007) and disagreement counters (FR-014)
# ---------------------------------------------------------------------------
@needs_scip
def test_below_threshold_document_retains_tree_sitter_edges(tmp_path):
    """A document whose join rate falls below the anomaly threshold writes no
    edges, leaves its file's tree-sitter rows byte-identical (ids included),
    and records exactly one scip_join_anomaly skip row for that file."""
    ws, db = _build_tree_sitter_only("drifted", tmp_path)
    q = (
        "select e.id, e.source_id, e.target_id, e.target_name, e.kind, e.line, "
        "e.column, e.resolution, coalesce(e.source,'') from edges e "
        "join symbols s on e.source_id=s.id join files f on s.file_id=f.id "
        "where f.path like '%app.py' order by e.id"
    )
    conn = sqlite3.connect(db)
    try:
        before = conn.execute(q).fetchall()
        record = import_scip_file(conn, str(ws / "index.scip"), str(ws))
        after = conn.execute(q).fetchall()
        skips = conn.execute(
            "select repo_id, path, reason from skipped_files "
            "where reason = 'scip_join_anomaly'"
        ).fetchall()
    finally:
        conn.close()

    assert before
    assert after == before
    assert record["edges"] == 0
    assert record["join_anomalies"] == 1
    assert len(skips) == 1
    repo_id, path, reason = skips[0]
    assert path == "app.py"
    assert reason == "scip_join_anomaly"
    assert repo_id  # the matched file's own repo, via the per-document substrate


@needs_scip
def test_drifted_index_matches_no_index_twin_edge_mix(tmp_path):
    """Index-on vs index-off twins of the drifted workspace carry identical
    calls/references mixes and zero index-sourced edges (TC-007 intent)."""
    ws_on, db_on = _build("drifted", tmp_path)
    _, db_off = _build("drifted-off", tmp_path)
    conn = sqlite3.connect(db_on)
    try:
        import_scip_file(conn, str(ws_on / "index.scip"), str(ws_on))
    finally:
        conn.close()

    for kind in ("calls", "references"):
        assert _count(db_on, f"select count(*) from edges where kind='{kind}'") == _count(
            db_off, f"select count(*) from edges where kind='{kind}'"
        )
    assert _count(db_on, "select count(*) from edges where source='scip'") == 0


@needs_scip
def test_above_threshold_document_writes_no_anomaly_row(tmp_path):
    """A document whose join rate clears the threshold imports normally and
    records no scip_join_anomaly row."""
    ws, db = _build("covered", tmp_path)
    conn = sqlite3.connect(db)
    try:
        record = import_scip_file(conn, str(ws / "index.scip"), str(ws))
        anomalies = conn.execute(
            "select count(*) from skipped_files where reason = 'scip_join_anomaly'"
        ).fetchone()[0]
    finally:
        conn.close()

    assert record["edges"] > 0
    assert record["join_anomalies"] == 0
    assert anomalies == 0


def _seed_ts_edge(conn, edge_id, target_id, resolution):
    """A tree-sitter calls edge from s_f at line 7 (the scip test's join site)."""
    conn.execute(
        "INSERT INTO edges (id, source_id, target_id, target_name, kind, line, "
        "column, resolution, source) VALUES (?, 's_f', ?, 'Cls', 'calls', 7, 0, "
        "?, 'tree_sitter')",
        (edge_id, target_id, resolution),
    )
    conn.commit()


def _site_index(tmp_path):
    """An index whose reference at 0-based line 6 joins s_f and binds s_m."""
    h = "python a.py a.py/m()."
    idx = tmp_path / "i.scip"
    idx.write_bytes(
        _index_bytes(
            _doc(
                symbols=[_sym(h, K_FUNCTION)],
                occurrences=[_occ([1, 0, 1], h, 1), _occ([6, 2, 3], h)],
            )
        )
    )
    return idx


@needs_scip
def test_disagreement_counts_when_scip_target_differs(fresh_db, tmp_path):
    """Same (kind, line), different resolved target: the scip binding wins and
    the displaced tree-sitter target is counted, never silently dropped."""
    _seed_graph(fresh_db, tmp_path)
    _seed_ts_edge(fresh_db, "e_ts", "s_cls", "exact")
    record = import_scip_file(fresh_db, str(_site_index(tmp_path)), str(tmp_path))

    assert record["disagreements"] == 1
    assert record["upgrades"] == 0
    rows = [
        tuple(r)
        for r in fresh_db.execute(
            "select target_id, resolution, coalesce(source,'') from edges "
            "where kind='calls' and line=7"
        ).fetchall()
    ]
    assert rows == [("s_m", "exact", "scip")]


@needs_scip
def test_upgrade_counts_when_scip_resolves_ambiguous_site(fresh_db, tmp_path):
    """A tree-sitter ambiguous/unresolved site that scip resolves exact counts
    as an upgrade, not a disagreement."""
    _seed_graph(fresh_db, tmp_path)
    _seed_ts_edge(fresh_db, "e_ts", None, "ambiguous")
    record = import_scip_file(fresh_db, str(_site_index(tmp_path)), str(tmp_path))

    assert record["upgrades"] == 1
    assert record["disagreements"] == 0
    rows = [
        tuple(r)
        for r in fresh_db.execute(
            "select target_id, resolution, coalesce(source,'') from edges "
            "where kind='calls' and line=7"
        ).fetchall()
    ]
    assert rows == [("s_m", "exact", "scip")]


@needs_scip
def test_import_record_exposes_edge_disagreement_upgrade_keys(fresh_db, tmp_path):
    """The import record always carries the edges/disagreements/upgrades
    counters the CLI summary and the zero-false-exact eval read."""
    idx = tmp_path / "ghost.scip"
    idx.write_bytes(_index_bytes(_doc(relative_path="ghost.py")))
    record = import_scip_file(fresh_db, str(idx), str(tmp_path))
    assert {"edges", "disagreements", "upgrades", "join_anomalies"} <= set(record)


@needs_scip
def test_cli_build_then_import_attributes_per_document(tmp_path):
    """Through the CLI build (--workspace/--db explicit, tmp only), the importer
    attributes per document against real scanner-produced files rows."""
    from click.testing import CliRunner

    from cairn.cli import main

    ws = _materialize_multirepo(tmp_path)
    db = str(tmp_path / "cli.db")
    result = CliRunner().invoke(main, ["build", "--workspace", str(ws), "--db", db])
    assert result.exit_code == 0, result.output

    record = _import(db, ws)
    assert record["edges"] == 3

    conn = sqlite3.connect(db)
    try:
        by_source_repo = conn.execute(
            "select f.repo_id, count(*) from edges e "
            "join symbols s on e.source_id=s.id join files f on s.file_id=f.id "
            "where e.source='scip' group by f.repo_id order by f.repo_id"
        ).fetchall()
    finally:
        conn.close()
    assert [tuple(r) for r in by_source_repo] == [("alpha", 2), ("beta", 1)]
