"""Pins for `cairn task drop` and `cairn task list --kind-prefix` (FR-004).

Queue contract pinned here (D-017, TC-009..TC-013):

``drop_task(bundle, task_id)`` (planned interface beside ``claim_task`` /
``complete_task`` in ``cairn.llm.tasks``; the bundle is positional, like its
siblings) returns an outcome dict:

* success (a pending or in-progress task is dropped):
  ``{"task_id": <id>, "dropped": True, "errors": []}`` — the task's status
  reads ``"dropped"`` through ``get_task``/``list_tasks``, stays visible in
  listings, and is refused by ``claim_task`` (the existing non-pending guard
  suffices — no claim/complete guard edits). Dropping an in-progress task
  removes its ``_tasks/<id>.claim`` marker.
* refusal (done / not-found / already-dropped):
  ``{"task_id": <id>, "dropped": False, "errors": [<reason>, ...]}`` — the
  task's status is unchanged. Extra keys mirroring ``complete_task``'s
  outcome idiom are permitted; these three are the contract.

``list_tasks(bundle, kind_prefix=...)`` takes an OPTIONAL kwarg (default
``None`` = unfiltered, so zero call sites change): ``"wiki-page"`` lists
every chain hop and never matches ``wiki-catalog``.

CLI pins follow the hermetic ``cli_env`` + ``CliRunner`` pattern of
tests/test_wiki_cli.py; only the specific ``cairn.cli.task`` module is
imported, never the ``cairn.cli`` package root (C-04). Queue-level pins call
``cairn.llm.tasks`` functions directly.
"""
from __future__ import annotations

import pytest
from click.testing import CliRunner

