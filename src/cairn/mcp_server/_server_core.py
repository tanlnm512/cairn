"""Shared core for the MCP server: the FastMCP singleton + conn/store helpers.

Holds the single ``FastMCP("cairn")`` instance every tools_*.py module
decorates, plus helpers: ``_conn`` (graph DB connection), ``_store`` (workspace
store resolution), ``_bundle`` (the OKFBundle for the current workspace), and
``_repo_of`` (symbol -> repo lookup).
"""

from __future__ import annotations

import os
import sqlite3
import threading
import warnings
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

# IncompleteFieldDefinitionWarning was added to pydantic-settings in a later
# release; older versions (e.g. 2.14.x) don't define it and never emit it.
# Import defensively so this module loads on both old and new versions.
try:
    from pydantic_settings.exceptions import IncompleteFieldDefinitionWarning
except ImportError:  # pragma: no cover - depends on installed version
    IncompleteFieldDefinitionWarning = None  # type: ignore[assignment,misc]

from cairn.graph.queries import find_definition
from cairn.graph.schema import get_db
from cairn.okf.bundle import OKFBundle
from cairn.paths import resolve_store


# --- Lifespan + shared state ----------------------------------------------
#
# Config resolution: every tool resolves the DB path, knowledge path, and
# read-only flag through the module-level ``_conn()`` / ``_store()`` /
# ``_read_only_mode()`` helpers below, which read ``CAIRN_*`` env vars (set by
# ``cairn serve``) or fall back to the workspace store. This is the single
# source of truth — there is intentionally no per-request ``AppContext``
# threaded through ``ctx.request_context.lifespan_context``. An earlier
# iteration scaffolded one (``AppContext`` dataclass + ``app_lifespan`` body),
# but no tool consumed it; it was dead code that implied a threading contract
# that didn't exist. Removed to avoid confusing future readers.
#
# If per-request config (e.g. testable read-only overrides without env vars)
# is ever needed, wire ``ctx: Context`` through the tools and read from a
# revived lifespan context — see docs/audit-remediation/spec.md (A1).


@asynccontextmanager
async def app_lifespan(server: FastMCP):
    """Minimal lifespan: FastMCP requires one for startup/shutdown hooks.

    Yields nothing — tools resolve config via ``_conn()``/``_store()``, not
    via the lifespan context (see the note above).
    """
    try:
        yield None
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
# so pydantic-settings warns on every construction (on versions that define
# IncompleteFieldDefinitionWarning). Upstream bug, harmless -- suppressed
# narrowly so it doesn't fire on every CLI invocation. On older pydantic-
# settings that doesn't define the warning class, the filter is skipped.
with warnings.catch_warnings():
    if IncompleteFieldDefinitionWarning is not None:
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


# --- Read-connection reuse (perf phase P5) -----------------------------------
#
# Every tool call used to open a fresh SQLite connection (open + WAL/
# busy_timeout PRAGMAs + close). Thread-local pooling keeps one connection
# per (thread, db path) alive for the server's lifetime instead. Guards:
#
# * Thread-local: sqlite3 connections are thread-affine by default and each
#   tool call runs on one thread; no cross-thread sharing, so no
#   ``check_same_thread=False`` is ever needed.
# * Identity check: a full ``cairn build`` swaps the DB file atomically
#   (``os.replace``), and a pooled connection would keep reading the
#   unlinked old inode forever. Each ``_conn()`` call stats the path and
#   reopens when (st_dev, st_ino) changes.
# * ``close()`` on the wrapper is a no-op release, so tool bodies' existing
#   ``finally: conn.close()`` keeps working unchanged.
# * ``CAIRN_CONN_POOL=0`` disables pooling entirely (escape hatch).


class _PooledConnection:
    """Delegating wrapper whose ``close()`` releases to the thread cache.

    All attribute access (execute, cursor, row_factory, ...) delegates to the
    underlying ``sqlite3.Connection``; only ``close()`` is intercepted.
    """

    __slots__ = ("_conn",)

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def close(self) -> None:  # noqa: D102 - see class docstring
        return None

    def __getattr__(self, name):
        return getattr(self._conn, name)


_conn_tls = threading.local()


def _reset_conn_pool() -> None:
    """Close and drop this thread's pooled connections (tests only)."""
    cache = getattr(_conn_tls, "by_path", None)
    if cache:
        for _dev, _ino, conn in cache.values():
            try:
                conn.close()
            except sqlite3.Error:
                pass
        _conn_tls.by_path = {}


