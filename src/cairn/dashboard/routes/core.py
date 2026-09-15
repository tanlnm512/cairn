"""Core landing, workspace, project, and health routes."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response

from . import DashboardContext


def register(
    routes: list[Any], context: DashboardContext, *, section: str = "primary"
) -> None:
    from starlette.responses import Response
    from starlette.routing import Route

    from ..data import get_health, get_read_only_db, list_projects
    from ..workspaces import enumerate_stores, probe_stores
    from ... import paths

    db_path = context.db_path
    knowledge_dir = context.knowledge_dir
    render = context.render
    resolve_selection = context.resolve_selection
    static_dir = context.static_dir

    async def landing(request: Request) -> Response:
        _, _, store_key = resolve_selection(request, db_path, knowledge_dir)
        return render(
            request,
            "index.html",
            {"db_path": db_path or "central store", "store_key": store_key},
        )


    def favicon(request: Request) -> Response:
        """The shell's icon, served at the conventional /favicon.ico path
        browsers probe when no <link rel="icon"> matched — a 200 here
        keeps the icon off the network log's error column. Same file the
        shell links; reread per request so a swapped icon needs no
        restart. A missing file is a plain 404, not a 500."""
        try:
            body = (static_dir / "favicon.svg").read_bytes()
        except FileNotFoundError:
            return Response("Not Found", status_code=404)
        return Response(body, media_type="image/svg+xml")


    def workspaces_overview(request: Request) -> Response:
        _, _, store_key = resolve_selection(request, db_path, knowledge_dir)
        home = Path(paths.CAIRN_HOME)
        rows = probe_stores(home, enumerate_stores(home))
        return render(
            request,
            "workspaces.html",
            {"stores": rows, "launch_db": db_path or "", "store_key": store_key},
        )

    def projects(request: Request) -> Response:
        selected_db, _, store_key = resolve_selection(
            request, db_path, knowledge_dir
        )
        conn = get_read_only_db(selected_db)
        try:
            rows = list_projects(conn)
        finally:
            conn.close()
        return render(
            request,
            "projects.html",
            {"projects": rows, "store_key": store_key},
        )

    def health(request: Request) -> Response:
        selected_db, _, store_key = resolve_selection(
            request, db_path, knowledge_dir
        )
        conn = get_read_only_db(selected_db)
        try:
            health_data = get_health(conn, selected_db)
        finally:
            conn.close()
        return render(
            request,
            "health.html",
            {
                "health": health_data,
                "db_path": selected_db or "central store",
                "store_key": store_key,
            },
        )

    if section == "health":
        routes.append(Route("/health", health, name="health"))
        return
    routes.extend(
        [
            Route("/", landing, name="index"),
            Route("/favicon.ico", favicon, name="favicon"),
            Route("/workspaces", workspaces_overview, name="workspaces"),
            Route("/projects", projects, name="projects"),
        ]
    )
