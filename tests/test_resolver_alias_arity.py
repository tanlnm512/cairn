"""Resolver regression tests for the alias/arity resolution shapes.

Covers the previously-ambiguous edge shapes that the alias map and the
arity tiebreak convert to `exact`, and the shapes that must STAY
`ambiguous` (precision outranks recall):

  - `from pkg import name as local` -> `local()`: the local alias is
    invisible to a path-tail-only import tier, so the call falls through
    to same-repo and ties on unrelated namesakes; rewriting the aliased
    callee to the imported path's final segment lets the import tier's
    DIRECT suffix match bind the aliased definition.
  - `import module as m` -> `m.func()`: same invisibility -- the call's
    target is the bare member name, two same-named definitions in the
    repo tie at the same-repo tier; the call's argument count splits
    them when exactly one definition matches.
  - An aliased callee whose rewritten name also has a same-file
    definition: the rewrite feeds the tier walk, it does not bypass it
    -- the same-file tier claims the name before the import tier.
  - Same-file overloads split by argument count resolve exact; an
    equal-arity namesake tie does not split and stays ambiguous.
  - Star-shaped re-exports (`from pkg import *`, `export * from`)
    record no alias binding anywhere, so calls through them stay
    ambiguous rather than binding to a guess.
"""
from __future__ import annotations

import sqlite3

from cairn.graph.builder import build_graph


def _make_fixture(tmp_path, name: str, files: dict) -> str:
    workspace = tmp_path / name
    repo = workspace / "demo"
    (repo / ".git").mkdir(parents=True)
    for fname, contents in files.items():
        target = repo / fname
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents)
    return str(workspace)


