"""Shared core for the MCP server: the FastMCP singleton + conn/store helpers.

Holds the single ``FastMCP("cairn")`` instance every tools_*.py module
decorates, plus helpers: ``_conn`` (graph DB connection), ``_store`` (workspace
store resolution), ``_bundle`` (the OKFBundle for the current workspace), and
``_repo_of`` (symbol -> repo lookup).
"""
from __future__ import annotations

import os
import sqlite3
import warnings
from contextlib import asynccontextmanager
from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP
from pydantic_settings.exceptions import IncompleteFieldDefinitionWarning

from cairn.graph.queries import find_definition
from cairn.graph.schema import get_db
from cairn.okf.bundle import OKFBundle
from cairn.paths import resolve_store


# --- Lifespan-managed shared state ---------------------------------------
# Shared state is yielded once by ``app_lifespan`` as an ``AppContext``,
# accessed inside tools via ``ctx.request_context.lifespan_context``. The
# module-level helpers below stay so existing tools keep working; new tools may
# take a ``ctx`` param and read from ``AppContext`` directly.

@dataclass
class AppContext:
    """Shared server state, yielded once by ``app_lifespan``."""

    db_path: str
    knowledge_path: str
    read_only: bool


@asynccontextmanager
async def app_lifespan(server: FastMCP):
    """Resolve shared state once at server startup; tear down on shutdown.

    Reads CAIRN_DB / CAIRN_KNOWLEDGE (set by ``cairn serve``) or falls back to
    the workspace store. Yields an ``AppContext``.
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
        pass


# The single FastMCP instance every tools_*.py module decorates. Imported as
# `from ._server_core import mcp` so @mcp.tool() decorators attach here.
# log_level is pinned to WARNING so constructing this singleton (imported by
# every CLI invocation) doesn't reconfigure the root logger and clobber other
# commands' output.
#
# mcp's own Settings.lifespan field has an unresolved forward reference to
# FastMCP (the mcp SDK never calls model_rebuild() after FastMCP is defined),
# so pydantic-settings warns on every construction. Upstream bug, harmless --
# suppressed narrowly so it doesn't fire on every CLI invocation.
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", category=IncompleteFieldDefinitionWarning)
    mcp = FastMCP("cairn", lifespan=app_lifespan, log_level="WARNING")


def _store():
    """Resolve the central store for this workspace.

    Honors CAIRN_DB / CAIRN_KNOWLEDGE if set; otherwise resolves from the
    workspace context.
    """
    return resolve_store()


def _read_only_mode() -> bool:
    """True if this server process is serving read-only (no write lock).

    Set by `cairn serve run --read-only` via the CAIRN_READ_ONLY env var. When
    true, _conn() opens the DB read-only so it can never contend with writers.
    """
    return os.environ.get("CAIRN_READ_ONLY", "").lower() in ("1", "true", "yes")


def _conn():
    """Open a SQLite connection to the graph DB for this workspace.

    Read-only when CAIRN_READ_ONLY is set; read-write otherwise. Callers that
    MUST write real data should call _rw_conn() instead so the requirement is
    explicit at the call site.
    """
    return get_db(
        os.environ.get("CAIRN_DB") or str(_store().db),
        read_only=_read_only_mode(),
    )


def _rw_conn():
    """Open a writable SQLite connection, even in read-only server mode.

    For write tools whose *purpose* is to write. In a read-only daemon this
    will contend with the CLI writer and can fail with "database is locked",
    surfaced as an error string.
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

    NOTE: ``pending_sync`` is only populated by a live filesystem watcher
    (the optional ``[watch]`` extra's ``watchdog.Observer``). cairn does not ship
    a live watcher in the default install -- ``watcher.py`` is boot-time
    stat-based catch-up only -- so in a default deployment this banner will not
    fire. The check is cheap and correct when a watcher is present; it simply
    stays inert otherwise.

    Guarded: when ``pending_sync`` is empty or the table is absent, this adds
    effectively zero latency (a single indexed ``SELECT ... WHERE path IN (...)``).
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
# Index freshness is browsable data, exposed as a subscribable resource a
# client lists under resources/ and polls cheaply.

@mcp.resource("cairn://status")
def status_resource() -> str:
    """Index freshness + build stats for the current workspace.

    Returns a compact status block: symbol/edge/file counts, edges-resolved
    fraction, files pending reindex (staleness), and the DB path.
    """

    try:
        conn = _conn()
        try:
            from cairn.graph.stats import get_stats

            stats = get_stats(conn)
        finally:
            conn.close()
    except Exception as e:
        return f"cairn status: unavailable ({e})"

    # Staleness: total files pending reindex.
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
