"""L5 knowledge MCP tools: knowledge_add, knowledge_search, knowledge_delete,
knowledge_status, trace_workflow.

Each builds an OKFBundle via the shared ``_bundle()`` helper.
"""
from __future__ import annotations

import logging
from pathlib import Path

from mcp.types import ToolAnnotations

from ._server_core import _bundle, _conn, _rw_conn, mcp
from .metric_buffering import instrument
from .tools_graph import _clamp

logger = logging.getLogger(__name__)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False))
@instrument
def knowledge_add(
    title: str,
    body: str,
    doc_type: str,
    tags: str = "",               # comma-separated (MCP tools take primitives)
    affects_modules: str = "",    # comma-separated
    affects_repos: str = "",      # comma-separated
    resource: str = "",
    epic_link: str = "",
) -> str:
    """Ingest a business document as knowledge. The PO's ingestion path.
    doc_type: business-rule | spec | decision. Tags and affects_* are
    comma-separated. Returns the concept_id on success."""
    from cairn.knowledge.store import add_document
    from cairn.paths import resolve_store

    # Ensure the knowledge dir exists.
    resolve_store().ensure()
    bundle = _bundle()

    def _split(s):
        return [x.strip() for x in s.split(",") if x.strip()]

    cid = add_document(
        bundle, title=title, body=body, doc_type=doc_type,
        tags=_split(tags), affects_modules=_split(affects_modules),
        affects_repos=_split(affects_repos), resource=resource or None,
        epic_link=epic_link or None,
    )
    return f"Stored knowledge document: {cid}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True))
@instrument
def knowledge_search(query: str, limit: int = 20) -> str:
    """Search business knowledge docs by meaning. Bridges to the code graph:
    documents with affects_repos get cross_repo_deps results appended.
    Lexical search works without the semantic extra; semantic adds recall."""
    from cairn.knowledge.search import search_knowledge

    limit = _clamp(limit, 1, 1000)  # bound LLM-supplied value at the boundary
    bundle = _bundle()
    conn = _conn()
    try:
        results = search_knowledge(conn, bundle, query, limit=limit)
    finally:
        conn.close()

    if not results:
        return (
            f"No knowledge documents matching '{query}'. The PO ingestion path "
            f"(knowledge_add) may not have indexed docs for this topic, or the "
            f"corpus isn't embedded yet (run `cairn knowledge embed`). Try broader "
            f"terms, or search_knowledge(query, ...) for the knowledge layer vs "
            f"this business-docs layer."
        )

    out = [f'=== knowledge_search: "{query}" ({len(results)} doc(s)) ===']
    for r in results:
        prov = r["provenance"]
        score = f" {r['score']:.2f}" if r["score"] < 1.0 else ""
        out.append(f"  [{prov}{score}] {r['title']}  ({r['doc_type']})")
        if r.get("affects_repos"):
            out.append(f"    affects_repos: {', '.join(r['affects_repos'])}")
        if r.get("graph_deps"):
            for repo, deps in r["graph_deps"].items():
                if isinstance(deps, dict) and deps.get("depends_on"):
                    out.append(f"    {repo} → depends on: {', '.join(deps['depends_on'])}")
    return "\n".join(out)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True))
@instrument
def knowledge_delete(doc_id: str) -> str:
    """Delete a knowledge document and its embedding rows. Irreversible."""
    from cairn.knowledge.store import delete_document
    from cairn.knowledge.store import get_document

    bundle = _bundle()
    # Scope check: confirm the resolved concept_id stays inside the knowledge/
    # namespace before deleting, so an LLM client can't point this destructive
    # tool at a compass/wiki/memory doc via a crafted doc_id.
    concept = get_document(bundle, doc_id)
    if concept is not None:
        # read_concept -> _validate_concept_path -> OKFConcept.from_file sets a
        # *resolved* absolute concept_id; normalize to bundle-relative before the
        # namespace prefix check (resolve both sides so symlinked /var vs
        # /private/var roots don't trip relative_to).
        resolved = concept.concept_id
        try:
            resolved = str(Path(resolved).resolve().relative_to(Path(bundle.root).resolve()))
        except ValueError:
            pass
        if not (resolved == "knowledge/" or resolved.startswith("knowledge/")):
            logger.warning(
                "knowledge_delete refused out-of-namespace target: requested=%r resolved=%r",
                doc_id, resolved,
            )
            return (
                f"Refused: '{doc_id}' resolves outside the knowledge/ namespace "
                f"(resolved to '{resolved}'). knowledge_delete only removes knowledge docs."
            )
    conn = _rw_conn()
    try:
        ok = delete_document(bundle, doc_id, conn=conn)
    finally:
        conn.close()
    if not ok:
        return f"Knowledge document not found: '{doc_id}'."
    logger.warning("knowledge_delete: deleted knowledge concept_id=%r", concept.concept_id if concept else doc_id)
    return f"Deleted knowledge document: '{doc_id}'."


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True))
@instrument
def knowledge_status(doc_id: str, new_status: str) -> str:
    """Update doc_status on a knowledge document (active → superseded → archived)."""
    from cairn.knowledge.store import update_status

    bundle = _bundle()
    ok = update_status(bundle, doc_id, new_status)
    if not ok:
        return f"Knowledge document not found: '{doc_id}'."
    return f"Updated '{doc_id}' status -> {new_status}."


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True))
@instrument
def trace_workflow(ref: str) -> str:
    """Trace a procedural workflow's ordered steps by title, slug, or
    concept_id. The direct analog of LeanKG's kg_trace_workflow, built on
    top of the knowledge-docs layer (a workflow is a knowledge doc with
    doc_type="workflow") rather than a separate live-synced ontology store.
    Each step may carry a symbol/file — follow those into find_definition/
    get_callers to jump from the procedure into the actual code."""
    from cairn.knowledge.workflow import trace_workflow as _trace

    bundle = _bundle()
    result = _trace(bundle, ref)
    if result is None:
        return (
            f"No workflow found matching '{ref}'. Try knowledge_search('{ref}') "
            "or list workflows via `cairn knowledge list --type workflow`."
        )

    status = result["doc_status"]
    status_note = f" [{status}]" if status != "active" else ""
    out = [f"{result['title']}{status_note} ({result['concept_id']})"]
    for i, step in enumerate(result["steps"], start=1):
        out.append(f"  {i}. {step.get('name', f'Step {i}')}")
        if step.get("description"):
            out.append(f"     {step['description']}")
        if step.get("symbol"):
            out.append(f"     symbol: {step['symbol']}")
        if step.get("file"):
            out.append(f"     file: {step['file']}")
    return "\n".join(out)
