"""Procedural workflow knowledge -- ordered, queryable step sequences.

A workflow is just another OKF concept (`doc_type="workflow"`) under the
knowledge/ layer, inheriting the existing lifecycle, filtering, and CLI
commands. This module adds only what's workflow-specific: turning an ordered
step list into both a readable body and a structured `steps` extension
(`add_workflow`), and resolving + returning those steps in order by title,
slug, or concept_id (`trace_workflow`).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    import sqlite3

from .store import add_document, get_document, list_documents, slugify
from ..okf.bundle import OKFBundle
from ..okf.concept import OKFConcept

DOC_TYPE = "workflow"


def render_steps_body(title: str, steps: List[dict]) -> str:
    """Render an ordered step list as a readable markdown body.

    This is a rendering; the `steps` extension (structured) is what
    `trace_workflow` actually reads, and wins if the two ever disagree.
    """
    lines = [f"# {title}\n"]
    for i, step in enumerate(steps, start=1):
        name = step.get("name") or f"Step {i}"
        lines.append(f"{i}. **{name}**")
        desc = step.get("description")
        if desc:
            lines.append(f"   {desc}")
        symbol = step.get("symbol")
        if symbol:
            lines.append(f"   - symbol: `{symbol}`")
        file_ = step.get("file")
        if file_:
            lines.append(f"   - file: `{file_}`")
    return "\n".join(lines) + "\n"


def add_workflow(
    bundle: OKFBundle,
    title: str,
    steps: List[dict],
    tags: Optional[List[str]] = None,
    affects_modules: Optional[List[str]] = None,
    affects_repos: Optional[List[str]] = None,
    resource: Optional[str] = None,
    owner: Optional[str] = None,
) -> str:
    """Add a workflow. Returns the concept_id (knowledge/workflow/<slug>).

    ``steps`` is an ordered list of dicts, each typically
    ``{"name", "description", "symbol", "file"}`` (only ``name`` required).
    ``symbol``/``file`` join a step back to the graph.

    Raises ValueError if ``steps`` is empty.
    """
    if not steps:
        raise ValueError("add_workflow requires at least one step")
    body = render_steps_body(title, steps)
    return add_document(
        bundle,
        title=title,
        body=body,
        doc_type=DOC_TYPE,
        tags=tags,
        affects_modules=affects_modules,
        affects_repos=affects_repos,
        resource=resource,
        owner=owner,
        steps=steps,
    )


def _resolve(bundle: OKFBundle, ref: str) -> Optional[OKFConcept]:
    """Resolve a workflow reference: exact concept_id, slug, or title match."""
    if ref.startswith(f"knowledge/{DOC_TYPE}/"):
        return get_document(bundle, ref)

    by_slug_id = f"knowledge/{DOC_TYPE}/{slugify(ref)}"
    concept = get_document(bundle, by_slug_id)
    if concept is not None:
        return concept

    # Fall back to an exact (case-insensitive) title match across all
    # workflow docs -- covers a caller passing the human title verbatim
    # rather than knowing the slug/concept_id.
    for c in list_documents(bundle, doc_type=DOC_TYPE):
        if (c.title or "").strip().lower() == ref.strip().lower():
            return c
    return None


def trace_workflow(bundle: OKFBundle, ref: str) -> Optional[dict]:
    """Resolve and return a workflow's ordered steps.

    ``ref`` may be a title, a slug, or a full concept_id. Returns
    ``{"concept_id", "title", "doc_status", "steps"}``, or ``None`` if no
    workflow matches.

    Does NOT filter on doc_status -- tracing a specific named workflow works
    even if archived; the caller gets ``doc_status`` back and decides.
    """
    concept = _resolve(bundle, ref)
    if concept is None:
        return None
    return {
        "concept_id": concept.concept_id,
        "title": concept.title,
        "doc_status": concept.extensions.get("doc_status", "active"),
        "steps": concept.extensions.get("steps", []),
    }


def list_workflows(bundle: OKFBundle, status: Optional[str] = None) -> List[OKFConcept]:
    """List all workflow documents."""
    return list_documents(bundle, doc_type=DOC_TYPE, status=status)


# ---------------------------------------------------------------------------
# Graph-derived workflows: bridge the declarative call-graph trace into
# procedural workflow steps.
# ---------------------------------------------------------------------------

# Cap on how many chain nodes become workflow steps.
DEFAULT_FLOW_STEP_LIMIT = 20


def flow_to_workflow(
    facts: dict,
    max_steps: int = DEFAULT_FLOW_STEP_LIMIT,
) -> List[dict]:
    """Convert a flow compass facts dict into workflow steps.

    Takes the ``chain_raw`` from ``_gather_flow_facts`` and returns an ordered
    ``steps[]`` list where each step has ``{name, symbol, file, description}``.
    Branch points and terminal calls are annotated.

    Args:
        facts: the dict returned by ``compass.generator._gather_flow_facts``.
        max_steps: cap on the number of steps (default 20).

    Returns:
        A list of step dicts, ready for ``add_workflow(steps=...)``.
    """
    chain = facts.get("chain_raw", [])
    branches = {b["symbol"]: b["callees"] for b in facts.get("branches", [])}
    leaves = set(facts.get("leaves", []))
    entry = facts.get("entry", "")

    steps: List[dict] = []
    for node in chain:
        if len(steps) >= max_steps:
            break
        sym = node.get("symbol", "?")
        if sym == entry and node.get("depth") == 0:
            # The entry point is step 1 — label it as the entry, not just its name.
            desc_parts = ["Entry point"]
        else:
            desc_parts = []

        kind = node.get("kind", "")
        parent = node.get("parent")

        if kind:
            desc_parts.append(kind)
        if parent:
            desc_parts.append(f"called by `{parent}`")

        # Annotate branch points.
        if sym in branches:
            callees = branches[sym]
            callee_str = ", ".join(f"`{c}`" for c in callees[:4])
            if len(callees) > 4:
                callee_str += f", +{len(callees) - 4} more"
            desc_parts.append(f"branches to {callee_str}")

        # Annotate terminal calls (side effects).
        if sym in leaves:
            desc_parts.append("terminal — side effect")

        description = "; ".join(desc_parts) if desc_parts else ""

        steps.append({
            "name": sym,
            "symbol": sym,
            "file": node.get("file", ""),
            "description": description,
        })

    # If the chain was longer than max_steps, note the omission.
    if len(chain) > max_steps:
        omitted = len(chain) - max_steps
        steps.append({
            "name": f"(trace truncated — {omitted} more steps omitted)",
            "description": "Raise --max-steps to include deeper calls.",
        })

    return steps


# ---------------------------------------------------------------------------
# Workflow staleness detection + sync.
# ---------------------------------------------------------------------------

def check_workflow_staleness(
    conn: sqlite3.Connection,
    bundle: OKFBundle,
    ref: str,
) -> Optional[dict]:
    """Check a workflow's step anchors against the current graph.

    For each step in ``concept.extensions["steps"]``, verifies that the step's
    ``symbol`` and ``file`` still exist in the graph. Returns a staleness
    report, or ``None`` if the workflow can't be resolved.
    """
    from ..refs import file_exists as _file_exists, symbol_exists as _symbol_exists

    concept = _resolve(bundle, ref)
    if concept is None:
        return None

    steps = concept.extensions.get("steps", [])
    stale_details = []
    for step in steps:
        sym = step.get("symbol", "")
        file_ = step.get("file", "")
        # Skip the truncation-notice pseudo-step.
        if not sym or sym.startswith("("):
            continue
        sym_ok = _symbol_exists(conn, sym) if sym else True
        file_ok = _file_exists(conn, file_) if file_ else True
        if not sym_ok or not file_ok:
            stale_details.append({
                "step": step.get("name", "?"),
                "symbol": sym,
                "file": file_,
                "symbol_ok": sym_ok,
                "file_ok": file_ok,
            })

    return {
        "concept_id": concept.concept_id,
        "title": concept.title or "",
        "resource": concept.resource or "",
        "total_steps": len(steps),
        "stale_count": len(stale_details),
        "stale_details": stale_details,
    }


def check_all_workflows(
    conn: sqlite3.Connection,
    bundle: OKFBundle,
) -> List[dict]:
    """Check staleness of every workflow doc. Returns only stale ones, sorted
    by stale step count (most stale first)."""
    reports = []
    for concept in list_documents(bundle, doc_type=DOC_TYPE):
        ref = concept.title or concept.concept_id
        report = check_workflow_staleness(conn, bundle, ref)
        if report and report["stale_count"] > 0:
            reports.append(report)
    reports.sort(key=lambda r: -r["stale_count"])
    return reports


def sync_workflow(
    conn: sqlite3.Connection,
    bundle: OKFBundle,
    ref: str,
    max_steps: int = DEFAULT_FLOW_STEP_LIMIT,
) -> Optional[dict]:
    """Re-trace a workflow's flow and rebuild its steps from the current graph.

    Re-traces via :func:`trace_flow` using the workflow's ``resource`` field as
    the entry point, rebuilds the steps via :func:`flow_to_workflow`, and writes
    the updated concept (only ``steps`` and ``body`` change). Returns a sync
    report, or ``None`` if the workflow can't be resolved.
    """
    from ..compass.generator import _gather_flow_facts
    from ..okf.concept import OKFConcept

    concept = _resolve(bundle, ref)
    if concept is None:
        return None

    resource = concept.resource or ""
    old_steps = concept.extensions.get("steps", [])
    old_names = {s.get("name", "") for s in old_steps}

    # Re-trace from the current graph.
    facts = _gather_flow_facts(conn, resource)
    if facts["total_steps"] <= 1:
        return {
            "concept_id": concept.concept_id,
            "title": concept.title or "",
            "resource": resource,
            "old_step_count": len(old_steps),
            "new_step_count": 0,
            "added": [],
            "removed": list(old_names),
            "error": f"Entry symbol '{resource}' no longer traces — it may have been renamed or removed. Workflow left unchanged.",
        }

    new_steps = flow_to_workflow(facts, max_steps=max_steps)
    new_names = {s.get("name", "") for s in new_steps}
    added = sorted(new_names - old_names)
    removed = sorted(old_names - new_names)

    # Preserve extensions except steps; re-render body from new steps.
    ext = dict(concept.extensions)
    ext["steps"] = new_steps
    new_body = render_steps_body(concept.title or resource, new_steps)

    # Write via add_document to reuse the store's atomic write + lifecycle.
    # We pass the existing concept_id so it overwrites in place.
    updated = OKFConcept(
        type=concept.type,
        title=concept.title,
        description=concept.description,
        resource=concept.resource,
        tags=concept.tags,
        concept_id=concept.concept_id,
        body=new_body,
        extensions=ext,
    )
    bundle.write_concept(updated)

    return {
        "concept_id": concept.concept_id,
        "title": concept.title or "",
        "resource": resource,
        "old_step_count": len(old_steps),
        "new_step_count": len(new_steps),
        "added": added,
        "removed": removed,
        "error": None,
    }
