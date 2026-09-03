"""Unit tests for cairn.wiki.lifecycle — the single owner of page identity
and derived truth (the two-kind contract: PLAN describes intent, CONTENT is
the only existence record, lifecycle is derived at read time and never
stored).

Pins: the composite task-key algebra (two repos sharing a page id never
collide), the write-kind-only liveness rule (enrich never blocks the plan),
the full derived-state matrix including the zombie rescue (done task with
no critic verdict derives failed), concept-only provenance sha (no plan
fallback), and the staleness vocabulary.
"""
from __future__ import annotations

import pytest

from cairn.llm.tasks import claim_task, create_task, drop_task
from cairn.okf.bundle import OKFBundle
from cairn.okf.concept import OKFConcept
from cairn.wiki.lifecycle import (
    DERIVED_STATES,
    derived_state,
    is_promoted,
    live_generation_tasks,
    page_chains,
    page_concept_id,
    plan_facts,
    read_page_concept,
    recorded_sha,
    staleness,
)

REPO = "r"


@pytest.fixture
def bundle(tmp_path):
    return OKFBundle(str(tmp_path / "knowledge"))


def _promote(bundle, repo, page_id, *, sha=None, type_="Wiki-Article"):
    """Write promoted content exactly as the promotion path would."""
    extensions = {"page_id": page_id, "input_hash": "h", "task_id": "t0"}
    if sha:
        extensions["commit_sha"] = sha
    bundle.write_concept(
        OKFConcept(
            type=type_,
            title=f"Wiki: {page_id}",
            body=f"# {page_id}\n\n## Sources\n",
            concept_id=page_concept_id(repo, page_id),
            tags=[repo, "wiki"],
            extensions=extensions,
        )
    )


def _queue(bundle, repo, page_id, *, kind="wiki-page", claim=False, drop=False):
    task = create_task(
        bundle, kind, page_id, facts={"repo": repo, "input_hash": "h"}
    )
    if claim:
        assert claim_task(bundle, task.id, "agent") is not None
    if drop:
        drop_task(bundle, task.id)
    return task


def test_derived_states_vocabulary_is_the_read_model_contract():
    assert DERIVED_STATES == (
        "planned",
        "queued",
        "in-progress",
        "promoted",
        "failed",
        "dropped",
    )


def test_page_concept_id_algebra(bundle):
    assert page_concept_id("demo", "overview") == "wiki/pages/demo/overview"


def test_composite_task_keys_keep_two_repos_from_colliding(bundle):
    """Two repos queueing the same page id get separate chains — the old
    bare-resource grouping made repo B's pending task cover repo A's page."""
    _queue(bundle, "alpha", "overview")
    _queue(bundle, "beta", "overview", kind="wiki-page-revise")

    chains = page_chains(bundle)
    assert set(chains) == {"alpha/overview", "beta/overview"}
    live = live_generation_tasks(bundle)
    assert set(live) == {"alpha/overview", "beta/overview"}


def test_enrich_tasks_never_count_as_live_generation(bundle):
    """Enrichment is content maintenance: it must not block the plan's skip
    decision, so enrich tasks never appear in live generation."""
    _queue(bundle, REPO, "overview", kind="wiki-page-enrich", claim=True)
    assert live_generation_tasks(bundle) == {}
    assert "overview" not in page_chains(bundle)


def test_live_generation_excludes_terminal_tasks(bundle):
    task = _queue(bundle, REPO, "done-page", claim=True)
    from cairn.llm.tasks import complete_task

    complete_task(bundle, task.id, "body\n\n## Sources\n")
    assert live_generation_tasks(bundle) == {}


def test_derived_state_promoted_beats_everything(bundle):
    _promote(bundle, REPO, "overview")
    _queue(bundle, REPO, "overview", claim=True)  # also in-progress
    assert derived_state(bundle, REPO, "overview", page_chains(bundle).get(f"{REPO}/overview", [])) == "promoted"
    assert is_promoted(bundle, REPO, "overview")


def test_derived_state_in_progress_then_queued(bundle):
    _queue(bundle, REPO, "wip", claim=True)
    _queue(bundle, REPO, "waiting")
    chains = page_chains(bundle)
    assert derived_state(bundle, REPO, "wip", chains[f"{REPO}/wip"]) == "in-progress"
    assert derived_state(bundle, REPO, "waiting", chains[f"{REPO}/waiting"]) == "queued"


def test_derived_state_dropped_beats_failed(bundle):
    _queue(bundle, REPO, "gone", drop=True)
    chains = page_chains(bundle)
    assert derived_state(bundle, REPO, "gone", chains[f"{REPO}/gone"]) == "dropped"


def test_derived_state_rescues_zombie_done_without_critic_verdict(bundle):
    """A completion that died after marking done but before the critic
    verdict landed used to display queued forever; it now derives failed
    so `wiki retry` reaches it. A conn-less completion writes the result
    with no critic verdict — exactly the zombie shape."""
    task = _queue(bundle, REPO, "zombie", claim=True)
    from cairn.llm.tasks import complete_task

    complete_task(bundle, task.id, "half a page\n\n## Sources\n")
    state = derived_state(
        bundle, REPO, "zombie", page_chains(bundle)[f"{REPO}/zombie"]
    )
    assert state == "failed"


def test_derived_state_planned_when_no_task_or_content(bundle):
    assert derived_state(bundle, REPO, "fresh-page", []) == "planned"


def test_promotion_requires_the_gated_article_type(bundle):
    """A non-article concept at the page path (e.g. the legacy ungated
    generator output) is not promoted content."""
    _promote(bundle, REPO, "overview", type_="Wiki-Architecture")
    assert read_page_concept(bundle, REPO, "overview") is None
    assert is_promoted(bundle, REPO, "overview") is False


def test_recorded_sha_is_content_only_and_staleness_vocabulary(bundle):
    assert recorded_sha(bundle, REPO, "no-such-page") is None
    assert staleness(None, "head") == "unknown"
    assert staleness("sha", None) == "unknown"

    _promote(bundle, REPO, "overview", sha="abc")
    assert recorded_sha(bundle, REPO, "overview") == "abc"
    assert staleness("abc", "abc") == "fresh"
    assert staleness("abc", "def") == "stale"


def test_plan_facts_shape_is_the_single_work_order(bundle):
    entry = {
        "page_id": "overview",
        "title": "demo architecture overview",
        "description": "desc",
        "module": "",
        "seeds": {"files": ["a.py"], "symbols": ["A"]},
        "input_hash": "h",
    }
    facts = plan_facts(entry, "demo", diagrams=True)
    assert facts == {
        "title": "demo architecture overview",
        "description": "desc",
        "module": "",
        "seeds": {"files": ["a.py"], "symbols": ["A"]},
        "input_hash": "h",
        "repo": "demo",
        "diagrams": True,
    }
