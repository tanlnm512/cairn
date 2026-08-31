"""Public wiki-generation pipeline: plan -> (optionally refine) -> queue.

``run_wiki_generate`` is the one entry point the CLI and MCP surfaces
delegate to. In the default path it plans pages from the graph, skips pages
whose recorded input hash is unchanged and whose promoted concept is
readable (unless ``force``), queues one ``wiki-page`` task per remaining
page, and persists the manifest atomically after the queue decisions. With
``refine_catalog`` the plan is not queued directly: the first run queues a
single ``wiki-catalog`` refinement task carrying the deterministic outline
and returns; a later run that finds the completed catalog's Task-Result
sibling validates the refined outline (invalid entries revert to the
deterministic plan) and queues page tasks from it; a refinement whose
result never landed (unparseable result or a dropped chain) queues the
deterministic plan. Promotion is never written here: it happens when a
claiming agent completes the task through the critic; the manifest only
records task ids and the concept file decides "promoted".
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional

from ..llm.tasks import (
    TASK_DIR,
    Task,
    create_task,
    list_tasks,
    read_result,
)
from ..okf.bundle import OKFBundle
from .catalog import build_page_plan
from .manifest import load_manifest, save_manifest, should_skip
from .refine import validate_refined_outline

_CATALOG_KIND = "wiki-catalog"


def _live_task_pages(bundle: OKFBundle) -> set:
    """Page ids with a live task in their chain: any ``wiki-page*``-kind
    task (page or revise) that is still pending or in progress. Chains are
    grouped by resource, so a revise pending after its original completed
    still counts as live."""
    return {
        t.resource
        for t in list_tasks(bundle)
        if t.task_kind.startswith("wiki-page")
        and t.status in ("pending", "in-progress")
    }


def _queue_pages(
    conn: sqlite3.Connection,
    bundle: OKFBundle,
    repo: str,
    plan: List[Dict[str, Any]],
    force: bool,
    diagrams: bool,
) -> List[str]:
    """Queue a ``wiki-page`` task per unskipped page; persist the manifest."""
    manifest = load_manifest(bundle)
    pages: Dict[str, Any] = manifest.setdefault("pages", {})
    live_pages = _live_task_pages(bundle)

    queued_task_ids: List[str] = []
    for entry in plan:
        page_id = entry["page_id"]
        key = f"{repo}/{page_id}"
        row = pages.get(key)
        if (
            not force
            and row is not None
            and row.get("input_hash") == entry["input_hash"]
            and (should_skip(row, entry, bundle, repo) or page_id in live_pages)
        ):
            continue
        facts: Dict[str, Any] = {
            "title": entry["title"],
            "description": entry["description"],
            "module": entry["module"],
            "seeds": entry["seeds"],
            "input_hash": entry["input_hash"],
            "repo": repo,
        }
        if diagrams:
            facts["diagrams"] = True
        task = create_task(bundle, "wiki-page", page_id, facts=facts)
        queued_task_ids.append(task.id)
        pages[key] = {
            **entry,
            "task_id": task.id,
            "state": "queued",
            "attempts": (row or {}).get("attempts", 0),
        }

    save_manifest(bundle.root, manifest)
    return queued_task_ids


def _latest(tasks: List[Task]) -> Task:
    return max(tasks, key=lambda t: (t.created_at, t.attempt))


def _parse_outline(result: Optional[str]) -> Optional[List[Any]]:
    """The refined outline JSON array, or None when the result never landed."""
    if not result:
        return None
    try:
        parsed = json.loads(result)
    except ValueError:
        return None
    return parsed if isinstance(parsed, list) else None


def _chain_dropped(bundle: OKFBundle, task_id: str) -> bool:
    """True when the chain's last attempt failed the critic: with no pending
    task left, a critic-failed result means the chain exhausted its revise
    cycles and the refinement never landed."""
    try:
        result_concept = bundle.read_concept(f"{TASK_DIR}/{task_id}.result")
    except Exception:
        return True
    return result_concept.extensions.get("critic_status") == "failed"


def _refine_catalog_step(
    conn: sqlite3.Connection,
    bundle: OKFBundle,
    repo: str,
    plan: List[Dict[str, Any]],
    force: bool,
    diagrams: bool,
) -> Dict[str, Any]:
    """One refine-catalog step: queue the catalog task, or consume its
    completed result (latest done chain task by creation time, then attempt)
    and queue the page tasks it validates to."""
    tasks = [t for t in list_tasks(bundle) if t.task_kind.startswith(_CATALOG_KIND)]
    pending = [t for t in tasks if t.status in ("pending", "in-progress")]
    if pending:
        return {
            "plan": plan,
            "queued_task_ids": [],
            "catalog_task_id": _latest(pending).id,
            "catalog_pending": True,
        }
    done = [t for t in tasks if t.status == "done"]
    refined = None
    if done:
        latest = _latest(done)
        if not _chain_dropped(bundle, latest.id):
            refined = _parse_outline(read_result(bundle, latest.id))
    if refined is None:
        if not tasks:
            task = create_task(
                bundle,
                _CATALOG_KIND,
                repo,
                facts={"repo": repo, "outline": json.dumps(plan)},
            )
            return {"plan": plan, "queued_task_ids": [], "catalog_task_id": task.id}
        queued = _queue_pages(conn, bundle, repo, plan, force, diagrams)
        return {"plan": plan, "queued_task_ids": queued}
    effective = validate_refined_outline(refined, plan, conn)
    queued = _queue_pages(conn, bundle, repo, effective, force, diagrams)
    return {"plan": effective, "queued_task_ids": queued}


def run_wiki_generate(
    conn: sqlite3.Connection,
    bundle: OKFBundle,
    repo: str,
    pages_cap: int = 10,
    force: bool = False,
    diagrams: bool = False,
    refine_catalog: bool = False,
) -> Dict[str, Any]:
    """Plan pages for ``repo`` and queue the work behind them.

    Returns ``{"plan": <ordered page records>, "queued_task_ids": [...]}``;
    with ``refine_catalog`` the result additionally carries
    ``catalog_task_id`` and the plan is not queued on the same call: the
    first run queues one ``wiki-catalog`` task carrying the deterministic
    outline, a later run queues the page tasks from the validated refined
    outline (falling back to the deterministic plan when the refinement
    never landed). Raises ``WikiPlannerError`` when the repo has no indexed
    files (nothing is queued). A page whose inputs are unchanged is skipped
    when its concept is promoted or a live task (pending or in progress)
    already covers it; changed inputs re-queue the page even when an older
    task lingers; ``force`` re-queues every page. The manifest is read
    before the queue decisions and written atomically afterwards; a page's
    cumulative ``attempts`` counter is preserved across re-queues.
    """
    plan = build_page_plan(conn, repo, pages_cap=pages_cap)
    if refine_catalog:
        return _refine_catalog_step(conn, bundle, repo, plan, force, diagrams)
    queued_task_ids = _queue_pages(conn, bundle, repo, plan, force, diagrams)
    return {"plan": plan, "queued_task_ids": queued_task_ids}
