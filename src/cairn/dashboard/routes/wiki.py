"""Wiki listing and page-detail routes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response

from . import DashboardContext


def _wiki_not_found() -> Response:
    from starlette.responses import HTMLResponse

    return HTMLResponse(
        "<html><head><title>cairn dashboard</title></head><body>"
        "<h1>Wiki page not found</h1>"
        "<p>No rendered article exists for this page id.</p>"
        '<p><a href="/wiki">Back to the wiki</a></p>'
        "</body></html>",
        status_code=404,
    )


def _wiki(request: Request, context: DashboardContext) -> Response:
    # Catalog filters, read like every other view's params: absent or
    # blank means no filter; ``state`` outside the derived vocabulary
    # falls back to no filter (silent fallback, matching the
    # scope/window fallbacks).
    from ...wiki.lifecycle import DERIVED_STATES as PAGE_STATES
    from ..data import get_wiki_pages

    repo = request.query_params.get("repo", "").strip() or None
    state = request.query_params.get("state", "").strip() or None
    query = request.query_params.get("q", "").strip()
    _, selected_knowledge, store_key = context.resolve_selection(
        request, context.db_path, context.knowledge_dir
    )
    try:
        pages = get_wiki_pages(selected_knowledge, repo=repo)
    except ValueError as exc:
        # A malformed manifest renders the explicit unreadable state —
        # the CLI's clean error mirrored, never a 500. The context keeps
        # the catalog's shape (pages empty, manifest_error set) so both
        # templates branch on the error alone.
        page_context: dict = {
            "pages": [],
            "page_states": PAGE_STATES,
            "filters": {"repo": repo or "", "state": state or "", "q": query},
            "store_key": store_key,
            "manifest_error": str(exc),
        }
        if context.is_hx_request(request):
            return context.templates.TemplateResponse(
                request, "wiki_results.html", page_context
            )
        return context.render(request, "wiki.html", page_context)
    if state in PAGE_STATES:
        pages = [p for p in pages if p["state"] == state]
    if query:
        needle = query.lower()
        pages = [
            p
            for p in pages
            if needle in p["title"].lower()
            or needle in p["page_id"].lower()
            or needle in (p.get("description") or "").lower()
        ]
    page_context = {
        "pages": pages,
        "page_states": PAGE_STATES,
        "filters": {"repo": repo or "", "state": state or "", "q": query},
        "store_key": store_key,
    }
    if context.is_hx_request(request):
        # htmx fragment: the search input's results region only.
        return context.templates.TemplateResponse(
            request, "wiki_results.html", page_context
        )
    return context.render(request, "wiki.html", page_context)


def _wiki_page(request: Request, context: DashboardContext) -> Response:
    """Legacy one-segment URL: a permanent redirect to the repo-qualified
    canonical URL (bookmarks and recorded links keep working), or the
    same 404 as before when no readable concept matches."""
    from urllib.parse import quote

    from starlette.responses import RedirectResponse
    from ..data import get_wiki_page

    _, selected_knowledge, store_key = context.resolve_selection(
        request, context.db_path, context.knowledge_dir
    )
    try:
        page = get_wiki_page(selected_knowledge, request.path_params["page_id"])
    except ValueError as exc:
        return context.render(
            request,
            "wiki_unreadable.html",
            {"manifest_error": str(exc), "store_key": store_key},
        )
    if page is None:
        return _wiki_not_found()
    target = "/wiki/{}/{}".format(
        quote(page["repo"], safe=""), quote(page["page_id"], safe="")
    )
    if store_key:
        target += "?store=" + quote(store_key, safe="")
    return RedirectResponse(target, status_code=307)


def _wiki_page_repo(request: Request, context: DashboardContext) -> Response:
    # The canonical URL: repo-qualified, so multi-repo workspaces can
    # reach every page even when repos plan colliding page ids (every
    # repo plans an "overview"). prev/next walk the repo's promoted
    # pages in manifest (plan) order — non-promoted rows have no
    # readable concept, so linking to them would be a dead end.
    from ..data import get_wiki_page, get_wiki_pages

    _, selected_knowledge, store_key = context.resolve_selection(
        request, context.db_path, context.knowledge_dir
    )
    repo = request.path_params["repo"]
    page_id = request.path_params["page_id"]
    try:
        page = get_wiki_page(
            selected_knowledge, page_id, repo=repo, store_key=store_key
        )
        promoted = [
            p
            for p in get_wiki_pages(selected_knowledge, repo=repo)
            if p["promoted"]
        ]
    except ValueError as exc:
        return context.render(
            request,
            "wiki_unreadable.html",
            {"manifest_error": str(exc), "store_key": store_key},
        )
    if page is None:
        return _wiki_not_found()
    index = next(
        (i for i, p in enumerate(promoted) if p["page_id"] == page_id), None
    )
    prev_page = promoted[index - 1] if index not in (None, 0) else None
    next_page = (
        promoted[index + 1]
        if index is not None and index + 1 < len(promoted)
        else None
    )
    return context.render(
        request,
        "wiki_page.html",
        {
            "page": page,
            "prev": prev_page,
            # The renderer emits <pre class="mermaid"> for mermaid
            # fences; only those pages ship the client-side loader.
            "has_mermaid": 'class="mermaid"' in page["html"],
            "next": next_page,
            "store_key": store_key,
        },
    )

def register(routes: list[Any], context: DashboardContext) -> None:
    from starlette.routing import Route

    def wiki(request: Request) -> Response:
        return _wiki(request, context)

    def wiki_page(request: Request) -> Response:
        return _wiki_page(request, context)

    def wiki_page_repo(request: Request) -> Response:
        return _wiki_page_repo(request, context)

    routes.extend(
        [
            Route("/wiki", wiki, name="wiki"),
            # Repo-qualified canonical URL first; the one-segment legacy route
            # follows as a redirect (a single path param never matches two
            # segments, so the two never shadow each other).
            Route("/wiki/{repo}/{page_id}", wiki_page_repo, name="wiki_page_repo"),
            Route("/wiki/{page_id}", wiki_page, name="wiki_page"),
        ]
    )
