"""Click-level tests for `cairn wiki generate --llm --force`, `wiki status`,
and `wiki retry`.

Business pins (TC-007/TC-016/TC-017/TC-018): `generate --llm --force`
re-queues an unchanged, promoted page that a plain re-run skips (FR-005);
`status` lists each planned page
exactly once with a state from queued/in-progress/promoted/failed plus
per-state aggregate counts; `retry` re-queues exactly the failed/dropped
pages as fresh task chains (parent_attempt=0, D-008) while bumping the
manifest's cumulative attempt counter and never touching promoted pages;
retry with nothing to retry is a friendly no-op with exit 0.

Manifest fixtures are written directly as JSON at
`<knowledge>/_wiki/manifest.json` (schema "cairn-wiki-manifest-2", rows
keyed "{repo}/{page_id}"), so the tests do not depend on
`cairn.wiki.manifest`. Only the specific CLI module
is imported, never the `cairn.cli` package root (C-04); the hermetic
``cli_env`` pattern is tests/test_knowledge_cli.py's.
"""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from cairn.cli.wiki import wiki
from cairn.graph.schema import get_db
from cairn.llm.tasks import (
    MAX_REVISE_CYCLES,
    claim_task,
    complete_task,
    create_task,
    list_tasks,
)
from cairn.okf.bundle import OKFBundle
from cairn.okf.concept import OKFConcept

MANIFEST_SCHEMA = "cairn-wiki-manifest-2"
REPO = "r"


def _key(page_id):
    return f"{REPO}/{page_id}"

QUEUED_A = "overview"
QUEUED_B = "mod-catalog"
IN_FLIGHT = "mod-graph"
PROMOTED = "mod-okf"
FAILED = "mod-dashboard"

# A result the critic must fail: the cited file does not resolve in the graph.
FAILING_BODY = "See `src/does_not_exist.py` for the page contents."


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


def _row(page_id, *, state, task_id="", attempts=0):
    """One manifest row: the plan entry plus the D-006 tracking fields."""
    return {
        "page_id": page_id,
        "title": "Wiki page",
        "description": "Describes the module.",
        "module": "some-module",
        "seeds": ["src/some-module/core.py"],
        "input_hash": "input-hash-value",
        "task_id": task_id,
        "state": state,
        "attempts": attempts,
    }


def _write_manifest(knowledge, pages):
    manifest_dir = knowledge / "_wiki"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    doc = {"schema": MANIFEST_SCHEMA, "pages": pages}
    (manifest_dir / "manifest.json").write_text(
        json.dumps(doc, indent=2) + "\n", encoding="utf-8"
    )


def _read_manifest(knowledge):
    return json.loads(
        (knowledge / "_wiki" / "manifest.json").read_text(encoding="utf-8")
    )


def _queue_page(bundle, page_id, *, claim=False):
    task = create_task(bundle, "wiki-page", page_id)
    if claim:
        assert claim_task(bundle, task.id, "agent") is not None
    return task


def _promote(bundle, page_id):
    """Write the D-007 promoted article so the concept resolves."""
    bundle.write_concept(
        OKFConcept(
            type="Wiki-Article",
            title="Wiki page",
            body=f"# {page_id}\n\n## Sources\n",
            concept_id=f"wiki/pages/{REPO}/{page_id}",
            tags=[REPO, "wiki"],
        )
    )


def _page_lines(out, page_id):
    return [line for line in out.splitlines() if page_id in line]


# --- wiki status (TC-016) ----------------------------------------------------


def test_status_lists_each_page_once_with_a_state_and_aggregates(cli_env):
    """Every planned page is listed exactly once with one of the four states
    plus per-state aggregate counts; a failed row renders failed even though
    its task chain is gone from the queue (the manifest is the authority)."""
    bundle = _bundle(cli_env)
    q1 = _queue_page(bundle, QUEUED_A)
    q2 = _queue_page(bundle, QUEUED_B)
    live = _queue_page(bundle, IN_FLIGHT, claim=True)
    _promote(bundle, PROMOTED)
    _write_manifest(cli_env, {
        _key(QUEUED_A): _row(QUEUED_A, state="queued", task_id=q1.id, attempts=1),
        _key(QUEUED_B): _row(QUEUED_B, state="queued", task_id=q2.id, attempts=1),
        _key(IN_FLIGHT): _row(IN_FLIGHT, state="in_progress", task_id=live.id, attempts=1),
        _key(PROMOTED): _row(PROMOTED, state="promoted", task_id="spent-chain", attempts=1),
        _key(FAILED): _row(FAILED, state="failed", task_id="dropped-chain", attempts=2),
    })

    result = CliRunner().invoke(wiki, ["status", "--knowledge", str(cli_env)])

    assert result.exit_code == 0, result.output
    out = result.stdout.replace("_", "-").lower()
    expected_states = {
        QUEUED_A: "queued",
        QUEUED_B: "queued",
        IN_FLIGHT: "in-progress",
        PROMOTED: "promoted",
        FAILED: "failed",
    }
    for page_id, state in expected_states.items():
        lines = _page_lines(out, page_id)
        assert len(lines) == 1, (page_id, lines)
        assert state in lines[0], lines[0]
    # Aggregate counts per state: each state word recurs beyond its row(s).
    assert out.count("queued") >= 3
    for state in ("in-progress", "promoted", "failed"):
        assert out.count(state) >= 2, state


