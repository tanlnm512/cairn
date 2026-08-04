"""LLM task queue: agent-decoupled synthesis via OKF Task concepts.

Tasks live in .knowledge/_tasks/<id>.md as OKF concepts (type: Task). Any agent
that can read markdown and run `cairn task` can process them — cairn never
calls an LLM directly. The deterministic critic gates promotion by
fact-checking backtick-quoted file/symbol references against the graph and
applying prose/quality heuristics; it is NOT a comprehensive hallucination
detector, since un-backticked prose is not verified (see
src/compass/critic.py). The critic runs automatically on task completion.

Task lifecycle:
  pending -> in-progress (claimed) -> done (critic run) -> [promoted | revised | dropped]
  A revised task spawns a new '<kind>-revise' task with the fact errors attached,
  up to MAX_REVISE_CYCLES times.

This decouples cairn from any specific LLM/agent. The agent loads the
cairn skill, reads a pending task, writes a result file, runs
`cairn task complete`. Prompts live in the skill, keyed by task_kind.
"""
from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..okf.bundle import OKFBundle
from ..okf.concept import OKFConcept

TASK_DIR = "_tasks"
MAX_REVISE_CYCLES = 3

# A claim marker older than this (in seconds) is treated as leaked -- e.g.
# left behind by a process that crashed between creating the `.claim` marker
# and updating the task status. On collision we re-claim once after this age so
# a leaked marker can't permanently block a task.
CLAIM_STALE_SECONDS = 3600


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Task:
    id: str
    task_kind: str  # compass-synthesize | compass-revise | flow-synthesize | flow-revise | wiki | memory-critic | memory-extract
    resource: str  # the module/symbol/transcript being worked on
    facts: Dict[str, Any] = field(default_factory=dict)  # graph-grounded, agent must not invent
    status: str = "pending"  # pending | in-progress | done | failed
    assigned_to: str = ""
    result_path: str = ""
    attempt: int = 1
    created_at: str = ""
    completed_at: str = ""
    claimed_at: str = ""  # ISO-8601 timestamp when task was claimed

    @property
    def concept_id(self) -> str:
        return f"{TASK_DIR}/{self.id}"

    @property
    def result_concept_id(self) -> str:
        return f"{TASK_DIR}/{self.id}.result"


def create_task(
    bundle: OKFBundle,
    task_kind: str,
    resource: str,
    facts: Optional[Dict[str, Any]] = None,
    parent_attempt: int = 0,
) -> Task:
    """Queue a new task. Returns the Task (already written to the bundle)."""
    task = Task(
        id=uuid.uuid4().hex[:12],
        task_kind=task_kind,
        resource=resource,
        facts=facts or {},
        attempt=parent_attempt + 1,
        created_at=_now(),
    )
    concept = _task_to_concept(task)
    bundle.write_concept(concept)
    return task


def list_tasks(
    bundle: OKFBundle, status: Optional[str] = None, kind: Optional[str] = None
) -> List[Task]:
    """List tasks, optionally filtered by status or kind."""
    out = []
    for cid in bundle.list_concepts(prefix=f"{TASK_DIR}/"):
        if cid.endswith(".result"):
            continue
        try:
            concept = bundle.read_concept(cid)
        except Exception:
            continue
        if concept.type != "Task":
            continue
        task = _concept_to_task(concept)
        if status and task.status != status:
            continue
        if kind and task.task_kind != kind:
            continue
        out.append(task)
    return out


def claim_task(bundle: OKFBundle, task_id: str, assigned_to: str = "") -> Optional[Task]:
    """Atomically claim a pending task. Returns None if not claimable.

    Uses os.open(O_CREAT|O_EXCL) on a claim-marker file for atomicity.
    Two concurrent claims on the same pending task yield exactly one winner.
    An already-claimed task cannot be re-claimed.

    If an existing `.claim` marker is older than CLAIM_STALE_SECONDS, it is
    treated as leaked (e.g. a crash between marker creation and status update)
    and reclaimed. This prevents a leaked marker from permanently blocking a
    task.
    """
    # Create claim marker path (sibling to the task file)
    claim_marker = bundle.root / f"{TASK_DIR}/{task_id}.claim"

    try:
        # Try to create the claim marker atomically.
        # This fails with FileExistsError if already exists (i.e., already claimed).
        fd = os.open(claim_marker, os.O_CREAT | os.O_EXCL)
        os.close(fd)
    except FileExistsError:
        # Marker already exists. Before giving up, check whether it's stale:
        # a crash between os.open above and the status update below can leave a
        # marker with the task still "pending", permanently blocking it.
        if not _try_remove_stale_marker(claim_marker):
            # Not stale (or couldn't remove) -- genuinely claimed by someone.
            return None
        # Stale marker removed: retry the atomic create exactly once. If another
        # agent grabbed the slot in the meantime this raises FileExistsError
        # again and we treat it as "not claimable", preserving single-winner.
        try:
            fd = os.open(claim_marker, os.O_CREAT | os.O_EXCL)
            os.close(fd)
        except FileExistsError:
            return None
        except (OSError, IOError):
            return None
    except (OSError, IOError):
        # Other filesystem error - treat as unclaimable
        return None

    try:
        # Re-read task status to ensure it's still pending
        task = _read(bundle, task_id)
        if task is None or task.status != "pending":
            # Task not claimable - remove marker and return None
            try:
                os.remove(claim_marker)
            except OSError:
                pass
            return None

        # Claim the task
        task.status = "in-progress"
        task.assigned_to = assigned_to
        task.claimed_at = _now()
        bundle.write_concept(_task_to_concept(task))
        return task
    except Exception:
        # Something went wrong - clean up marker and re-raise
        try:
            os.remove(claim_marker)
        except OSError:
            pass
        raise


