"""Starlette app factory for the read-only dashboard (FR-001, FR-010).

Routes: landing, workspaces overview, projects, graph (plus its
/graph/candidates symbol-search and /graph/neighbors node-expansion JSON),
history, tokens (plus their .csv/.json exports), chains, health, memory,
tasks.

starlette / jinja2 arrive only as transitive deps of mcp, so they are
imported inside :func:`create_app`: importing this module (or the package)
must never load the server stack. The factory takes the DB path as a
parameter — the CLI command constructs the app, never the other way round.
"""
from __future__ import annotations

import csv
import io
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import Response

_PACKAGE_DIR = Path(__file__).resolve().parent

# Loopback only — never 0.0.0.0 (the dashboard is a single-user local tool).
# 8765 is distinct from the SSE daemon's 9876 so the two never collide.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

# llm.tasks Task.status vocabulary — the /tasks filter's allowed values.
TASK_STATUSES = ("pending", "in-progress", "done", "failed")

# Traffic-view time-window presets (FR-002) — the ``window`` param's
# allowed values; "all" is the unbounded default.
WINDOW_PRESETS = ("24h", "7d", "30d", "all")

# Preset -> seconds back from now; "all" is absent (unbounded).
_WINDOW_SECONDS = {"24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}


def _human_size(num_bytes) -> str:
    """Byte count as B / KiB / MiB / GiB / TiB, one decimal above 1024."""
    size = float(num_bytes or 0)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024:
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"


def _human_ts(value) -> str:
    """``time.time()`` epoch float as a UTC wall-clock string."""
    try:
        dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return "—"
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def _human_duration(ms) -> str:
    """Milliseconds as ``ms`` below one second, ``s`` above (one decimal)."""
    if ms is None:
        return "—"
    elapsed = float(ms)
    if elapsed < 1000:
        return f"{elapsed:.0f} ms"
    return f"{elapsed / 1000:.1f} s"


def _est_tokens(row: dict) -> str:
    """Per-call request/response token estimates (US4-AC2); ``unknown``
    when the row predates payload-size recording (NULL sizes)."""
    req = row.get("est_req_tokens")
    resp = row.get("est_resp_tokens")
    if req is None and resp is None:
        return "unknown"
    return f"~{req if req is not None else '—'} / ~{resp if resp is not None else '—'}"


def _human_span(seconds) -> str:
    """Elapsed seconds as ``Ns`` / ``Nm`` / ``Nh Mm`` (chain spans)."""
    secs = max(int(seconds or 0), 0)
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    return f"{secs // 3600}h {secs % 3600 // 60}m"


def _rel_offset(ts, base) -> str:
    """Seconds of ``ts`` after ``base`` as ``+Ns`` / ``+Nm`` / ``+Nh Mm``;
    ``—`` when either timestamp is unknown."""
    if ts is None or base is None:
        return "—"
    return "+" + _human_span(float(ts) - float(base))


def _fmt_mean(value) -> str:
    """Mean tokens as a whole number when exact, one decimal otherwise."""
    num = float(value or 0)
    return f"{int(num)}" if num == int(num) else f"{num:.1f}"


def _resolve_window(window: str | None) -> tuple[str, float | None]:
    """Validated ``window`` param plus its ``since`` epoch cutoff (FR-002).

    Unknown values silently fall back to ``"all"``, matching the graph
    handler's scope fallback; ``since`` is None (unbounded) for ``"all"``.
    """
    preset = window if window in WINDOW_PRESETS else "all"
    seconds = _WINDOW_SECONDS.get(preset)
    return preset, time.time() - seconds if seconds is not None else None


# Exports fetch the filtered set in one unpaginated call (FR-005): a single
# list_history page large enough to cover it — never a cursor-following
# duplicate of the view's paging.
_EXPORT_ROW_LIMIT = 1_000_000

# Content-Disposition filename alphabet: anything else collapses to ``_``.
_FILENAME_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _export_filename(view: str, ext: str, hints) -> str:
    """Attachment filename: view name plus the active filter hints
    (``history-tool-x-window-7d.csv``), header-safe by construction."""
    parts = [view, *(f"{key}-{value}" for key, value in hints if value)]
    return _FILENAME_UNSAFE.sub("_", "-".join(parts))[:120] + f".{ext}"


def _csv_text(rows) -> str:
    """Row dicts as RFC 4180 CSV text (the csv module quotes as needed);
    None renders as an empty field, the CSV reading of JSON's null."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    if rows:
        writer.writerow(rows[0].keys())
        writer.writerows(
            ["" if value is None else value for value in row.values()]
            for row in rows
        )
    return buf.getvalue()


def create_app(
    db_path: str | None = None, knowledge_dir: str | None = None
) -> Starlette:
    """Build the dashboard app over a read-only connection to ``db_path``.

    ``db_path=None`` defers store resolution to the data layer;
    ``knowledge_dir=None`` defers to the workspace's default knowledge dir
    (resolved per request — a pure path computation). Either way the factory
    itself performs no filesystem work beyond locating its own templates
    and static assets.
    """
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Mount, Route
    from starlette.staticfiles import StaticFiles
    from starlette.templating import Jinja2Templates

    templates = Jinja2Templates(directory=str(_PACKAGE_DIR / "templates"))
    templates.env.filters["filesize"] = _human_size
    templates.env.filters["epoch"] = _human_ts
    templates.env.filters["duration"] = _human_duration
    templates.env.filters["tokens"] = _est_tokens
    templates.env.filters["span"] = _human_span
    templates.env.filters["offset"] = _rel_offset
    templates.env.filters["mean"] = _fmt_mean
    static_dir = _PACKAGE_DIR / "static"

    from .data import (
        GRAPH_SCOPES,
        SESSION_GAP_S,
        MissingDatabaseError,
        get_graph,
        get_health,
        get_read_only_db,
        get_recent_memories,
        get_session_chains,
        get_task_queue,
        get_tool_tokens,
        list_history,
        list_projects,
        prewarm_probes,
        symbol_candidates,
    )
    from .workspaces import enumerate_stores, probe_stores
    from .. import paths
    from ..paths import default_knowledge_path
    from ..viz import query as viz_query

    def resolve_selection(
        request: Request, db_path: str | None, knowledge_dir: str | None
    ) -> tuple[str | None, str, str]:
        """This request's ``(db, knowledge_root, store_key)`` (FR-003, D-001).

        No ``store`` param keeps the launch store — today's behavior,
        byte-identical (``db`` may stay None for the data layer to resolve).
        A present param must name a populated key from
        :func:`enumerate_stores` — the param is a registry key, never a raw
        path (no arbitrary-file-open vector). An unknown, empty, or missing
        key raises MissingDatabaseError so the app-level handler renders
        the missing-DB page: the friendly missing state, never an error.
        """
        store_key = request.query_params.get("store", "").strip()
        if not store_key:
            return db_path, knowledge_dir or str(default_knowledge_path()), ""
        home = Path(paths.CAIRN_HOME)
        state = next(
            (
                row["state"]
                for row in enumerate_stores(home)
                if row["key"] == store_key
            ),
            None,
        )
        if state != "populated":
            raise MissingDatabaseError(store_key)
        # Layout constants mirroring paths.StorePaths (db/.knowledge names).
        store_dir = home / store_key
        return str(store_dir / ".kg"), str(store_dir / ".knowledge"), store_key

    async def landing(request: Request) -> Response:
        _, _, store_key = resolve_selection(request, db_path, knowledge_dir)
        return templates.TemplateResponse(
            request,
            "index.html",
            {"db_path": db_path or "central store", "store_key": store_key},
        )

    # Plain-def handlers on purpose: Starlette runs them in a threadpool, so
    # the blocking read-only SQL below never stalls the event loop.

    # Machine-wide by design: reads the process-wide CAIRN_HOME, not the
    # launch db; the store param is echoed for the nav only — the overview
    # itself never switches (FR-003's seam is the data views).
    def workspaces_overview(request: Request) -> Response:
        _, _, store_key = resolve_selection(request, db_path, knowledge_dir)
        home = Path(paths.CAIRN_HOME)
        rows = probe_stores(home, enumerate_stores(home))
        return templates.TemplateResponse(
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
        return templates.TemplateResponse(
            request,
            "projects.html",
            {"projects": rows, "store_key": store_key},
        )

    def graph(request: Request) -> Response:
        scope = request.query_params.get("scope", "module")
        if scope not in GRAPH_SCOPES:
            scope = "module"
        focus = request.query_params.get("focus", "").strip() or None
        repo = request.query_params.get("repo", "").strip() or None
        depth_raw = request.query_params.get("depth", "").strip()
        depth = int(depth_raw) or None if depth_raw.isdigit() else None
        # Layout choice (FR-004): only "force" | "hier" are meaningful;
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
                conn, scope=scope, focus=focus, repo=repo, depth=depth
            )
        finally:
            conn.close()
        return templates.TemplateResponse(
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

    def graph_neighbors(request: Request) -> Response:
        # Repeatable ``name`` param (FR-003): strip each, drop empties,
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

    def health(request: Request) -> Response:
        selected_db, _, store_key = resolve_selection(
            request, db_path, knowledge_dir
        )
        conn = get_read_only_db(selected_db)
        try:
            health_data = get_health(conn, selected_db)
        finally:
            conn.close()
        return templates.TemplateResponse(
            request,
            "health.html",
            {
                "health": health_data,
                "db_path": selected_db or "central store",
                "store_key": store_key,
            },
        )

    def history(request: Request) -> Response:
        tool = request.query_params.get("tool", "").strip() or None
        session = request.query_params.get("session", "").strip() or None
        source = request.query_params.get("source", "").strip() or None
        before = request.query_params.get("before", "").strip() or None
        after = request.query_params.get("after", "").strip() or None
        window, since = _resolve_window(request.query_params.get("window"))
        selected_db, _, store_key = resolve_selection(
            request, db_path, knowledge_dir
        )
        conn = get_read_only_db(selected_db)
        try:
            result = list_history(
                conn,
                tool_name=tool,
                session_id=session,
                source=source,
                before=before,
                after=after,
                since=since,
            )
        finally:
            conn.close()
        return templates.TemplateResponse(
            request,
            "history.html",
            {
                "calls": result["rows"],
                "tool": tool or "",
                "session": session or "",
                "source": source or "",
                "before": before or "",
                "after": after or "",
                "window": window,
                "next_cursor": result["next"],
                "prev_cursor": result["prev"],
                "store_key": store_key,
            },
        )

    def tokens(request: Request) -> Response:
        window, since = _resolve_window(request.query_params.get("window"))
        selected_db, _, store_key = resolve_selection(
            request, db_path, knowledge_dir
        )
        conn = get_read_only_db(selected_db)
        try:
            rows = get_tool_tokens(conn, since=since)
        finally:
            conn.close()
        return templates.TemplateResponse(
            request,
            "tokens.html",
            {"tools": rows, "window": window, "store_key": store_key},
        )

    # FR-005 exports ride the same seams the views ride: resolve_selection
    # for the store, _resolve_window for the window, the view's filter
    # params, and the very data functions the views render from — parity
    # by construction. The cursor params (before/after) page the HTML view
    # and are not filters, so the unpaginated export drops them.

    def _history_export_rows(request: Request):
        tool = request.query_params.get("tool", "").strip() or None
        session = request.query_params.get("session", "").strip() or None
        source = request.query_params.get("source", "").strip() or None
        window, since = _resolve_window(request.query_params.get("window"))
        selected_db, _, _ = resolve_selection(request, db_path, knowledge_dir)
        conn = get_read_only_db(selected_db)
        try:
            result = list_history(
                conn,
                tool_name=tool,
                session_id=session,
                source=source,
                since=since,
                limit=_EXPORT_ROW_LIMIT,
            )
        finally:
            conn.close()
        return result["rows"], [
            ("tool", tool),
            ("session", session),
            ("source", source),
            ("window", None if window == "all" else window),
        ]

    def _tokens_export_rows(request: Request):
        window, since = _resolve_window(request.query_params.get("window"))
        selected_db, _, _ = resolve_selection(request, db_path, knowledge_dir)
        conn = get_read_only_db(selected_db)
        try:
            rows = get_tool_tokens(conn, since=since)
        finally:
            conn.close()
        return rows, [("window", None if window == "all" else window)]

    def _attached(response: Response, filename: str) -> Response:
        response.headers["Content-Disposition"] = (
            f'attachment; filename="{filename}"'
        )
        return response

    def history_csv(request: Request) -> Response:
        rows, hints = _history_export_rows(request)
        return _attached(
            Response(_csv_text(rows), media_type="text/csv"),
            _export_filename("history", "csv", hints),
        )

    def history_json(request: Request) -> Response:
        rows, hints = _history_export_rows(request)
        return _attached(
            JSONResponse(rows), _export_filename("history", "json", hints)
        )

    def tokens_csv(request: Request) -> Response:
        rows, hints = _tokens_export_rows(request)
        return _attached(
            Response(_csv_text(rows), media_type="text/csv"),
            _export_filename("tokens", "csv", hints),
        )

    def tokens_json(request: Request) -> Response:
        rows, hints = _tokens_export_rows(request)
        return _attached(
            JSONResponse(rows), _export_filename("tokens", "json", hints)
        )

    def chains(request: Request) -> Response:
        window, since = _resolve_window(request.query_params.get("window"))
        expand = request.query_params.get("expand", "").strip() or None
        # Session filter (FR-002), read like history's tool/session params:
        # absent or blank means no filter.
        session = request.query_params.get("session", "").strip() or None
        selected_db, _, store_key = resolve_selection(
            request, db_path, knowledge_dir
        )
        conn = get_read_only_db(selected_db)
        try:
            result = get_session_chains(
                conn, since=since, session_id=session, expand=expand
            )
        finally:
            conn.close()
        return templates.TemplateResponse(
            request,
            "chains.html",
            {
                "chains": result["chains"],
                "chains_truncated": result["truncated"],
                "total_chains": result["total_chains"],
                "expand": expand or "",
                "gap_minutes": SESSION_GAP_S // 60,
                "session": session or "",
                "window": window,
                "store_key": store_key,
            },
        )

    def memory(request: Request) -> Response:
        _, selected_knowledge, store_key = resolve_selection(
            request, db_path, knowledge_dir
        )
        entries = get_recent_memories(selected_knowledge)
        return templates.TemplateResponse(
            request,
            "memory.html",
            {"memories": entries, "store_key": store_key},
        )

    def tasks(request: Request) -> Response:
        status = request.query_params.get("status", "all").strip() or "all"
        if status not in TASK_STATUSES:
            status = "all"
        _, selected_knowledge, store_key = resolve_selection(
            request, db_path, knowledge_dir
        )
        entries = get_task_queue(
            selected_knowledge, status=None if status == "all" else status
        )
        return templates.TemplateResponse(
            request,
            "tasks.html",
            {
                "tasks": entries,
                "statuses": TASK_STATUSES,
                "status": status,
                "store_key": store_key,
            },
        )

    routes = [
        Route("/", landing, name="index"),
        Route("/workspaces", workspaces_overview, name="workspaces"),
        Route("/projects", projects, name="projects"),
        Route("/graph", graph, name="graph"),
        Route("/graph/candidates", graph_candidates, name="graph_candidates"),
        Route("/graph/neighbors", graph_neighbors, name="graph_neighbors"),
        Route("/history", history, name="history"),
        Route("/history.csv", history_csv, name="history_csv"),
        Route("/history.json", history_json, name="history_json"),
        Route("/tokens", tokens, name="tokens"),
        Route("/tokens.csv", tokens_csv, name="tokens_csv"),
        Route("/tokens.json", tokens_json, name="tokens_json"),
        Route("/chains", chains, name="chains"),
        Route("/health", health, name="health"),
        Route("/memory", memory, name="memory"),
        Route("/tasks", tasks, name="tasks"),
        Mount(
            "/static",
            app=StaticFiles(directory=str(static_dir)),
            name="static",
        ),
    ]

    def missing_db(request, exc):
        from html import escape

        from starlette.responses import HTMLResponse

        return HTMLResponse(
            "<html><head><title>cairn dashboard</title></head><body>"
            "<h1>cairn dashboard</h1>"
            f"<p>No graph database found at <code>{escape(str(exc))}</code>.</p>"
            "<p>Run <code>cairn build</code> to index this workspace, "
            "then refresh.</p>"
            "</body></html>"
        )

    # Prewarm the health probes off the request path (FR-001); the flag set
    # must be synchronous, so create_app never blocks on the probe import.
    prewarm_probes()

    return Starlette(
        routes=routes,
        exception_handlers={MissingDatabaseError: missing_db},
    )
