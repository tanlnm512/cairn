"""--polish stage: skill-body polish through the LLM task queue.

The stage never runs a model in-process. ``run_polish_stage`` queues one
``skill-polish`` task carrying the deterministic draft, reports the live
task, or consumes a completed task's result: the polished document
replaces the deterministic SKILL.md bytes only after the frontmatter
``name`` is preserved and ``verify_draft`` accepts the exact bytes
destined for disk. A result the queue critic failed is never applied.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional, Tuple

import yaml

from ..llm.tasks import TASK_DIR, Task, create_task, list_tasks, read_result
from ..okf.bundle import OKFBundle
from .gate import verify_draft

__all__ = ["POLISH_KIND", "PolishOutcome", "run_polish_stage"]

POLISH_KIND = "skill-polish"

_LIVE_STATUSES = ("pending", "in-progress")

POLISH_INSTRUCTIONS = (
    "Polish the SKILL.md named in facts.skill_path: improve its wording; keep "
    "the YAML frontmatter and the section structure, and reuse only its "
    "existing backtick-quoted references. Return the full polished document text."
)


@dataclass(frozen=True)
class PolishOutcome:
    """One --polish stage step.

    ``state``: ``queued`` (task enqueued), ``pending`` (a live task
    exists), ``applied`` (``rendered`` holds the gate-accepted bytes to
    write), ``rejected`` (result refused; ``failing_refs``/``reason``
    say why), ``unavailable`` (the last chain failed the queue critic).
    """

    state: str
    task_id: str = ""
    rendered: str = ""
    failing_refs: Tuple[str, ...] = ()
    reason: str = ""


def run_polish_stage(
    bundle: OKFBundle,
    conn: sqlite3.Connection,
    slug: str,
    rendered: str,
    skill_path: str,
) -> PolishOutcome:
    """Advance the polish stage for the skill ``slug``.

    A live task (including its revise chain) is reported, not duplicated;
    the latest completed task's result is gated and returned for apply;
    with no tasks at all one polish task is enqueued. Facts point at the
    written draft (``skill_path``) — bodies never ride in task facts.
    """
    tasks = [
        t
        for t in list_tasks(bundle, kind_prefix=POLISH_KIND)
        if t.resource == slug
    ]
    live = [t for t in tasks if t.status in _LIVE_STATUSES]
    if live:
        return PolishOutcome("pending", task_id=_latest(live).id)
    done = [t for t in tasks if t.status == "done"]
    if done:
        latest = _latest(done)
        # The concept wrapper pads the stored body; strip back to the document.
        polished = (read_result(bundle, latest.id) or "").lstrip("\n") or None
        if polished and not _result_rejected(bundle, latest.id):
            return _gate(conn, latest.id, rendered, polished)
        return PolishOutcome(
            "unavailable",
            task_id=latest.id,
            reason="the last polish result failed the queue critic",
        )
    task = create_task(
        bundle,
        POLISH_KIND,
        slug,
        facts={"skill_path": skill_path, "instructions": POLISH_INSTRUCTIONS},
    )
    return PolishOutcome("queued", task_id=task.id)


def _gate(
    conn: sqlite3.Connection, task_id: str, deterministic: str, polished: str
) -> PolishOutcome:
    """Gate the polished document; only accepted bytes may replace the draft."""
    name = _frontmatter_name(deterministic)
    if _frontmatter_name(polished) != name:
        return PolishOutcome(
            "rejected",
            task_id=task_id,
            reason=f"polished frontmatter must keep name {name!r}",
        )
    gate = verify_draft(conn, polished)
    if gate:
        return PolishOutcome("applied", task_id=task_id, rendered=polished)
    return PolishOutcome(
        "rejected",
        task_id=task_id,
        failing_refs=gate.failing_refs,
        reason="the polished document failed the critic gate",
    )


def _result_rejected(bundle: OKFBundle, task_id: str) -> bool:
    """True when the task's stored result carries a failed critic verdict."""
    try:
        result = bundle.read_concept(f"{TASK_DIR}/{task_id}.result")
    except Exception:
        return True
    return result.extensions.get("critic_status") == "failed"


def _frontmatter_name(rendered: str) -> Optional[str]:
    """The frontmatter ``name``; None when the document is not the emitter's shape."""
    if not rendered.startswith("---\n"):
        return None
    parts = rendered[4:].split("\n---\n", 1)
    if len(parts) != 2:
        return None
    try:
        meta = yaml.safe_load(parts[0])
    except yaml.YAMLError:
        return None
    return meta.get("name") if isinstance(meta, dict) else None


def _latest(tasks: list[Task]) -> Task:
    return max(tasks, key=lambda t: (t.created_at, t.attempt))
