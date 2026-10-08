"""cairn MCP server: exposes graph query tools to AI agents."""
from __future__ import annotations

import logging
import os
import signal
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

# Bootstrap: allow running as a script (python .../server.py) OR as a module
# (python -m src.mcp_server.server). Agents invoke the script directly, so we
# ensure the project root is on sys.path so that absolute `src.` imports work.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from cairn.graph.schema import get_db
from cairn.paths import render_env_resolution_chain, resolve_store
from cairn.utils.logging import configure_logging, quiet_server_noise
from mcp.server.transport_security import TransportSecuritySettings

from .auth import bearer_key_ok

# Wire the conn factory before the tool imports: the first
# instrument-wrapped call would otherwise hit a None factory.
from ._server_core import _bundle, _conn, _rw_conn, mcp
from .metric_buffering import configure_conn
configure_conn(_conn)

# Memory-embed buffering needs a genuinely writable connection (unlike
# metrics, it must not skip under CAIRN_READ_ONLY), so it's wired with
# _rw_conn rather than _conn.
from . import embed_buffering
embed_buffering.configure(_rw_conn, _bundle)

# Importing the tools_*.py modules registers every @mcp.tool() on the shared
# `mcp` instance via decorator side effects. The names aren't used directly
# here -- the import is what does the work.
from . import tools_compass  # noqa: F401
from . import tools_federation  # noqa: F401
from . import tools_graph    # noqa: F401
from . import tools_knowledge  # noqa: F401
from . import tools_memory   # noqa: F401
from . import tools_wiki     # noqa: F401

# Expected tool count - assertion fires if tools are missing due to import issues
_EXPECTED_TOOL_COUNT = 26


def _drain_buffered_telemetry() -> None:
    """Synchronously drain every buffered telemetry sink (best-effort, isolated per sink)."""
    try:
        from cairn.telemetry import flush as _telemetry_flush

        _telemetry_flush()  # events buffer
    except Exception:
        pass
    try:
        from cairn.telemetry import otel as _otel

        _otel.flush()  # OTLP side buffer (no-op unless CAIRN_OTEL_ENDPOINT)
    except Exception:
        pass
    try:
        from .metric_buffering import _flush_metrics

        _flush_metrics()  # tool_metrics buffer
    except Exception:
        pass
    try:
        from . import embed_buffering

        embed_buffering._flush()  # queued memory embeddings
    except Exception:
        pass


def _watch_parent_loop() -> None:
    """Parent-death watchdog body: drain buffers, then os._exit(0) when the parent changes."""
    try:
        parent = os.getppid()
    except OSError:
        _drain_buffered_telemetry()
        os._exit(0)
    if parent <= 1:
        return  # already orphaned/unsupported (e.g. some containers) — skip
    while True:
        time.sleep(5.0)
        try:
            current = os.getppid()
        except OSError:
            _drain_buffered_telemetry()
            os._exit(0)
        if current != parent:
            _drain_buffered_telemetry()
            os._exit(0)


def _install_exit_watchdog():
    """Ensure the server dies with its parent (signals + parent-pid poll); never reads stdin."""
    def _signal_handler(_signum, _frame):
        raise SystemExit(0)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _signal_handler)
        except (ValueError, OSError):
            # Non-main thread or unsupported signal — best-effort.
            pass

    threading.Thread(target=_watch_parent_loop, daemon=True).start()


# Counts tools ACTUALLY registered on the FastMCP `mcp` instance rather than a
# hardcoded literal, so a dropped tools_*.py import or removed @mcp.tool
# decorator trips this guard.
def _count_fastmcp_tools():
    """Count registered tools via FastMCP internals; AttributeError degrades to 0."""
    try:
        return len(mcp._tool_manager.list_tools())
    except AttributeError:
        # FastMCP internals changed (SDK upgrade); can't count safely.
        return 0


