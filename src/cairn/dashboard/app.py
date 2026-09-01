"""Starlette app factory for the read-only dashboard (FR-001, FR-010).

Routes: landing, workspaces overview, projects, graph (plus its
/graph/candidates symbol-search and /graph/neighbors node-expansion JSON),
history, tokens (plus their .csv/.json exports), chains, health, memory,
tasks, wiki (list plus per-page detail), settings, embeddings — the
settings section (FR-011) carries the
app's only POST routes (/settings/save, /settings/parity-check); the
embeddings status view and everything else stay GET-only so the read-only
views keep their assumptions.

starlette / jinja2 arrive only as transitive deps of mcp, so they are
imported inside :func:`create_app`: importing this module (or the package)
must never load the server stack. The factory takes the DB path as a
parameter — the CLI command constructs the app, never the other way round.
"""
from __future__ import annotations

import csv
import io
import os
import re
import threading
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

# Settings-section knob set (FR-011): the CAIRN_EMBED_* keys the page reads
# and writes in $CAIRN_HOME/config.json. Values persist as strings — the
# D-008 resolver (embeddings._config_or_env) honors str file values only.
SETTINGS_BACKENDS = ("local", "server", "omlx", "ollama", "hash")
SETTINGS_KEYS = (
    "CAIRN_EMBED_BACKEND",
    "CAIRN_EMBED_SERVER_MODEL",
    "CAIRN_EMBED_API_KEY",
    "CAIRN_EMBED_TIMEOUT",
    "CAIRN_EMBED_SERVER_BATCH",
    "CAIRN_EMBED_MODEL_STAMP",
    "CAIRN_EMBED_BASE_URL",
)

# Keys whose file values must parse as numbers (the server client float()s /
# int()s them unguarded), validated at save time so the form can never write
# a value that breaks the embed path.
SETTINGS_NUMERIC = {
    "CAIRN_EMBED_TIMEOUT": (float, "a number of seconds"),
    "CAIRN_EMBED_SERVER_BATCH": (int, "a whole batch size"),
}


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


