"""Regression tests for Kotlin operator-invoke call resolution.

Covers the bug found on a real Android/Kotlin codebase (Lalatok): a bare call
`someUseCase(params)` is Kotlin sugar for `someUseCase.invoke(params)` on a
DI-injected property/param/local (the standard UseCase / functional-object
pattern in Android Clean Architecture). Before this fix, the parser emitted
the call edge's target_name as the *variable* name ("updateProfileUseCase"),
which the resolver's same-file tier "successfully" (but uselessly) resolved
to the local property declaration in the calling file -- so `get_callers` /
`impact_analysis` on the shared UseCase *class* (e.g. "UpdateProfileUseCase")
returned nothing, for every one of ~150 UseCase classes in the real codebase,
even though every ViewModel that injects them calls them constantly.

The fix rewrites the edge's target to the callee's declared TYPE (reusing the
parser's existing Phase-10 `_resolve_bare_name_type` inference) whenever the
call is a true bare call on a lowercase identifier whose inferred type is a
different, capitalized name.
"""
from __future__ import annotations

import sqlite3

from cairn.graph.builder import build_graph
from cairn.parsers.kotlin import KotlinParser


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


_USE_CASE_FILES = {
    "UpdateProfileUseCase.kt": (
        "class UpdateProfileUseCase {\n"
        "    operator fun invoke(name: String): String {\n"
        "        return name\n"
        "    }\n"
        "}\n"
    ),
    "GetPhotosUseCase.kt": (
        "class GetPhotosUseCase {\n"
        "    operator fun invoke(): String {\n"
        '        return "photos"\n'
        "    }\n"
        "}\n"
    ),
    "ProfileViewModel.kt": (
        "class ProfileViewModel(\n"
        "    private val updateProfileUseCase: UpdateProfileUseCase\n"
        ") {\n"
        "    fun save(name: String) {\n"
        "        updateProfileUseCase(name)\n"
        "    }\n"
        "    fun saveViaInvoke(name: String) {\n"
        "        updateProfileUseCase.invoke(name)\n"
        "    }\n"
        "    fun helper(name: String): String {\n"
        "        return name\n"
        "    }\n"
        "    fun callHelper(name: String) {\n"
        "        helper(name)\n"
        "    }\n"
        "    fun createFoo() {\n"
        "        Foo(\"x\")\n"
        "    }\n"
        "}\n"
        'class Foo(val name: String)\n'
    ),
}


def test_operator_invoke_resolves_to_usecase_class(tmp_path):
    """Regression: a bare `useCase(p)` call must resolve to the UseCase class,
    not the local property. See BUGS.md."""
    ws = _make_fixture(tmp_path, "ws_invoke", _USE_CASE_FILES)
    db_path = str(tmp_path / "graph.db")
    build_graph(workspace=ws, db_path=db_path, verbose=False)

    rows = _edges(db_path, "UpdateProfileUseCase")
    save_rows = [r for r in rows if r["caller"] == "save"]
    assert len(save_rows) == 1, f"expected exactly one edge from save(), got {rows}"
    assert save_rows[0]["target_qname"] == "UpdateProfileUseCase"
    assert save_rows[0]["resolution"] == "exact"


def test_callers_and_impact_reach_the_usecase_class(tmp_path):
    """The real user-visible regression guard: pre-fix this returned 0 rows
    for every one of ~150 UseCase classes in the source codebase."""
    from cairn.graph.traversal import get_callers, impact_analysis

    ws = _make_fixture(tmp_path, "ws_callers", _USE_CASE_FILES)
    db_path = str(tmp_path / "graph.db")
    build_graph(workspace=ws, db_path=db_path, verbose=False)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        callers = get_callers(conn, "UpdateProfileUseCase")
        assert len(callers) >= 1, "expected ProfileViewModel.save to show up as a caller"
        assert any(r["caller_name"] == "save" for r in callers)

        impact = impact_analysis(conn, "UpdateProfileUseCase")
        assert impact["total"] >= 1
    finally:
        conn.close()


def test_two_viewmodels_both_reach_same_usecase(tmp_path):
    """Regression: every caller injecting the same UseCase must reach the class,
    not silently resolve to its own local property. See BUGS.md."""
    files = dict(_USE_CASE_FILES)
    files["MoodViewModel.kt"] = (
        "class MoodViewModel(\n"
        "    private val updateProfileUseCase: UpdateProfileUseCase\n"
        ") {\n"
        "    fun updateMood(name: String) {\n"
        "        updateProfileUseCase(name)\n"
        "    }\n"
        "}\n"
    )
    ws = _make_fixture(tmp_path, "ws_two_vms", files)
    db_path = str(tmp_path / "graph.db")
    build_graph(workspace=ws, db_path=db_path, verbose=False)

    from cairn.graph.traversal import get_callers

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        callers = get_callers(conn, "UpdateProfileUseCase")
    finally:
        conn.close()
    names = {r["caller_name"] for r in callers}
    assert {"save", "updateMood"} <= names