# --- wiki retry (TC-017 / TC-018) ---------------------------------------------


def test_retry_requeues_only_failed_pages_as_fresh_chains(cli_env):
    """Exactly the failed/dropped pages return to the queue on a fresh chain
    (attempt restarts at 1) while the manifest's cumulative counter grows and
    promoted and still-queued pages are untouched."""
    bundle = _bundle(cli_env)
    waiting = _queue_page(bundle, QUEUED_B)
    _promote(bundle, PROMOTED)
    promoted_row = _row(PROMOTED, state="promoted", task_id="spent-chain", attempts=1)
    failed_row = _row(FAILED, state="failed", task_id="dropped-chain", attempts=2)
    _write_manifest(cli_env, {
        _key(QUEUED_B): _row(QUEUED_B, state="queued", task_id=waiting.id, attempts=1),
        _key(PROMOTED): promoted_row,
        _key(FAILED): failed_row,
    })

    result = CliRunner().invoke(wiki, ["retry", "--knowledge", str(cli_env)])

    assert result.exit_code == 0, result.output
    pending = list_tasks(bundle, kind="wiki-page", status="pending")
    assert len(pending) == 2
    retried = [t for t in pending if t.resource == FAILED]
    assert len(retried) == 1
    assert retried[0].attempt == 1
    assert [t.id for t in pending if t.resource == QUEUED_B] == [waiting.id]
    assert [t for t in pending if t.resource == PROMOTED] == []

    on_disk = _read_manifest(cli_env)["pages"]
    assert on_disk[_key(FAILED)]["attempts"] == failed_row["attempts"] + 1
    assert on_disk[_key(FAILED)]["state"] == "queued"
    assert on_disk[_key(PROMOTED)] == promoted_row
    assert on_disk[_key(QUEUED_B)]["attempts"] == 1
    assert f"wiki/pages/{REPO}/{PROMOTED}" in bundle.list_concepts()


def test_retry_with_nothing_to_retry_queues_nothing_and_exits_zero(cli_env):
    """No failed pages: a friendly message, exit 0, and no new tasks."""
    bundle = _bundle(cli_env)
    waiting = _queue_page(bundle, QUEUED_B)
    _promote(bundle, PROMOTED)
    _write_manifest(cli_env, {
        _key(QUEUED_B): _row(QUEUED_B, state="queued", task_id=waiting.id, attempts=1),
        _key(PROMOTED): _row(PROMOTED, state="promoted", task_id="spent-chain", attempts=1),
    })

    result = CliRunner().invoke(wiki, ["retry", "--knowledge", str(cli_env)])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip()
    pending = list_tasks(bundle, kind="wiki-page", status="pending")
    assert [t.id for t in pending] == [waiting.id]
    assert _read_manifest(cli_env)["pages"][_key(QUEUED_B)]["attempts"] == 1


def _drive_dropped_chain(bundle, page_id, conn):
    """Run a wiki-page chain to its drop: every attempt fails the critic,
    so the chain ends done at MAX_REVISE_CYCLES with no successor."""
    current = _queue_page(bundle, page_id)
    while current.attempt < MAX_REVISE_CYCLES:
        claim_task(bundle, current.id, "agent")
        complete_task(bundle, current.id, FAILING_BODY, conn=conn)
        current = [t for t in list_tasks(bundle, status="pending")
                   if t.task_kind.startswith("wiki-page")][0]
    claim_task(bundle, current.id, "agent")
    outcome = complete_task(bundle, current.id, FAILING_BODY, conn=conn)
    assert outcome["dropped"] is True
    return current


