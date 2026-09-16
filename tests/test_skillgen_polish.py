"""--polish: queue-backed skill polish behind the critic gate (FR-003, TC-010).

Contract: ``--polish`` never runs a model in-process — it enqueues one
``skill-polish`` task through the existing task queue (visible via
``cairn task list --status pending``) and the deterministic skill is
written immediately. The queue critic applies no section scoring to
skill-polish results (D-008); quality is enforced by ``verify_draft`` at
the write (D-005): a completed task's result replaces the SKILL.md bytes
only after the frontmatter name is preserved and the exact bytes pass the
gate — a bogus reference or a drifted name keeps the deterministic body.

C-04: no eager ``cairn.cli`` imports — command modules are imported
inside each test function. The queue worker side is faked with
``claim_task``/``complete_task`` or the task CLI.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from cairn.graph.builder import build_graph
from cairn.graph.schema import get_db
from cairn.llm.tasks import (
    TASK_DIR,
    claim_task,
    complete_task,
    get_task,
    list_tasks,
)
from cairn.memory.promotion import capture_memory
from cairn.okf.bundle import OKFBundle
from cairn.okf.concept import OKFConcept

CORE_SRC = (
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

POLISHED_NOTE = "\n\nPolished note: `mid_one` calls `zeta_hub` directly.\n"
BOGUS_NOTE = "\n\nPolished note: `no_such_symbol_anywhere` never resolves.\n"


def _indexed_workspace(ws: Path) -> None:
    """pkg_a fixture: a hub symbol with callers, a compass concept, and two
    memories feed the skill sections (same shape as the determinism tests)."""
    (ws / ".git").mkdir(parents=True)
    (ws / "pkg_a").mkdir()
    (ws / "pkg_a" / "core.py").write_text(CORE_SRC)
    build_graph(workspace=str(ws), db_path=str(ws / "graph.db"), verbose=False)

    conn = get_db(str(ws / "graph.db"))
    from cairn.graph.dataflow import build_transitive_closure

    build_transitive_closure(conn)
    bundle = OKFBundle(str(ws / "knowledge"))
    bundle.write_concept(
        OKFConcept(
            type="Compass",
            title="pkg_a",
            resource="pkg_a",
            concept_id="compass/pkg_a",
            tags=["pkg_a"],
            body="Hub: `zeta_hub` in `pkg_a/core.py`.",
        )
    )
    capture_memory(
        conn,
        bundle,
        type_="pattern",
        title="pkg_a zeta_hub hub contract",
        body="`zeta_hub` is the hub of pkg_a; check its callers before editing it.",
        resource="pkg_a/core.py",
    )
    capture_memory(
        conn,
        bundle,
        type_="decision",
        title="pkg_a alpha_leaf stability",
        body="`alpha_leaf` has no callers; treat it as a stable leaf.",
        resource="pkg_a/core.py",
    )
    conn.close()


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    """cwd in tmp; CAIRN_DB/CAIRN_KNOWLEDGE under tmp_path."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAIRN_DB", str(tmp_path / "graph.db"))
    monkeypatch.setenv("CAIRN_KNOWLEDGE", str(tmp_path / "knowledge"))
    return tmp_path


def _generate(*args):
    from cairn.cli.skill import skill

    return CliRunner().invoke(skill, ["generate", *args])


def _task_cli(*args, knowledge: Path):
    from cairn.cli.task import task as task_group

    return CliRunner().invoke(
        task_group, [*args, "--knowledge", str(knowledge)]
    )


def _streams(result) -> str:
    """stdout + stderr (click 8.2+ captures the streams separately)."""
    return result.output + (result.stderr or "")


def _polish_tasks(knowledge: Path):
    return list_tasks(OKFBundle(str(knowledge)), kind_prefix="skill-polish")


def _polish_once(tmp: Path, slug: str = "pkg_a"):
    """One --polish run against the fixture workspace; returns the result."""
    return _generate(
        slug,
        "--polish",
        "--output",
        str(tmp / "out"),
        "--knowledge",
        str(tmp / "knowledge"),
    )


