"""Graph, symbol lookup, palette, and graph-inspection routes."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response

from . import DashboardContext


def register(routes: list[Any], context: DashboardContext) -> None:
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    from ..data import (
        GRAPH_SCOPES,
        get_graph,
        get_read_only_db,
        symbol_candidates,
        symbol_suggest,
        inspect_symbol,
    )
    from ..shell import shell_context
    from ..workspaces import enumerate_stores
    from ... import paths
    from ...viz import query as viz_query

    db_path = context.db_path
    knowledge_dir = context.knowledge_dir
    render = context.render
    resolve_selection = context.resolve_selection
    templates = context.templates
    is_hx_request = context.is_hx_request

    def graph(request: Request) -> Response:
        scope = request.query_params.get("scope", "module")
        if scope not in GRAPH_SCOPES:
            scope = "module"
        focus = request.query_params.get("focus", "").strip() or None
        repo = request.query_params.get("repo", "").strip() or None
        depth_raw = request.query_params.get("depth", "").strip()
        depth = int(depth_raw) or None if depth_raw.isdigit() else None
        # Tests toggle: only an explicit opt-in includes test symbols in the
        # module scope; anything else falls back to the curated default,
        # matching the scope/layout fallback conventions.
        include_tests = request.query_params.get("tests", "") in ("1", "on", "true")
        # Layout choice: only "force" | "hier" are meaningful;
        # absent/bogus falls back to force, matching the scope fallback.
        layout = request.query_params.get("layout", "force")
        if layout not in ("force", "hier"):
            layout = "force"
        selected_db, _, store_key = resolve_selection(
            request, db_path, knowledge_dir
        )
        conn = get_read_only_db(selected_db)
        try:
            graph_data = get_graph(
                conn, scope=scope, focus=focus, repo=repo, depth=depth,
                include_tests=include_tests,
            )
        finally:
            conn.close()
        return render(
            request,
            "graph.html",
            {
                "graph": graph_data,
                "scopes": GRAPH_SCOPES,
                "scope": scope,
                "focus": focus or "",
                "repo": repo or "",
                "depth": depth_raw if depth is not None else "",
                "layout": layout,
                "include_tests": include_tests,
                "store_key": store_key,
            },
        )

    def graph_candidates(request: Request) -> Response:
        name = request.query_params.get("name", "").strip()
        selected_db, _, _ = resolve_selection(request, db_path, knowledge_dir)
        conn = get_read_only_db(selected_db)
        try:
            result = symbol_candidates(conn, name)
        finally:
            conn.close()
        return JSONResponse(result)

    def graph_suggest(request: Request) -> Response:
        prefix = request.query_params.get("name", "").strip()
        selected_db, _, _ = resolve_selection(request, db_path, knowledge_dir)
        conn = get_read_only_db(selected_db)
        try:
            result = symbol_suggest(conn, prefix)
        finally:
            conn.close()
        return JSONResponse(result)

    def palette_results(request: Request) -> Response:
        """The command palette's filtered rows as an HTML fragment (the
        palette input's ``hx-get`` target, swapped into the listbox).

        ``q`` filters the palette's two seed sources the way the old
        client-side filter did — case-insensitive substring over view and
        workspace labels, composed from the same shell_context the seed
        JSON rides — and from two characters up merges ``symbol_suggest``
        (the /graph/suggest data function, same 8-row cap and truncation
        notice) after them, each symbol row linking into /graph's symbol
        scope with the focus (and selected store) params. Rows carry
        their action as a data attribute; this route decides content, the
        palette component decides focus and activation."""
        query = request.query_params.get("q", "").strip()
        selected_db, _, store_key = resolve_selection(
            request, db_path, knowledge_dir
        )
        palette = shell_context(
            enumerate_stores(Path(paths.CAIRN_HOME)), store_key, "/",
            launch_db=db_path,
        )["palette"]
        lowered = query.lower()
        rows = [
            {"label": view["label"], "hint": "view", "href": view["href"]}
            for view in palette["views"]
            if not lowered or lowered in view["label"].lower()
        ]
        rows += [
            {
                "label": workspace["label"],
                "hint": "workspace",
                "store_key": workspace["key"],
            }
            for workspace in palette["workspaces"]
            if not lowered or lowered in (workspace["label"] or "").lower()
        ]
        if len(query) >= 2:
            conn = get_read_only_db(selected_db)
            try:
                suggested = symbol_suggest(conn, query)
            finally:
                conn.close()
            for match in suggested["matches"][:8]:
                href = "/graph?scope=symbol&focus=" + quote(match["name"], safe="")
                if store_key:
                    href += "&store=" + quote(store_key, safe="")
                hint = match["kind"] or ""
                if match.get("file"):
                    hint += " — " + match["file"]
                rows.append({"label": match["name"], "hint": hint, "href": href})
            if suggested["truncated"]:
                rows.append(
                    {"label": "more matches…", "hint": "keep typing to narrow"}
                )
        return templates.TemplateResponse(
            request, "palette_results.html", {"rows": rows}
        )

    def graph_neighbors(request: Request) -> Response:
        # Repeatable ``name`` param: strip each, drop empties,
        # dedupe preserving first-seen order (dict.fromkeys is ordered).
        # An empty list after cleaning hits the function's empty contract
        # (200 with empty nodes), never an error.
        names = [
            name
            for name in dict.fromkeys(
                raw.strip() for raw in request.query_params.getlist("name")
            )
            if name
        ]
        # Absent/bogus depth -> function default (silent fallback, matching
        # the graph handler); a valid one is clamped to >= 1.
        depth_raw = request.query_params.get("depth", "").strip()
        depth_kwargs = (
            {"depth": max(1, int(depth_raw))} if depth_raw.isdigit() else {}
        )
        selected_db, _, _ = resolve_selection(request, db_path, knowledge_dir)
        conn = get_read_only_db(selected_db)
        try:
            result = viz_query.get_symbol_neighbors(conn, names, **depth_kwargs)
        finally:
            conn.close()
        return JSONResponse(result)

    def graph_inspect(request: Request) -> Response:
        # Side-panel payload for one symbol (identity + callers + callees +
        # impact with affected tests). Missing/blank names hit the data
        # function's not-found contract (200 with found=False), never an
        # error — the panel just stays empty. The canvas node-click fetch
        # rides htmx (HX-Request header) and gets the server-rendered panel
        # fragment; other callers keep the JSON payload.
        name = request.query_params.get("name", "")
        selected_db, _, store_key = resolve_selection(request, db_path, knowledge_dir)
        conn = get_read_only_db(selected_db)
        try:
            result = inspect_symbol(conn, name)
        finally:
            conn.close()
        if is_hx_request(request):
            context = dict(result)
            context["store_key"] = store_key
            return templates.TemplateResponse(
                request, "graph_inspect_panel.html", context
            )
        return JSONResponse(result)

    routes.extend(
        [
            Route("/graph", graph, name="graph"),
            Route("/graph/candidates", graph_candidates, name="graph_candidates"),
            Route("/graph/suggest", graph_suggest, name="graph_suggest"),
            Route("/graph/neighbors", graph_neighbors, name="graph_neighbors"),
            Route("/graph/inspect", graph_inspect, name="graph_inspect"),
            # The command palette's filtered-rows fragment (the input's
            # hx-get target); inherently fragment-only, like the JSON routes
            # above.
            Route("/palette/results", palette_results, name="palette_results"),
        ]
    )