def _pooling_enabled() -> bool:
    return os.environ.get("CAIRN_CONN_POOL", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def _conn():
    """Open a SQLite connection to the graph DB for this workspace.

    Read-only when CAIRN_READ_ONLY is set; read-write otherwise. Callers that
    MUST write real data should call _rw_conn() instead so the requirement is
    explicit at the call site.

    Pooled per (thread, db path): the returned object's ``close()`` is a
    no-op release (see _PooledConnection). Set CAIRN_CONN_POOL=0 for the
    unpooled per-call behaviour.
    """
    db_path = os.environ.get("CAIRN_DB") or str(_store().db)
    if not _pooling_enabled():
        return get_db(db_path, read_only=_read_only_mode())

    cache = getattr(_conn_tls, "by_path", None)
    if cache is None:
        cache = _conn_tls.by_path = {}

    entry = cache.get(db_path)
    if entry is not None:
        dev, ino, conn = entry
        try:
            st = os.stat(db_path)
            if (st.st_dev, st.st_ino) == (dev, ino):
                return _PooledConnection(conn)
        except OSError:
            pass
        # The file was swapped (full build's os.replace) or deleted -- the
        # pooled connection reads a dead inode. Drop it and reopen.
        cache.pop(db_path, None)
        try:
            conn.close()
        except sqlite3.Error:
            pass

    conn = get_db(db_path, read_only=_read_only_mode())
    try:
        st = os.stat(db_path)
        # One entry per thread: a server re-pointed at another workspace
        # should not keep the old store's connection open.
        for other in [p for p in cache if p != db_path]:
            _d, _i, old = cache.pop(other)
            try:
                old.close()
            except sqlite3.Error:
                pass
        cache[db_path] = (st.st_dev, st.st_ino, conn)
    except OSError:
        pass  # unstatable path -- pool nothing, behaviour degrades to unpooled
    return _PooledConnection(conn)


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


def _build_age_str(started_at) -> str | None:
    """Human-readable age of a ``build_runs.started_at`` value, or None.

    ``started_at`` is ISO-8601 (``builder._iso_ts``). Mirrors the doctor
    command's ``_age_str`` (cli/system.py) but kept local so the server surface
    doesn't pull in CLI deps. None when the value is missing or unparseable so
    the status resource reports ``never`` rather than crashing.
    """
    from datetime import datetime, timezone

    if not started_at:
        return None
    try:
        dt = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    secs = int((datetime.now(timezone.utc) - dt).total_seconds())
    if secs < 0:
        return "just now"  # clock skew / a future-dated row
    if secs >= 86400:
        return f"{secs // 86400}d old"
    if secs >= 3600:
        return f"{secs // 3600}h old"
    if secs >= 60:
        return f"{secs // 60}m old"
    return f"{secs}s old"


def _health_block(conn) -> dict:
    """Compute the ``health`` block for the status resource (spec §6.5 / T14).

    Read-only + crash-proof: every probe is guarded so a missing table or
    unresolvable backend degrades to a null/0 field rather than raising --
    the status resource must never fail because a telemetry table is absent on
    an unmigrated DB. Mirrors the query shape ``cairn doctor`` uses
    (cli/system.py) but kept self-contained; the status resource is a separate
    surface and must not import CLI code.

    Fields:
    - degradations: active backend degradations (embeddings hash fallback; ANN
      unavailable when sqlite-vec was *expected*). Empty list when healthy.
    - pending_sync: count of pending_sync rows (0 when the table is absent).
    - last_build_age: age string of the newest build_runs row, or None
      ("never") when none recorded.
    - error_rate_24h: tool error rate over the last 24h (errors / total).
    - tool_calls_24h / tool_errors_24h: the numerator/denominator behind the
      rate, surfaced so the number is interpretable.
    """
    import time

    degradations: list[str] = []

    # Embeddings: silent hash fallback (configured 'local' but no
    # sentence-transformers). An explicit CAIRN_EMBED_BACKEND=hash is an
    # informed choice, not a degradation -- is_hash_fallback() already
    # accounts for that.
    try:
        from cairn.graph.embeddings import is_hash_fallback

        if is_hash_fallback():
            degradations.append("embeddings=hash_fallback")
    except Exception:
        pass

    # ANN: only a degradation when sqlite-vec was *expected* (env unset or
    # '=sqlite-vec', the default) but unavailable. An explicit
    # CAIRN_ANN_BACKEND=off is an informed choice -- mirrors the rationale in
    # ann_index.ann_backend_enabled() and the doctor's _check_ann. Beyond the
    # load probe, an embeddings-populated model with no vec0 table is also a
    # degradation (semantic queries silently run the brute-force scan) --
    # mirrors the doctor's index_exists probe.
    try:
        configured = (
            os.environ.get("CAIRN_ANN_BACKEND", "sqlite-vec").strip().lower()
            or "sqlite-vec"
        )
        if configured == "sqlite-vec":
            from cairn.graph.ann_index import ann_backend_enabled, index_exists
            from cairn.graph.embeddings import current_model, embed_count

            if not ann_backend_enabled():
                degradations.append("ann=unavailable")
            elif embed_count(conn) > 0 and not index_exists(conn, current_model()):
                degradations.append("ann=no_index")
    except Exception:
        pass

    try:
        row = conn.execute("SELECT COUNT(*) AS c FROM pending_sync").fetchone()
        pending_sync = row["c"] if row else 0
    except sqlite3.Error:
        pending_sync = 0

    last_build_age: str | None = None
    try:
        brow = conn.execute(
            "SELECT started_at FROM build_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if brow:
            last_build_age = _build_age_str(brow["started_at"])
    except sqlite3.Error:
        pass

    # tool_metrics.invoked_at is a raw time.time() epoch float (the buffered
    # sinks enqueue time.time() directly), so a numeric cutoff is correct here
    # -- mirroring the doctor's _check_tool_health.
    cutoff = time.time() - 24 * 3600
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS errors "
            "FROM tool_metrics WHERE invoked_at >= ?",
            (cutoff,),
        ).fetchone()
        total = row["total"] if row else 0
        # SUM over zero rows is SQL NULL -> None; coerce to 0 so the rate and
        # its display stay numeric when no tool calls were recorded.
        errors = (row["errors"] if row else 0) or 0
    except sqlite3.Error:
        total, errors = 0, 0
    error_rate_24h = (errors / total) if total else 0.0

    return {
        "degradations": degradations,
        "pending_sync": pending_sync,
        "last_build_age": last_build_age,
        "error_rate_24h": error_rate_24h,
        "tool_calls_24h": total,
        "tool_errors_24h": errors,
    }


# --- Index/build status as a Resource ----------------------------------
# Index freshness is browsable data, exposed as a subscribable resource a
# client lists under resources/ and polls cheaply.


@mcp.resource("cairn://status")
def status_resource() -> str:
    """Index freshness + build stats for the current workspace.

    Returns a compact status block: symbol/edge/file counts, edges-resolved
    fraction, files pending reindex (staleness), the DB path, and a ``health``
    block (backend degradations, pending-sync count, last-build age, 24h tool
    error rate -- spec observability-telemetry §6.5 / T14).
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

    # Staleness + health: read-only probes against the same DB. ``_health_block``
    # is computed first because it guards every table read internally (a missing
    # pending_sync/build_runs/tool_metrics table degrades to 0/never, never
    # raises); the bare staleness SELECT below can still raise on an unmigrated
    # DB, but by then health is already populated, so the block stays complete.
    stale_count = 0
    health: dict | None = None
    try:
        conn = _conn()
        try:
            health = _health_block(conn)
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

    # Health block (spec §6.5 / T14): backend degradations, pending-sync count,
    # last-build age, 24h tool error rate. ``health`` is None only when the
    # staleness/health connection itself failed -- then report unavailable
    # rather than omitting the block, so the surface shape stays stable.
    lines.append("health:")
    if health is None:
        lines.append("  unavailable")
    else:
        degs = health["degradations"]
        deg_str = "none" if not degs else ", ".join(degs)
        lines.append(f"  degradations: {deg_str}")
        lines.append(f"  pending_sync: {health['pending_sync']}")
        lines.append(f"  last_build_age: {health['last_build_age'] or 'never'}")
        lines.append(
            f"  error_rate_24h: {health['error_rate_24h']:.1%} "
            f"({health['tool_errors_24h']} errors / {health['tool_calls_24h']} calls)"
        )
    return "\n".join(lines)
