"""Contract tests for the wiki catalog planner (FR-001).

Pins the contract of ``build_page_plan(conn, repo, pages_cap=10)`` in
``src/cairn/wiki/catalog.py``:

- an overview page is planned first;
- modules whose indexed files are majority test files (``test``/``spec``
  path segments) are excluded from the plan entirely — page budget flows to
  the next product-code module;
- modules are ranked by cross-module incoming edge degree DESC, ties broken
  by module name ASC (D-005) — a large self-referential module must not win;
- the plan is capped at ``pages_cap`` (default 10), overview included;
- every page record carries ``page_id`` (filesystem-safe slug), ``title``,
  ``description``, ``module``, ``seeds`` (``{"files", "symbols"}``), and
  ``input_hash`` (sha256 over the canonical JSON of the entry without the
  hash itself);
- two builds over one unchanged graph are identical;
- an empty/unindexed graph raises ``WikiPlannerError``.

Module = first 2-3 path segments of ``files.path`` (the ``group_by_top_level``
bucketing); with the two-segment paths seeded here, module == first segment.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import re
import sqlite3

import pytest

from cairn.wiki.catalog import WikiPlannerError, build_page_plan


def _slug(module: str) -> str:
    """Contract: a module's page_id is its slug — lowercased, runs of
    non-alphanumeric characters collapsed to '-', leading/trailing '-' stripped."""
    return re.sub(r"[^a-z0-9]+", "-", module.lower()).strip("-")


def _seed_graph(conn: sqlite3.Connection) -> None:
    """Seed a four-module graph with known cross-module incoming degrees.

    Cross-module incoming degree ledger: m_a=3, m_b=2, m_c=2, m_big=0.
    m_b/m_c tie at 2 (name ASC must put m_b first); m_big has the most edges
    overall but all internal, so it must rank last, not first.
    """
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('r', 'r', '/tmp/r')")
    for fid, path in [
        ("f1", "m_a/core.py"),
        ("f2", "m_b/api.py"),
        ("f3", "m_c/util.py"),
        ("f4", "m_big/one.py"),
        ("f5", "m_big/two.py"),
    ]:
        conn.execute(
            "INSERT INTO files (id, repo_id, path, language) VALUES (?, 'r', ?, 'python')",
            (fid, path),
        )
    for sid, fid, name in [
        ("s_a", "f1", "a_core"),
        ("s_b", "f2", "b_api"),
        ("s_c", "f3", "c_util"),
        ("s_big1", "f4", "big1"),
        ("s_big1h", "f4", "big1_helper"),
        ("s_big2", "f5", "big2"),
    ]:
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
            "VALUES (?, ?, ?, 'function', ?, 1, 10)",
            (sid, fid, name, name),
        )
    for eid, src, dst in [
        ("e1", "s_a", "s_b"),        # m_a -> m_b
        ("e2", "s_big1", "s_b"),     # m_big -> m_b
        ("e3", "s_b", "s_c"),        # m_b -> m_c
        ("e4", "s_big1", "s_c"),     # m_big -> m_c
        ("e5", "s_b", "s_a"),        # m_b -> m_a
        ("e6", "s_c", "s_a"),        # m_c -> m_a
        ("e7", "s_big1", "s_a"),     # m_big -> m_a
        ("e8", "s_big1h", "s_big1"),  # m_big internal — must not count
        ("e9", "s_big2", "s_big1"),
        ("e10", "s_big1", "s_big1h"),
        ("e11", "s_big2", "s_big1h"),
        ("e12", "s_big1h", "s_big2"),
    ]:
        conn.execute(
            "INSERT INTO edges (id, source_id, target_id, kind) VALUES (?, ?, ?, 'calls')",
            (eid, src, dst),
        )
    conn.commit()


def _modules(plan: list) -> list:
    return [page["module"] for page in plan if page["module"]]


def _seed_graph_with_tests_module(conn: sqlite3.Connection) -> None:
    """Seed a five-module graph: ``_seed_graph`` plus a pure test module that
    outranks every code module.

    Cross-module incoming degree ledger: tests=4, m_a=3, m_b=2, m_c=2,
    m_big=0. Every ``tests/`` file sits under a ``tests`` path segment, so
    the module is test-majority and must never be planned.
    """
    _seed_graph(conn)
    for fid, path in [
        ("f_t1", "tests/conftest.py"),
        ("f_t2", "tests/test_planner.py"),
    ]:
        conn.execute(
            "INSERT INTO files (id, repo_id, path, language) VALUES (?, 'r', ?, 'python')",
            (fid, path),
        )
    for sid, fid, name in [
        ("s_t1", "f_t1", "t_hook"),
        ("s_t2", "f_t2", "t_case"),
    ]:
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
            "VALUES (?, ?, ?, 'function', ?, 1, 10)",
            (sid, fid, name, name),
        )
    for eid, src, dst in [
        ("e_t1", "s_a", "s_t1"),     # m_a -> tests
        ("e_t2", "s_b", "s_t1"),     # m_b -> tests
        ("e_t3", "s_c", "s_t1"),     # m_c -> tests
        ("e_t4", "s_big1", "s_t2"),  # m_big -> tests
    ]:
        conn.execute(
            "INSERT INTO edges (id, source_id, target_id, kind) VALUES (?, ?, ?, 'calls')",
            (eid, src, dst),
        )
    conn.commit()


def _page_for(plan: list, module: str) -> dict:
    return next(page for page in plan if page["module"] == module)


class TestPageRecordContract:
    """Every page record carries the FR-001 fields with the pinned shapes."""

    def test_every_page_record_carries_the_pinned_fields(self, fresh_db):
        _seed_graph(fresh_db)
        plan = build_page_plan(fresh_db, "r")
        assert isinstance(plan, list)
        assert plan
        for page in plan:
            assert isinstance(page, dict)
            assert set(page.keys()) == {
                "page_id",
                "title",
                "description",
                "module",
                "seeds",
                "input_hash",
            }
            assert isinstance(page["title"], str) and page["title"]
            assert isinstance(page["description"], str) and page["description"]
            assert isinstance(page["module"], str)
            seeds = page["seeds"]
            assert set(seeds.keys()) == {"files", "symbols"}
            assert all(isinstance(f, str) and f for f in seeds["files"])
            assert all(isinstance(s, str) and s for s in seeds["symbols"])
            assert re.fullmatch(r"[0-9a-f]{64}", page["input_hash"])

    def test_page_id_is_a_filesystem_safe_unique_slug(self, fresh_db):
        _seed_graph(fresh_db)
        plan = build_page_plan(fresh_db, "r")
        page_ids = [page["page_id"] for page in plan]
        assert len(set(page_ids)) == len(page_ids)
        for page in plan:
            assert re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", page["page_id"])
        for page in plan[1:]:
            assert page["page_id"] == _slug(page["module"])

    def test_module_page_seeds_name_module_files_and_top_symbols(self, fresh_db):
        _seed_graph(fresh_db)
        plan = build_page_plan(fresh_db, "r")
        m_a = _page_for(plan, "m_a")
        assert set(m_a["seeds"]["files"]) == {"m_a/core.py"}
        assert "a_core" in m_a["seeds"]["symbols"]
        m_big = _page_for(plan, "m_big")
        assert set(m_big["seeds"]["files"]) == {"m_big/one.py", "m_big/two.py"}
        graph_files = {row["path"] for row in fresh_db.execute("SELECT path FROM files")}
        graph_symbols = {row["name"] for row in fresh_db.execute("SELECT name FROM symbols")}
        for page in plan:
            assert set(page["seeds"]["files"]) <= graph_files
            assert set(page["seeds"]["symbols"]) <= graph_symbols

    def test_input_hash_is_sha256_over_canonical_json_of_the_entry(self, fresh_db):
        _seed_graph(fresh_db)
        plan = build_page_plan(fresh_db, "r")
        for page in plan:
            entry = {k: v for k, v in page.items() if k != "input_hash"}
            canonical = json.dumps(entry, sort_keys=True, separators=(",", ":"))
            expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            assert page["input_hash"] == expected


class TestPlanOrdering:
    """Overview first; test-majority modules excluded entirely; module
    ranking per D-005 over the remaining code modules."""

    def test_overview_page_is_planned_first(self, fresh_db):
        _seed_graph(fresh_db)
        plan = build_page_plan(fresh_db, "r")
        assert plan[0]["page_id"] == "overview"
        assert plan[0]["module"] == ""
        assert plan[0]["title"] and plan[0]["description"]
        assert len(plan) == 5  # overview + all four seeded modules

    def test_modules_ranked_by_cross_module_incoming_degree_desc(self, fresh_db):
        _seed_graph_with_tests_module(fresh_db)
        plan = build_page_plan(fresh_db, "r")
        # Degrees: tests=4, m_a=3, m_b=2, m_c=2, m_big=0 (5 internal edges
        # ignored). The test-majority module outranks every code module yet
        # is absent from the plan entirely; the top code module takes its
        # slot and the rest keep their degree order.
        assert "tests" not in _modules(plan)
        assert _modules(plan) == ["m_a", "m_b", "m_c", "m_big"]
        capped = build_page_plan(fresh_db, "r", pages_cap=2)
        assert [page["page_id"] for page in capped] == ["overview", "m-a"]

    def test_all_test_majority_modules_raise_wiki_planner_error(self, fresh_db):
        fresh_db.execute(
            "INSERT INTO repos (id, name, path) VALUES ('r', 'r', '/tmp/r')"
        )
        for fid, path in [
            ("f1", "tests/conftest.py"),
            ("f2", "tests/test_core.py"),
        ]:
            fresh_db.execute(
                "INSERT INTO files (id, repo_id, path, language) VALUES (?, 'r', ?, 'python')",
                (fid, path),
            )
        fresh_db.commit()
        # Files are indexed, but no product-code module survives the
        # exclusion — a distinct path from the empty-graph error below.
        with pytest.raises(WikiPlannerError, match=r".+"):
            build_page_plan(fresh_db, "r")

    def test_equal_degree_modules_tiebroken_by_module_name_asc(self, fresh_db):
        _seed_graph(fresh_db)
        plan = build_page_plan(fresh_db, "r")
        # m_b and m_c tie at degree 2; name ASC must keep m_b ahead of m_c.
        assert plan.index(_page_for(plan, "m_b")) < plan.index(_page_for(plan, "m_c"))


class TestPlanCap:
    """The plan never exceeds pages_cap, overview page included."""

    def test_default_cap_is_ten_pages(self):
        signature = inspect.signature(build_page_plan)
        assert list(signature.parameters) == ["conn", "repo", "pages_cap"]
        assert signature.parameters["pages_cap"].default == 10

    def test_plan_capped_at_pages_cap_keeping_top_ranked_modules(self, fresh_db):
        _seed_graph(fresh_db)
        plan = build_page_plan(fresh_db, "r", pages_cap=3)
        assert len(plan) == 3
        assert _modules(plan) == ["m_a", "m_b"]

    def test_cap_of_one_leaves_only_the_overview(self, fresh_db):
        _seed_graph(fresh_db)
        plan = build_page_plan(fresh_db, "r", pages_cap=1)
        assert [page["page_id"] for page in plan] == ["overview"]


class TestDeterminism:
    """The plan is a pure function of the graph."""

    def test_two_builds_over_unchanged_graph_are_identical(self, fresh_db):
        _seed_graph(fresh_db)
        first = build_page_plan(fresh_db, "r")
        second = build_page_plan(fresh_db, "r")
        assert first == second

    def test_plan_reflects_graph_changes(self, fresh_db):
        _seed_graph(fresh_db)
        before = build_page_plan(fresh_db, "r")
        fresh_db.execute(
            "INSERT INTO edges (id, source_id, target_id, kind) "
            "VALUES ('e13', 's_big2', 's_c', 'calls')"
        )
        fresh_db.commit()
        after = build_page_plan(fresh_db, "r")
        # m_c's cross-module incoming degree rises 2 -> 3, past m_b.
        assert _modules(after) == ["m_a", "m_c", "m_b", "m_big"]
        assert _modules(after) != _modules(before)


class TestEmptyGraph:
    """An empty/unindexed graph raises a clean planner error (US1 AC3)."""

    def test_empty_graph_raises_wiki_planner_error(self, fresh_db):
        with pytest.raises(WikiPlannerError, match=r".+"):
            build_page_plan(fresh_db, "r")

    def test_repo_row_without_files_raises_wiki_planner_error(self, fresh_db):
        fresh_db.execute(
            "INSERT INTO repos (id, name, path) VALUES ('r', 'r', '/tmp/r')"
        )
        fresh_db.commit()
        with pytest.raises(WikiPlannerError, match=r".+"):
            build_page_plan(fresh_db, "r")

    def test_wiki_planner_error_is_an_exception(self):
        assert issubclass(WikiPlannerError, Exception)
