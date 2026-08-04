"""Phase 10 — type-resolution tier (receiver-type dispatch).

Covers:
  - Two classes in different files defining a same-named method: the bare
    resolver (tiers 1/3/4) would mark every call `ambiguous`; the type-aware
    Tier 0 disambiguates using the parser's inferred `receiver_type`.
  - Same-file collision: both same-named methods AND the call site live in one
    file. The same-file tier alone would short-circuit to `ambiguous`; because
    the type-aware tier runs FIRST (Tier 0), a typed receiver still resolves.
  - Inheritance: a call through a subclass instance resolves to the base
    class's method via `build_ancestor_index`, even when another unrelated
    class defines a same-named method.
  - Abstain safety: a call with no inferable receiver type (e.g. a bare
    same-file call) resolves exactly as it did before Phase 10 -- the new
    tier never turns a previously-`exact` edge into anything else, and it
    never fabricates a resolution when `receiver_type` is None.
"""
from __future__ import annotations

import sqlite3

from cairn.graph.builder import build_graph
from cairn.graph.resolver import (
    resolve_edge,
    build_members_index,
    build_ancestor_index,
)


def _make_fixture(tmp_path, name: str, files: dict) -> str:
    workspace = tmp_path / name
    repo = workspace / "demo"
    (repo / ".git").mkdir(parents=True)
    for fname, contents in files.items():
        (repo / fname).write_text(contents)
    return str(workspace)


def _edges(db_path, target_name):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            """SELECT s.name AS caller, e.resolution, t.qualified_name AS target_qname
               FROM edges e JOIN symbols s ON e.source_id = s.id
               LEFT JOIN symbols t ON e.target_id = t.id
               WHERE e.kind='calls' AND (e.target_name = ? OR t.name = ?)""",
            (target_name, target_name),
        ).fetchall()
    finally:
        conn.close()


def test_receiver_type_disambiguates_same_named_method(tmp_path):
    files = {
        "Profile.kt": 'class Profile {\n    fun displayName(): String { return "x" }\n}\n',
        "Account.kt": 'class Account {\n    fun displayName(): String { return "y" }\n}\n',
        "UserRepo.kt": (
            "class UserRepo {\n"
            "    val profile: Profile = Profile()\n"
            "    fun run() {\n"
            "        val local: Profile = Profile()\n"
            "        local.displayName()\n"
            "        this.profile.displayName()\n"
            "        profile.displayName()\n"
            "        val other = Account()\n"
            "        other.displayName()\n"
            "        Profile().displayName()\n"
            "    }\n"
            "}\n"
        ),
    }
    ws = _make_fixture(tmp_path, "ws1", files)
    db_path = str(tmp_path / "graph.db")
    summary = build_graph(workspace=ws, db_path=db_path, verbose=False)

    assert summary["resolution"]["ambiguous"] == 0

    rows = _edges(db_path, "displayName")
    assert len(rows) == 5
    targets = [r["target_qname"] for r in rows]
    assert targets.count("Profile.displayName") == 4
    assert targets.count("Account.displayName") == 1
    assert all(r["resolution"] == "exact" for r in rows)


def test_same_file_collision_resolved_by_receiver_type(tmp_path):
    """Regression: both `render` methods AND the typed call live in one file.

    The same-file tier sees two `render` candidates in the caller's own file and
    would return `ambiguous`. Because the type-aware tier runs first (Tier 0),
    the typed `p.render()` still resolves to `Profile.render`. Guards the
    tier-ordering fix (type-aware ahead of same-file).
    """
    files = {
        "All.kt": (
            'class Profile {\n    fun render(): String { return "p" }\n}\n'
            'class Widget {\n    fun render(): String { return "w" }\n}\n'
            "class Screen {\n"
            "    fun show() {\n"
            "        val p: Profile = Profile()\n"
            "        p.render()\n"
            "    }\n"
            "}\n"
        ),
    }
    ws = _make_fixture(tmp_path, "ws_samefile", files)
    db_path = str(tmp_path / "graph.db")
    summary = build_graph(workspace=ws, db_path=db_path, verbose=False)

    assert summary["resolution"]["ambiguous"] == 0
    rows = _edges(db_path, "render")
    assert len(rows) == 1
    assert rows[0]["target_qname"] == "Profile.render"
    assert rows[0]["resolution"] == "exact"


