"""codegraph MCP server: exposes graph query tools to AI agents.

Implements 26 tools across 5 layers:
  L1 (graph): find_definition, get_callers, get_callees, impact_analysis,
              search_symbols, cross_repo_deps, explore, semantic_search
  L2/L3 (knowledge base): search_knowledge, get_compass,
                          trace_flow, generate_flow
  L4 (memory + router): recall_memory, record_memory, ask_compass,
                         visualize_graph, memory_promote, memory_demote,
                         memory_delete, memory_decay
  L5 (knowledge): knowledge_add, knowledge_search, knowledge_delete,
                  knowledge_status, trace_workflow (ordered procedural
                  steps -- a knowledge doc with doc_type="workflow",
                  see src/knowledge/workflow.py)

Transport: stdio (default) or SSE. Uses the mcp SDK (FastMCP).

Freshness is reconciled once at boot (see run()) by diffing the files table
against disk and re-indexing anything changed while no server was running.
Tool calls do NOT re-check freshness per-query (concurrency safety -- see
src/graph/watcher.py) -- edits made while a `cg serve` process is up require a
server restart (or `cg build`) to show up in query results.

Architecture: this file is the thin entry point. Tool implementations live
in split modules (tools_graph, tools_memory, tools_knowledge, tools_compass),
each of which decorates the shared FastMCP instance from _server_core.
Metric buffering lives in metric_buffering. This file owns boot (the
sys.path bootstrap shim, the boot catch-up, the parent-pid watchdog) and
the run() entry point.
"""
from __future__ import annotations

import os
import signal
import sqlite3
import sys
import threading
import time
from pathlib import Path

# Bootstrap: allow running as a script (python .../server.py) OR as a module
# (python -m src.mcp_server.server). Agents invoke the script directly, so we
# ensure the project root is on sys.path so that absolute `src.` imports work.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from codegraph.graph.schema import get_db
from codegraph.paths import resolve_store

# Wire the metric-buffering conn factory BEFORE importing any tools_*.py:
# the first @instrument-wrapped tool call would otherwise hit a None factory.
# _conn in _server_core is exactly the connection factory metric_buffering
# needs (open graph DB for the current workspace).
from ._server_core import _conn, mcp
from .metric_buffering import configure_conn
configure_conn(_conn)

# Importing the tools_*.py modules registers every @mcp.tool() on the shared
# `mcp` instance via decorator side effects. The names aren't used directly
# here -- the import is what does the work.
from . import tools_compass  # noqa: F401
from . import tools_graph    # noqa: F401
from . import tools_knowledge  # noqa: F401
from . import tools_memory   # noqa: F401

# Expected tool count - assertion fires if tools are missing due to import issues
_EXPECTED_TOOL_COUNT = 26


def _install_exit_watchdog():
    """Ensure the server dies when its parent (the MCP client) dies.

    Without this, stdio MCP servers park on stdin forever if the editor is
    hard-closed, force-quit, or the laptop sleeps — the SDK's stdin-EOF
    handler is sometimes missed, leaving `cg serve` processes accumulating
    across sessions and holding the SQLite WAL lock.

    Two mechanisms:
      1. SIGTERM/SIGINT -> SystemExit raised in the main thread.
      2. Background daemon thread polling the parent pid; when it changes
         (reparented to init on POSIX) -> SystemExit.

    Deliberately does NOT touch stdin: the MCP SDK's own stdio transport
    (anyio) is the sole reader of stdin. An earlier version of this watchdog
    read raw bytes off the stdin fd directly to detect EOF, which raced with
    anyio's reader for the same fd — bytes belonging to JSON-RPC messages
    were randomly stolen by the watchdog, so the server would never see a
    complete request and `cg serve` appeared to hang/never respond to
    clients. Polling os.getppid() detects the same "parent is gone"
    condition without consuming anything from stdin.
    """
    def _signal_handler(_signum, _frame):
        raise SystemExit(0)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _signal_handler)
        except (ValueError, OSError):
            # Non-main thread or unsupported signal — best-effort.
            pass

    def _watch_parent():
        try:
            parent = os.getppid()
        except OSError:
            return
        if parent <= 1:
            return  # already orphaned/unsupported (e.g. some containers) — skip
        while True:
            time.sleep(5.0)
            try:
                current = os.getppid()
            except OSError:
                os._exit(0)
            if current != parent:
                os._exit(0)

    threading.Thread(target=_watch_parent, daemon=True).start()