def verify_tool_count() -> None:
    """Raise AssertionError when the registered tool count drifts from the expected."""
    actual = _count_fastmcp_tools()
    assert actual == _EXPECTED_TOOL_COUNT, (
        f"Expected {_EXPECTED_TOOL_COUNT} tools, but found {actual}. "
        "If you added or removed a tool, update _EXPECTED_TOOL_COUNT in server.py."
    )


def _timestamped_print(msg: str, file=None) -> None:
    """Print ``[<local time>] <msg>``; defaults to stderr (stdout is the stdio JSON-RPC channel)."""
    from datetime import datetime

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", file=file or sys.stderr, flush=True)


# --- HTTP transport: auth gate, bind policy, health probe -------------------

_HTTP_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
_HTTP_WILDCARD_HOSTS = ("0.0.0.0", "::")
_HEALTHZ_PATH = "/healthz"
_auth_logger = logging.getLogger(__name__)


def _authorization_header(scope) -> str | None:
    """Return the first Authorization header value in the ASGI scope, or None."""
    for name, value in scope.get("headers", []):
        if name == b"authorization":
            return value.decode("latin-1")
    return None


class _BearerAuthMiddleware:
    """Pure-ASGI bearer gate: 401 on bad credentials; non-HTTP scopes and /healthz pass through."""

    def __init__(self, app, api_key: str):
        self._app = app
        self._api_key = api_key

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") == _HEALTHZ_PATH:
            await self._app(scope, receive, send)
            return
        if bearer_key_ok(_authorization_header(scope), self._api_key):
            await self._app(scope, receive, send)
            return
        # Never log the presented credential or the configured key.
        _auth_logger.warning(
            "cairn: rejected unauthorized HTTP request for %s", scope.get("path", "")
        )
        body = b'{"error":"unauthorized"}'
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"www-authenticate", b"Bearer"),
            ],
        })
        await send({"type": "http.response.body", "body": body})


def _transport_security_for(host: str) -> TransportSecuritySettings:
    """Host-header protection for the bind class; off on wildcard, where the API key is the gate."""
    if host in _HTTP_LOOPBACK_HOSTS:
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
        )
    if host in _HTTP_WILDCARD_HOSTS:
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=False, allowed_hosts=[], allowed_origins=[],
        )
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[host, f"{host}:*", "127.0.0.1:*", "localhost:*", "[::1]:*"],
        allowed_origins=[],
    )


def _require_http_api_key(host: str, api_key: str | None) -> None:
    """Refuse (exit 1, pre-bind) a non-loopback HTTP bind without a resolved key."""
    if api_key or host in _HTTP_LOOPBACK_HOSTS:
        return
    _timestamped_print(
        f"cairn: error: refusing to serve HTTP on non-loopback host '{host}' "
        f"without an API key. Set CAIRN_MCP_API_KEY or pass --api-key."
    )
    sys.exit(1)


def _register_healthz_route() -> None:
    """Register GET /healthz on the singleton exactly once (custom_route appends unconditionally)."""
    if any(
        getattr(route, "path", None) == _HEALTHZ_PATH
        for route in mcp._custom_starlette_routes
    ):
        return
    from ._server_core import healthz_response

    mcp.custom_route(_HEALTHZ_PATH, methods=["GET"])(healthz_response)


def _build_http_app(
    host: str | None,
    port: int | None,
    api_key: str | None,
    stateless: bool,
):
    """Apply the bind policy to mcp.settings, then build the ASGI app (settings snapshot once)."""
    if host:
        mcp.settings.host = host
    if port:
        mcp.settings.port = port
    mcp.settings.stateless_http = bool(stateless)
    mcp.settings.transport_security = _transport_security_for(mcp.settings.host)
    _require_http_api_key(mcp.settings.host, api_key)
    _register_healthz_route()
    # ASGI callables are untyped: the bare app or the auth-wrapped one.
    app: Any = mcp.streamable_http_app()
    if api_key:
        app = _BearerAuthMiddleware(app, api_key)
    return app