def test_dropped_chain_derives_failed_for_status_and_retry(cli_env, fresh_db):
    """A chain exhausted at the revise cap (terminal done task whose result
    failed the critic, no successor, concept absent) is failed for status
    and retry even though the manifest row still says queued (TC-017)."""
    bundle = _bundle(cli_env)
    original = _drive_dropped_chain(bundle, FAILED, fresh_db)
    _promote(bundle, PROMOTED)
    _write_manifest(cli_env, {
        _key(PROMOTED): _row(PROMOTED, state="promoted", task_id="spent-chain", attempts=1),
        _key(FAILED): _row(FAILED, state="queued", task_id=original.id, attempts=1),
    })

    status = CliRunner().invoke(wiki, ["status", "--knowledge", str(cli_env)])
    assert status.exit_code == 0, status.output
    lines = _page_lines(status.stdout.replace("_", "-").lower(), FAILED)
    assert len(lines) == 1
    assert "failed" in lines[0]

    result = CliRunner().invoke(wiki, ["retry", "--knowledge", str(cli_env)])
    assert result.exit_code == 0, result.output
    pending = list_tasks(bundle, kind="wiki-page", status="pending")
    assert [t.resource for t in pending] == [FAILED]
    assert pending[0].attempt == 1
    on_disk = _read_manifest(cli_env)["pages"]
    assert on_disk[_key(FAILED)]["state"] == "queued"
    assert on_disk[_key(FAILED)]["attempts"] == 2
    assert on_disk[_key(FAILED)]["task_id"] == pending[0].id


def test_pending_revise_keeps_the_chain_alive_and_untouched_by_retry(
    cli_env, fresh_db
):
    """One failing completion leaves a pending revise successor: the chain
    is alive, so status shows it in flight and retry does not re-queue it."""
    bundle = _bundle(cli_env)
    original = _queue_page(bundle, FAILED)
    claim_task(bundle, original.id, "agent")
    complete_task(bundle, original.id, FAILING_BODY, conn=fresh_db)
    revise = [t for t in list_tasks(bundle, status="pending")
              if t.task_kind.startswith("wiki-page")][0]
    _promote(bundle, PROMOTED)
    _write_manifest(cli_env, {
        _key(PROMOTED): _row(PROMOTED, state="promoted", task_id="spent-chain", attempts=1),
        _key(FAILED): _row(FAILED, state="queued", task_id=original.id, attempts=1),
    })

    status = CliRunner().invoke(wiki, ["status", "--knowledge", str(cli_env)])
    assert status.exit_code == 0, status.output
    line = _page_lines(status.stdout.replace("_", "-").lower(), FAILED)[0]
    assert "queued" in line
    assert "failed" not in line

    result = CliRunner().invoke(wiki, ["retry", "--knowledge", str(cli_env)])
    assert result.exit_code == 0, result.output
    assert "Nothing to retry" in result.stdout
    pending = [t for t in list_tasks(bundle, status="pending")
               if t.task_kind.startswith("wiki-page")]
    assert [t.id for t in pending] == [revise.id]
    assert pending[0].attempt == 2
    on_disk = _read_manifest(cli_env)["pages"]
    assert on_disk[_key(FAILED)]["attempts"] == 1
    assert on_disk[_key(FAILED)]["task_id"] == original.id


# --- wiki generate --llm --force (TC-007 / FR-005) ----------------------------


def _seed_indexed_repo(conn):
    """One indexed repo ('r') with a single file+symbol: enough for the
    planner to yield an overview page under --pages 1."""
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('r', 'r', '/tmp/r')")
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES "
        "(1, 'r', 'src/graph/queries.py', 'python')"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
        "VALUES (1, 1, 'queries', 'module', 'queries', 1, 100)"
    )
    conn.commit()


def test_generate_llm_force_requeues_unchanged_promoted_page(cli_env, tmp_path):
    """A re-run over an unchanged, promoted page skips it (no new task);
    the same re-run with --force re-queues it (skip logic bypassed)."""
    db = tmp_path / "graph.db"
    conn = get_db(str(db))
    try:
        _seed_indexed_repo(conn)
    finally:
        conn.close()
    knowledge = cli_env
    generate = ["generate", "--llm", "--pages", "1",
                "--db", str(db), "--knowledge", str(knowledge)]

    first = CliRunner().invoke(wiki, generate)
    assert first.exit_code == 0, first.output
    bundle = _bundle(knowledge)
    queued = list_tasks(bundle, kind="wiki-page", status="pending")
    assert [t.resource for t in queued] == ["overview"]
    _promote(bundle, "overview")

    plain = CliRunner().invoke(wiki, generate)
    assert plain.exit_code == 0, plain.output
    assert "Up to date, skipped" in plain.stdout
    assert "Queued 0 new" in plain.stdout
    assert {t.id for t in list_tasks(bundle, kind="wiki-page", status="pending")} == \
        {queued[0].id}

    forced = CliRunner().invoke(wiki, generate + ["--force"])
    assert forced.exit_code == 0, forced.output
    assert "Queued 1 new" in forced.stdout
    requeued = list_tasks(bundle, kind="wiki-page", status="pending")
    assert [t.resource for t in requeued] == ["overview", "overview"]
    ids = {t.id for t in requeued}
    assert len(ids) == 2 and queued[0].id in ids