def test_polish_enqueues_one_pending_task_and_writes_deterministic_skill(
    cli_env, monkeypatch
):
    """TC-010: the flag queues a visible pending task; no LLM runs in-process."""
    tmp = cli_env
    _indexed_workspace(tmp)
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    def _boom(*args, **kwargs):
        raise AssertionError("polish enqueue constructed an LLM client")

    import cairn.llm as llm_pkg
    from cairn.llm import client as llm_client

    monkeypatch.setattr(llm_client, "get_client", _boom)
    monkeypatch.setattr(llm_pkg, "get_client", _boom)
    monkeypatch.setattr(llm_client.FileQueueBackend, "__init__", _boom)
    monkeypatch.setattr(llm_client.SubprocessBackend, "__init__", _boom)

    knowledge = tmp / "knowledge"
    result = _polish_once(tmp)
    assert result.exit_code == 0, _streams(result)
    deterministic = (tmp / "out" / "SKILL.md").read_text(encoding="utf-8")
    assert "name: cairn-pkg-a" in deterministic

    listed = _task_cli("list", "--status", "pending", knowledge=knowledge)
    assert listed.exit_code == 0, _streams(listed)
    assert "skill-polish" in listed.output

    tasks = _polish_tasks(knowledge)
    assert len(tasks) == 1
    assert tasks[0].status == "pending"
    assert tasks[0].resource == "pkg-a"
    assert tasks[0].facts["skill_path"] == str(tmp / "out" / "SKILL.md")


def test_polish_rerun_with_live_task_does_not_duplicate(cli_env):
    tmp = cli_env
    _indexed_workspace(tmp)
    assert _polish_once(tmp).exit_code == 0
    assert _polish_once(tmp).exit_code == 0
    assert len(_polish_tasks(tmp / "knowledge")) == 1


def test_default_generation_queues_nothing(cli_env):
    tmp = cli_env
    _indexed_workspace(tmp)
    result = _generate(
        "pkg_a",
        "--output",
        str(tmp / "out"),
        "--knowledge",
        str(tmp / "knowledge"),
    )
    assert result.exit_code == 0, _streams(result)
    assert list_tasks(OKFBundle(str(tmp / "knowledge"))) == []


def test_completed_polish_replaces_body_after_gate(cli_env):
    tmp = cli_env
    _indexed_workspace(tmp)
    knowledge = tmp / "knowledge"
    assert _polish_once(tmp).exit_code == 0
    deterministic = (tmp / "out" / "SKILL.md").read_text(encoding="utf-8")
    (task,) = _polish_tasks(knowledge)

    # Fake the worker side: claim, then complete with a polished document
    # whose backtick references all resolve in the fixture graph.
    bundle = OKFBundle(str(knowledge))
    assert claim_task(bundle, task.id) is not None
    polished = deterministic.rstrip("\n") + POLISHED_NOTE
    outcome = complete_task(bundle, task.id, polished)
    assert outcome["errors"] == []

    rerun = _polish_once(tmp)
    assert rerun.exit_code == 0, _streams(rerun)
    applied = (tmp / "out" / "SKILL.md").read_text(encoding="utf-8")
    assert applied != deterministic
    assert "Polished note:" in applied
    assert "name: cairn-pkg-a" in applied
    assert "Applied polished" in rerun.output


def test_cli_complete_with_valid_result_reaches_done(cli_env):
    """The documented worker flow — claim, then `task complete --result-file`
    with the critic connection attached — lands a valid skill-polish result
    in done with a passing critic verdict and no revise spawn (D-008): the
    queue critic applies no compass section scoring to skill-polish results;
    quality is enforced by verify_draft at the skill write. The completed
    result then replaces the deterministic body on the next --polish run."""
    tmp = cli_env
    _indexed_workspace(tmp)
    knowledge = tmp / "knowledge"
    assert _polish_once(tmp).exit_code == 0
    deterministic = (tmp / "out" / "SKILL.md").read_text(encoding="utf-8")
    (task,) = _polish_tasks(knowledge)

    claimed = _task_cli("claim", task.id, knowledge=knowledge)
    assert claimed.exit_code == 0, _streams(claimed)

    result_file = tmp / "result.md"
    result_file.write_text(
        deterministic.rstrip("\n") + POLISHED_NOTE, encoding="utf-8"
    )
    completed = _task_cli(
        "complete",
        task.id,
        "--result-file",
        str(result_file),
        "--db",
        str(tmp / "graph.db"),
        knowledge=knowledge,
    )
    assert completed.exit_code == 0, _streams(completed)
    assert "completed" in completed.output

    bundle = OKFBundle(str(knowledge))
    assert [t.status for t in _polish_tasks(knowledge)] == ["done"]
    assert get_task(bundle, task.id).status == "done"
    result = bundle.read_concept(f"{TASK_DIR}/{task.id}.result")
    assert result.extensions.get("critic_status") == "passed"

    rerun = _polish_once(tmp)
    assert rerun.exit_code == 0, _streams(rerun)
    assert "Polished note:" in (tmp / "out" / "SKILL.md").read_text(encoding="utf-8")


