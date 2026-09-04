"""cairn MCP server: exposes graph query tools to AI agents.

Implements 22 tools across 4 layers (graph, knowledge base + compass,
memory, knowledge). Transport: stdio (default) or SSE, via the mcp SDK (FastMCP).

This file owns boot (sys.path bootstrap shim, boot catch-up, parent-pid
watchdog) and the run() entry point; tool implementations live in the
tools_*.py modules and decorate the shared FastMCP instance from _server_core.
"""
from __future__ import annotations

import os
import signal
import sqlite3
import sys
import threading
import time
from pathlib import Path
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

# Wire the metric-buffering conn factory BEFORE importing any tools_*.py:
# the first @instrument-wrapped tool call would otherwise hit a None factory.
# _conn in _server_core is exactly the connection factory metric_buffering
# needs (open graph DB for the current workspace).
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
from . import tools_graph    # noqa: F401
from . import tools_knowledge  # noqa: F401
from . import tools_memory   # noqa: F401
from . import tools_wiki     # noqa: F401

# Expected tool count - assertion fires if tools are missing due to import issues
_EXPECTED_TOOL_COUNT = 22


def _drain_buffered_telemetry() -> None:
    """Synchronously drain every buffered telemetry sink (best-effort).

    The parent-death watchdog exits via ``os._exit(0)`` from a non-main
    thread, which bypasses ``atexit`` entirely -- so the sinks' atexit drains
    (telemetry sink ``_flush_all``, ``embed_buffering._flush``) never run and
    up to 30s of buffered events/tool_metrics, the OTLP side buffer, and 15s
    of queued memory embeddings would be silently lost on a NORMAL session
    end (client disconnect). Direct flush calls are the robust route:
    ``atexit`` only fires on main-thread interpreter shutdown, so there is
    nothing to hook from the watchdog thread. Each flush is individually
    isolated so one failing sink can't block the others, and a drain failure
    must never prevent the exit that follows it.
    """
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
    """Body of the parent-death watchdog thread (see _install_exit_watchdog).

    Module-level (not nested) so the drain-then-exit contract is unit-testable
    without spawning the thread.
    """
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
    """Ensure the server dies when its parent (the MCP client) dies.

    Two mechanisms: SIGTERM/SIGINT -> SystemExit in the main thread, and a
    background daemon thread polling the parent pid (reparented to init on
    POSIX) -> buffered-telemetry drain + os._exit(0). Deliberately does NOT
    read stdin: the MCP SDK's anyio transport is the sole reader of the stdin
    fd, and reading it here steals bytes from JSON-RPC messages.
    """
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
    """Count tools actually registered on the FastMCP instance.

    Reads the live registry synchronously via FastMCP internals. The private
    API can move across SDK versions, so :class:`AttributeError` degrades to a
    safe count of 0 rather than crashing the boot guard.
    """
    try:
        return len(mcp._tool_manager.list_tools())
    except AttributeError:
        # FastMCP internals changed (SDK upgrade); can't count safely.
        return 0


def verify_tool_count() -> None:
    """Raise AssertionError if the registered tool count drifts.

    Deliberately a callable rather than a module-level ``assert`` so merely
    importing :mod:`cairn.mcp_server` never trips it; a regression should
    surface at server start, not as an import-time error.
    """
    actual = _count_fastmcp_tools()
    assert actual == _EXPECTED_TOOL_COUNT, (
        f"Expected {_EXPECTED_TOOL_COUNT} tools, but found {actual}. "
        "If you added or removed a tool, update _EXPECTED_TOOL_COUNT in server.py."
    )