from cairn.cli.task import task
from cairn.llm.tasks import (
    claim_task,
    complete_task,
    create_task,
    get_task,
    list_tasks,
)
from cairn.okf.bundle import OKFBundle


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    """Hermetic store: cwd in tmp, CAIRN_DB/CAIRN_KNOWLEDGE under tmp_path."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAIRN_DB", str(tmp_path / "graph.db"))
    monkeypatch.setenv("CAIRN_KNOWLEDGE", str(tmp_path / "knowledge"))
    knowledge = tmp_path / "knowledge"
    (knowledge / "_tasks").mkdir(parents=True)
    return knowledge


def _bundle(knowledge):
    return OKFBundle(str(knowledge))


# --- drop_task (queue level, TC-009..TC-012) ---------------------------------


def test_drop_pending_task_is_dropped_visible_and_unclaimable(cli_env):
    """TC-009: a pending task reads "dropped" after the drop, appears under
    the dropped listing (and no longer under pending), and claim_task's
    existing non-pending guard refuses it — permanently."""
    bundle = _bundle(cli_env)
    queued = create_task(bundle, "wiki-page", "mod-a")

    from cairn.llm.tasks import drop_task

    out = drop_task(bundle, queued.id)

    assert out["task_id"] == queued.id
    assert out["dropped"] is True
    assert out["errors"] == []
    assert get_task(bundle, queued.id).status == "dropped"
    assert [t.id for t in list_tasks(bundle, status="dropped")] == [queued.id]
    assert [t.id for t in list_tasks(bundle, status="pending")] == []
    assert claim_task(bundle, queued.id, "agent") is None
    assert get_task(bundle, queued.id).status == "dropped"


def test_drop_in_progress_task_releases_the_claim_marker(cli_env):
    """TC-010: dropping an in-progress task removes its _tasks/<id>.claim
    marker, so a fresh chain for the same page claims without conflict."""
    bundle = _bundle(cli_env)
    task_in_flight = create_task(bundle, "wiki-page", "mod-a")
    assert claim_task(bundle, task_in_flight.id, "agent") is not None
    marker = bundle.root / "_tasks" / f"{task_in_flight.id}.claim"
    assert marker.exists()

    from cairn.llm.tasks import drop_task

    out = drop_task(bundle, task_in_flight.id)

    assert out["task_id"] == task_in_flight.id
    assert out["dropped"] is True
    assert out["errors"] == []
    assert get_task(bundle, task_in_flight.id).status == "dropped"
    assert not marker.exists()
    fresh = create_task(bundle, "wiki-page", "mod-a")
    assert claim_task(bundle, fresh.id, "agent-2") is not None


def test_drop_done_task_is_refused(cli_env):
    """TC-011: a done task is refused by drop with a reason; its status
    stays done and nothing appears under the dropped listing."""
    bundle = _bundle(cli_env)
    finished = create_task(bundle, "compass-synthesize", "test")
    assert claim_task(bundle, finished.id, "agent") is not None
    complete_task(bundle, finished.id, "A plain result body.", conn=None)
    assert get_task(bundle, finished.id).status == "done"

    from cairn.llm.tasks import drop_task

    out = drop_task(bundle, finished.id)

    assert out["task_id"] == finished.id
    assert out["dropped"] is False
    assert isinstance(out["errors"], list) and out["errors"]
    assert get_task(bundle, finished.id).status == "done"
    assert list_tasks(bundle, status="dropped") == []


def test_drop_already_dropped_task_is_refused(cli_env):
    """TC-012: a second drop of the same task refuses with a reason and the
    dropped listing is unchanged (the id appears exactly once)."""
    bundle = _bundle(cli_env)
    queued = create_task(bundle, "wiki-page", "mod-a")

    from cairn.llm.tasks import drop_task

    assert drop_task(bundle, queued.id)["dropped"] is True

    out = drop_task(bundle, queued.id)

    assert out["task_id"] == queued.id
    assert out["dropped"] is False
    assert out["errors"]
    assert get_task(bundle, queued.id).status == "dropped"
    assert [t.id for t in list_tasks(bundle, status="dropped")] == [queued.id]


def test_drop_unknown_task_is_refused(cli_env):
    """A task id that does not exist is refused with the refusal shape."""
    bundle = _bundle(cli_env)

    from cairn.llm.tasks import drop_task

    out = drop_task(bundle, "no-such-task")

    assert out["task_id"] == "no-such-task"
    assert out["dropped"] is False
    assert out["errors"]


# --- list_tasks(kind_prefix=...) (TC-013) ------------------------------------


def test_kind_prefix_lists_every_chain_hop_disjoint_from_catalog(cli_env):
    """TC-013: kind_prefix="wiki-page" lists every hop of the page chain
    (initial + revise kinds) and never matches wiki-catalog; the catalog
    prefix lists only catalog tasks — the two listings are disjoint."""
    bundle = _bundle(cli_env)
    hop1 = create_task(bundle, "wiki-page", "mod-a")
    hop2 = create_task(bundle, "wiki-page-revise", "mod-a")
    catalog = create_task(bundle, "wiki-catalog", "outline")
    other = create_task(bundle, "compass-synthesize", "test")

    pages = list_tasks(bundle, kind_prefix="wiki-page")
    cats = list_tasks(bundle, kind_prefix="wiki-catalog")

    assert {t.id for t in pages} == {hop1.id, hop2.id}
    assert {t.id for t in cats} == {catalog.id}
    assert {t.id for t in pages}.isdisjoint(t.id for t in cats)
    assert other.id not in {t.id for t in pages} | {t.id for t in cats}


def test_kind_prefix_defaults_to_the_unfiltered_listing(cli_env):
    """kind_prefix is optional with default None: omitting it (or passing
    None explicitly) lists every task, so zero call sites change."""
    bundle = _bundle(cli_env)
    ids = {
        create_task(bundle, "wiki-page", "mod-a").id,
        create_task(bundle, "wiki-catalog", "outline").id,
    }

    assert {t.id for t in list_tasks(bundle)} == ids
    assert {t.id for t in list_tasks(bundle, kind_prefix=None)} == ids


# --- CLI surface: task drop / list --kind-prefix / --status help --------------


def test_task_drop_cli_marks_the_task_dropped(cli_env):
    """TC-009 (CLI pass condition): `task drop <id>` exits 0 and the id
    shows under `task list --status dropped`."""
    bundle = _bundle(cli_env)
    queued = create_task(bundle, "wiki-page", "mod-a")

    result = CliRunner().invoke(
        task, ["drop", queued.id, "--knowledge", str(cli_env)]
    )

    assert result.exit_code == 0, result.output
    assert get_task(bundle, queued.id).status == "dropped"

    listing = CliRunner().invoke(
        task, ["list", "--status", "dropped", "--knowledge", str(cli_env)]
    )
    assert listing.exit_code == 0, listing.output
    assert queued.id in listing.stdout


def test_task_list_kind_prefix_flag_splits_wiki_chains_from_catalog(cli_env):
    """TC-013 (CLI pass condition): `--kind-prefix wiki-page` lists the page
    chain hops and not the catalog task; `--kind-prefix wiki-catalog` the
    reverse."""
    bundle = _bundle(cli_env)
    hop1 = create_task(bundle, "wiki-page", "mod-a")
    hop2 = create_task(bundle, "wiki-page-revise", "mod-a")
    catalog = create_task(bundle, "wiki-catalog", "outline")

    pages = CliRunner().invoke(
        task, ["list", "--kind-prefix", "wiki-page", "--knowledge", str(cli_env)]
    )
    cats = CliRunner().invoke(
        task,
        ["list", "--kind-prefix", "wiki-catalog", "--knowledge", str(cli_env)],
    )

    assert pages.exit_code == 0, pages.output
    assert cats.exit_code == 0, cats.output
    assert hop1.id in pages.stdout
    assert hop2.id in pages.stdout
    assert catalog.id not in pages.stdout
    assert catalog.id in cats.stdout
    assert hop1.id not in cats.stdout
    assert hop2.id not in cats.stdout


def test_task_list_status_help_enumerates_dropped(cli_env):
    """The --status help on `task list` enumerates `dropped`."""
    result = CliRunner().invoke(task, ["list", "--help"])

    assert result.exit_code == 0, result.output
    assert "dropped" in result.stdout