def test_inheritance_resolves_to_base_class_method(tmp_path):
    files = {
        "Base.kt": 'class Base {\n    fun greet(): String { return "hi" }\n}\n',
        "Derived.kt": "class Derived : Base() {\n    fun other() {}\n}\n",
        "Widget.kt": 'class Widget {\n    fun greet(): String { return "yo" }\n}\n',
        "Caller.kt": (
            "class Caller {\n"
            "    fun run() {\n"
            "        val d: Derived = Derived()\n"
            "        d.greet()\n"
            "    }\n"
            "}\n"
        ),
    }
    ws = _make_fixture(tmp_path, "ws2", files)
    db_path = str(tmp_path / "graph.db")
    summary = build_graph(workspace=ws, db_path=db_path, verbose=False)

    assert summary["resolution"]["ambiguous"] == 0
    rows = _edges(db_path, "greet")
    assert len(rows) == 1
    assert rows[0]["target_qname"] == "Base.greet"
    assert rows[0]["resolution"] == "exact"


def test_receiver_type_none_is_abstain_safe():
    """With no receiver_type, resolve_edge must behave exactly as pre-Phase-10:
    two same-named candidates in the same repo (different files) -> ambiguous,
    never a guessed `exact`.
    """
    symbols_by_name = {
        "greet": [
            ("sid_a", "repoA", "file_a", "Base.greet"),
            ("sid_b", "repoA", "file_b", "Widget.greet"),
        ],
    }
    imports_by_file: dict = {}
    members_by_type = {
        ("Base", "greet"): ["sid_a"],
        ("Widget", "greet"): ["sid_b"],
    }
    ancestors: dict = {}

    # No receiver_type at all: same as calling the old 5-arg resolve_edge.
    target_id, label = resolve_edge(
        "greet", "file_c", "repoA", symbols_by_name, imports_by_file,
    )
    assert (target_id, label) == (None, "ambiguous")

    # Passing the type indices but receiver_type=None must not change the
    # outcome (abstain path).
    target_id2, label2 = resolve_edge(
        "greet", "file_c", "repoA", symbols_by_name, imports_by_file,
        None, members_by_type, ancestors,
    )
    assert (target_id2, label2) == (None, "ambiguous")


def test_build_members_index_and_ancestor_index_shapes(tmp_path):
    # build_ancestor_index reads edges.target_name directly (the bare parent
    # name), by design BEFORE the resolver's UPDATE clears it on a resolved
    # inheritance edge (see resolve_repo_edges). So this is a unit test over
    # freshly inserted rows, not a full build_graph() pipeline run -- once the
    # pipeline resolves the `implements Base` edge, target_name is nulled and
    # a *later* call to build_ancestor_index would (correctly, by convention)
    # see nothing, which is exactly why resolve_repo_edges builds this index
    # up front, before any UPDATE is flushed.
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE symbols (id TEXT, file_id TEXT, name TEXT, qualified_name TEXT, kind TEXT);
        CREATE TABLE edges (id TEXT, source_id TEXT, target_id TEXT, target_name TEXT, kind TEXT);
        INSERT INTO symbols VALUES ('sid_base', 'f1', 'Base', 'Base', 'class');
        INSERT INTO symbols VALUES ('sid_greet', 'f1', 'greet', 'Base.greet', 'method');
        INSERT INTO symbols VALUES ('sid_derived', 'f2', 'Derived', 'Derived', 'class');
        -- top-level function in package `pkg`: must NOT be indexed as a member
        -- of `pkg` (spurious ('pkg','helper') key). Guards the type-name filter.
        INSERT INTO symbols VALUES ('sid_helper', 'f3', 'helper', 'pkg.helper', 'function');
        INSERT INTO edges VALUES ('e1', 'sid_derived', NULL, 'Base', 'implements');
        """
    )

    members = build_members_index(conn)
    ancestors = build_ancestor_index(conn)
    conn.close()

    assert ("Base", "greet") in members
    # `pkg` is not a type symbol, so the top-level function is excluded.
    assert ("pkg", "helper") not in members
    assert ancestors.get("Derived") == ["Base"]
