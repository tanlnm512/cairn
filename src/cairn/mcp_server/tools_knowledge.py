"""L5 knowledge MCP tools: knowledge_add, knowledge_search, knowledge_delete, knowledge_status, trace_workflow."""
from __future__ import annotations

import logging

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
                # cross_repo_deps returns {dependencies: [{repo, ...}], dependents:
                # [...]}; extract the repo names for the enrichment line.
                if isinstance(deps, dict) and deps.get("dependencies"):
                    dep_repos = [d["repo"] for d in deps["dependencies"] if d.get("repo")]
                    if dep_repos:
                        out.append(f"    {repo} → depends on: {', '.join(dep_repos)}")
    return "\n".join(out)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True))
@instrument
def knowledge_delete(doc_id: str) -> str:
    """Delete a knowledge document and its embedding rows. Irreversible."""
    from cairn.knowledge.store import (
        _refuse_out_of_namespace,
        delete_document,
        get_document,
    )

    bundle = _bundle()
    try:
        _refuse_out_of_namespace(bundle, doc_id, get_document(bundle, doc_id))
    except ValueError as exc:
        logger.warning("knowledge_delete refused out-of-namespace target: %r", doc_id)
        return str(exc)
    conn = _rw_conn()
    try:
        ok = delete_document(bundle, doc_id, conn=conn)
        conn.commit()
    finally:
        conn.close()
    if not ok:
        return f"Knowledge document not found: '{doc_id}'."
    logger.warning("knowledge_delete: deleted knowledge doc_id=%r", doc_id)
    return f"Deleted knowledge document: '{doc_id}'."


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False))
@instrument
def knowledge_status(doc_id: str, new_status: str) -> str:
    """Update doc_status on a knowledge document (active → superseded → archived); a write, not idempotent."""
    from cairn.knowledge.store import _refuse_out_of_namespace, get_document, update_status

    bundle = _bundle()
    try:
        _refuse_out_of_namespace(bundle, doc_id, get_document(bundle, doc_id))
    except ValueError as exc:
        logger.warning("knowledge_status refused out-of-namespace target: %r", doc_id)
        return str(exc)
    ok = update_status(bundle, doc_id, new_status)
    if not ok:
        return f"Knowledge document not found: '{doc_id}'."
    return f"Updated '{doc_id}' status -> {new_status}."


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True))
@instrument
def trace_workflow(ref: str) -> str:
    """Trace a workflow's ordered steps by title/slug/concept_id; steps carry symbol/file pointers into the graph."""
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
