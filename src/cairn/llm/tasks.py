"""LLM task queue: agent-decoupled synthesis via OKF Task concepts.

Tasks live in .knowledge/_tasks/<id>.md as OKF concepts (type: Task). Any agent
that can read markdown and run `cairn task` can process them. The deterministic
critic gates promotion by fact-checking backtick-quoted file/symbol references
against the graph; un-backticked prose is NOT verified (see src/compass/critic.py).

Task lifecycle:
  pending -> in-progress (claimed) -> done (critic run) -> [promoted | revised | dropped]
  A revised task spawns a new '<kind>-revise' task with the fact errors attached,
  up to MAX_REVISE_CYCLES times.
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
from ..telemetry import TASK_LIFECYCLE, emit as _emit

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
    status: str = "pending"  # pending | in-progress | done | failed | dropped
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
    # Privacy floor (audit F9, mirrored from complete_task's result gate):
    # memory-* task facts derive from user session content (memory-extract
    # carries the raw conversation transcript), so scrub secret-shaped
    # substrings BEFORE the concept is persisted -- facts land both in the
    # rendered body and structurally in extensions. This is the single
    # chokepoint every queueing backend passes through: the CLI's no-backend
    # capture fallback, FileQueueBackend directly, and SubprocessBackend's
    # fallback to it all create tasks here, so none can bypass the strip.
    # Non-string facts (ints/lists used by non-memory kinds) pass through.
    if task_kind.startswith("memory-") and facts:
        from ..memory.privacy import strip_private_data

        facts = {
            k: strip_private_data(v) if isinstance(v, str) else v
            for k, v in facts.items()
        }
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
    bundle: OKFBundle,
    status: Optional[str] = None,
    kind: Optional[str] = None,
    kind_prefix: Optional[str] = None,
) -> List[Task]:
    """List tasks, optionally filtered by status, exact kind, or kind prefix."""
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
        if kind_prefix and not task.task_kind.startswith(kind_prefix):
            continue
        out.append(task)
    return out


def claim_task(bundle: OKFBundle, task_id: str, assigned_to: str = "") -> Optional[Task]:
    """Atomically claim a pending task. Returns None if not claimable.

    Uses os.open(O_CREAT|O_EXCL) on a claim-marker file; two concurrent claims
    on the same pending task yield exactly one winner. A `.claim` marker older
    than CLAIM_STALE_SECONDS is treated as leaked and reclaimed.
    """
    # Create claim marker path (sibling to the task file)
    claim_marker = bundle.root / f"{TASK_DIR}/{task_id}.claim"

    try:
        # Create the claim marker atomically (O_EXCL fails if already claimed).
        fd = os.open(claim_marker, os.O_CREAT | os.O_EXCL)
        os.close(fd)
    except FileExistsError:
        # Marker exists -- check if it's stale (leaked by a crash between marker
        # creation and status update) before giving up.
        if not _try_remove_stale_marker(claim_marker):
            return None
        # Stale marker removed: retry once. A concurrent winner raises again.
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
        # task_lifecycle: claimed -- emit is best-effort (never raises), so a
        # telemetry outage can't block a claim (spec §5.6).
        _emit(
            TASK_LIFECYCLE,
            task_kind=task.task_kind,
            event="claimed",
            attempt=task.attempt,
        )
        return task
    except Exception:
        # Something went wrong - clean up marker and re-raise
        try:
            os.remove(claim_marker)
        except OSError:
            pass
        raise


def _try_remove_stale_marker(claim_marker: Path) -> bool:
    """Remove `claim_marker` if older than CLAIM_STALE_SECONDS.

    Returns True if the stale marker was removed (or vanished), False if it is
    fresh or could not be removed.
    """
    try:
        mtime = os.stat(claim_marker).st_mtime
    except OSError:
        # Marker disappeared between FileExistsError and stat -- report True so
        # the caller retries the atomic create.
        return True
    if (time.time() - mtime) < CLAIM_STALE_SECONDS:
        return False
    try:
        os.remove(claim_marker)
        return True
    except OSError:
        return False


def _enriched_article(
    bundle: OKFBundle,
    task: Task,
    task_id: str,
    repo: str,
    page_id: str,
    new_sections: str,
    new_sources: Optional[List[Dict[str, Any]]],
) -> OKFConcept:
    """The enriched Wiki-Article: the promoted page's body with
    ``new_sections`` appended, sources merged old entries first and deduped
    by entry value, extensions refreshed from the task's facts. The promoted
    concept is read at completion time; a page with no readable concept
    falls back to ``facts["current_body"]``."""
    try:
        current = bundle.read_concept(f"wiki/pages/{repo}/{page_id}")
    except Exception:
        current = None
    base = (
        current.body
        if current is not None
        else str(task.facts.get("current_body") or "")
    )
    merged: List[Dict[str, Any]] = (
        list(current.sources) if current is not None and current.sources else []
    )
    for entry in new_sources or []:
        if entry not in merged:
            merged.append(entry)
    return OKFConcept(
        type="Wiki-Article",
        title=f"Wiki: {page_id}",
        description=f"Wiki article for {repo}/{page_id}",
        resource=page_id,
        tags=[repo, "wiki"],
        timestamp=_now(),
        concept_id=f"wiki/pages/{repo}/{page_id}",
        sources=merged or None,
        body=f"{base}\n\n{new_sections}" if base else new_sections,
        extensions={
            "page_id": page_id,
            "input_hash": task.facts.get("input_hash"),
            "task_id": task_id,
            "refine_catalog": task.facts.get("refine_catalog"),
            **(
                {"commit_sha": task.facts["commit_sha"]}
                if task.facts.get("commit_sha")
                else {}
            ),
        },
    )


def complete_task(
    bundle: OKFBundle,
    task_id: str,
    result: str,
    conn: Optional[Any] = None,
    claimer: Optional[str] = None,
) -> Dict[str, Any]:
    """Mark a task done and run the deterministic critic on its result.

    Returns {task_id, promoted, revised, dropped, errors, quality}. On fact-check
    failure, spawns a revise task (up to MAX_REVISE_CYCLES). When `claimer` is
    provided it must match the task's `assigned_to`, else completion is refused.
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

    # Ownership guard. Refuse if a claimer identity was supplied but does not
    # own this task. Empty-string assigned_to means the task was claimed
    # anonymously and anyone may complete it.
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

    # Privacy floor (audit F9): memory-* task results are derived from user
    # session content (memory-extract embeds the transcript, memory-critic
    # quotes draft bodies), so scrub secret-shaped substrings before the
    # result body is persisted as a concept. Deliberately gated on a
    # ``memory-`` kind prefix: compass/wiki/flow synthesis bodies are
    # graph-derived and skip the pass (the strip is pattern-based and safe,
    # but the narrow gate keeps the write path's redaction contract
    # explicit).
    if task.task_kind.startswith("memory-"):
        from ..memory.privacy import strip_private_data

        result = strip_private_data(result)

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
            from ..compass.critic import CriticResult, critic_concept

            # Wiki pages are scored on the Sources footer, not compass sections.
            wiki_page = task.task_kind.startswith("wiki-page")
            if wiki_page:
                critic_result = critic_concept(
                    result_concept, conn, section_vocab=("## Sources",)
                )
            else:
                critic_result = critic_concept(result_concept, conn)

            # Footer entries may ride inline links the backtick critic never
            # sees, so they are resolved here; an unresolved entry fails the
            # gate and the revise carries the reason.
            wiki_sources: Optional[List[Dict[str, Any]]] = None
            if wiki_page:
                from ..refs import file_exists as _file_exists
                from ..refs import (
                    extract_file_refs as _extract_file_refs,
                    unresolved_file_refs as _unresolved_file_refs,
                )
                from ..wiki.sources import parse_sources_footer, resolve_sources

                resolved, source_errors = resolve_sources(
                    parse_sources_footer(result), conn
                )
                # One error per distinct dead path: a footer entry the
                # body's own citations already reported stays out of the
                # merge.
                body_unresolved = _unresolved_file_refs(
                    conn, _extract_file_refs(result)
                )
                source_errors = [
                    e
                    for e in source_errors
                    if not any(e.endswith(f": {ref}") for ref in body_unresolved)
                ]
                if source_errors:
                    critic_result = CriticResult(
                        errors=critic_result.errors + source_errors,
                        warnings=critic_result.warnings,
                        quality_score=critic_result.quality_score,
                        passed=False,
                    )
                else:
                    wiki_sources = [
                        {"path": e} if _file_exists(conn, e) else {"symbol": e}
                        for e in resolved
                    ]

            # Mark result concept with critic status
            if critic_result.passed:
                result_concept.extensions["critic_status"] = "passed"
            else:
                result_concept.extensions["critic_status"] = "failed"
            bundle.write_concept(result_concept)
            
            # Branch on critic result
            if critic_result.passed:
                # Promote compass/flow results into compass/ so the
                # list/gaps/validate tools pick them up. The result body is
                # promoted AS-IS; when the critic emitted warnings, a visible
                # marker is prepended so the promoted concept carries its caveat.
                # (Un-backticked prose is NOT critic-verified.)
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
                    # coverage detection recognizes them.
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

                if task.task_kind.startswith("wiki-page"):
                    repo = task.facts.get("repo")
                    if not repo:
                        return {
                            "task_id": task_id,
                            "promoted": False,
                            "revised": False,
                            "dropped": False,
                            "errors": [
                                "wiki-page task is missing the required 'repo' fact"
                            ],
                            "quality": critic_result.quality_score,
                        }
                    page_id = (
                        task.resource.replace("/", "-")
                        .replace(".", "-")
                        .replace("#", "-")
                    )
                    if task.task_kind.startswith("wiki-page-enrich"):
                        wiki_concept = _enriched_article(
                            bundle, task, task_id, repo, page_id, result,
                            wiki_sources,
                        )
                    else:
                        promoted_body = result
                        if critic_result.warnings:
                            marker = "> [critic-warning] " + "; ".join(
                                critic_result.warnings
                            ) + "\n\n"
                            promoted_body = marker + result
                        wiki_concept = OKFConcept(
                            type="Wiki-Article",
                            title=f"Wiki: {page_id}",
                            description=f"Wiki article for {repo}/{page_id}",
                            resource=page_id,
                            tags=[repo, "wiki"],
                            timestamp=_now(),
                            concept_id=f"wiki/pages/{repo}/{page_id}",
                            sources=wiki_sources,
                            body=promoted_body,
                            extensions={
                                "page_id": page_id,
                                "input_hash": task.facts.get("input_hash"),
                                "task_id": task_id,
                                "refine_catalog": task.facts.get("refine_catalog"),
                                **(
                                    {"commit_sha": task.facts["commit_sha"]}
                                    if task.facts.get("commit_sha")
                                    else {}
                                ),
                            },
                        )
                    bundle.write_concept(wiki_concept)
                    promoted = True

                _emit(
                    TASK_LIFECYCLE,
                    task_kind=task.task_kind,
                    event="completed",
                    attempt=task.attempt,
                )
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
                    # Spawn a revise task. A synthesize task becomes the matching
                    # revise kind; any other kind appends "-revise".
                    if task.task_kind.endswith("-synthesize"):
                        revise_kind = task.task_kind[: -len("-synthesize")] + "-revise"
                    else:
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

                    _emit(
                        TASK_LIFECYCLE,
                        task_kind=task.task_kind,
                        event="revised",
                        attempt=task.attempt,
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
                    _emit(
                        TASK_LIFECYCLE,
                        task_kind=task.task_kind,
                        event="dropped",
                        attempt=task.attempt,
                    )
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


def drop_task(bundle: OKFBundle, task_id: str) -> Dict[str, Any]:
    """Mark a pending or in-progress task dropped (terminal status).

    Returns {task_id, dropped, errors}. Done, unknown, and already-dropped
    tasks are refused with their status left unchanged. A dropped task is
    never claimable again (claim_task claims pending only); dropping an
    in-progress task removes its claim marker so the resource can be
    re-queued.
    """
    task = _read(bundle, task_id)
    if task is None:
        return {
            "task_id": task_id,
            "dropped": False,
            "errors": ["task not found"],
        }
    if task.status not in ("pending", "in-progress"):
        return {
            "task_id": task_id,
            "dropped": False,
            "errors": [f"task is {task.status}; only pending or in-progress "
                       "tasks can be dropped"],
        }
    task.status = "dropped"
    bundle.write_concept(_task_to_concept(task))
    claim_marker = bundle.root / f"{TASK_DIR}/{task_id}.claim"
    try:
        os.remove(claim_marker)
    except OSError:
        pass
    _emit(
        TASK_LIFECYCLE,
        task_kind=task.task_kind,
        event="dropped",
        attempt=task.attempt,
    )
    return {"task_id": task_id, "dropped": True, "errors": []}


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
    # Task lifecycle status rides on the OKF v0.2 first-class `status` field.
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
    lines.append(_output_spec(task.task_kind, task.facts))
    lines.append(f"\nWrite your result, then run: `cairn task complete {task.id}`")
    return "\n".join(lines) + "\n"


def _output_spec(task_kind: str, facts: Optional[Dict[str, Any]] = None) -> str:
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
        "wiki-page": (
            "Write an architectural wiki article in markdown for the page described in "
            "the facts. Only reference files/symbols from the facts (the seeds); never "
            "reference anything outside the graph. Use backticks for code. End the "
            "article with a `## Sources` footer listing the files you cited."
        ),
        "wiki-page-revise": (
            "The previous wiki draft had factual errors (listed in facts.errors). "
            "Rewrite the article fixing ONLY those errors; keep correct content. "
            "Only reference files/symbols from the facts seeds; never reference "
            "anything outside the graph. End with a `## Sources` footer."
        ),
        "wiki-catalog": (
            "Refine the deterministic wiki outline provided in the facts: you may "
            "reorder, retitle, or merge entries, but every entry must name a module "
            "that exists in the graph. Output the refined outline as JSON with the "
            "same fields as the input."
        ),
        "wiki-catalog-revise": (
            "The previous catalog refinement had factual errors (listed in "
            "facts.errors). Fix ONLY those errors; keep valid entries. Every entry "
            "must still name a module that exists in the graph. Output the corrected "
            "JSON outline."
        ),
        "memory-critic": "For each draft memory in facts, judge accuracy/usefulness/specificity/non-redundancy. Output one JSON line per memory: {title, keep: bool, score: 0-1, reason}.",
        "memory-extract": "From the session transcript in facts.transcript, extract candidate memories. Output one JSON line per candidate: {type: decision|pattern|mistake|workaround, title, body, confidence}.",
    }
    spec = specs.get(task_kind)
    if spec is None:
        if task_kind.startswith("wiki-page"):
            spec = specs["wiki-page"]
        else:
            spec = "Process per the cairn skill."
    if task_kind.startswith("wiki-page") and facts and facts.get("diagrams"):
        spec += "\nInclude Mermaid fenced code blocks for the key structures you describe."
    return spec


def _concept_to_task(concept: OKFConcept) -> Task:
    ext = concept.extensions
    # Task status lives on the OKF v0.2 first-class `status` field; fall back
    # to extensions["status"] for tasks written before the v0.2 upgrade.
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