def _try_remove_stale_marker(claim_marker: Path) -> bool:
    """If `claim_marker` is older than CLAIM_STALE_SECONDS, remove it.

    Returns True if the stale marker was removed (caller should retry the
    atomic create). Returns False if the marker is fresh or could not be
    removed safely.
    """
    try:
        mtime = os.stat(claim_marker).st_mtime
    except OSError:
        # Marker disappeared between the FileExistsError and the stat -- it's
        # gone now, so report True so the caller retries the atomic create
        # (which will either succeed or lose cleanly to a concurrent winner).
        return True
    if (time.time() - mtime) < CLAIM_STALE_SECONDS:
        return False
    try:
        os.remove(claim_marker)
        return True
    except OSError:
        return False


def complete_task(
    bundle: OKFBundle,
    task_id: str,
    result: str,
    conn: Optional[Any] = None,
    claimer: Optional[str] = None,
) -> Dict[str, Any]:
    """Mark a task done and run the deterministic critic on its result.

    Returns {task_id, promoted, revised, dropped, errors, quality}.
    On fact-check failure, spawns a revise task (up to MAX_REVISE_CYCLES).
    On pass, the caller is responsible for promoting (e.g. into compass/).

    Ownership check: when `claimer` is provided, it must match the task's
    `assigned_to` (the agent that claimed it); a mismatch refuses completion
    with a clear error and leaves the task in-progress. When `claimer` is None
    the call is permissive -- callers without an identity still work, at the
    cost of leaving the ownership guardrail inert.
    """
    task = _read(bundle, task_id)
    if task is None or task.status != "in-progress":
        return {
            "task_id": task_id,
            "promoted": False,
            "revised": False,
            "dropped": True,
            "errors": ["task not in-progress or not found"],
            "quality": 0.0,
        }

    # Ownership guard. Refuse if a claimer identity was supplied but does
    # not own this task. Leave the task in-progress so the rightful owner can
    # still complete it. (Empty-string assigned_to means the task was claimed
    # anonymously; treat that as "anyone may complete".)
    if claimer is not None and task.assigned_to and claimer != task.assigned_to:
        return {
            "task_id": task_id,
            "promoted": False,
            "revised": False,
            "dropped": True,
            "errors": [
                f"ownership mismatch: task owned by '{task.assigned_to}' "
                f"but complete called by '{claimer}'"
            ],
            "quality": 0.0,
        }

    task.status = "done"
    task.completed_at = _now()
    
    # Write the result as a sibling concept.
    result_concept = OKFConcept(
        type="Task-Result",
        title=f"Result for {task_id}",
        resource=task.resource,
        timestamp=_now(),
        concept_id=task.result_concept_id,
        body=result,
    )
    bundle.write_concept(result_concept)
    bundle.write_concept(_task_to_concept(task))
    
    # Remove claim marker if it exists
    claim_marker = bundle.root / f"{TASK_DIR}/{task_id}.claim"
    try:
        os.remove(claim_marker)
    except OSError:
        pass
    
    # Run critic if a connection is provided
    if conn is not None:
        try:
            from ..compass.critic import critic_concept
            
            critic_result = critic_concept(result_concept, conn)
            
            # Mark result concept with critic status
            if critic_result.passed:
                result_concept.extensions["critic_status"] = "passed"
            else:
                result_concept.extensions["critic_status"] = "failed"
            bundle.write_concept(result_concept)
            
            # Branch on critic result
            if critic_result.passed:
                # Pass: promote compass results into compass/<module> so
                # cairn compass list/gaps/validate pick them up. (Other kinds,
                # e.g. wiki, have no current task producer -- left unpromoted
                # per this function's documented "caller promotes" contract.)
                #
                # The result body is promoted AS-IS. The critic gates this by
                # verifying backtick-quoted file/symbol refs and (for
                # prose-heavy / low-ref drafts) demanding a higher quality
                # score, but un-backticked prose is NOT verified. When the
                # critic emitted warnings, prepend a visible marker so the
                # promoted concept carries its provenance/caveat rather than
                # looking fully checked.
                promoted = False
                if task.task_kind in ("compass-synthesize", "compass-revise"):
                    from ..compass.generator import _derive_title

                    module_path = task.resource
                    promoted_body = result
                    if critic_result.warnings:
                        marker = "> [critic-warning] " + "; ".join(
                            critic_result.warnings
                        ) + "\n\n"
                        promoted_body = marker + result
                    compass_concept = OKFConcept(
                        type="Compass",
                        title=_derive_title(module_path),
                        description=f"Navigation guide for {module_path}",
                        resource=module_path,
                        tags=[t for t in module_path.strip("/").split("/") if t][:6],
                        timestamp=_now(),
                        concept_id=f"compass/{module_path.strip('/').replace('/', '-')}",
                        body=promoted_body,
                    )
                    bundle.write_concept(compass_concept)
                    promoted = True

                if task.task_kind in ("flow-synthesize", "flow-revise"):
                    # Promote flow results to compass/flow-{entry} so flow-gaps
                    # coverage detection recognizes them. concept_id scheme
                    # matches generate_flow_compass (generator.py).
                    entry = task.resource
                    promoted_body = result
                    if critic_result.warnings:
                        marker = "> [critic-warning] " + "; ".join(
                            critic_result.warnings
                        ) + "\n\n"
                        promoted_body = marker + result
                    safe_id = entry.replace("/", "-").replace(".", "-").replace("#", "-")
                    flow_concept = OKFConcept(
                        type="Compass",
                        title=f"Flow: {entry}",
                        description=f"Execution flow traced from `{entry}`",
                        resource=entry,
                        tags=["flow", entry.split(".")[-1]][:6],
                        timestamp=_now(),
                        concept_id=f"compass/flow-{safe_id}",
                        body=promoted_body,
                    )
                    bundle.write_concept(flow_concept)
                    promoted = True

                return {
                    "task_id": task_id,
                    "promoted": promoted,
                    "revised": False,
                    "dropped": False,
                    "errors": critic_result.errors,
                    "quality": critic_result.quality_score,
                }
            else:
                # Fail with errors
                if task.attempt < MAX_REVISE_CYCLES:
                    # Spawn a revise task
                    revise_kind = task.task_kind.replace("-synthesize", "-revise")
                    if "-revise" not in revise_kind and task.task_kind != revise_kind:
                        revise_kind = f"{task.task_kind}-revise"
                    
                    create_task(
                        bundle,
                        task_kind=revise_kind,
                        resource=task.resource,
                        facts={
                            **task.facts,
                            "errors": critic_result.errors,
                            "parent_task_id": task_id,
                        },
                        parent_attempt=task.attempt,
                    )
                    
                    return {
                        "task_id": task_id,
                        "promoted": False,
                        "revised": True,
                        "dropped": False,
                        "errors": critic_result.errors,
                        "quality": critic_result.quality_score,
                    }
                else:
                    # Max cycles reached - drop the task
                    return {
                        "task_id": task_id,
                        "promoted": False,
                        "revised": False,
                        "dropped": True,
                        "errors": critic_result.errors,
                        "quality": critic_result.quality_score,
                    }
        except Exception:
            # Critic run failed - treat as success but log error
            # (This preserves existing behavior when critic is unavailable)
            return {
                "task_id": task_id,
                "promoted": False,
                "revised": False,
                "dropped": False,
                "errors": ["critic execution failed"],
                "quality": 0.0,
            }
    
    # No connection provided - return basic completion
    return {
        "task_id": task_id,
        "promoted": False,
        "revised": False,
        "dropped": False,
        "errors": [],
        "quality": 0.0,
    }


