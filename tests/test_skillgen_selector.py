"""Selector resolution for skill generation (FR-001).

Each test builds a real two-package workspace and asserts resolution
against its live graph: module name, directory prefix, explicit symbol
list, and unresolvable selectors.

Contract: ``resolve_selector(conn, selector) -> SelectorResolution`` never
raises for an unresolvable selector — it returns the resolved
``candidates`` (qualified symbol names) plus the ``unmatched`` tokens.
A caller MUST treat non-empty ``unmatched`` (or empty ``candidates``)
as a failure and generate nothing — no partial skill.
"""
from __future__ import annotations

from cairn.graph.builder import build_graph
from cairn.graph.schema import get_db
from cairn.skillgen.selector import SelectorResolution, resolve_selector

PKG_A_TAILS = {"zeta_hub", "alpha_leaf"}


def _two_pkg_conn(tmp_path):
    """Workspace: pkg_a/{zeta_hub, ZetaNest.alpha_leaf}, pkg_b/{beta_one}."""
    repo = tmp_path / "demo"
    (repo / ".git").mkdir(parents=True)
    (repo / "pkg_a").mkdir()
    (repo / "pkg_b").mkdir()
    (repo / "pkg_a" / "zeta.py").write_text(
        "def zeta_hub() -> str:\n"
        "    return 'zeta'\n"
        "\n"
        "class ZetaNest:\n"
        "    def alpha_leaf(self) -> str:\n"
        "        return 'leaf'\n"
    )
    (repo / "pkg_b" / "beta.py").write_text(
        "def beta_one() -> str:\n"
        "    return 'beta'\n"
    )
    db_path = tmp_path / "graph.db"
    build_graph(workspace=str(tmp_path), db_path=str(db_path), verbose=False)
    return get_db(str(db_path))


def _tails(candidates):
    return {c.rsplit(".", 1)[-1] for c in candidates}


def test_module_name_selects_that_modules_symbols(tmp_path):
    conn = _two_pkg_conn(tmp_path)
    res = resolve_selector(conn, "pkg_a")
    assert isinstance(res, SelectorResolution)
    assert res.unmatched == []
    assert PKG_A_TAILS <= _tails(res.candidates)
    assert "beta_one" not in _tails(res.candidates)


def test_directory_prefix_selects_same_symbols_as_module_name(tmp_path):
    conn = _two_pkg_conn(tmp_path)
    assert (
        resolve_selector(conn, "pkg_a/").candidates
        == resolve_selector(conn, "pkg_a").candidates
    )


def test_explicit_symbol_list_selects_exactly_those_symbols(tmp_path):
    conn = _two_pkg_conn(tmp_path)
    res = resolve_selector(conn, "zeta_hub ZetaNest.alpha_leaf")
    assert res.unmatched == []
    assert _tails(res.candidates) == PKG_A_TAILS
    assert "beta_one" not in _tails(res.candidates)


def test_unresolvable_selector_returns_empty_candidates_and_unmatched(tmp_path):
    conn = _two_pkg_conn(tmp_path)
    res = resolve_selector(conn, "no_such_mod_qq")
    assert res.candidates == []
    assert res.unmatched == ["no_such_mod_qq"]


def test_module_kind_symbol_does_not_shadow_module_path(tmp_path):
    """A pkg_a/__init__.py indexes a module-kind symbol named pkg_a; the
    token must resolve the module's contents, not the lone module symbol."""
    repo = tmp_path / "demo"
    (repo / ".git").mkdir(parents=True)
    (repo / "pkg_a").mkdir()
    (repo / "pkg_a" / "__init__.py").write_text(
        "from pkg_a.core import zeta_hub\n"
    )
    (repo / "pkg_a" / "core.py").write_text(
        "def zeta_hub():\n"
        "    return 1\n"
        "\n"
        "def mid_one():\n"
        "    return zeta_hub()\n"
        "\n"
        "def mid_two():\n"
        "    return zeta_hub()\n"
        "\n"
        "def alpha_leaf():\n"
        "    return 2\n"
    )
    (repo / "sub_mod.py").write_text(
        "def beta_one():\n"
        "    return 1\n"
    )
    db_path = tmp_path / "graph.db"
    build_graph(workspace=str(tmp_path), db_path=str(db_path), verbose=False)
    conn = get_db(str(db_path))

    res = resolve_selector(conn, "pkg_a")

    assert res.unmatched == []
    assert len(res.candidates) >= 4
    assert {"zeta_hub", "alpha_leaf", "mid_one", "mid_two"} <= _tails(res.candidates)
    assert res.candidates != ["pkg_a"]
    assert "beta_one" not in _tails(res.candidates)


def test_mixed_selector_lists_unmatched_alongside_resolved(tmp_path):
    conn = _two_pkg_conn(tmp_path)
    res = resolve_selector(conn, "zeta_hub no_such_sym_qq")
    assert res.unmatched == ["no_such_sym_qq"]
    assert PKG_A_TAILS & _tails(res.candidates)


def test_resolution_is_deterministic(tmp_path):
    conn = _two_pkg_conn(tmp_path)
    assert (
        resolve_selector(conn, "pkg_a").candidates
        == resolve_selector(conn, "pkg_a").candidates
    )


def test_resolution_is_read_only(tmp_path):
    conn = _two_pkg_conn(tmp_path)
    before = conn.total_changes
    resolve_selector(conn, "pkg_a")
    resolve_selector(conn, "zeta_hub")
    assert conn.total_changes == before
