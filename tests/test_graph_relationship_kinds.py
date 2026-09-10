"""Relationship-kind enrichment: parser extraction, builder derivation,
imports-edge materialization, ancestor-index coverage, traversal exclusion,
and viz-layer kind passthrough.

Covers the canonical edge-kind vocabulary beyond `calls`:
- parsers: extends (python base classes, swift superclass-first),
  implements (swift protocol conformance), embeds (go struct/interface
  embedding), decorates (python decorators), references (python signature
  annotations, kotlin type annotations)
- builder: contains (module->top-level, parent->nested), module symbols,
  module-level edge ownership, imports edges (module->module)
- resolver: embeds joins extends/implements in the ancestor index
- traversal: the new kinds stay outside STRUCTURAL_EDGE_KINDS
- viz: symbol/neighbors scopes pass the DB's edge kind through verbatim
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from cairn.graph.schema import _apply_schema


def _parse(parser_cls, source: bytes, suffix: str):
    """Parse ``source`` with ``parser_cls`` via a temp file. Returns ParsedFile."""
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False, mode="wb") as f:
        f.write(source)
        path = f.name
    try:
        return parser_cls().parse(path)
    finally:
        Path(path).unlink(missing_ok=True)


def _edges(pf):
    """{(source, kind, target)} from a ParsedFile."""
    return {(e.source_name, e.kind, e.target_name) for e in pf.edges}


# ---------------------------------------------------------------------------
# Python: extends / decorates / references
# ---------------------------------------------------------------------------

class TestPythonRelationshipEdges:
    def test_base_classes_are_extends(self):
        from cairn.parsers.python_parser import PythonParser

        pf = _parse(PythonParser, b"class Repo(Base, Serializable):\n    pass\n", ".py")
        assert ("Repo", "extends", "Base") in _edges(pf)
        assert ("Repo", "extends", "Serializable") in _edges(pf)
        assert not any(k == "implements" for _, k, _ in _edges(pf))

    def test_decorators_emit_decorates_edges(self):
        from cairn.parsers.python_parser import PythonParser

        src = (
            b"@dataclass(frozen=True)\n"
            b"@app.route('/x')\n"
            b"class Repo:\n"
            b"    @property\n"
            b"    def id(self):\n"
            b"        return 0\n"
        )
        pf = _parse(PythonParser, src, ".py")
        assert ("Repo", "decorates", "dataclass") in _edges(pf)
        assert ("Repo", "decorates", "route") in _edges(pf)
        assert ("id", "decorates", "property") in _edges(pf)

    def test_signature_annotations_emit_references(self):
        from cairn.parsers.python_parser import PythonParser

        src = (
            b"def load(path: Path, limit: int = 10) -> Repo:\n"
            b"    return Repo(path)\n"
        )
        pf = _parse(PythonParser, src, ".py")
        assert ("load", "references", "Path") in _edges(pf)
        assert ("load", "references", "Repo") in _edges(pf)
        # builtin typing tokens stay out
        assert ("load", "references", "int") not in _edges(pf)

    def test_subscripted_annotations_contribute_inner_names(self):
        from cairn.parsers.python_parser import PythonParser

        pf = _parse(PythonParser, b"def f() -> dict[str, Repo]:\n    pass\n", ".py")
        assert ("f", "references", "Repo") in _edges(pf)
        assert ("f", "references", "dict") not in _edges(pf)


# ---------------------------------------------------------------------------
# Go: embeds
# ---------------------------------------------------------------------------

class TestGoEmbedsEdges:
    def test_struct_embedding(self):
        from cairn.parsers.go import GoParser

        src = (
            b"package main\n"
            b"type Base struct{ ID int }\n"
            b"type S struct {\n"
            b"	*Base\n"
            b"	Name string\n"
            b"	pkg.Config\n"
            b"}\n"
        )
        pf = _parse(GoParser, src, ".go")
        assert ("S", "embeds", "Base") in _edges(pf)
        assert ("S", "embeds", "Config") in _edges(pf)
        # named fields are members, not embeds
        assert ("S", "embeds", "Name") not in _edges(pf)

    def test_interface_embedding(self):
        from cairn.parsers.go import GoParser

        src = (
            b"package main\n"
            b"type Reader interface {\n"
            b"	Read() error\n"
            b"	io.Writer\n"
            b"}\n"
        )
        pf = _parse(GoParser, src, ".go")
        assert ("Reader", "embeds", "Writer") in _edges(pf)
        # method elements are members, not embeds
        assert ("Reader", "embeds", "Read") not in _edges(pf)


# ---------------------------------------------------------------------------
# Swift: extends (superclass) / implements (protocol conformance)
# ---------------------------------------------------------------------------

class TestSwiftInheritanceSplit:
    def test_class_first_target_extends_rest_implements(self):
        from cairn.parsers.swift import SwiftParser

        pf = _parse(
            SwiftParser,
            b"class MyView: BaseView, UITableViewDelegate {\n func r() {}\n}\n",
            ".swift",
        )
        assert ("MyView", "extends", "BaseView") in _edges(pf)
        assert ("MyView", "implements", "UITableViewDelegate") in _edges(pf)

    def test_struct_and_enum_targets_all_implement(self):
        from cairn.parsers.swift import SwiftParser

        pf = _parse(
            SwiftParser,
            b"struct Price: Codable, Hashable {}\nenum Coin: Int {}\n",
            ".swift",
        )
        assert ("Price", "implements", "Codable") in _edges(pf)
        assert ("Price", "implements", "Hashable") in _edges(pf)
        assert not any(k == "extends" for _, k, _ in _edges(pf))


# ---------------------------------------------------------------------------
# Kotlin: references from type annotations
# ---------------------------------------------------------------------------

class TestKotlinTypeReferences:
    def test_param_return_and_property_types(self):
        from cairn.parsers.kotlin import KotlinParser

        src = (
            b"class UseCase {\n"
            b"    private val repo: Repo = Repo()\n"
            b"    fun execute(user: User): Result {\n"
            b"        return repo.load(user.id)\n"
            b"    }\n"
            b"}\n"
        )
        pf = _parse(KotlinParser, src, ".kt")
        assert ("execute", "references", "User") in _edges(pf)
        assert ("execute", "references", "Result") in _edges(pf)
        assert ("repo", "references", "Repo") in _edges(pf)


# ---------------------------------------------------------------------------
# Builder: module symbols, contains edges, module-level ownership
# ---------------------------------------------------------------------------

_PY_FIXTURE = {
    "pkg/__init__.py": "",
    "pkg/models.py": (
        "class Model:\n"
        "    def validate(self) -> bool:\n"
        "        return True\n"
        "\n"
        "    class Meta:\n"
        "        verbose = True\n"
    ),
    "pkg/store.py": (
        "from pkg.models import Model\n"
        "\n"
        "validate_all = len([1, 2])\n"
        "\n"
        "class Store:\n"
        "    def load(self) -> Model:\n"
        "        return Model()\n"
),
}


def _make_workspace(tmp_path, name="ws"):
    workspace = tmp_path / name
    repo = workspace / "demo"
    (repo / ".git").mkdir(parents=True)
    for fname, contents in _PY_FIXTURE.items():
        target = repo / fname
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents)
    return str(workspace)


@pytest.fixture()
def built_db(tmp_path):
    from cairn.graph.builder import build_graph

    ws = _make_workspace(tmp_path)
    db_path = str(tmp_path / "graph.db")
    build_graph(workspace=ws, db_path=db_path, verbose=False)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _kinds(conn) -> dict:
    return dict(conn.execute("SELECT kind, COUNT(*) FROM edges GROUP BY kind").fetchall())


class TestBuilderModuleAndContains:
    def test_module_symbol_per_file(self, built_db):
        modules = built_db.execute(
            "SELECT s.name, f.path FROM symbols s JOIN files f ON s.file_id = f.id "
            "WHERE s.kind = 'module'"
        ).fetchall()
        by_path = {m["path"]: m["name"] for m in modules}
        assert by_path.get("pkg/models.py") == "models"
        assert by_path.get("pkg/store.py") == "store"
        # __init__.py still yields a module symbol named after the file stem
        assert by_path.get("pkg/__init__.py") == "__init__"

    def test_contains_module_to_top_level(self, built_db):
        row = built_db.execute(
            """SELECT COUNT(*) FROM edges e
               JOIN symbols src ON e.source_id = src.id
               JOIN symbols tgt ON e.target_id = tgt.id
               WHERE e.kind = 'contains' AND src.kind = 'module'
                 AND src.file_id = (SELECT id FROM files WHERE path = 'pkg/models.py')
                 AND tgt.name = 'Model'"""
        ).fetchone()
        assert row[0] == 1

    def test_contains_parent_to_nested(self, built_db):
        row = built_db.execute(
            """SELECT e.resolution FROM edges e
               JOIN symbols src ON e.source_id = src.id
               JOIN symbols tgt ON e.target_id = tgt.id
               WHERE e.kind = 'contains' AND src.name = 'Model' AND tgt.name = 'Meta'"""
        ).fetchone()
        assert row is not None
        assert row[0] == "exact"

    def test_module_level_edge_attaches_to_module(self, built_db):
        # `len(...)` runs at module level in store.py: the edge's source is
        # the store module symbol, not dropped on the floor.
        row = built_db.execute(
            """SELECT src.name FROM edges e
               JOIN symbols src ON e.source_id = src.id
               WHERE e.kind = 'calls' AND e.target_name = 'len'"""
        ).fetchone()
        assert row is not None
        assert row[0] == "store"


class TestBuilderImportsEdges:
    def test_intra_repo_import_edge(self, built_db):
        rows = built_db.execute(
            """SELECT src.name AS src_name, e.target_name, e.resolution
               FROM edges e JOIN symbols src ON e.source_id = src.id
               WHERE e.kind = 'imports'"""
        ).fetchall()
        assert rows, "expected at least one imports edge from `from pkg.models import Model`"
        store_edges = [r for r in rows if r["src_name"] == "store"]
        assert any(
            r["target_name"] == "pkg.models" and r["resolution"] == "exact"
            for r in store_edges
        )

    def test_materialize_is_idempotent(self, built_db):
        from cairn.graph.builder import materialize_import_edges

        before = built_db.execute(
            "SELECT COUNT(*) FROM edges WHERE kind = 'imports'"
        ).fetchone()[0]
        again = materialize_import_edges(built_db)
        after = built_db.execute(
            "SELECT COUNT(*) FROM edges WHERE kind = 'imports'"
        ).fetchone()[0]
        assert again == before
        assert after == before

    def test_ambiguous_import_target_skipped(self, tmp_path):
        # Two files share the bare candidate "models": the import resolves
        # only through a longer suffix, which the longest-first pass finds.
        from cairn.graph.builder import build_graph

        workspace = tmp_path / "ws2"
        repo = workspace / "demo"
        (repo / ".git").mkdir(parents=True)
        (repo / "a").mkdir()
        (repo / "a" / "models.py").write_text("X = 1\n")
        (repo / "b").mkdir()
        (repo / "b" / "models.py").write_text("Y = 2\n")
        (repo / "main.py").write_text("import a.models\nimport b.models\n")
        db_path = str(tmp_path / "graph2.db")
        build_graph(workspace=str(workspace), db_path=db_path, verbose=False)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            targets = [
                r[0]
                for r in conn.execute(
                    "SELECT target_name FROM edges WHERE kind = 'imports'"
                ).fetchall()
            ]
            assert sorted(targets) == ["a.models", "b.models"]
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Resolver + traversal semantics
# ---------------------------------------------------------------------------

class TestAncestorIndexAndTraversal:
    def test_ancestor_index_includes_embeds(self):
        from cairn.graph.resolver import build_ancestor_index

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        _apply_schema(conn)
        conn.executemany(
            "INSERT INTO symbols (id, file_id, name, kind) VALUES (?, ?, ?, ?)",
            [
                ("s1", "f1", "Base", "class"),
                ("s2", "f1", "S", "class"),
                ("s3", "f1", "Iface", "interface"),
            ],
        )
        conn.execute("INSERT INTO files (id, repo_id, path, language) VALUES ('f1', 'r', 'a.go', 'go')")
        conn.executemany(
            "INSERT INTO edges (id, source_id, target_id, target_name, kind) VALUES (?, ?, ?, ?, ?)",
            [
                ("e1", "s2", "s1", "Base", "embeds"),
                ("e2", "s2", "s3", "Iface", "implements"),
            ],
        )
        conn.commit()
        anc = build_ancestor_index(conn)
        assert set(anc.get("S", [])) == {"Base", "Iface"}

    def test_new_kinds_stay_unstructural(self):
        from cairn.graph.traversal import STRUCTURAL_EDGE_KINDS

        for kind in ("contains", "imports", "references", "decorates", "embeds", "with"):
            assert kind not in STRUCTURAL_EDGE_KINDS

    def test_impact_does_not_traverse_contains(self, built_db):
        from cairn.graph.queries import impact_analysis

        # Store.load calls Model; impact over `load` must reach its caller
        # kinds, but contains/imports edges must never widen the blast
        # radius: `Model.Meta` is reachable only via contains from Model.
        result = impact_analysis(built_db, "Model")
        impacted = {r["symbol"] for r in result["impacted"]}
        # Nothing calls Model()... store.load does (Model() constructor call).
        assert "load" in impacted
        # Meta hangs off Model only through contains: not an impact entry.
        assert "Meta" not in impacted
        assert "store" not in impacted  # imports edge is not traversal


# ---------------------------------------------------------------------------
# Viz layer: kind passthrough
# ---------------------------------------------------------------------------

def _seed_viz(conn):
    conn.executemany(
        "INSERT INTO symbols (id, file_id, name, kind) VALUES (?, ?, ?, ?)",
        [
            ("s1", "f1", "main", "function"),
            ("s2", "f1", "helper", "function"),
            ("s3", "f1", "Base", "class"),
        ],
    )
    conn.execute("INSERT INTO files (id, repo_id, path, language) VALUES ('f1', 'r', 'a.py', 'python')")
    conn.executemany(
        "INSERT INTO edges (id, source_id, target_id, target_name, kind) VALUES (?, ?, ?, ?, ?)",
        [
            ("e1", "s1", "s2", "helper", "calls"),
            ("e2", "s1", "s3", "Base", "extends"),
            ("e3", "s3", "s1", "main", "references"),
        ],
    )
    conn.commit()


@pytest.fixture()
def viz_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    _seed_viz(conn)
    try:
        yield conn
    finally:
        conn.close()


class TestVizKindPassthrough:
    def test_symbol_scope_passes_kind_through(self, viz_conn):
        from cairn.viz.query import get_symbol_graph

        graph = get_symbol_graph(viz_conn, "main")
        kinds = {(e["source"], e["target"], e["kind"]) for e in graph["edges"]}
        assert ("main", "helper", "calls") in kinds
        assert ("main", "Base", "extends") in kinds

    def test_symbol_scope_caller_keeps_real_kind(self, viz_conn):
        from cairn.viz.query import get_symbol_graph

        graph = get_symbol_graph(viz_conn, "main")
        kinds = {(e["source"], e["target"], e["kind"]) for e in graph["edges"]}
        # Base --references--> main shows as references, not calls.
        assert ("Base", "main", "references") in kinds

    def test_neighbors_passes_kind_through(self, viz_conn):
        from cairn.viz.query import get_symbol_neighbors

        graph = get_symbol_neighbors(viz_conn, ["main"])
        kinds = {(e["source"], e["target"], e["kind"]) for e in graph["edges"]}
        assert ("main", "helper", "calls") in kinds
        assert ("main", "Base", "extends") in kinds
        assert ("Base", "main", "references") in kinds

    def test_inspect_payload_carries_edge_kind(self, viz_conn):
        from cairn.dashboard.data import inspect_symbol

        data = inspect_symbol(viz_conn, "main")
        caller_kinds = {(c["name"], c["edge_kind"]) for c in data["callers"]}
        assert ("Base", "references") in caller_kinds
        callee_kinds = {(c["name"], c["edge_kind"]) for c in data["callees"]}
        assert ("helper", "calls") in callee_kinds
        assert ("Base", "extends") in callee_kinds


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