def read_result(bundle: OKFBundle, task_id: str) -> Optional[str]:
    """Read the result body for a completed task."""
    try:
        c = bundle.read_concept(f"{TASK_DIR}/{task_id}.result")
        return c.body
    except FileNotFoundError:
        return None


def get_task(bundle: OKFBundle, task_id: str) -> Optional[Task]:
    return _read(bundle, task_id)


# --- OKF (de)serialization ----------------------------------------------

def _task_to_concept(task: Task) -> OKFConcept:
    extensions: Dict[str, Any] = {
        "task_kind": task.task_kind,
        "assigned_to": task.assigned_to,
        "attempt": task.attempt,
        "result_path": task.result_path,
        "completed_at": task.completed_at,
        "claimed_at": task.claimed_at,
    }
    # Facts rendered as YAML-ish sections in the body for agent readability,
    # plus stored structurally in extensions for programmatic access.
    extensions["facts"] = task.facts
    body = _render_body(task)
    # Task lifecycle status rides on the OKF v0.2 `status` first-class field.
    # (Task values pending|in-progress|done|failed are distinct from OKF's
    # draft|stable|deprecated, so there is no semantic ambiguity; `concept.status`
    # is simply where per-concept lifecycle now lives.)
    return OKFConcept(
        type="Task",
        title=f"{task.task_kind}: {task.resource}",
        resource=task.resource,
        tags=[task.task_kind],
        timestamp=task.created_at,
        status=task.status,
        concept_id=task.concept_id,
        body=body,
        extensions=extensions,
    )


