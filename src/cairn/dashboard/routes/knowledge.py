"""Knowledge catalog, document, and relationship-graph routes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response

from . import DashboardContext


def _knowledge_catalog(request: Request, context: DashboardContext) -> Response:

    """The knowledge catalog: every stored doc once, filterable by
    family/status/tag. Filters, read like every other view's params:
    absent or blank means no filter; a value outside the vocabulary
    the selects offer (the classifier's families + the doc statuses,
    each unioned with what this corpus actually contains) falls back
    to no filter (silent fallback, matching the tasks/memory/wiki
    filters). The options always offer every family/status the corpus
    contains, so a doc under a custom type stays reachable from the
    filter."""
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
        # The corpus values ride the result's pre-filter counts, so
        # the vocabulary is complete after the fetch; a fallback
        # re-reads with the reset value so the rows match the select
        # the page renders.
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
    """One doc's detail page at /knowledge/{family}/{slug} — the bare
    concept id's two variable segments. get_knowledge_doc_detail
    assembles identity, rendered body, the relationship panels
    (related docs, supersede chain, code refs) and ingest provenance;
    None = the plain not-found page, out-of-namespace resolutions
    included."""
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
    from ..data import get_knowledge_graph, get_read_only_db

    """The knowledge relationship canvas at /knowledge/graph: every
    stored doc as a node, every indexed relationship as a directed
    edge. The graph JSON rides the #knowledge-graph-data script tag
    (the /graph machinery's DataSet-from-script-tag pattern) and
    knowledge-graph.js owns the canvas: legend chips filter edge
    kinds/relations client-side (no reload, no refetch), and a node
    click fetches the doc's inspect fragment into the side panel.
    Full-page only — the filters live in the client, so there is no
    filter param for a fragment branch to serve."""
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
    from ..data import get_knowledge_graph_inspect, get_read_only_db

    """One doc's inspect-panel fragment (the canvas's node-click
    fetch target): identity plus the same grouped ``related_docs``
    rows the detail panels render. Inherently fragment-only — the
    canvas script is the only client, no UI links the bare path (the
    documented exception to the full-page/fragment seam) — and an
    unknown doc renders the panel's not-found note at 200, matching
    /graph/inspect's found=False contract."""
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
    from starlette.routing import Route

    def knowledge_catalog(request: Request) -> Response:
        return _knowledge_catalog(request, context)

    def knowledge_doc(request: Request) -> Response:
        return _knowledge_doc(request, context)

    def knowledge_graph(request: Request) -> Response:
        return _knowledge_graph(request, context)

    def knowledge_graph_inspect(request: Request) -> Response:
        return _knowledge_graph_inspect(request, context)

    routes.extend(
        [
            # The knowledge catalog; /knowledge/{family}/{slug} is the bare
            # concept id's two variable segments — a single path param never
            # matches "/", so a sibling one-segment route (/knowledge/graph)
            # can never shadow these and vice versa. The canvas + its inspect
            # fragment precede the two-param route on purpose: the fragment
            # path (/knowledge/graph/inspect) WOULD match it as
            # family=graph, slug=inspect if it followed.
            Route("/knowledge", knowledge_catalog, name="knowledge"),
            Route("/knowledge/graph", knowledge_graph, name="knowledge_graph"),
            Route(
                "/knowledge/graph/inspect",
                knowledge_graph_inspect,
                name="knowledge_graph_inspect",
            ),
            Route(
                "/knowledge/{family}/{slug}",
                knowledge_doc,
                name="knowledge_doc",
            ),
        ]
    )