def _human_iso(value) -> str:
    """ISO-8601 timestamp string as a UTC wall-clock string (``—`` when
    absent or unparseable). Status views render stored ISO columns
    (build_runs.started_at, embeddings.embedded_at) through this so a
    raw ``2026-08-28T01:30:08.173238+00:00`` never reaches the page."""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


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

    static_dir = _PACKAGE_DIR / "static"
    templates = Jinja2Templates(directory=str(_PACKAGE_DIR / "templates"))
    # Asset version = newest static-file mtime, so template URLs carry
    # ?v=<version> and any browser holding a stale cached app.js/app.css
    # (heuristic caching predating the no-cache header, or an old
    # install) fetches fresh the moment the files change.
    templates.env.globals["asset_version"] = max(
        (p.stat().st_mtime_ns for p in static_dir.rglob("*") if p.is_file()),
        default=0,
    )
    templates.env.filters["filesize"] = _human_size
    templates.env.filters["epoch"] = _human_ts
    templates.env.filters["isots"] = _human_iso
    templates.env.filters["duration"] = _human_duration
    templates.env.filters["tokens"] = _est_tokens
    templates.env.filters["span"] = _human_span
    templates.env.filters["offset"] = _rel_offset
    templates.env.filters["mean"] = _fmt_mean

    class _RevalidatingStaticFiles(StaticFiles):
        """Browsers heuristic-cache uncontrolled responses for a lazy
        freshness lifetime, so an upgraded dashboard keeps serving the
        OLD app.js/app.css (stale UI, new HTML) until the cache evicts.
        The assets are local and tiny — always revalidate instead."""

        def file_response(self, *args, **kwargs):
            response = super().file_response(*args, **kwargs)
            response.headers["Cache-Control"] = "no-cache"
            return response

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
        get_wiki_page,
        get_wiki_pages,
        inspect_symbol,
        list_history,
        list_projects,
        prewarm_probes,
        symbol_candidates,
        symbol_suggest,
    )
    from .workspaces import enumerate_stores, probe_stores
    from .shell import shell_context
    from .. import paths
    from ..graph import embed_ladder, embeddings
    from ..paths import default_knowledge_path
    from ..viz import query as viz_query

    # The FR-013 banner reflects THIS process's observability only: the
    # ladder cache is per-process and nothing in this read-only app evaluates
    # it, so the first page render adds one uncached server probe (FR-002's
    # 2 s discipline) whose failure seeds the ladder here; later requests
    # read that cached verdict. The status view shares the same one-probe
    # seam for its probe-health row.
    _probe_lock = threading.Lock()
    _probed = False
    _probe_ok = None

    def _server_probe_once():
        """One uncached server probe per dashboard process, shared by the
        banner and the status view. None when the backend is not
        server-family (nothing probes); a failed probe seeds this process's
        ladder verdict, exactly as the banner always has. The lock is held
        across the probe AND the verdict assignment: a concurrent first
        request blocks (worst case the probe's ~2 s timeout — a localhost
        tool) instead of racing past an unassigned verdict."""
        nonlocal _probed, _probe_ok
        if embeddings._backend_name() not in embeddings._SERVER_FAMILY:
            return None
        with _probe_lock:
            if _probed:
                return _probe_ok
            _probed = True
            ok = embeddings._run_server_probe()
            if not ok:
                embed_ladder.evaluate_ladder()  # seed this process's verdict
            _probe_ok = ok
            return ok

    def embed_banner() -> str:
        """The degradation banner text for this request ("" when healthy)."""
        if embed_ladder.degradation_active():
            return embed_ladder.degradation_banner()
        if _server_probe_once() is False:
            return embed_ladder.degradation_banner()
        return ""

    def render(
        request: Request, name: str, context: dict, status_code: int = 200
    ) -> Response:
        """TemplateResponse carrying the banner and shell context on every
        page. The selector's options come from enumerate_stores (stat-only,
        never probed — probe_stores and its 100-open budget stay exclusive
        to the workspaces overview) and cost one registry read plus one
        directory scan per render."""
        context["embed_banner"] = embed_banner()
        if "shell" not in context:
            context["shell"] = shell_context(
                enumerate_stores(Path(paths.CAIRN_HOME)),
                context.get("store_key", ""),
                request.url.path,
            )
        return templates.TemplateResponse(
            request, name, context, status_code=status_code
        )

    def resolve_selection(
        request: Request,
        db_path: str | None,
        knowledge_dir: str | None,
        form=None,
    ) -> tuple[str | None, str, str]:
        """This request's ``(db, knowledge_root, store_key)`` (FR-003, D-001).

        No ``store`` param keeps the launch store — today's behavior,
        byte-identical (``db`` may stay None for the data layer to resolve).
        A present param must name a populated key from
        :func:`enumerate_stores` — the param is a registry key, never a raw
        path (no arbitrary-file-open vector). An unknown, empty, or missing
        key raises MissingDatabaseError so the app-level handler renders
        the missing-DB page: the friendly missing state, never an error.

        ``form`` is the already-parsed body of a POST whose form carries the
        hidden store input (settings): the body store is a fallback for the
        query param only — one seam, two transports, same validation.
        """
        store_key = request.query_params.get("store", "").strip()
        if not store_key and form is not None:
            raw = form.get("store")
            store_key = (str(raw) if raw is not None else "").strip()
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
        return render(
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

    def graph_inspect(request: Request) -> Response:
        # Side-panel payload for one symbol (identity + callers + callees +
        # impact with affected tests). Missing/blank names hit the data
        # function's not-found contract (200 with found=False), never an
        # error — the panel just stays empty.
        name = request.query_params.get("name", "")
        selected_db, _, _ = resolve_selection(request, db_path, knowledge_dir)
        conn = get_read_only_db(selected_db)
        try:
            result = inspect_symbol(conn, name)
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
        return render(
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
        return render(
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
        return render(
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
        return render(
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
        return render(
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
        return render(
            request,
            "tasks.html",
            {
                "tasks": entries,
                "statuses": TASK_STATUSES,
                "status": status,
                "store_key": store_key,
            },
        )

    def wiki(request: Request) -> Response:
        # Catalog filters, read like every other view's params: absent or
        # blank means no filter; ``state`` outside PAGE_STATES falls back to
        # no filter (silent fallback, matching the scope/window fallbacks).
        from ..wiki.manifest import PAGE_STATES

        repo = request.query_params.get("repo", "").strip() or None
        state = request.query_params.get("state", "").strip() or None
        query = request.query_params.get("q", "").strip()
        _, selected_knowledge, store_key = resolve_selection(
            request, db_path, knowledge_dir
        )
        pages = get_wiki_pages(selected_knowledge, repo=repo)
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
        return render(
            request,
            "wiki.html",
            {
                "pages": pages,
                "page_states": PAGE_STATES,
                "filters": {"repo": repo or "", "state": state or "", "q": query},
                "store_key": store_key,
            },
        )

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

    def wiki_page(request: Request) -> Response:
        """Legacy one-segment URL: a permanent redirect to the repo-qualified
        canonical URL (bookmarks and recorded links keep working), or the
        same 404 as before when no readable concept matches."""
        from urllib.parse import quote

        from starlette.responses import RedirectResponse

        _, selected_knowledge, _ = resolve_selection(
            request, db_path, knowledge_dir
        )
        page = get_wiki_page(selected_knowledge, request.path_params["page_id"])
        if page is None:
            return _wiki_not_found()
        target = "/wiki/{}/{}".format(
            quote(page["repo"], safe=""), quote(page["page_id"], safe="")
        )
        store = request.query_params.get("store", "").strip()
        if store:
            target += "?store=" + quote(store, safe="")
        return RedirectResponse(target, status_code=307)

    def wiki_page_repo(request: Request) -> Response:
        # The canonical URL: repo-qualified, so multi-repo workspaces can
        # reach every page even when repos plan colliding page ids (every
        # repo plans an "overview"). prev/next walk the repo's promoted
        # pages in manifest (plan) order — non-promoted rows have no
        # readable concept, so linking to them would be a dead end.
        _, selected_knowledge, store_key = resolve_selection(
            request, db_path, knowledge_dir
        )
        repo = request.path_params["repo"]
        page_id = request.path_params["page_id"]
        page = get_wiki_page(
            selected_knowledge, page_id, repo=repo, store_key=store_key
        )
        if page is None:
            return _wiki_not_found()
        promoted = [
            p
            for p in get_wiki_pages(selected_knowledge, repo=repo)
            if p["promoted"]
        ]
        index = next(
            (i for i, p in enumerate(promoted) if p["page_id"] == page_id), None
        )
        prev_page = promoted[index - 1] if index not in (None, 0) else None
        next_page = (
            promoted[index + 1]
            if index is not None and index + 1 < len(promoted)
            else None
        )
        return render(
            request,
            "wiki_page.html",
            {
                "page": page,
                "prev": prev_page,
                "next": next_page,
                "store_key": store_key,
            },
        )

    def _settings_context(
        store_key: str, saved: bool = False, error: str = "", parity=None
    ) -> dict:
        """Settings-page context: per-knob file/effective state (D-008).

        ``prefill`` is the file value when one exists — the form edits the
        file layer — else the effective value; ``pinned`` marks an env var
        shadowing whatever the file holds, rendered as an "overridden by
        environment" marker so a save that "does nothing" is explainable.
        """
        cfg = {}
        for key in SETTINGS_KEYS:
            env_value = (os.environ.get(key) or "").strip()
            file_raw = paths.get_config_value(key)
            file_value = "" if file_raw is None else str(file_raw).strip()
            # The API key is write-only: its prefill stays "" unconditionally
            # (the form's key input never renders a secret back). The
            # set/not-set badges read ``effective``, so they still work.
            cfg[key] = {
                "prefill": (
                    "" if key == "CAIRN_EMBED_API_KEY" else file_value or env_value
                ),
                "effective": env_value or file_value,
                "pinned": bool(env_value),
            }
        return {
            "cfg": cfg,
            "backends": SETTINGS_BACKENDS,
            "backend_value": cfg["CAIRN_EMBED_BACKEND"]["prefill"] or "local",
            "saved": saved,
            "error": error,
            "parity": parity,
            "store_key": store_key,
        }

    def settings(request: Request) -> Response:
        _, _, store_key = resolve_selection(request, db_path, knowledge_dir)
        return render(request, "settings.html", _settings_context(store_key))

    # Async on purpose: reading the urlencoded form body is await-only, and
    # the handler's own work is one small atomic file write. The blocking-SQL
    # handlers above stay plain-def (threadpool) — this route touches no SQL.
    async def settings_save(request: Request) -> Response:
        form = await request.form()
        _, _, store_key = resolve_selection(
            request, db_path, knowledge_dir, form=form
        )
        submitted = {
            key: str(form.get(key) or "").strip()
            for key in SETTINGS_KEYS
            if key in form
        }
        for key, (cast, what) in SETTINGS_NUMERIC.items():
            value = submitted.get(key, "")
            if value:
                try:
                    cast(value)
                except ValueError:
                    return render(
                        request,
                        "settings.html",
                        _settings_context(
                            store_key, error=f"Refused: {key} must be {what}."
                        ),
                        status_code=400,
                    )
        current_base = str(
            paths.get_config_value("CAIRN_EMBED_BASE_URL") or ""
        ).strip()
        if (
            "CAIRN_EMBED_BASE_URL" in submitted
            and submitted["CAIRN_EMBED_BASE_URL"] != current_base
            and "confirm_base_url" not in form
        ):
            return render(
                request,
                "settings.html",
                _settings_context(
                    store_key,
                    error="Refused: a base-URL change requires the "
                    "'Confirm base-URL change' checkbox.",
                ),
                status_code=400,
            )
        values = {}
        for key, value in submitted.items():
            if key == "CAIRN_EMBED_API_KEY":
                if value:  # write-only: blank submit leaves the key untouched
                    values[key] = value
            elif key == "CAIRN_EMBED_BACKEND":
                if value in SETTINGS_BACKENDS:  # never write an unknown arm
                    values[key] = value
            elif value:
                # A blank submit means "no change", never a write of "":
                # treating it as one silently CLEARED the stored value. A
                # knob is cleared by editing config.json, not from the form.
                values[key] = value
        if not paths.set_config_values(values):
            return render(
                request,
                "settings.html",
                _settings_context(
                    store_key,
                    error=(
                        f"Could not write {paths.CONFIG_FILE}; "
                        "nothing was saved."
                    ),
                ),
                status_code=500,
            )
        paths.reset_config_cache()
        embeddings.reset_backend_cache()  # this process re-resolves on next use
        return render(
            request, "settings.html", _settings_context(store_key, saved=True)
        )

    def settings_parity_check(request: Request) -> Response:
        selected_db, _, store_key = resolve_selection(
            request, db_path, knowledge_dir
        )
        conn = get_read_only_db(selected_db)
        try:
            try:
                result = embed_ladder.check_parity(
                    conn, embeddings.current_model()
                )
                parity = {
                    "passed": bool(result.passed),
                    "mean": (
                        "—"
                        if result.mean_cosine is None
                        else f"{result.mean_cosine:.4f}"
                    ),
                    "sampled": result.sampled,
                    "reason": result.reason,
                }
            except Exception as exc:  # embed failures ARE the verdict text
                parity = {
                    "passed": False,
                    "mean": "—",
                    "sampled": None,
                    "reason": f"{type(exc).__name__}: {exc}",
                }
        finally:
            conn.close()
        return render(
            request,
            "settings.html",
            _settings_context(store_key, parity=parity),
        )

    def _embeddings_rows(conn) -> list:
        """Per-corpus ``{corpus, model, count, last}`` rows for the status
        view: the code corpus reads the embeddings table directly,
        knowledge/memory ride embed_knowledge_count/embed_memory_count.
        A corpus whose count cannot be read (store predating the table,
        unresolvable or malformed backend config) reports unknown instead
        of failing the page.
        """
        rows = []
        for corpus, table in (
            ("code", "embeddings"),
            ("knowledge", "knowledge_embeddings"),
            ("memory", "memory_embeddings"),
        ):
            model, count, last = None, None, None
            try:
                if corpus == "knowledge":
                    model = embeddings.current_model(corpus="knowledge")
                    count = embeddings.embed_knowledge_count(conn)
                elif corpus == "memory":
                    model = embeddings.current_model(corpus="memory")
                    count = embeddings.embed_memory_count(conn)
                else:
                    model = embeddings.current_model()
                    count = conn.execute(
                        "SELECT COUNT(*) FROM embeddings WHERE model = ?",
                        (model,),
                    ).fetchone()[0]
                last = conn.execute(
                    f"SELECT MAX(embedded_at) FROM {table} WHERE model = ?",
                    (model,),
                ).fetchone()[0]
            except Exception:
                # Unknown, rendered as an em-dash — never a 500. Broad on
                # purpose: this is a status page, and current_model()'s
                # URL resolution can raise ValueError on a malformed
                # CAIRN_EMBED_BASE_URL, not just RuntimeError/sqlite3
                # errors.
                pass
            rows.append(
                {"corpus": corpus, "model": model, "count": count, "last": last}
            )
        return rows

    # Plain-def like the SQL views: the store read is blocking, and a
    # first-request server probe rides the banner's once-per-process seam.
    def embeddings_status(request: Request) -> Response:
        """FR-011's status view: effective backend + precedence, resolved
        stamp, per-corpus counts, probe health, and the active fallback
        rung — the rung rows read ladder_state(), the same accessor the
        FR-013 banner text builds from (one degradation source)."""
        selected_db, _, store_key = resolve_selection(
            request, db_path, knowledge_dir
        )
        probe_ok = _server_probe_once()  # first: rung adoption may retarget
        backend = embeddings._backend_name()
        try:
            stamp = embeddings.current_model()
        except Exception as exc:  # unresolvable backend is status, not a 500
            stamp = f"unresolved ({type(exc).__name__}: {exc})"
        state = embed_ladder.ladder_state()
        conn = get_read_only_db(selected_db)
        try:
            corpora = _embeddings_rows(conn)
        finally:
            conn.close()
        return render(
            request,
            "embeddings.html",
            {
                "backend": backend,
                "stamp": stamp,
                "is_server": backend in embeddings._SERVER_FAMILY,
                "probe_ok": probe_ok,
                "rung": state if state is not None and state.active else None,
                "corpora": corpora,
                "cfg": _settings_context(store_key)["cfg"],
                "store_key": store_key,
            },
        )

    routes = [
        Route("/", landing, name="index"),
        Route("/workspaces", workspaces_overview, name="workspaces"),
        Route("/projects", projects, name="projects"),
        Route("/graph", graph, name="graph"),
        Route("/graph/candidates", graph_candidates, name="graph_candidates"),
        Route("/graph/suggest", graph_suggest, name="graph_suggest"),
        Route("/graph/neighbors", graph_neighbors, name="graph_neighbors"),
        Route("/graph/inspect", graph_inspect, name="graph_inspect"),
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
        Route("/wiki", wiki, name="wiki"),
        # Repo-qualified canonical URL first; the one-segment legacy route
        # follows as a redirect (a single path param never matches two
        # segments, so the two never shadow each other).
        Route("/wiki/{repo}/{page_id}", wiki_page_repo, name="wiki_page_repo"),
        Route("/wiki/{page_id}", wiki_page, name="wiki_page"),
        Route("/embeddings", embeddings_status, name="embeddings"),
        Route("/settings", settings, name="settings"),
        # The app's first POST routes (FR-011) — loopback-only by the CLI's
        # DEFAULT_HOST + _require_loopback; the GET views stay untouched.
        Route(
            "/settings/save",
            settings_save,
            methods=["POST"],
            name="settings_save",
        ),
        Route(
            "/settings/parity-check",
            settings_parity_check,
            methods=["POST"],
            name="settings_parity_check",
        ),
        Mount(
            "/static",
            app=_RevalidatingStaticFiles(directory=str(static_dir)),
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
