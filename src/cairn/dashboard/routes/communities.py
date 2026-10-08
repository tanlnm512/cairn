"""Subsystem-community routes over the persisted Louvain partition."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response

from . import DashboardContext


def register(routes: list[Any], context: DashboardContext) -> None:
    from starlette.routing import Route

    from ..data import get_communities, get_community_members, get_read_only_db

    db_path = context.db_path
    knowledge_dir = context.knowledge_dir
    render = context.render
    resolve_selection = context.resolve_selection
    templates = context.templates
    is_hx_request = context.is_hx_request

    def communities(request: Request) -> Response:
        # An HX drill-down renders only the member region and skips the listing query.
        raw = request.query_params.get("community", "").strip()
        community_id = int(raw) if raw.isdigit() else None
        fragment = is_hx_request(request)
        selected_db, _, store_key = resolve_selection(
            request, db_path, knowledge_dir
        )
        conn = get_read_only_db(selected_db)
        try:
            listing = [] if fragment else get_communities(conn)
            selected = (
                get_community_members(conn, community_id)
                if community_id is not None
                else None
            )
        finally:
            conn.close()
        template_context = {
            "communities": listing,
            "selected": selected,
            "community_id": community_id,
            "store_key": store_key,
        }
        if fragment:
            template_context["fragment"] = True
            return templates.TemplateResponse(
                request, "communities.html", template_context
            )
        return render(request, "communities.html", template_context)

    routes.extend(
        [Route("/communities", communities, name="communities")]
    )
