"""Shared core for the MCP server: the FastMCP singleton + conn/store helpers.

Holds the single ``FastMCP("cairn")`` instance every tools_*.py module
decorates, plus the helpers every tool uses: ``_conn`` (graph DB connection),
``_store`` (workspace store resolution), ``_bundle`` (the OKFBundle for the
current workspace), and ``_repo_of`` (symbol -> repo lookup, used only by the
impact_analysis tool).
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP

from cairn.graph.queries import find_definition
from cairn.graph.schema import get_db
from cairn.okf.bundle import OKFBundle
from cairn.paths import resolve_store


# --- Lifespan-managed shared state ---------------------------------------
# The SDK-blessed pattern: a server is constructed with ``lifespan=app_lifespan``
# which yields an ``AppContext`` (the shared state) accessed inside tools via
# ``ctx.request_context.lifespan_context``. This is more idiomatic than the
# module-level ``_conn()``/``_bundle()`` re-resolution and future-proofs for a
# pooled backend (pgvector-style). The module-level helpers below stay as the
# current call sites so existing tools keep working unchanged; new tools may
# take a ``ctx`` param and read from ``AppContext`` directly.

@dataclass
class AppContext:
    """Shared server state, yielded once by ``app_lifespan``.

    Holds the resolved DB path and knowledge-dir so tools don't re-resolve the
    store on every call. A future pooled-backend migration would add the pool
    here and tear it down in the lifespan's finally block.
    """

    db_path: str
    knowledge_path: str
    read_only: bool


@asynccontextmanager
async def app_lifespan(server: FastMCP):
    """Resolve shared state once at server startup; tear down on shutdown.

    Reads CAIRN_DB / CAIRN_KNOWLEDGE (set by ``cairn serve``) or falls
    back to the workspace store. Yields an ``AppContext``; the yielded object
    is what ``ctx.request_context.lifespan_context`` returns inside a tool.
    """
    store = resolve_store()
    ctx = AppContext(
        db_path=os.environ.get("CAIRN_DB") or str(store.db),
        knowledge_path=os.environ.get("CAIRN_KNOWLEDGE") or str(store.knowledge),
        read_only=os.environ.get("CAIRN_READ_ONLY", "").lower() in ("1", "true", "yes"),
    )
    try:
        yield ctx
    finally:
        # No persistent resources to close today (conns are per-call). A future
        # connection pool would be closed here on shutdown.
        pass


# The single FastMCP instance every tools_*.py module decorates. Imported as
# `from ._server_core import mcp` so @mcp.tool() decorators attach here.
mcp = FastMCP("cairn", lifespan=app_lifespan)


def _store():
    """Resolve the central store for this workspace.

    Honors CAIRN_DB / CAIRN_KNOWLEDGE if set (cg serve sets both);
    otherwise resolves from the workspace context (cwd at launch).
    """
    return resolve_store()


def _read_only_mode() -> bool:
    """True if this server process is serving read-only (no write lock).

    Set by `cairn serve run --read-only` (and the launchd daemon plist) via the
    CAIRN_READ_ONLY env var. When true, _conn() opens the DB read-only so
    it can never contend with writers. The serving-time write paths (memory
    ref-counting, tool metrics) are analytics and already swallow read-only /
    OperationalError, so read-only mode silently degrades them to no-ops.
    """
    return os.environ.get("CAIRN_READ_ONLY", "").lower() in ("1", "true", "yes")


def _conn():
    """Open a SQLite connection to the graph DB for this workspace.

    Read-only when CAIRN_READ_ONLY is set (the safe shared-daemon mode);
    read-write otherwise. Callers that MUST write real data (record_memory,
    knowledge_add, etc.) should call _rw_conn() instead so the requirement is
    explicit at the call site -- a read-only server will degrade those tools
    gracefully rather than silently succeed-halfway.
    """
    return get_db(
        os.environ.get("CAIRN_DB") or str(_store().db),
        read_only=_read_only_mode(),
    )


def _rw_conn():
    """Open a writable SQLite connection, even in read-only server mode.

    For write tools (record_memory, knowledge_add, knowledge_delete,
    knowledge_status, memory_promote/delete) whose *purpose* is to write. In a
    read-only daemon this still opens writable -- it will contend with the CLI
    writer and can fail with "database is locked" under load, but that's the
    honest failure mode for a write tool, and the tool surfaces it as an error
    string instead of pretending success.
    """
    return get_db(os.environ.get("CAIRN_DB") or str(_store().db), read_only=False)


def _bundle() -> OKFBundle:
    """Build an OKFBundle for the current workspace's .knowledge/ dir."""
    knowledge = os.environ.get("CAIRN_KNOWLEDGE") or str(_store().knowledge)
    return OKFBundle(knowledge)