def test_bare_local_function_and_constructor_calls_unchanged(tmp_path):
    ws = _make_fixture(tmp_path, "ws_unchanged", _USE_CASE_FILES)
    db_path = str(tmp_path / "graph.db")
    summary = build_graph(workspace=ws, db_path=db_path, verbose=False)

    assert summary["resolution"]["ambiguous"] == 0

    helper_rows = _edges(db_path, "helper")
    assert len(helper_rows) == 1
    assert helper_rows[0]["target_qname"] == "ProfileViewModel.helper"

    foo_rows = _edges(db_path, "Foo")
    assert len(foo_rows) == 1
    assert foo_rows[0]["target_qname"] == "Foo"


def test_lambda_typed_property_not_rewritten(tmp_path):
    """A function-typed property (`() -> Unit`) is never recorded with an
    inferable user_type by _var_name_and_type/_param_types, so the rewrite
    guard abstains and the bare call keeps its original variable-name target.
    """
    src_file = tmp_path / "Screen.kt"
    src_file.write_text(
        "class Screen(val onClick: () -> Unit) {\n"
        "    fun handle() {\n"
        "        onClick()\n"
        "    }\n"
        "}\n"
    )
    pf = KotlinParser().parse(str(src_file))
    calls = [e for e in pf.edges if e.kind == "calls" and e.source_name == "handle"]
    assert len(calls) == 1
    assert calls[0].target_name == "onClick"
    assert calls[0].receiver_type is None


def test_genuine_method_call_not_rewritten(tmp_path):
    """The WI-1 rewrite must NOT fire for a real method call on a typed
    property: `repo.save()` where `save` is a method, not a property-invoke.
    The target stays 'save' (the method) and receiver_type is Repo (the
    property's declared type). Guards against the rewrite corrupting ordinary
    method-call edges. (Moved from the deleted test_wi1_explicit_receiver_invoke.py.)"""
    src_file = tmp_path / "Guard.kt"
    src_file.write_text(
        "class Repo { fun save(p: String) {} }\n"
        "class Screen(private val repo: Repo) {\n"
        "    fun handle(p: String) { repo.save(p) }\n"
        "}\n"
    )
    pf = KotlinParser().parse(str(src_file))
    calls = [e for e in pf.edges if e.kind == "calls" and e.source_name == "handle"]
    assert len(calls) == 1
    assert calls[0].target_name == "save"
    assert calls[0].receiver_type == "Repo"


def test_explicit_invoke_call_unchanged(tmp_path):
    """`useCase.invoke(p)` must still emit target_name='invoke' with
    receiver_type set to the class -- guards that the rewrite only fires for
    bare (no navigation_expression) calls."""
    ws = _make_fixture(tmp_path, "ws_explicit_invoke", _USE_CASE_FILES)
    src_file = ws + "/demo/ProfileViewModel.kt"
    pf = KotlinParser().parse(src_file)
    calls = [
        e for e in pf.edges
        if e.kind == "calls" and e.source_name == "saveViaInvoke"
    ]
    assert len(calls) == 1
    assert calls[0].target_name == "invoke"
    assert calls[0].receiver_type == "UpdateProfileUseCase"


def test_call_shape_table(tmp_path):
    """Table-driven regression net over every call shape the guard must
    distinguish, from one file, at the parser level (no resolution)."""
    src_file = tmp_path / "Shapes.kt"
    src_file.write_text(
        "class UseCaseA {\n"
        "    operator fun invoke(p: String) {}\n"
        "}\n"
        "class Shapes(private val useCaseA: UseCaseA) {\n"
        "    fun bareCall(p: String) {\n"
        "        useCaseA(p)\n"
        "    }\n"
        "    fun explicitInvoke(p: String) {\n"
        "        useCaseA.invoke(p)\n"
        "    }\n"
        "    fun thisPrefixed(p: String) {\n"
        "        this.useCaseA(p)\n"
        "    }\n"
        "    fun constructorCall(p: String) {\n"
        "        UseCaseA()\n"
        "    }\n"
        "    fun localHelper(p: String) {\n"
        "        helperFn(p)\n"
        "    }\n"
        "    fun helperFn(p: String) {}\n"
        "}\n"
    )
    pf = KotlinParser().parse(str(src_file))
    by_caller = {}
    for e in pf.edges:
        if e.kind == "calls":
            by_caller.setdefault(e.source_name, []).append((e.target_name, e.receiver_type))

    assert by_caller["bareCall"] == [("UseCaseA", None)]
    assert by_caller["explicitInvoke"] == [("invoke", "UseCaseA")]
    # WI-1 (2026-07-30): `this.useCaseA(p)` is operator-invoke sugar on the
    # `useCaseA` property of type UseCaseA. The parser now infers the property's
    # declared type (UseCaseA) as the receiver and rewrites the target to it --
    # so this resolves to the UseCase class, exactly like the bare `useCaseA(p)`
    # case. Pre-WI-1 this emitted ("useCaseA", "Shapes") and resolved to the
    # local property, a real-but-useless resolution.
    assert by_caller["thisPrefixed"] == [("UseCaseA", "UseCaseA")]
    assert by_caller["constructorCall"] == [("UseCaseA", None)]
    assert by_caller["localHelper"] == [("helperFn", None)]