# Verify the registered tool count matches expected.
#
# Counts the tools ACTUALLY registered on the FastMCP `mcp` instance rather
# than a hardcoded literal, so a dropped import (a tools_*.py module failing
# to load, or a decorator being removed) actually trips this guard. Each
# tools_*.py module registers its tools via @mcp.tool() decorator side
# effects at import time (see the imports above); if any of those imports
# silently fails, fewer tools land on `mcp` and the count drops.
#
# FastMCP exposes the live registry via the tool manager; the sync
# ToolManager.list_tools() (the backing store behind the async
# FastMCP.list_tools()) returns the registered Tool objects directly, so we
# don't need an event loop here.
def _count_fastmcp_tools():
    """Count tools actually registered on the FastMCP instance."""
    return len(mcp._tool_manager.list_tools())


def verify_tool_count() -> None:
    """Raise AssertionError if the registered tool count drifts.

    The tools_*.py modules register their @mcp.tool() decorators as an import
    side effect (see the imports above). If one of those imports silently
    fails, or a decorator is removed, fewer tools land on `mcp` and the count
    drops. This guard catches that.

    Deliberately a callable rather than a module-level ``assert`` so that
    merely importing :mod:`codegraph.mcp_server` (which re-exports ``run``)
    never trips it -- a tool-count regression should surface at server start,
    not turn into an ``AssertionError`` for unrelated importers. Called from
    :func:`run` and exercised by the test suite.
    """
    actual = _count_fastmcp_tools()
    assert actual == _EXPECTED_TOOL_COUNT, (
        f"Expected {_EXPECTED_TOOL_COUNT} tools, but found {actual}. "
        "If you added or removed a tool, update _EXPECTED_TOOL_COUNT in server.py."
    )


