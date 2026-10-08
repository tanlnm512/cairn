"""Shared core for the MCP server: the FastMCP singleton + conn/store helpers."""

from __future__ import annotations

import os
import sqlite3
import threading
import warnings
from contextlib import asynccontextmanager
from datetime import date

from mcp.server.fastmcp import FastMCP

# IncompleteFieldDefinitionWarning was added to pydantic-settings in a later
# release; older versions (e.g. 2.14.x) don't define it and never emit it.
# Import defensively so this module loads on both old and new versions.
try:
    from pydantic_settings.exceptions import IncompleteFieldDefinitionWarning  # type: ignore[import-not-found, attr-defined]
except ImportError:  # pragma: no cover - depends on installed version
    IncompleteFieldDefinitionWarning = None  # type: ignore[assignment,misc]

from cairn.graph.queries import find_definition
from cairn.graph.schema import get_db
from cairn.okf.bundle import OKFBundle
from cairn.paths import resolve_store


# --- Lifespan + shared state ----------------------------------------------


@asynccontextmanager
async def app_lifespan(server: FastMCP):
    """Minimal lifespan: yields nothing; tools resolve config via module helpers."""
    try:
        yield None
    finally:
        pass


# The single FastMCP instance every tools_*.py module decorates; log_level is
# pinned so this import-time singleton never reconfigures the root logger.
with warnings.catch_warnings():
    if IncompleteFieldDefinitionWarning is not None:
        warnings.filterwarnings("ignore", category=IncompleteFieldDefinitionWarning)
    mcp = FastMCP("cairn", lifespan=app_lifespan, log_level="WARNING")


def _store():
    """Resolve the central store, honoring CAIRN_DB / CAIRN_KNOWLEDGE overrides."""
    return resolve_store()


def _read_only_mode() -> bool:
    """True when this server process serves read-only via CAIRN_READ_ONLY."""
    return os.environ.get("CAIRN_READ_ONLY", "").lower() in ("1", "true", "yes")


def _fresh_graph(conn):
    """Probe graph freshness on the caller's connection before a graph read."""
    from cairn.graph.watcher import refresh_for_query

    return refresh_for_query(
        conn, repair=False if _read_only_mode() else None
    )


# --- Read-connection reuse ---------------------------------------------------


class _PooledConnection:
    """Delegating SQLite connection wrapper whose close() releases to the thread cache."""

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
    """Open this workspace's graph DB; pooled per thread, and write tools use _rw_conn()."""
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
    """Open a writable connection even in read-only server mode (write-purpose tools only)."""
    return get_db(os.environ.get("CAIRN_DB") or str(_store().db), read_only=False)


def _bundle() -> OKFBundle:
    """Build an OKFBundle for the current workspace's .knowledge/ dir."""
    knowledge = os.environ.get("CAIRN_KNOWLEDGE") or str(_store().knowledge)
    return OKFBundle(knowledge)


def _session_id() -> str:
    """Session id recorded into memory_refs by MCP recall paths:
    ``mcp-<pid>-<YYYY-MM-DD>``."""
    return f"mcp-{os.getpid()}-{date.today().isoformat()}"


def _repo_of(conn, name: str) -> str:
    """Look up the repo that defines a symbol."""
    rows = find_definition(conn, name, limit=1)
    return rows[0]["repo"] if rows else ""


def _staleness_banner(conn, file_paths) -> str:
    """Return a staleness banner when any file path has an unindexed edit pending; empty otherwise."""
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


def _embed_degradation_footnote() -> str:
    """Cached-state degradation footnote; empty when no embedding degradation is active."""
    from cairn.graph.embed_ladder import degradation_footnote

    return degradation_footnote()


def _append_embed_degradation_footnote(text: str) -> str:
    """``text`` with the degradation footnote appended as one trailing
    line; byte-identical when no degradation is active."""
    footnote = _embed_degradation_footnote()
    return f"{text}\n{footnote}" if footnote else text


def _build_age_str(started_at) -> str | None:
    """Human-readable age of a build_runs.started_at value; None when missing or unparseable."""
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
    """Compute the crash-proof health block: degradations, pending_sync, build age, 24h tool errors."""
    import time

    degradations: list[str] = []

    # An explicit CAIRN_EMBED_BACKEND=hash is an informed choice, not a
    # degradation; is_hash_fallback() already accounts for it.
    try:
        from cairn.graph.embeddings import is_hash_fallback

        if is_hash_fallback():
            degradations.append("embeddings=hash_fallback")
    except Exception:
        pass

    # ANN: a degradation only when sqlite-vec was expected but unavailable,
    # missing its vec0 table, or drifted from the embed count; an explicit
    # CAIRN_ANN_BACKEND=off is an informed choice.
    try:
        from cairn.graph.ann_index import (
            ann_backend_enabled,
            configured_backend,
            index_exists,
            index_row_count,
        )

        configured = configured_backend()
        if configured == "sqlite-vec":
            from cairn.graph.embeddings import current_model, embed_count

            if not ann_backend_enabled():
                degradations.append("ann=unavailable")
            else:
                model = current_model()
                emb_n = embed_count(conn)
                if emb_n > 0 and not index_exists(conn, model):
                    degradations.append("ann=no_index")
                elif emb_n > 0:
                    idx_n = index_row_count(conn, model)
                    if idx_n is not None and idx_n != emb_n:
                        if idx_n < emb_n:
                            degradations.append(
                                f"ann=stale({emb_n - idx_n} unindexed)"
                            )
                        else:
                            degradations.append(
                                f"ann=stale({idx_n - emb_n} stale vectors)"
                            )
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


def healthz_payload() -> dict:
    """Bounded /healthz payload; an unreachable store degrades to status "unhealthy"."""
    store_reachable = False
    degradations = 0
    try:
        conn = _conn()
        try:
            conn.execute("SELECT 1").fetchone()
            degradations = len(_health_block(conn)["degradations"])
            store_reachable = True
        finally:
            conn.close()
    except Exception:
        pass
    return {
        "status": "ok" if store_reachable else "unhealthy",
        "store_reachable": store_reachable,
        "read_only": _read_only_mode(),
        "degradations": degradations,
    }


async def healthz_response(request):
    """GET /healthz handler: the bounded health payload as JSON (HTTP 200 even when unhealthy)."""
    from starlette.responses import JSONResponse

    return JSONResponse(healthz_payload())


# --- Index/build status as a Resource ----------------------------------
# Index freshness is browsable data, exposed as a subscribable resource a
# client lists under resources/ and polls cheaply.


@mcp.resource("cairn://status")
def status_resource() -> str:
    """Index freshness + build stats + a health block for the current workspace."""

    try:
        conn = _conn()
        try:
            from cairn.graph.stats import get_stats

            stats = get_stats(conn)
        finally:
            conn.close()
    except Exception as e:
        return f"cairn status: unavailable ({e})"

    # _health_block is computed first: it guards every table read, while the
    # bare staleness SELECT below can still raise on an unmigrated DB.
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

    # ``health`` is None only when the connection itself failed -- report
    # unavailable so the surface shape stays stable.
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
