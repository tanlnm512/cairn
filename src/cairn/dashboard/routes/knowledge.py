"""Knowledge catalog, document, and relationship-graph routes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response

from . import DashboardContext


def _knowledge_catalog(request: Request, context: DashboardContext) -> Response:

    """Render the knowledge catalog with corpus-complete filters and silent fallbacks."""
    from ..app import KNOWLEDGE_FAMILIES
    from ..data import get_read_only_db, list_knowledge_docs
    from ...knowledge.store import DOC_STATUSES

    family_param = request.query_params.get("family", "all").strip() or "all"
    status_param = request.query_params.get("status", "all").strip() or "all"
    tag = request.query_params.get("tag", "").strip()
    selected_db, selected_knowledge, store_key = context.resolve_selection(
        request, context.db_path, context.knowledge_dir
    )
    conn = get_read_only_db(selected_db)
    try:
        result = list_knowledge_docs(
            conn,
            selected_knowledge,
            family=None if family_param == "all" else family_param,
            status=None if status_param == "all" else status_param,
            tag=tag or None,
        )
        # Corpus counts complete the options; fallback refetches rows to match selection.
        family = family_param
        if family != "all" and family not in (
            set(KNOWLEDGE_FAMILIES) | set(result["families"])
        ):
            family = "all"
        status = status_param
        if status != "all" and status not in (
            set(DOC_STATUSES) | set(result["statuses"])
        ):
            status = "all"
        if (family, status) != (family_param, status_param):
            result = list_knowledge_docs(
                conn,
                selected_knowledge,
                family=None if family == "all" else family,
                status=None if status == "all" else status,
                tag=tag or None,
            )
    finally:
        conn.close()
    page_context = {
        "docs": result["docs"],
        "families": result["families"],
        "statuses": result["statuses"],
        "total": result["total"],
        "family": family,
        "status": status,
        "tag": tag,
        "family_options": sorted(
            set(KNOWLEDGE_FAMILIES) | set(result["families"])
        ),
        "status_options": sorted(set(DOC_STATUSES) | set(result["statuses"])),
        "store_key": store_key,
    }
    if context.is_hx_request(request):
        # htmx fragment: the filter form's results region only.
        return context.templates.TemplateResponse(
            request, "knowledge_results.html", page_context
        )
    return context.render(request, "knowledge.html", page_context)


def _knowledge_doc(request: Request, context: DashboardContext) -> Response:
    """Render one knowledge doc detail or the plain not-found page."""
    from starlette.responses import HTMLResponse
    from ..data import get_knowledge_doc_detail, get_read_only_db
    from ... import paths

    doc_id = "knowledge/{family}/{slug}".format(
        family=request.path_params["family"],
        slug=request.path_params["slug"],
    )
    selected_db, selected_knowledge, store_key = context.resolve_selection(
        request, context.db_path, context.knowledge_dir
    )
    # The staged-ingest manifest lives under the workspace root (the
    # outbox is a workspace artifact, not a store one).
    try:
        workspace = str(paths.resolve_workspace())
    except OSError:
        workspace = None
    conn = get_read_only_db(selected_db)
    try:
        doc = get_knowledge_doc_detail(
            conn,
            selected_knowledge,
            doc_id,
            workspace=workspace,
            store_key=store_key,
        )
    finally:
        conn.close()
    if doc is None:
        return HTMLResponse(
            "<html><head><title>cairn dashboard</title></head><body>"
            "<h1>Knowledge document not found</h1>"
            "<p>No knowledge document exists at this id.</p>"
            '<p><a href="/knowledge">Back to the catalog</a></p>'
            "</body></html>",
            status_code=404,
        )
    return context.render(
        request,
        "knowledge_doc.html",
        {"doc": doc, "store_key": store_key},
    )


def _knowledge_graph(request: Request, context: DashboardContext) -> Response:
    """Render the client-owned knowledge graph canvas and its embedded JSON."""
    from ..data import get_knowledge_graph, get_read_only_db

    selected_db, selected_knowledge, store_key = context.resolve_selection(
        request, context.db_path, context.knowledge_dir
    )
    conn = get_read_only_db(selected_db)
    try:
        graph = get_knowledge_graph(conn, selected_knowledge)
    finally:
        conn.close()
    return context.render(
        request,
        "knowledge_graph.html",
        {"graph": graph, "store_key": store_key},
    )


def _knowledge_graph_inspect(request: Request, context: DashboardContext) -> Response:
    """Render the canvas node-click inspect fragment; unknown docs stay 200."""
    from ..data import get_knowledge_graph_inspect, get_read_only_db

    doc_id = request.query_params.get("doc", "").strip()
    selected_db, selected_knowledge, store_key = context.resolve_selection(
        request, context.db_path, context.knowledge_dir
    )
    conn = get_read_only_db(selected_db)
    try:
        doc = (
            get_knowledge_graph_inspect(conn, selected_knowledge, doc_id)
            if doc_id
            else None
        )
    finally:
        conn.close()
    return context.templates.TemplateResponse(
        request,
        "knowledge_graph_panel.html",
        {"doc": doc, "doc_id": doc_id, "store_key": store_key},
    )

def register(routes: list[Any], context: DashboardContext) -> None:
    from functools import partial

    from starlette.routing import Route

    routes.extend(
        [
            # Fixed graph and inspect routes must precede the two-segment doc catchall.
            Route(
                "/knowledge",
                partial(_knowledge_catalog, context=context),
                name="knowledge",
            ),
            Route(
                "/knowledge/graph",
                partial(_knowledge_graph, context=context),
                name="knowledge_graph",
            ),
            Route(
                "/knowledge/graph/inspect",
                partial(_knowledge_graph_inspect, context=context),
                name="knowledge_graph_inspect",
            ),
            Route(
                "/knowledge/{family}/{slug}",
                partial(_knowledge_doc, context=context),
                name="knowledge_doc",
            ),
        ]
    )
