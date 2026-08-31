"""L5 wiki MCP tool: wiki_generate.

Thin boundary over the wiki pipeline's public entry point
(``cairn.wiki.pipeline.run_wiki_generate``): queue the wiki-page tasks for a
repo's plan, or (with ``refine_catalog``) the wiki-catalog refinement task
that precedes them.
"""
from __future__ import annotations

from mcp.types import ToolAnnotations

from ._server_core import _bundle, _conn, mcp
from .metric_buffering import instrument
from .tools_graph import _clamp


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False))
@instrument
def wiki_generate(
    repo: str,
    pages: int = 10,
    refine_catalog: bool = False,
    diagrams: bool = False,
    force: bool = False,
) -> str:
    """Plan the deterministic wiki for a repo and queue one wiki-page task per
    unskipped page. With refine_catalog, queues a wiki-catalog refinement task
    instead — re-run after completing it to queue the page tasks from the
    validated refined outline. Returns the page plan plus the queued task ids;
    claim those tasks through the LLM task queue (promotion is critic-gated on
    completion)."""
    from cairn.wiki.catalog import WikiPlannerError
    from cairn.wiki.pipeline import run_wiki_generate

    pages = _clamp(pages, 1, 50)  # bound LLM-supplied value at the boundary
    conn = _conn()
    try:
        try:
            result = run_wiki_generate(
                conn, _bundle(), repo, pages_cap=pages, force=force,
                diagrams=diagrams, refine_catalog=refine_catalog,
            )
        except WikiPlannerError as exc:
            return f"wiki_generate: {exc} Nothing was queued."
    finally:
        conn.close()

    plan = result["plan"]
    queued_ids = result["queued_task_ids"]
    catalog_task_id = result.get("catalog_task_id")
    out = [
        f"=== wiki_generate: {repo} ({len(plan)} page(s) planned, "
        f"{len(queued_ids)} task(s) queued) ==="
    ]
    for i, page in enumerate(plan, start=1):
        out.append(f"  {i}. {page['page_id']}: {page['title']} [module {page['module']}]")
    if queued_ids:
        out.append(
            "Queued wiki-page task ids (claim via the LLM task queue): "
            + ", ".join(queued_ids)
        )
    if catalog_task_id is not None:
        state = "Already pending" if result.get("catalog_pending") else "Queued"
        out.append(
            f"{state} wiki-catalog task {catalog_task_id} — page tasks are only "
            "queued from the validated refined outline. Re-run wiki_generate "
            "after completing the catalog task."
        )
    elif not queued_ids:
        out.append(
            "No new tasks queued — every planned page is already promoted and "
            "unchanged (pass force=True to re-queue)."
        )
    return "\n".join(out)
