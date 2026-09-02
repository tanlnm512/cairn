"""Task CLI: the task group (list/show/claim/complete/drop)."""
from __future__ import annotations

import click
import sys
from pathlib import Path

from .main import DEFAULT_DB_PATH, get_db, main

@main.group()
def task():
    """LLM task queue: any agent with the skill processes pending tasks."""


@task.command("list")
@click.option("--status", default=None,
              help="Filter: pending|in-progress|done|failed|dropped")
@click.option("--kind", default=None, help="Filter by task kind")
@click.option("--kind-prefix", default=None,
              help="Filter by task kind prefix (e.g. wiki-page)")
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def task_list(status, kind, kind_prefix, knowledge):
    from ..llm.tasks import list_tasks
    from ..okf.bundle import OKFBundle

    bundle = OKFBundle(knowledge)
    tasks = list_tasks(bundle, status=status, kind=kind,
                       kind_prefix=kind_prefix)
    if not tasks:
        click.echo("No tasks.")
        return
    for t in tasks:
        click.echo(f"  [{t.status:11}] {t.task_kind:20} {t.id}  {t.resource}")


@task.command("show")
@click.argument("task_id")
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def task_show(task_id, knowledge):
    """Show a task's full body (facts + output spec) for an agent to process."""
    from ..llm.tasks import get_task, read_result
    from ..okf.bundle import OKFBundle

    bundle = OKFBundle(knowledge)
    t = get_task(bundle, task_id)
    if not t:
        click.echo(f"No task '{task_id}'.", err=True)
        sys.exit(1)
    click.echo(f"# Task {t.id}")
    click.echo(f"kind: {t.task_kind}   status: {t.status}   attempt: {t.attempt}")
    click.echo(f"resource: {t.resource}")
    click.echo("")
    if t.facts:
        click.echo("## Facts")
        for k, v in t.facts.items():
            click.echo(f"  {k}: {v}")
    click.echo("")
    if t.status == "done":
        result = read_result(bundle, task_id)
        if result:
            click.echo("## Result")
            click.echo(result)
    else:
        click.echo(f"To process: `cairn task claim {task_id}`")


@task.command("claim")
@click.argument("task_id")
@click.option("--as", "assigned_to", default="", help="Who/what is claiming this task")
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def task_claim(task_id, assigned_to, knowledge):
    """Claim a pending task (sets status in-progress)."""
    from ..llm.tasks import claim_task
    from ..okf.bundle import OKFBundle

    bundle = OKFBundle(knowledge)
    t = claim_task(bundle, task_id, assigned_to=assigned_to)
    if not t:
        click.echo(f"Could not claim '{task_id}' (not pending or not found).", err=True)
        sys.exit(1)
    click.echo(f"Claimed {task_id}. Write your result, then:")
    click.echo(f"  cairn task complete {task_id} --result-file <path>")


@task.command("complete")
@click.argument("task_id")
@click.option("--result", default=None, help="Result text (alternative to --result-file)")
@click.option("--result-file", default=None, help="Read result from this file")
@click.option("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB path.")
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def task_complete(task_id, result, result_file, db, knowledge):
    """Mark a task done. Runs the deterministic critic automatically."""
    from ..llm.tasks import complete_task, MAX_REVISE_CYCLES
    from ..okf.bundle import OKFBundle

    bundle = OKFBundle(knowledge)
    if result_file:
        result = Path(result_file).read_text(encoding="utf-8")
    if not result:
        click.echo("No result provided. Use --result or --result-file.", err=True)
        sys.exit(1)

    # Open DB connection for critic
    conn = get_db(db)
    
    try:
        outcome = complete_task(bundle, task_id, result, conn=conn)
        if outcome.get("dropped"):
            click.echo(f"Task {task_id} dropped after {MAX_REVISE_CYCLES} failed attempts.", err=True)
            if outcome.get("errors"):
                for err in outcome["errors"]:
                    click.echo(f"  Error: {err}", err=True)
            sys.exit(1)
        elif outcome.get("revised"):
            click.echo(f"Task {task_id} had errors. A revise task has been spawned.")
            if outcome.get("errors"):
                click.echo("Errors:", err=True)
                for err in outcome["errors"]:
                    click.echo(f"  - {err}", err=True)
            click.echo(f"Quality score: {outcome.get('quality', 0.0):.2f}")
        elif outcome.get("promoted"):
            click.echo(f"Task {task_id} completed and promoted.")
        elif outcome.get("errors"):
            # Completion aborted before any outcome (critic failure, missing
            # required facts): the task is untouched and re-completable —
            # never report this as success.
            click.echo(f"Task {task_id} was not completed; it remains "
                       "in-progress and can be completed again.", err=True)
            for err in outcome["errors"]:
                click.echo(f"  Error: {err}", err=True)
            sys.exit(1)
        else:
            # Passed but not auto-promoted
            click.echo(f"Task {task_id} completed. Result stored.")
            click.echo(f"Quality score: {outcome.get('quality', 0.0):.2f}")
            if outcome.get("errors"):
                click.echo("Warnings:", err=True)
                for err in outcome["errors"]:
                    click.echo(f"  - {err}", err=True)
    finally:
        conn.close()


@task.command("drop")
@click.argument("task_id")
@click.option("--knowledge", default=str(DEFAULT_DB_PATH.parent / ".knowledge"))
def task_drop(task_id, knowledge):
    """Drop a pending or in-progress task (terminal; never claimable again)."""
    from ..llm.tasks import drop_task
    from ..okf.bundle import OKFBundle

    bundle = OKFBundle(knowledge)
    outcome = drop_task(bundle, task_id)
    if not outcome["dropped"]:
        reasons = "; ".join(outcome["errors"]) or "not droppable"
        click.echo(f"Could not drop '{task_id}': {reasons}", err=True)
        sys.exit(1)
    click.echo(f"Dropped {task_id}.")