def test_polish_with_bogus_ref_keeps_deterministic_body(cli_env):
    tmp = cli_env
    _indexed_workspace(tmp)
    knowledge = tmp / "knowledge"
    assert _polish_once(tmp).exit_code == 0
    deterministic = (tmp / "out" / "SKILL.md").read_text(encoding="utf-8")
    (task,) = _polish_tasks(knowledge)

    # The queue stored the result without a critic run: the apply-time gate
    # must still reject it.
    bundle = OKFBundle(str(knowledge))
    assert claim_task(bundle, task.id) is not None
    outcome = complete_task(bundle, task.id, deterministic.rstrip("\n") + BOGUS_NOTE)
    assert outcome["errors"] == []

    rerun = _polish_once(tmp)
    assert rerun.exit_code == 0, _streams(rerun)
    assert (tmp / "out" / "SKILL.md").read_text(encoding="utf-8") == deterministic
    assert "no_such_symbol_anywhere" in _streams(rerun)


def test_polish_with_drifted_name_keeps_deterministic_body(cli_env):
    tmp = cli_env
    _indexed_workspace(tmp)
    knowledge = tmp / "knowledge"
    assert _polish_once(tmp).exit_code == 0
    deterministic = (tmp / "out" / "SKILL.md").read_text(encoding="utf-8")
    (task,) = _polish_tasks(knowledge)

    bundle = OKFBundle(str(knowledge))
    assert claim_task(bundle, task.id) is not None
    drifted = deterministic.replace("name: cairn-pkg-a", "name: cairn-other", 1)
    assert drifted != deterministic
    outcome = complete_task(bundle, task.id, drifted)
    assert outcome["errors"] == []

    rerun = _polish_once(tmp)
    assert rerun.exit_code == 0, _streams(rerun)
    assert (tmp / "out" / "SKILL.md").read_text(encoding="utf-8") == deterministic
    assert "cairn-pkg-a" in _streams(rerun)


def test_queue_accepted_bogus_result_still_needs_gate(cli_env):
    """D-008 exempts skill-polish from the queue critic's section scoring, so
    a conn-carrying completion lands even a bogus-ref result in done with a
    passing verdict — and the apply side must still refuse it: verify_draft
    stays unconditional (D-005)."""
    tmp = cli_env
    _indexed_workspace(tmp)
    knowledge = tmp / "knowledge"
    assert _polish_once(tmp).exit_code == 0
    deterministic = (tmp / "out" / "SKILL.md").read_text(encoding="utf-8")
    (task,) = _polish_tasks(knowledge)

    bundle = OKFBundle(str(knowledge))
    assert claim_task(bundle, task.id) is not None
    conn = get_db(str(tmp / "graph.db"))
    try:
        outcome = complete_task(
            bundle, task.id, deterministic.rstrip("\n") + BOGUS_NOTE, conn=conn
        )
    finally:
        conn.close()
    assert outcome["errors"] == []
    assert [t.status for t in _polish_tasks(knowledge)] == ["done"]
    result = bundle.read_concept(f"{TASK_DIR}/{task.id}.result")
    assert result.extensions.get("critic_status") == "passed"

    rerun = _polish_once(tmp)
    assert rerun.exit_code == 0, _streams(rerun)
    assert (tmp / "out" / "SKILL.md").read_text(encoding="utf-8") == deterministic
    assert "no_such_symbol_anywhere" in _streams(rerun)


def test_polish_help_names_queue_and_critic():
    """TC-010 control: where the polish option is offered, its wording names
    the task queue or the critic — never an inline model call."""
    from cairn.cli.skill import skill

    result = CliRunner().invoke(skill, ["generate", "--help"])
    assert result.exit_code == 0
    assert "--polish" in result.output
    lowered = result.output.lower()
    assert "task queue" in lowered or "critic" in lowered
