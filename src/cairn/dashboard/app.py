"""Starlette app factory for the read-only dashboard (FR-001, FR-010).

Routes: landing, projects, graph, history, tokens, chains, health, memory,
tasks.

starlette / jinja2 arrive only as transitive deps of mcp, so they are
imported inside :func:`create_app`: importing this module (or the package)
must never load the server stack. The factory takes the DB path as a
parameter — the CLI command constructs the app, never the other way round.
"""
from __future__ import annotations

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


def _human_size(num_bytes) -> str:
    """Byte count as B / KiB / MiB / GiB / TiB, one decimal above 1024."""
    size = float(num_bytes or 0)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


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
    )
    from ..paths import default_knowledge_path

    def knowledge_root() -> str:
        return knowledge_dir or str(default_knowledge_path())

    async def landing(request: Request) -> Response:
        return templates.TemplateResponse(
            request,
            "index.html",
            {"db_path": db_path or "central store"},
        )

    # Plain-def handlers on purpose: Starlette runs them in a threadpool, so
    # the blocking read-only SQL below never stalls the event loop.
    def projects(request: Request) -> Response:
        conn = get_read_only_db(db_path)
        try:
            rows = list_projects(conn)
        finally:
            conn.close()
        return templates.TemplateResponse(
            request,
            "projects.html",
            {"projects": rows},
        )

    def graph(request: Request) -> Response:
        scope = request.query_params.get("scope", "module")
        if scope not in GRAPH_SCOPES:
            scope = "module"
        focus = request.query_params.get("focus", "").strip() or None
        repo = request.query_params.get("repo", "").strip() or None
        depth_raw = request.query_params.get("depth", "").strip()
        depth = int(depth_raw) or None if depth_raw.isdigit() else None
        conn = get_read_only_db(db_path)
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
            },
        )

    def health(request: Request) -> Response:
        conn = get_read_only_db(db_path)
        try:
            health_data = get_health(conn, db_path)
        finally:
            conn.close()
        return templates.TemplateResponse(
            request,
            "health.html",
            {"health": health_data, "db_path": db_path or "central store"},
        )

    def history(request: Request) -> Response:
        tool = request.query_params.get("tool", "").strip() or None
        session = request.query_params.get("session", "").strip() or None
        conn = get_read_only_db(db_path)
        try:
            calls = list_history(conn, tool_name=tool, session_id=session)
        finally:
            conn.close()
        return templates.TemplateResponse(
            request,
            "history.html",
            {"calls": calls, "tool": tool or "", "session": session or ""},
        )

    def tokens(request: Request) -> Response:
        conn = get_read_only_db(db_path)
        try:
            rows = get_tool_tokens(conn)
        finally:
            conn.close()
        return templates.TemplateResponse(
            request,
            "tokens.html",
            {"tools": rows},
        )

    def chains(request: Request) -> Response:
        conn = get_read_only_db(db_path)
        try:
            rows = get_session_chains(conn)
        finally:
            conn.close()
        return templates.TemplateResponse(
            request,
            "chains.html",
            {"chains": rows, "gap_minutes": SESSION_GAP_S // 60},
        )

    def memory(request: Request) -> Response:
        entries = get_recent_memories(knowledge_root())
        return templates.TemplateResponse(
            request,
            "memory.html",
            {"memories": entries},
        )

    def tasks(request: Request) -> Response:
        status = request.query_params.get("status", "all").strip() or "all"
        if status not in TASK_STATUSES:
            status = "all"
        entries = get_task_queue(
            knowledge_root(), status=None if status == "all" else status
        )
        return templates.TemplateResponse(
            request,
            "tasks.html",
            {"tasks": entries, "statuses": TASK_STATUSES, "status": status},
        )

    routes = [
        Route("/", landing, name="index"),
        Route("/projects", projects, name="projects"),
        Route("/graph", graph, name="graph"),
        Route("/history", history, name="history"),
        Route("/tokens", tokens, name="tokens"),
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

    return Starlette(
        routes=routes,
        exception_handlers={MissingDatabaseError: missing_db},
    )
