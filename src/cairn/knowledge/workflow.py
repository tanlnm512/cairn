"""Procedural workflow knowledge -- ordered, queryable step sequences."""
from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    import sqlite3

from .store import (
    _redact_step_descriptions,
    add_document,
    get_document,
    list_documents,
    slugify,
)
from ..memory.privacy import strip_private_data
from ..okf.bundle import OKFBundle
from ..okf.concept import OKFConcept

DOC_TYPE = "workflow"


def render_steps_body(title: str, steps: List[dict]) -> str:
    """Render the authoritative structured steps as a markdown body."""
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
    """Store one nonempty workflow and return its concept id."""
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
    """Resolve a workflow and return its status and structured steps."""
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


# Cap on how many chain nodes become workflow steps.
DEFAULT_FLOW_STEP_LIMIT = 20


def flow_to_workflow(
    facts: dict,
    max_steps: int = DEFAULT_FLOW_STEP_LIMIT,
) -> List[dict]:
    """Convert flow facts into bounded annotated workflow steps."""
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
    """Return a staleness report for a workflow's graph anchors."""
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
    """Re-trace a workflow and replace only its steps and rendered body."""
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
    new_steps = _redact_step_descriptions(new_steps)
    ext["steps"] = new_steps
    # Privacy floor: this sync path bypasses the add_document chokepoint, so
    # apply the same redaction it would enforce. Steps are graph-derived;
    # title/resource and the rendered body are free text.
    new_body = strip_private_data(
        render_steps_body(concept.title or resource, new_steps)
    )

    # Write via bundle.write_concept (not add_document, which derives a fresh
    # concept_id from the title): the existing concept_id overwrites in place.
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
