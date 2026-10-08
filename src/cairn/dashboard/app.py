"""Starlette app factory for the read-only dashboard."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
# "dropped" is written by drop_task; omitting it degrades ?status=dropped
# to the unfiltered view.
TASK_STATUSES = ("pending", "in-progress", "done", "failed", "dropped")

# Agent-memory type vocabulary (cairn memory record) — the /memory
# filter's allowed values.
MEMORY_TYPES = ("decision", "pattern", "mistake", "workaround")

# Seed knowledge families; route options add families found in the corpus.
KNOWLEDGE_FAMILIES = ("business-rule", "decision", "spec", "workflow")

# Traffic-view time-window presets — the ``window`` param's
# allowed values; "all" is the unbounded default.
WINDOW_PRESETS = ("24h", "7d", "30d", "all")

# Preset -> seconds back from now; "all" is absent (unbounded).
_WINDOW_SECONDS = {"24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}

# Settings-section knob set: the CAIRN_EMBED_* keys the page reads
# and writes in $CAIRN_HOME/config.json. Values persist as strings — the
# The resolver (embeddings._config_or_env) honors str file values only.
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
    """ISO-8601 timestamp as a UTC wall-clock string, or ``—`` when unparseable."""
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
    """Per-call request/response token estimates; ``unknown``
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
    """Validated window preset plus its epoch cutoff (None means unbounded)."""
    preset = window if window in WINDOW_PRESETS else "all"
    seconds = _WINDOW_SECONDS.get(preset)
    return preset, time.time() - seconds if seconds is not None else None


def is_hx_request(request: "Request") -> bool:
    """True when this htmx request wants a fragment; history restores veto it."""
    if (
        (request.headers.get("HX-History-Restore-Request") or "")
        .strip()
        .lower()
        == "true"
    ):
        return False
    return (request.headers.get("HX-Request") or "").strip().lower() == "true"


# Exports fetch the filtered set in one unpaginated call: a single
# list_history page large enough to cover it — never a cursor-following
# duplicate of the view's paging.
EXPORT_ROW_LIMIT = 1_000_000


def create_app(
    db_path: str | None = None, knowledge_dir: str | None = None
) -> Starlette:
    """Build the dashboard app without resolving stores or probing at factory time."""
    from starlette.applications import Starlette
    from starlette.routing import Mount
    from starlette.staticfiles import StaticFiles
    from starlette.templating import Jinja2Templates

    static_dir = _PACKAGE_DIR / "static"
    templates = Jinja2Templates(directory=str(_PACKAGE_DIR / "templates"))
    # Asset version makes stale cached assets refetch when files change.
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
        """Static file handler serving assets with Cache-Control: no-cache."""

        def file_response(self, *args, **kwargs):
            response = super().file_response(*args, **kwargs)
            response.headers["Cache-Control"] = "no-cache"
            return response

    from .data import MissingDatabaseError, prewarm_probes
    from .workspaces import enumerate_stores
    from .shell import shell_context
    from .. import paths
    from ..graph import embed_ladder, embeddings
    from ..paths import default_knowledge_path

    # Probe verdicts are process-local and shared by the banner and status view.
    _probe_lock = threading.Lock()
    _probed = False
    _probe_ok = None

    def _server_probe_once():
        """Run one uncached server probe per process; hold the lock through verdict assignment."""
        nonlocal _probed, _probe_ok
        if embeddings.backend_name() not in embeddings.SERVER_FAMILY:
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
        """Render a template with shell context while keeping store enumeration stat-only."""
        context["embed_banner"] = embed_banner()
        if "shell" not in context:
            context["shell"] = shell_context(
                enumerate_stores(Path(paths.CAIRN_HOME)),
                context.get("store_key", ""),
                request.url.path,
                launch_db=db_path,
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
        """Resolve request/form input to a validated registry store selection."""
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


    from .routes import (
        DashboardContext,
        communities,
        core,
        editor,
        graph,
        history,
        knowledge,
        memory,
        settings,
        wiki,
    )

    routes: list[Any] = []
    context = DashboardContext(
        db_path=db_path,
        knowledge_dir=knowledge_dir,
        render=render,
        resolve_selection=resolve_selection,
        templates=templates,
        is_hx_request=is_hx_request,
        resolve_window=_resolve_window,
        server_probe_once=_server_probe_once,
        static_dir=static_dir,
    )

    core.register(routes, context, section="primary")
    graph.register(routes, context)
    communities.register(routes, context)
    history.register(routes, context)
    core.register(routes, context, section="health")
    memory.register(routes, context)
    knowledge.register(routes, context)
    wiki.register(routes, context)
    settings.register(routes, context)
    editor.register(routes, context)
    routes.append(
        Mount(
            "/static",
            app=_RevalidatingStaticFiles(directory=str(static_dir)),
            name="static",
        )
    )

    def missing_db(request, exc):
        from html import escape

        from starlette.responses import HTMLResponse

        return HTMLResponse(
            "<html><head><title>cairn dashboard</title></head><body>"
            "<h1>cairn dashboard</h1>"
            f"<p>No graph database found at <code>{escape(str(exc))}</code>.</p>"
            "<p>Run <code>cairn build</code> to index this workspace, "
            "then refresh.</p>"
            '<p><a href="/workspaces">Open the workspaces overview</a> '
            "to pick an available store.</p>"
            "</body></html>"
        )

    # Prewarm the health probes off the request path; the flag set
    # must be synchronous, so create_app never blocks on the probe import.
    prewarm_probes()

    return Starlette(
        routes=routes,
        exception_handlers={MissingDatabaseError: missing_db},
    )