def run(
    transport: str = "stdio",
    port: int | None = None,
    host: str | None = None,
    api_key: str | None = None,
    stateless: bool = False,
):
    """Run the MCP server (``api_key`` pre-resolved; None means keyless), with boot catch-up."""
    # Per-process session id: metric_buffering/telemetry/builder stamp rows
    # with CAIRN_SESSION (default "unknown"); setdefault keeps an externally
    # provided id in control.
    os.environ.setdefault("CAIRN_SESSION", uuid4().hex[:12])

    # Configure the `cairn` logger namespace only, with a stderr handler:
    # stdout is the JSON-RPC channel under stdio.
    configure_logging()

    # Long-running server boot: suppress non-actionable third-party noise.
    quiet_server_noise()

    # Fail fast if tool registration drifted.
    verify_tool_count()

    # Stdio servers die with their client: the parent-pid watchdog is wrong
    # for SSE (launchd/zsh parent), where SIGTERM manages the daemon.
    if transport == "stdio":
        _install_exit_watchdog()

    db_path = os.environ.get("CAIRN_DB") or str(resolve_store().db)
    workspace = resolve_store().workspace

    # Boot guard for missing store: check if symbols table exists
    # Use raw sqlite3 connection to avoid schema auto-creation
    try:
        check_conn = sqlite3.connect(db_path)
        tables = check_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='symbols'"
        ).fetchall()
        if not tables:
            _timestamped_print(
                "cairn: error: database is missing the 'symbols' table. "
                "Run 'cairn init && cairn build' first."
            )
            check_conn.close()
            sys.exit(1)
        check_conn.close()
    except Exception as e:
        # If we can't even check the DB, exit with a helpful message.
        # Name the resolved db path, the env resolution chain
        # in effect, and the CAIRN_HOME remediation -- not the bare exception.
        _timestamped_print(
            f"cairn: error: failed to check database: {e}. "
            f"Resolved db path: {db_path}. "
            f"Env resolution chain: {render_env_resolution_chain()}. "
            f"Fix: set CAIRN_HOME to the parent of the populated store "
            f"(default ~/.cairn), then run 'cairn init && cairn build' first."
        )
        sys.exit(1)

    # Warm cached semantic weights in a background thread so the first
    # semantic_search skips the lazy model load; never downloads.
    from cairn.graph.model_warmup import warm_models_in_background

    warm_models_in_background()

    # Read-only servers never hold the writer lock.
    read_only = os.environ.get("CAIRN_READ_ONLY", "").lower() in ("1", "true", "yes")

    # close() in `finally` so failed catch-up never pins the writer lock.
    if read_only:
        _timestamped_print(
            "cairn: read-only mode -- boot catch-up and memory decay "
            "skipped (run `cairn update` / `cairn memory decay` on the writable side)"
        )
    else:
        conn = None
        try:
            from cairn.graph.watcher import ensure_fresh_force

            conn = get_db(db_path)
            n = ensure_fresh_force(conn, str(workspace))
            if n:
                # stderr only: stdout is the stdio JSON-RPC channel, and a
                # plain-text line there corrupts the framing.
                _timestamped_print(
                    f"cairn: caught up {n} file(s) changed while the server was down"
                )
        except Exception as e:
            _timestamped_print(f"cairn: catch-up failed: {e}")
            if conn is not None:
                conn.rollback()
        finally:
            if conn is not None:
                conn.close()

    # Boot-time memory decay archives stale raw memories (writable side only).
    if not read_only:
        try:
            from cairn.memory.promotion import decay
            from cairn.okf.bundle import OKFBundle

            knowledge_path = str(resolve_store().knowledge)
            bundle = OKFBundle(knowledge_path)
            # Open a writable conn so decay can also reap embedding rows orphaned
            # by the tier moves (otherwise dead vectors accumulate and tax the
            # brute-force memory cosine scan on every recall).
            reap_conn = get_db(db_path)
            try:
                decay_result = decay(bundle, conn=reap_conn)
            finally:
                reap_conn.close()
            if decay_result.get("expired_raw", 0) > 0 or decay_result.get("archived_tribal", 0) > 0:
                _timestamped_print(
                    f"cairn: memory decay: archived {decay_result['expired_raw']} stale raw memories, "
                    f"{decay_result['archived_tribal']} stale tribal memories"
                )
        except Exception as e:
            # Don't fail server boot if decay has an issue (e.g., knowledge dir doesn't exist yet)
            _timestamped_print(f"cairn: memory decay failed (non-critical): {e}")

    # Live watching keeps the graph fresh for edits made while this server
    # runs; start() is a logged no-op without the watch extra or with
    # CAIRN_WATCH=0, and test boots must not leave a real observer thread.
    live_watcher = None
    if not read_only and not os.environ.get("PYTEST_CURRENT_TEST"):
        from cairn.graph.watcher import FileWatcherService

        live_watcher = FileWatcherService(workspace=str(workspace), db_path=db_path)
        live_watcher.start()

    try:
        if transport == "sse":
            # SSE-only: a stdio server is itself a potential stray and must not
            # kill its siblings; the sweeper lets a restarted daemon self-heal.
            _install_stray_sweeper(db_path, interval_s=60.0)

            # FastMCP.run() in mcp>=1.0 reads host/port from mcp.settings, not kwargs.
            if port:
                mcp.settings.port = port
            # stdout (the default stream here): SSE has no JSON-RPC stdio
            # framing to protect, and this line is the daemon's readiness signal.
            _timestamped_print(
                f"cairn: MCP server listening on "
                f"http://{mcp.settings.host}:{mcp.settings.port}/sse",
                file=sys.stdout,
            )
            mcp.run(transport="sse")
        elif transport == "http":
            app = _build_http_app(
                host=host, port=port, api_key=api_key, stateless=stateless
            )
            import uvicorn

            # Readiness signal, mirroring the SSE bind print: stdout has no
            # JSON-RPC framing to protect under HTTP.
            _timestamped_print(
                f"cairn: MCP server listening on "
                f"http://{mcp.settings.host}:{mcp.settings.port}/mcp",
                file=sys.stdout,
            )
            server = uvicorn.Server(
                uvicorn.Config(
                    app,
                    host=mcp.settings.host,
                    port=mcp.settings.port,
                    log_level=mcp.settings.log_level.lower(),
                )
            )
            server.run()
        else:
            mcp.run()
    finally:
        # Clean shutdown of the watcher (joins the observer). The stdio
        # parent-death watchdog's os._exit bypasses this finally -- acceptable:
        # the observer thread is daemonized exactly for that exit path.
        if live_watcher is not None:
            live_watcher.stop()


def _run_stray_sweep(db_path: str) -> int:
    """One stray-sweep pass; emits stray_swept only when a pass actually killed."""
    from ..mcp_server import lifecycle as lc
    from cairn.telemetry import STRAY_SWEPT, emit as _emit

    killed = lc.sweep_strays(db_path, log=True)
    if killed:
        # emit is best-effort (never raises); count is a small int (bounded).
        _emit(STRAY_SWEPT, count=killed)
    return killed


def _install_stray_sweeper(db_path: str, interval_s: float = 60.0):
    """Background daemon thread that periodically evicts orphan `cairn serve` pids."""
    def _loop():
        # Delay the first sweep so a freshly-started daemon doesn't race a
        # still-initializing sibling it shouldn't touch.
        time.sleep(interval_s)
        while True:
            try:
                _run_stray_sweep(db_path)
            except Exception:
                # The sweeper must never take the daemon down.
                pass
            time.sleep(interval_s)

    t = threading.Thread(target=_loop, name="cairn-stray-sweeper", daemon=True)
    t.start()


if __name__ == "__main__":
    run()