def run(transport: str = "stdio", port: int | None = None):
    """Run the MCP server.

    Runs a one-time catch-up at boot to absorb edits made while the server was
    down. This is the ONLY freshness check -- tool calls do not re-check
    per-query, so edits made while this process is running require a restart
    to be picked up (see module docstring above).
    """
    # Fail fast if tool registration drifted (a tools_*.py import silently
    # failed or a @mcp.tool decorator was removed). See verify_tool_count().
    verify_tool_count()

    # Stdio servers should die when their MCP client disconnects (prevents
    # stale-process buildup across editor sessions). The watchdog polls
    # os.getppid() and self-exits when the parent changes -- correct for stdio
    # where the client IS the parent, but WRONG for SSE where the parent is
    # launchd/zsh and changes are unrelated to client connections. SSE daemons
    # are managed via `cg serve stop` (SIGTERM) instead.
    if transport == "stdio":
        _install_exit_watchdog()

    db_path = os.environ.get("CODEGRAPH_DB") or str(resolve_store().db)
    workspace = resolve_store().workspace

    # Boot guard for missing store: check if symbols table exists
    # Use raw sqlite3 connection to avoid schema auto-creation
    try:
        check_conn = sqlite3.connect(db_path)
        check_conn.row_factory = sqlite3.Row
        tables = check_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='symbols'"
        ).fetchall()
        if not tables:
            from datetime import datetime
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{ts}] codegraph: error: database is missing the 'symbols' table. "
                  f"Run 'cg init && cg build' first.", file=sys.stderr, flush=True)
            check_conn.close()
            sys.exit(1)
        check_conn.close()
    except Exception as e:
        # If we can't even check the DB, exit with a helpful message
        from datetime import datetime
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts}] codegraph: error: failed to check database: {e}. "
              f"Run 'cg init && cg build' first.", file=sys.stderr, flush=True)
        sys.exit(1)

    # Read-only mode: the shared SSE daemon opens the DB with mode=ro so it
    # can never hold the writer lock and therefore never contends with
    # `cg build`/`cg embed`/`cg memory`. The two boot write paths below
    # (catch-up reindex, memory decay) are SKIPPED in read-only mode: they
    # would either error on the read-only connection or, worse, open a
    # writable connection and reintroduce contention. Their jobs are covered
    # by the writable CLI side -- `cg update` for catch-up, `cg memory decay`
    # (or the CLI's own boot) for archival. Serving-time analytics writes
    # (memory_refs ref-counting, tool_metrics) already no-op under read-only.
    read_only = os.environ.get("CODEGRAPH_READ_ONLY", "").lower() in ("1", "true", "yes")

    # Boot catch-up: absorb edits made while no server was running.
    #
    # conn.close() lives in `finally`, not after ensure_fresh_force(), because
    # a mid-transaction failure there (e.g. FOREIGN KEY constraint) must not
    # leave the connection open: an uncommitted write transaction pinned by
    # the exception's traceback would hold SQLite's writer lock for the rest
    # of this (long-lived) process's life, permanently locking out `cg update`
    # / `cg build` with "database is locked" no matter how long their
    # busy_timeout is. rollback() first so the failed transaction doesn't
    # linger even if close() itself is delayed.
    if read_only:
        from datetime import datetime
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{ts}] codegraph: read-only mode -- boot catch-up and memory decay "
              f"skipped (run `cg update` / `cg memory decay` on the writable side)",
              file=sys.stderr, flush=True)
    else:
        conn = None
        try:
            from codegraph.graph.watcher import ensure_fresh_force

            conn = get_db(db_path)
            n = ensure_fresh_force(conn, str(workspace))
            if n:
                # Boot log line -- plain print, not rich. Written to stderr, NOT
                # stdout: for stdio transport, stdout IS the JSON-RPC channel the
                # MCP client reads, and any plain-text line written there before
                # mcp.run() starts corrupts the framing (client fails to parse it
                # as JSON-RPC and drops the connection -- the process stays alive
                # but the client never sees any tools). SSE transport also logs
                # to stderr here for consistency; its own "listening on" line
                # below is the only stdout write and only happens under SSE.
                # Format kept parseable: "[YYYY-MM-DD HH:MM:SS] message".
                from datetime import datetime
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"[{ts}] codegraph: caught up {n} file(s) changed while the server was down", file=sys.stderr, flush=True)
        except Exception as e:
            from datetime import datetime
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{ts}] codegraph: catch-up failed: {e}", file=sys.stderr, flush=True)
            if conn is not None:
                conn.rollback()
        finally:
            if conn is not None:
                conn.close()

    # Run memory decay at server boot to archive stale raw memories automatically.
    # This prevents raw memory growth in long-running servers without manual intervention.
    # Skipped in read-only mode (see note above): decay writes, so it belongs on the
    # writable CLI side. Wrapped in the same read_only guard as the catch-up.
    if not read_only:
        try:
            from codegraph.memory.promotion import decay
            from codegraph.okf.bundle import OKFBundle

            knowledge_path = str(resolve_store().knowledge)
            bundle = OKFBundle(knowledge_path)
            decay_result = decay(bundle)
            if decay_result.get("expired_raw", 0) > 0 or decay_result.get("archived_tribal", 0) > 0:
                from datetime import datetime
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(
                    f"[{ts}] codegraph: memory decay: archived {decay_result['expired_raw']} stale raw memories, "
                    f"{decay_result['archived_tribal']} stale tribal memories",
                    file=sys.stderr, flush=True
                )
        except Exception as e:
            # Don't fail server boot if decay has an issue (e.g., knowledge dir doesn't exist yet)
            from datetime import datetime
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{ts}] codegraph: memory decay failed (non-critical): {e}", file=sys.stderr, flush=True)

    if transport == "sse":
        # The shared SSE daemon is the canonical writer-free reader. Stray
        # per-editor stdio `cg serve` processes (left over from hard-closed
        # editors, force-quits, or sleep -- the exit watchdog can miss these)
        # can still hold the WAL lock and reintroduce "database is locked"
        # even against a read-only daemon, because the *stray* opened the DB
        # read-write. A background sweeper evicts them periodically so a daemon
        # crash+restart self-heals without manual `cg serve stop`. Runs only
        # under SSE: a stdio server is itself a potential stray and must not
        # kill its siblings.
        _install_stray_sweeper(db_path, interval_s=60.0)

        # FastMCP.run() in mcp>=1.0 reads host/port from mcp.settings, not
        # from kwargs. Set them here.
        if port:
            mcp.settings.port = port
        from datetime import datetime
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(
            f"[{ts}] codegraph: MCP server listening on "
            f"http://{mcp.settings.host}:{mcp.settings.port}/sse",
            flush=True,
        )
        mcp.run(transport="sse")
    else:
        mcp.run()


def _install_stray_sweeper(db_path: str, interval_s: float = 60.0):
    """Background daemon thread that periodically evicts orphan `cg serve` PIDs.

    Only called from the SSE daemon path (see run()). The sweep itself is
    best-effort and logged to stderr: it prints one line per kill so the
    daemon log records self-healing events. Idempotent start (a single
    daemon process needs only one sweeper).
    """
    from ..mcp_server import lifecycle as lc

    def _loop():
        # Delay the first sweep so a freshly-started daemon doesn't race a
        # still-initializing sibling it shouldn't touch.
        time.sleep(interval_s)
        while True:
            try:
                lc.sweep_strays(db_path, log=True)
            except Exception:
                # The sweeper must never take the daemon down.
                pass
            time.sleep(interval_s)

    t = threading.Thread(target=_loop, name="cg-stray-sweeper", daemon=True)
    t.start()


if __name__ == "__main__":
    run()
