"""Contract tests for the ``--refine-catalog`` two-step wiki generation (FR-007).

D-003: with ``--refine-catalog``, ``cairn wiki generate --llm`` queues exactly
one ``wiki-catalog`` task and returns with claim/complete instructions (the
``cli/compass.py`` queued-task echo shape); page tasks spawn only on a re-run
that finds the completed catalog's Task-Result sibling (``llm/tasks.py
read_result`` — never a promoted concept).

CLI scenarios (TC-019..TC-022, over a seeded four-module graph whose
deterministic plan is ``overview, m_a, m_b, m_c, m_big``):

- TC-019  first run: one pending ``wiki-catalog`` task, zero ``wiki-page``
  tasks, output carries the task id and claim/complete instructions;
- TC-020  after the catalog task completes with a fully valid refined
  outline, the re-run queues page tasks from that outline (retitles prove
  refinement drove the queue) and no ``wiki-catalog`` task stays pending;
- TC-021  a refined entry naming a nonexistent module is rejected and the
  deterministic plan's entry is kept in its slot (positionally), while the
  valid refined entries are honored;
- TC-022  a catalog chain that fails through its revise cycle to
  ``dropped: True`` falls back to the full deterministic plan on the re-run.

Validator unit contract (implemented over the graph, LIKE prefix precedent
``viz/query.py:get_module_graph``, seed resolution via ``refs.py:file_exists``):

    validate_refined_outline(refined, deterministic_plan, conn) -> list[dict]

``refined`` is the parsed catalog result: a JSON array of
``{title, description, module, seeds?}`` entries. The return value is the
effective page plan in refined order; every record carries the planner
record shape (``page_id/title/description/module/seeds/input_hash``). An
entry is kept when its ``module`` matches a real ``files.path`` prefix
(``module == ""`` is the repo-wide overview and always valid) and every
``seeds.files`` path resolves; otherwise the deterministic plan's entry for
the same slot (same index) is kept verbatim. Omitted ``seeds`` are inherited
from the deterministic entry for the same module; ``input_hash`` is
recomputed with the planner's sha256-canonical-JSON scheme.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path

import pytest
from click.testing import CliRunner

from cairn.cli.wiki import wiki
from cairn.llm.tasks import (
    claim_task,
    complete_task,
    create_task,
    get_task,
    list_tasks,
    read_result,
)
from cairn.okf.bundle import OKFBundle
from cairn.wiki.catalog import build_page_plan

# A result the critic must fail: the cited file does not resolve in the
# graph and the body has none of the scored section headings.
_FAILING_RESULT = "See `src/does_not_exist.py` for the catalog contents."


def _seed_graph(conn: sqlite3.Connection) -> None:
    """Seed the four-module graph with known cross-module incoming degrees.

    Same ledger as the planner contract tests: ranking m_a, m_b, m_c, m_big.
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
        ("e1", "s_a", "s_b"),
        ("e2", "s_big1", "s_b"),
        ("e3", "s_b", "s_c"),
        ("e4", "s_big1", "s_c"),
        ("e5", "s_b", "s_a"),
        ("e6", "s_c", "s_a"),
        ("e7", "s_big1", "s_a"),
        ("e8", "s_big1h", "s_big1"),
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


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    """Hermetic store: cwd in tmp, CAIRN_DB/CAIRN_KNOWLEDGE under tmp_path."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAIRN_DB", str(tmp_path / "graph.db"))
    monkeypatch.setenv("CAIRN_KNOWLEDGE", str(tmp_path / "knowledge"))
    return tmp_path


def _make_db(tmp_path: Path) -> str:
    """Create and seed the graph DB file; return its path as a string."""
    from cairn.graph.schema import get_db

    db_path = str(tmp_path / "graph.db")
    conn = get_db(db_path)
    _seed_graph(conn)
    conn.close()
    return db_path


def _open_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _generate(db_path: str, knowledge: str) -> object:
    return CliRunner().invoke(
        wiki,
        [
            "generate", "--llm", "--refine-catalog",
            "--db", db_path,
            "--knowledge", knowledge,
            "--repo", "r",
        ],
    )


def _pending_catalog_task(bundle: OKFBundle):
    tasks = list_tasks(bundle, kind="wiki-catalog", status="pending")
    assert len(tasks) == 1
    return tasks[0]


def _complete_catalog(bundle: OKFBundle, task_id: str, result: str, conn=None):
    assert claim_task(bundle, task_id, "test-agent") is not None
    return complete_task(bundle, task_id, result, conn=conn)


def _page_modules(bundle: OKFBundle, status: str = "pending"):
    return list_tasks(bundle, kind="wiki-page", status=status)


def _pending_catalog_chain(bundle: OKFBundle):
    """Pending tasks of the catalog chain (synthesize + derived revises)."""
    return [
        t for t in list_tasks(bundle, status="pending")
        if t.task_kind.startswith("wiki-catalog")
    ]


def _expected_hash(entry: dict) -> str:
    canonical = json.dumps(entry, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --- TC-019: the first run queues only the catalog task --------------------


class TestFirstRunQueuesOnlyCatalogTask:
    def test_refine_catalog_queues_one_catalog_and_zero_page_tasks(self, cli_env):
        tmp_path = cli_env
        db = _make_db(tmp_path)
        knowledge = str(tmp_path / ".knowledge")

        result = _generate(db, knowledge)
        assert result.exit_code == 0, result.output

        bundle = OKFBundle(knowledge)
        catalog = list_tasks(bundle, kind="wiki-catalog")
        assert len(catalog) == 1
        assert catalog[0].status == "pending"
        assert _page_modules(bundle) == []
        assert list_tasks(bundle, kind="wiki-page") == []

    def test_output_echoes_task_id_and_claim_complete_instructions(self, cli_env):
        tmp_path = cli_env
        db = _make_db(tmp_path)
        knowledge = str(tmp_path / ".knowledge")

        result = _generate(db, knowledge)
        assert result.exit_code == 0, result.output

        bundle = OKFBundle(knowledge)
        catalog = list_tasks(bundle, kind="wiki-catalog")
        assert catalog[0].id in result.output
        assert "claim" in result.output
        assert "complete" in result.output


# --- TC-020: a valid refined outline drives the page tasks -----------------


class TestRerunQueuesPagesFromRefinedOutline:
    def test_rerun_after_valid_catalog_completes_queues_refined_pages(self, cli_env):
        tmp_path = cli_env
        db = _make_db(tmp_path)
        knowledge = str(tmp_path / ".knowledge")
        bundle = OKFBundle(knowledge)

        first = _generate(db, knowledge)
        assert first.exit_code == 0, first.output
        catalog = _pending_catalog_task(bundle)

        refined = [
            {"title": "Repository overview", "description": "Overview first.",
             "module": ""},
            {"title": "C utilities (retitled)", "description": "Util module.",
             "module": "m_c"},
            {"title": "A core", "description": "Core module.", "module": "m_a"},
            {"title": "B api", "description": "Api module.", "module": "m_b"},
            {"title": "Big module", "description": "Big module.", "module": "m_big"},
        ]
        outcome = _complete_catalog(bundle, catalog.id, json.dumps(refined))
        assert outcome["promoted"] is False

        # The result is a Task-Result sibling, not a promoted concept.
        assert get_task(bundle, catalog.id).status == "done"
        assert json.loads(read_result(bundle, catalog.id)) == refined
        assert bundle.list_concepts(prefix="wiki/") == []

        second = _generate(db, knowledge)
        assert second.exit_code == 0, second.output

        pages = _page_modules(bundle)
        assert Counter(t.resource for t in pages) == Counter(
            {"overview": 1, "m-c": 1, "m-a": 1, "m-b": 1, "m-big": 1}
        )
        by_resource = {t.resource: t for t in pages}
        # The retitled refined entry (not the deterministic title) drove the queue.
        assert by_resource["m-c"].facts["title"] == "C utilities (retitled)"
        assert by_resource["overview"].facts["title"] == "Repository overview"
        assert list_tasks(bundle, kind="wiki-catalog", status="pending") == []


# --- TC-021: an invalid refined entry reverts to the deterministic entry ---


class TestInvalidRefinedEntryKeepsDeterministicSlot:
    def test_phantom_module_entry_reverts_to_deterministic_plan_slot(self, cli_env):
        tmp_path = cli_env
        db = _make_db(tmp_path)
        knowledge = str(tmp_path / ".knowledge")
        bundle = OKFBundle(knowledge)
        det = build_page_plan(_open_db(db), "r")

        first = _generate(db, knowledge)
        assert first.exit_code == 0, first.output
        catalog = _pending_catalog_task(bundle)

        refined = [
            {"title": "Repository overview", "description": "Overview first.",
             "module": ""},
            {"title": "B refined", "description": "Refined B.", "module": "m_b"},
            {"title": "Ghost page", "description": "Phantom module.",
             "module": "m_ghost"},
            {"title": "A refined", "description": "Refined A.", "module": "m_a"},
        ]
        assert claim_task(bundle, catalog.id, "test-agent") is not None
        assert complete_task(bundle, catalog.id, json.dumps(refined))["dropped"] is False

        second = _generate(db, knowledge)
        assert second.exit_code == 0, second.output

        pages = _page_modules(bundle)
        # The rejected slot keeps the deterministic plan's entry in its place;
        # the valid refined entries are honored.
        assert Counter(t.resource for t in pages) == Counter(
            {"overview": 1, "m-b": 2, "m-a": 1}
        )
        b_pages = [t for t in pages if t.resource == "m-b"]
        refined_b = [t for t in b_pages if t.facts["title"] == "B refined"]
        det_b = [t for t in b_pages if t.facts["input_hash"] == det[2]["input_hash"]]
        assert len(refined_b) == 1
        assert len(det_b) == 1
        assert det_b[0].facts["title"] == det[2]["title"]
        a_page = next(t for t in pages if t.resource == "m-a")
        assert a_page.facts["title"] == "A refined"
        assert list_tasks(bundle, kind="wiki-catalog", status="pending") == []


# --- TC-022: a failed/dropped refinement falls back to the deterministic plan


class TestDroppedCatalogFallsBackToDeterministicPlan:
    def test_rerun_after_catalog_chain_drops_queues_deterministic_plan(self, cli_env):
        tmp_path = cli_env
        db = _make_db(tmp_path)
        knowledge = str(tmp_path / ".knowledge")
        bundle = OKFBundle(knowledge)

        first = _generate(db, knowledge)
        assert first.exit_code == 0, first.output

        # Drive the catalog chain through its bounded revise cycle to the drop.
        conn = _open_db(db)
        try:
            outcome = None
            for _ in range(3):
                chain = _pending_catalog_chain(bundle)
                assert len(chain) == 1
                outcome = _complete_catalog(
                    bundle, chain[0].id, _FAILING_RESULT, conn=conn
                )
            assert outcome["dropped"] is True
        finally:
            conn.close()

        conn2 = _open_db(db)
        try:
            det = build_page_plan(conn2, "r")
        finally:
            conn2.close()

        second = _generate(db, knowledge)
        assert second.exit_code == 0, second.output

        pages = _page_modules(bundle)
        assert Counter(t.resource for t in pages) == Counter(
            {page["page_id"]: 1 for page in det}
        )
        det_by_id = {page["page_id"]: page for page in det}
        for t in pages:
            assert t.facts["input_hash"] == det_by_id[t.resource]["input_hash"]
        assert list_tasks(bundle, kind="wiki-catalog", status="pending") == []


# --- validator unit contract ------------------------------------------------


class TestValidateRefinedOutline:
    def _det(self, fresh_db):
        _seed_graph(fresh_db)
        return build_page_plan(fresh_db, "r")

    def test_valid_entries_are_kept_in_refined_order(self, fresh_db):
        from cairn.wiki.refine import validate_refined_outline

        det = self._det(fresh_db)
        refined = [
            {"title": "Repository overview", "description": "d0", "module": ""},
            {"title": "C utilities", "description": "d1", "module": "m_c"},
            {"title": "A core", "description": "d2", "module": "m_a"},
        ]
        eff = validate_refined_outline(refined, det, fresh_db)
        assert [page["module"] for page in eff] == ["", "m_c", "m_a"]
        assert eff[1]["title"] == "C utilities"
        assert eff[2]["title"] == "A core"

    def test_phantom_module_entry_is_replaced_by_deterministic_slot_entry(
        self, fresh_db
    ):
        from cairn.wiki.refine import validate_refined_outline

        det = self._det(fresh_db)
        refined = [
            {"title": "Repository overview", "description": "d0", "module": ""},
            {"title": "Ghost page", "description": "g", "module": "m_ghost"},
        ]
        eff = validate_refined_outline(refined, det, fresh_db)
        assert eff[0]["title"] == "Repository overview"
        assert eff[1] == det[1]

    def test_entry_with_unresolvable_seed_file_is_replaced(self, fresh_db):
        from cairn.wiki.refine import validate_refined_outline

        det = self._det(fresh_db)
        refined = [
            {"title": "Repository overview", "description": "d0", "module": ""},
            {
                "title": "B bad seeds", "description": "d1", "module": "m_b",
                "seeds": {"files": ["m_b/phantom.py"], "symbols": ["b_api"]},
            },
            {
                "title": "B good seeds", "description": "d2", "module": "m_c",
                "seeds": {"files": ["m_c/util.py"], "symbols": ["c_util"]},
            },
        ]
        eff = validate_refined_outline(refined, det, fresh_db)
        assert eff[1] == det[1]
        assert eff[2]["title"] == "B good seeds"
        assert eff[2]["seeds"] == {"files": ["m_c/util.py"], "symbols": ["c_util"]}

    def test_empty_module_is_the_valid_overview_entry(self, fresh_db):
        from cairn.wiki.refine import validate_refined_outline

        det = self._det(fresh_db)
        refined = [
            {"title": "Custom overview", "description": "d", "module": ""},
        ]
        eff = validate_refined_outline(refined, det, fresh_db)
        assert len(eff) == 1
        assert eff[0]["title"] == "Custom overview"
        assert eff[0]["page_id"] == "overview"

    def test_records_carry_planner_shape_and_recomputed_input_hash(self, fresh_db):
        from cairn.wiki.refine import validate_refined_outline

        det = self._det(fresh_db)
        seeds = {"files": ["m_b/api.py"], "symbols": ["b_api"]}
        refined = [
            {
                "title": "B retitled", "description": "d", "module": "m_b",
                "seeds": seeds,
            },
            {"title": "Ghost page", "description": "g", "module": "m_ghost"},
        ]
        eff = validate_refined_outline(refined, det, fresh_db)
        assert set(eff[0].keys()) == set(det[0].keys())
        assert eff[0]["page_id"] == "m-b"
        assert eff[0]["input_hash"] == _expected_hash(
            {
                "page_id": "m-b", "title": "B retitled", "description": "d",
                "module": "m_b", "seeds": seeds,
            }
        )
        # The replaced slot keeps the deterministic record verbatim, hash included.
        assert eff[1] == det[1]
        assert eff[1]["input_hash"] == det[1]["input_hash"]

    def test_omitted_seeds_inherit_from_deterministic_entry_for_the_module(
        self, fresh_db
    ):
        from cairn.wiki.refine import validate_refined_outline

        det = self._det(fresh_db)
        det_b = next(page for page in det if page["module"] == "m_b")
        refined = [{"title": "B!", "description": "d", "module": "m_b"}]
        eff = validate_refined_outline(refined, det, fresh_db)
        assert eff[0]["title"] == "B!"
        assert eff[0]["seeds"] == det_b["seeds"]
        assert eff[0]["input_hash"] == _expected_hash(
            {
                "page_id": "m-b", "title": "B!", "description": "d",
                "module": "m_b", "seeds": det_b["seeds"],
            }
        )


# --- the queue stays untouched by the refine contract ----------------------


class TestQueueIntegration:
    def test_refine_task_completes_through_the_existing_queue_primitives(
        self, fresh_db, tmp_path
    ):
        """The catalog result rides the standard claim/complete path; no
        completion hook spawns anything (D-003) — completion alone must not
        create page tasks."""
        knowledge = tmp_path / ".knowledge"
        knowledge.mkdir()
        bundle = OKFBundle(knowledge)
        task = create_task(bundle, "wiki-catalog", "r", facts={"repo": "r"})
        outcome = _complete_catalog(bundle, task.id, json.dumps([{"title": "t"}]))
        assert outcome["dropped"] is False
        assert _page_modules(bundle) == []