def _repo_of(conn, name: str) -> str:
    """Look up the repo that defines a symbol."""
    rows = find_definition(conn, name, limit=1)
    return rows[0]["repo"] if rows else ""


def _staleness_banner(conn, file_paths) -> str:
    """Return a staleness banner if any of ``file_paths`` has an unindexed edit
    pending in the ``pending_sync`` table; empty string otherwise.

    The ``pending_sync`` table is populated by the file watcher's debounce path
    when a source file changes. Graph tools pass the file paths in their result
    set, and a one-line banner is prepended when any are stale, so a
    long-running ``cairn serve`` doesn't silently answer from a stale graph.

    Guarded: when ``pending_sync`` is empty (the common case) or the table is
    absent, this adds effectively zero latency -- a single ``SELECT ... WHERE
    path IN (...)`` against an indexed primary key (``pending_sync.path``).
    """
    paths = [p for p in file_paths if p]
    if not paths:
        return ""
    # Cap the IN-list; for very large result sets a staleness check over all
    # rows isn't worth it -- the caller already truncated the display.
    paths = paths[:200]
    placeholders = ",".join("?" for _ in paths)
    try:
        rows = conn.execute(
            f"SELECT path FROM pending_sync WHERE path IN ({placeholders})", paths
        ).fetchall()
    except sqlite3.Error:
        # Table missing on an unmigrated DB -- staleness tracking is
        # best-effort, never fatal.
        return ""
    if not rows:
        return ""
    stale = sorted({r["path"] for r in rows if r["path"]})
    # Show repo-relative tails so the banner stays readable; cap at 3.
    shown = [p.split("/")[-1] for p in stale[:3]]
    more = f" (+{len(stale) - 3} more)" if len(stale) > 3 else ""
    return (
        f"⚠ Stale graph: {len(stale)} file(s) in this result have unindexed "
        f"edits pending reindex ({', '.join(shown)}{more}). Results may be "
        f"incomplete until `cairn update` runs. "  # trailing space separates from following output
    )


# --- Index/build status as a Resource ----------------------------------
# By the MCP spec's own boundary rule, "is the index fresh / how stale" is data
# the agent BROWSES, not an action -- it belongs as a subscribable resource a
# client lists under resources/ and polls cheaply, not as a tool that clutters
# the tool palette. The inline ``_staleness_banner`` above stays for per-query
# signals; this resource is the aggregate browsable surface.

@mcp.resource("cairn://status")
def status_resource() -> str:
    """Index freshness + build stats for the current workspace.

    Returns a compact, human-readable status block: symbol/edge/file counts,
    edges-resolved fraction, the count of files pending reindex (staleness),
    and the DB path. A client reads this via ``read_resource("cairn://status")``
    to decide whether to trust a graph query or first prompt ``cairn update``.
    """
    import json

    try:
        conn = _conn()
        try:
            from cairn.graph.stats import get_stats

            stats = get_stats(conn)
        finally:
            conn.close()
    except Exception as e:
        return f"cairn status: unavailable ({e})"

    # Staleness: total files pending reindex (not just those in a given query
    # result, which is what _staleness_banner reports).
    stale_count = 0
    try:
        conn = _conn()
        try:
            row = conn.execute("SELECT COUNT(*) AS c FROM pending_sync").fetchone()
            stale_count = row["c"] if row else 0
        finally:
            conn.close()
    except sqlite3.Error:
        pass  # pending_sync missing on an unmigrated DB -- report 0.

    total_edges = stats.get("edges", 0)
    resolved = stats.get("edges_resolved", 0)
    resolved_frac = (resolved / total_edges) if total_edges else 0.0
    db_path = os.environ.get("CAIRN_DB") or str(_store().db)

    lines = [
        "cairn status",
        f"  db: {db_path}",
        f"  repos: {stats.get('repos', 0)}",
        f"  files: {stats.get('files', 0)}",
        f"  symbols: {stats.get('symbols', 0)}",
        f"  edges: {total_edges} ({resolved} resolved, {resolved_frac:.0%})",
        f"  imports: {stats.get('imports', 0)}",
        f"  pending reindex: {stale_count} file(s)"
        + ("  ⚠ stale -- run `cairn update`" if stale_count else "  ✓ fresh"),
    ]
    if stats.get("skipped_total"):
        lines.append(f"  skipped files: {stats['skipped_total']}")
    return "\n".join(lines)