def _render_body(task: Task) -> str:
    lines = [f"# Task: {task.task_kind}", ""]
    lines.append(f"**Resource:** `{task.resource}`  ")
    lines.append(f"**Status:** {task.status}  ")
    lines.append(f"**Attempt:** {task.attempt}/{MAX_REVISE_CYCLES + 1}")
    lines.append("")
    if task.facts:
        lines.append("## Facts (graph-grounded — do not invent beyond these)")
        for key, val in task.facts.items():
            lines.append(f"**{key}:** {val}")
        lines.append("")
    lines.append("## Output spec")
    lines.append(_output_spec(task.task_kind))
    lines.append(f"\nWrite your result, then run: `cairn task complete {task.id}`")
    return "\n".join(lines) + "\n"


def _output_spec(task_kind: str) -> str:
    specs = {
        "compass-synthesize": (
            "Write a 25-35 line compass file with exactly these 5 sections:\n"
            "# What Does This Module Do? / # Common Modification Patterns / "
            "# Build-Failure Patterns / # Cross-Module Dependencies / # Tribal Knowledge\n"
            "Only reference files/symbols listed in the facts. Use backticks for code."
        ),
        "compass-revise": (
            "The previous draft had factual errors (listed in facts.errors). "
            "Rewrite the compass file fixing ONLY those errors; keep correct content. "
            "Same 5-section format. Only reference files/symbols from facts.key_files."
        ),
        "flow-synthesize": (
            "Write a business flow compass file with exactly these 5 sections:\n"
            "# What Does This Flow Do? / # Call Sequence / # Failure-Prone Steps / "
            "# Modules Spanned / # Tribal Knowledge\n"
            "Use the traced call chain in facts.chain as the call sequence. "
            "Only reference files/symbols listed in the facts (chain_raw file paths). "
            "Use backticks for code. Note branch points and terminal calls from facts."
        ),
        "flow-revise": (
            "The previous flow draft had factual errors (listed in facts.errors). "
            "Rewrite fixing ONLY those errors; keep correct content. "
            "Same 5-section flow format. Only reference files/symbols from facts.chain_raw."
        ),
        "wiki": "Write an architectural wiki article in markdown. Only reference graph-verified symbols.",
        "memory-critic": "For each draft memory in facts, judge accuracy/usefulness/specificity/non-redundancy. Output one JSON line per memory: {title, keep: bool, score: 0-1, reason}.",
        "memory-extract": "From the session transcript in facts.transcript, extract candidate memories. Output one JSON line per candidate: {type: decision|pattern|mistake|workaround, title, body, confidence}.",
    }
    return specs.get(task_kind, "Process per the cairn skill.")


def _concept_to_task(concept: OKFConcept) -> Task:
    ext = concept.extensions
    # Task status lives on the OKF v0.2 first-class `status` field. Fall back to
    # a legacy `extensions["status"]` for tasks written before the v0.2 upgrade.
    task_status = concept.status or ext.get("status", "pending")
    return Task(
        id=concept.concept_id.split("/")[-1],
        task_kind=ext.get("task_kind", ""),
        resource=concept.resource or "",
        facts=ext.get("facts", {}),
        status=task_status,
        assigned_to=ext.get("assigned_to", ""),
        result_path=ext.get("result_path", ""),
        attempt=ext.get("attempt", 1),
        created_at=concept.timestamp or "",
        completed_at=ext.get("completed_at", ""),
        claimed_at=ext.get("claimed_at", ""),
    )


def _read(bundle: OKFBundle, task_id: str) -> Optional[Task]:
    try:
        concept = bundle.read_concept(f"{TASK_DIR}/{task_id}")
        if concept.type != "Task":
            return None
        return _concept_to_task(concept)
    except FileNotFoundError:
        return None