def _calls(db_path, source_file: str):
    """All `calls` edges owned by symbols in ``source_file``."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            """SELECT f.path AS src_file, e.resolution, e.target_id,
                      tf.path AS target_file, t.qualified_name AS target_qname,
                      t.arity AS target_arity
               FROM edges e
               JOIN symbols s ON e.source_id = s.id
               JOIN files f ON s.file_id = f.id
               LEFT JOIN symbols t ON e.target_id = t.id
               LEFT JOIN files tf ON t.file_id = tf.id
               WHERE e.kind = 'calls' AND f.path = ?""",
            (source_file,),
        ).fetchall()
    finally:
        conn.close()


def _build(tmp_path, name: str, files: dict):
    ws = _make_fixture(tmp_path, name, files)
    db_path = str(tmp_path / "graph.db")
    build_graph(workspace=ws, db_path=db_path, verbose=False)
    return db_path


def test_from_import_alias_call_resolves_exact_to_aliased_definition(tmp_path):
    """`from util import name as local` + `local()`: the aliased callee is
    invisible to the path-tail import tier, so the call ties on the
    unrelated `local` namesakes at the same-repo tier and stays ambiguous
    unless the alias binding rewrites it to the imported name first."""
    db_path = _build(
        tmp_path,
        "ws_from_alias",
        {
            "main.py": "from util import name as local\n\n\ndef caller():\n    local()\n",
            "util.py": "def name():\n    return 1\n",
            "decoy_a.py": "def local():\n    return 2\n",
            "decoy_b.py": "def local():\n    return 3\n",
        },
    )
    rows = _calls(db_path, "main.py")
    assert len(rows) == 1
    assert rows[0]["resolution"] == "exact"
    assert rows[0]["target_file"] == "util.py"
    assert rows[0]["target_qname"] == "name"


def test_module_import_alias_call_resolves_exact_via_arity_split(tmp_path):
    """`import helper as h` + `h.Func(...)`: the call's target is the bare
    member name, so two same-named `Func` definitions tie at the same-repo
    tier; the call's argument count must pick out the aliased module's
    definition."""
    db_path = _build(
        tmp_path,
        "ws_module_alias",
        {
            "main.go": (
                "package main\n"
                "\n"
                'import h "example.com/proj/helper"\n'
                "\n"
                "func Run() int {\n"
                "\treturn h.Func(1, 2)\n"
                "}\n"
            ),
            "helper/helper.go": (
                "package helper\n"
                "\n"
                "func Func(a int, b int) int {\n"
                "\treturn a + b\n"
                "}\n"
            ),
            "decoy.go": (
                "package other\n"
                "\n"
                "func Func(a int) int {\n"
                "\treturn a\n"
                "}\n"
            ),
        },
    )
    rows = _calls(db_path, "main.go")
    assert len(rows) == 1
    assert rows[0]["resolution"] == "exact"
    assert rows[0]["target_file"] == "helper/helper.go"
    assert rows[0]["target_arity"] == 2


def test_import_alias_rewrite_same_file_definition_wins(tmp_path):
    """An aliased callee whose rewritten name also has a same-file
    definition: the alias rewrite feeds the tier walk, it does not bypass
    it -- the same-file tier claims the rewritten name before the import
    tier can bind the imported definition."""
    db_path = _build(
        tmp_path,
        "ws_alias_guard",
        {
            "main.py": (
                "from util import name as local\n"
                "\n"
                "\n"
                "def name():\n"
                '    return "same-file"\n'
                "\n"
                "\n"
                "def caller():\n"
                "    local()\n"
            ),
            "util.py": "def name():\n    return 1\n",
        },
    )
    rows = _calls(db_path, "main.py")
    assert len(rows) == 1
    assert rows[0]["resolution"] == "exact"
    assert rows[0]["target_file"] == "main.py"


def test_same_file_overloads_split_by_arity_resolve_exact(tmp_path):
    """Two same-named overloads in one file tie at the same-file tier; a
    call whose argument count matches exactly one of them resolves to that
    overload instead of staying ambiguous."""
    db_path = _build(
        tmp_path,
        "ws_overload",
        {
            "render.cpp": (
                "int process(int a) {\n"
                "\treturn a;\n"
                "}\n"
                "\n"
                "int process(int a, int b) {\n"
                "\treturn a + b;\n"
                "}\n"
                "\n"
                "int run() {\n"
                "\treturn process(7);\n"
                "}\n"
            ),
        },
    )
    rows = _calls(db_path, "render.cpp")
    assert len(rows) == 1
    assert rows[0]["resolution"] == "exact"
    assert rows[0]["target_file"] == "render.cpp"
    assert rows[0]["target_arity"] == 1


def test_equal_arity_tie_across_files_stays_ambiguous(tmp_path):
    """Two same-named same-arity definitions in different files: argument
    count cannot split the tie, so the call stays ambiguous -- never a
    guessed exact."""
    db_path = _build(
        tmp_path,
        "ws_arity_tie",
        {
            "tie_a.cpp": "int merge(int a) {\n\treturn a;\n}\n",
            "tie_b.cpp": "int merge(int a) {\n\treturn a + 1;\n}\n",
            "main.cpp": "int start() {\n\treturn merge(3);\n}\n",
        },
    )
    rows = _calls(db_path, "main.cpp")
    assert len(rows) == 1
    assert rows[0]["resolution"] == "ambiguous"
    assert rows[0]["target_id"] is None


def test_init_star_reexport_stays_ambiguous(tmp_path):
    """`from pkg import *` (backed by `from .sub import *` in pkg's
    __init__): star imports record no alias binding, so the call cannot be
    attributed through the re-export and stays ambiguous rather than
    binding to a guess."""
    db_path = _build(
        tmp_path,
        "ws_init_star",
        {
            "pkg/__init__.py": "from .sub import *\n",
            "pkg/sub.py": "def shared():\n    return 1\n",
            "decoy.py": "def shared():\n    return 2\n",
            "main.py": "from pkg import *\n\n\ndef caller():\n    shared()\n",
        },
    )
    rows = _calls(db_path, "main.py")
    assert len(rows) == 1
    assert rows[0]["resolution"] == "ambiguous"
    assert rows[0]["target_id"] is None


def test_export_star_reexport_stays_ambiguous(tmp_path):
    """`import { shared } from "./barrel"` where barrel is `export * from
    "./impl"`: the re-exported name's true home is one hop past the
    imported module, and nothing in the import row pins it -- the call
    stays ambiguous rather than binding through the extra hop."""
    db_path = _build(
        tmp_path,
        "ws_export_star",
        {
            "barrel.js": 'export * from "./impl";\n',
            "impl.js": "export function shared() {\n\treturn 1;\n}\n",
            "decoy.js": "export function shared() {\n\treturn 2;\n}\n",
            "main.js": (
                'import { shared } from "./barrel";\n'
                "\n"
                "export function run() {\n"
                "\tshared();\n"
                "}\n"
            ),
        },
    )
    rows = _calls(db_path, "main.js")
    assert len(rows) == 1
    assert rows[0]["resolution"] == "ambiguous"
    assert rows[0]["target_id"] is None