def run(transport: str = "stdio", port: int | None = None):
    """Run the MCP server.

    Runs a one-time catch-up at boot to absorb edits made while the server was
    down, then (FRESH-1) starts a live file watcher so source edits made while
    this process runs are reindexed within the debounce window (~2s). The
    watcher needs the ``[watch]`` extra; without it freshness falls back to
    boot catch-up + explicit ``cairn update``. It never starts in read-only
    mode or when CAIRN_WATCH=0.
    """
    # Per-process session id: metric_buffering/telemetry/builder stamp rows
    # with CAIRN_SESSION (default "unknown"); setdefault keeps an externally
    # provided id in control.
    os.environ.setdefault("CAIRN_SESSION", uuid4().hex[:12])

    # Central logging config for the server surface: reads CAIRN_LOG_LEVEL
    # (default WARNING) and attaches a stderr handler to the `cairn` logger
    # only — never root. stdout is the JSON-RPC channel under stdio, so every
    # other diagnostic in this file is already hand-stamped to stderr; the
    # logger handler follows the same rule. FastMCP pins its own level
    # (_server_core.py:75) to avoid reconfiguring root, which this complements
    # rather than fights (it configures the `cairn` namespace, not root).
    configure_logging()

    # Long-running server boot: suppress non-actionable third-party noise.
    quiet_server_noise()

    # Fail fast if tool registration drifted.
    verify_tool_count()

    # Stdio servers should die when their MCP client disconnects. The watchdog
    # polls os.getppid() and self-exits when the parent changes -- correct for
    # stdio where the client IS the parent, but wrong for SSE where the parent
    # is launchd/zsh. SSE daemons are managed via `cairn serve stop` (SIGTERM).
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
            from datetime import datetime
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{ts}] cairn: error: database is missing the 'symbols' table. "
                  f"Run 'cairn init && cairn build' first.", file=sys.stderr, flush=True)
            check_conn.close()
            sys.exit(1)
        check_conn.close()
    except Exception as e:
        # If we can't even check the DB, exit with a helpful message.
        # FR-004 (D-008): name the resolved db path, the env resolution chain
        # in effect, and the CAIRN_HOME remediation -- not the bare exception.
        from datetime import datetime
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts}] cairn: error: failed to check database: {e}. "
              f"Resolved db path: {db_path}. "
              f"Env resolution chain: {render_env_resolution_chain()}. "
              f"Fix: set CAIRN_HOME to the parent of the populated store "
              f"(default ~/.cairn), then run 'cairn init && cairn build' first.",
              file=sys.stderr, flush=True)
        sys.exit(1)

    # Warm the semantic models (embedder + reranker) in a background daemon
    # thread (P0-1): the first semantic_search otherwise pays the full lazy
    # model load (~9.4s measured, ~5s of it HF Hub metadata round-trips even
    # on cached weights). Runs on the shared path so BOTH stdio and SSE get
    # it, before serving starts, without blocking boot (thread started, not
    # joined). Only ever warms weights already in the local HF cache -- it
    # never downloads -- and is inert for hash/openai embed backends and a
    # disabled reranker. Placed after the DB guard so an unbootable server
    # doesn't load weights it will never use, and before the catch-up pass
    # so weights load in parallel with reindexing. The kill switch
    # (CAIRN_WARM_MODELS=0/false/no) is checked inside the function so the
    # gate is unit-testable; the warm thread never writes to stdout (stdout
    # is the JSON-RPC channel under stdio) -- it logs via the
    # stderr-configured `cairn` logger only.
    from cairn.graph.model_warmup import warm_models_in_background

    warm_models_in_background()

    # Read-only mode: the shared SSE daemon opens the DB with mode=ro so it
    # can never hold the writer lock and therefore never contends with
    # `cairn build`/`cairn embed`/`cairn memory`. The two boot write paths
    # below (catch-up reindex, memory decay) are SKIPPED in read-only mode:
    # they are covered by the writable CLI side (`cairn update`, `cairn memory
    # decay`). Serving-time analytics writes already no-op under read-only.
    read_only = os.environ.get("CAIRN_READ_ONLY", "").lower() in ("1", "true", "yes")

    # Boot catch-up: absorb edits made while no server was running.
    # conn.close() lives in `finally` (with a preceding rollback()) so a
    # mid-transaction failure doesn't leave an uncommitted write transaction
    # pinning SQLite's writer lock for the life of this process.
    if read_only:
        from datetime import datetime
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts}] cairn: read-only mode -- boot catch-up and memory decay "
              f"skipped (run `cairn update` / `cairn memory decay` on the writable side)",
              file=sys.stderr, flush=True)
    else:
        conn = None
        try:
            from cairn.graph.watcher import ensure_fresh_force

            conn = get_db(db_path)
            n = ensure_fresh_force(conn, str(workspace))
            if n:
                # Boot log line goes to stderr, NOT stdout: for stdio transport,
                # stdout IS the JSON-RPC channel the MCP client reads, and a
                # plain-text line written there before mcp.run() corrupts the
                # framing.
                from datetime import datetime
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"[{ts}] cairn: caught up {n} file(s) changed while the server was down", file=sys.stderr, flush=True)
        except Exception as e:
            from datetime import datetime
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{ts}] cairn: catch-up failed: {e}", file=sys.stderr, flush=True)
            if conn is not None:
                conn.rollback()
        finally:
            if conn is not None:
                conn.close()

    # Run memory decay at server boot to archive stale raw memories automatically.
    # Skipped in read-only mode: decay writes, so it belongs on the writable CLI side.
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
                from datetime import datetime
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(
                    f"[{ts}] cairn: memory decay: archived {decay_result['expired_raw']} stale raw memories, "
                    f"{decay_result['archived_tribal']} stale tribal memories",
                    file=sys.stderr, flush=True
                )
        except Exception as e:
            # Don't fail server boot if decay has an issue (e.g., knowledge dir doesn't exist yet)
            from datetime import datetime
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{ts}] cairn: memory decay failed (non-critical): {e}", file=sys.stderr, flush=True)

    # Live file watching (FRESH-1): keep the graph fresh for edits made while
    # this server runs. Started on the shared path so BOTH stdio and SSE get
    # it, after the DB guard and boot catch-up, with the SAME workspace/db the
    # server resolved (passed explicitly -- the graph layer must not import
    # mcp_server). start() is a logged no-op when the [watch] extra is absent
    # or CAIRN_WATCH=0, and never runs in read-only mode (the watcher writes
    # pending_sync rows + reindexes, so the read-only SSE daemon stays
    # writer-free by construction). The PYTEST_CURRENT_TEST guard mirrors the
    # model-warmup pattern: in-process test boots of run() must not leave a
    # real filesystem observer thread watching the developer's machine across
    # test boundaries.
    live_watcher = None
    if not read_only and not os.environ.get("PYTEST_CURRENT_TEST"):
        from cairn.graph.watcher import FileWatcherService

        live_watcher = FileWatcherService(workspace=str(workspace), db_path=db_path)
        live_watcher.start()

    try:
        if transport == "sse":
            # The shared SSE daemon is the canonical writer-free reader. Stray
            # per-editor stdio `cairn serve` processes can still hold the WAL lock
            # and reintroduce "database is locked" because the stray opened the DB
            # read-write. A background sweeper evicts them periodically so a daemon
            # crash+restart self-heals. Runs only under SSE: a stdio server is
            # itself a potential stray and must not kill its siblings.
            _install_stray_sweeper(db_path, interval_s=60.0)

            # FastMCP.run() in mcp>=1.0 reads host/port from mcp.settings, not kwargs.
            if port:
                mcp.settings.port = port
            from datetime import datetime
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(
                f"[{ts}] cairn: MCP server listening on "
                f"http://{mcp.settings.host}:{mcp.settings.port}/sse",
                flush=True,
            )
            mcp.run(transport="sse")
        else:
            mcp.run()
    finally:
        # Clean shutdown of the watcher (joins the observer). The stdio
        # parent-death watchdog's os._exit bypasses this finally -- acceptable:
        # the observer thread is daemonized exactly for that exit path.
        if live_watcher is not None:
            live_watcher.stop()


def _run_stray_sweep(db_path: str) -> int:
    """One stray-sweep pass: kill orphan ``cairn serve`` PIDs + emit when any die.

    Factored out of ``_install_stray_sweeper``'s loop so the
    emit-on-genuine-kill behavior (spec §6.4 ``stray_swept``) is unit-testable
    without spinning the daemon thread (which sleeps ``interval_s`` between
    ticks). Returns the count killed. The emit fires ONLY when a pass actually
    killed something -- an idle sweep (the common case) emits nothing, so a
    healthy daemon doesn't generate a ``stray_swept`` row every 60s.
    """
    from ..mcp_server import lifecycle as lc
    from cairn.telemetry import STRAY_SWEPT, emit as _emit

    killed = lc.sweep_strays(db_path, log=True)
    if killed:
        # emit is best-effort (never raises); count is a small int (bounded).
        _emit(STRAY_SWEPT, count=killed)
    return killed


def _install_stray_sweeper(db_path: str, interval_s: float = 60.0):
    """Background daemon thread that periodically evicts orphan `cairn serve` PIDs.

    Called only from the SSE daemon path (see run()). Best-effort: the sweep
    logs one line per kill to stderr. Idempotent start.
    """
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
