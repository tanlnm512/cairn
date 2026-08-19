"""Read-only data access for the dashboard.

Every dashboard read goes through :func:`get_read_only_db`, which opens the
graph DB via SQLite's ``file:...?mode=ro`` URI: such a connection can never
hold the writer lock, so the dashboard cannot contend with — let alone
mutate — writer processes (FR-010). View-data assembly functions are pure
functions over the returned connection.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from cairn.bench.agent_suite import CHARS_PER_TOKEN
from cairn.graph.ann_index import ann_backend_enabled, index_exists, index_row_count
from cairn.graph.embeddings import current_model, embed_count, is_hash_fallback
from cairn.graph.reranker import reranker_available
from cairn.graph.schema import get_db
from cairn.llm.tasks import list_tasks
from cairn.okf.bundle import OKFBundle
from cairn.paths import resolve_store
from cairn.viz import query as viz_query

GRAPH_SCOPES = ("symbol", "module", "impact", "deps", "repo")


def list_projects(conn: sqlite3.Connection) -> List[dict]:
    """One summary row per indexed project: counts, freshness, embedding status.

    Freshness prefers the newest ``files.indexed_at`` and falls back to
    ``repos.indexed_at`` for projects whose file rows carry no timestamp.
    ``repos.path`` is workspace-relative — returned verbatim, never resolved
    against the dashboard's cwd.
    """
    rows = conn.execute(
        """
        SELECT r.id, r.name, r.path, r.language,
               r.indexed_at AS repo_indexed_at,
               (SELECT COUNT(*) FROM files f WHERE f.repo_id = r.id) AS file_count,
               (SELECT COUNT(*) FROM symbols s JOIN files f ON s.file_id = f.id
                WHERE f.repo_id = r.id) AS symbol_count,
               (SELECT COUNT(*) FROM edges e JOIN symbols s ON e.source_id = s.id
                JOIN files f ON s.file_id = f.id
                WHERE f.repo_id = r.id) AS edge_count,
               (SELECT MAX(f.indexed_at) FROM files f WHERE f.repo_id = r.id)
                   AS last_file_indexed
        FROM repos r
        ORDER BY r.name
        """
    ).fetchall()

    embedded_counts: Dict[str, int] = {
        row["repo_id"]: row["embedded"]
        for row in conn.execute(
            """
            SELECT f.repo_id AS repo_id, COUNT(DISTINCT e.symbol_id) AS embedded
            FROM embeddings e
            JOIN symbols s ON e.symbol_id = s.id
            JOIN files f ON s.file_id = f.id
            GROUP BY f.repo_id
            """
        )
    }
    models: Dict[str, List[str]] = {}
    for row in conn.execute(
        """
        SELECT DISTINCT f.repo_id AS repo_id, e.model
        FROM embeddings e
        JOIN symbols s ON e.symbol_id = s.id
        JOIN files f ON s.file_id = f.id
        ORDER BY e.model
        """
    ):
        models.setdefault(row["repo_id"], []).append(row["model"])

    projects = []
    for row in rows:
        embedded = embedded_counts.get(row["id"], 0)
        if embedded == 0 or row["symbol_count"] == 0:
            status = "not"
        elif embedded < row["symbol_count"]:
            status = "partial"
        else:
            status = "embedded"
        projects.append(
            {
                "id": row["id"],
                "name": row["name"],
                "path": row["path"],
                "language": row["language"],
                "file_count": row["file_count"],
                "symbol_count": row["symbol_count"],
                "edge_count": row["edge_count"],
                "last_indexed": row["last_file_indexed"] or row["repo_indexed_at"],
                "embedding_status": status,
                "embedding_models": models.get(row["id"], []),
            }
        )
    return projects


def get_graph(
    conn: sqlite3.Connection,
    scope: str = "module",
    focus: Optional[str] = None,
    repo: Optional[str] = None,
    depth: Optional[int] = None,
) -> Dict:
    """Dispatch to a viz query scope; returns its ``{nodes, edges, metadata}``
    verbatim — the scope functions already cap result size (LIMIT 30/50,
    ``max_nodes``), and their metadata carries the possibly-truncated counts.
    """
    if scope == "symbol":
        return viz_query.get_symbol_graph(conn, focus or "", 1 if depth is None else depth)
    if scope == "module":
        return viz_query.get_module_graph(conn, focus or "")
    if scope == "impact":
        return viz_query.get_impact_graph(conn, focus or "", 3 if depth is None else depth)
    if scope == "deps":
        return viz_query.get_deps_graph(conn)
    if scope == "repo":
        return viz_query.get_repo_graph(conn, repo or "", max_nodes=30)
    raise ValueError(f"unknown graph scope: {scope!r}")


def _parse_ts(value) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp (concept or ``build_runs``) to an aware
    UTC datetime, or None when missing/unparseable."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _age_str(started_at) -> Optional[str]:
    """Human-readable age of a ``build_runs.started_at`` value, or None.

    Formatted exactly as ``cairn doctor``'s freshness check renders it, so
    the panel and doctor read identically on the same database.
    """
    dt = _parse_ts(started_at)
    if dt is None:
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


def get_health(conn: sqlite3.Connection, db_path: Optional[str] = None) -> Dict:
    """Health panel data (FR-008): DB size, index freshness, vector backend
    mode, reranker status.

    The backend probes call the same graph-layer helpers ``cairn doctor``
    uses, so the panel's conclusions agree with doctor's on the same
    database. DB reads degrade to null/0 on a missing table rather than
    raising. ``db_path`` falls back to the connection's own file (empty for
    an in-memory DB, hence size 0).
    """
    if db_path is None:
        row = conn.execute("PRAGMA database_list").fetchone()
        db_path = (row["file"] if row else "") or None
    try:
        db_size_bytes = os.stat(db_path).st_size if db_path else 0
    except OSError:
        db_size_bytes = 0

    last_build_at: Optional[str] = None
    last_build_age: Optional[str] = None
    try:
        brow = conn.execute(
            "SELECT started_at FROM build_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if brow:
            last_build_at = brow["started_at"]
            last_build_age = _age_str(last_build_at)
    except sqlite3.Error:
        pass

    model = current_model()
    try:
        embedding_rows = embed_count(conn)
    except sqlite3.Error:
        embedding_rows = 0
    try:
        # The index probes are moot with no embeddings: a fresh store has no
        # vec0 table by design, which is doctor's "no embeddings to index
        # yet", not a missing index -- reported as None rather than False.
        index_present: Optional[bool] = (
            index_exists(conn, model) if embedding_rows else None
        )
        index_rows = index_row_count(conn, model) if index_present else None
    except sqlite3.Error:
        index_present, index_rows = None, None

    return {
        "db_size_bytes": db_size_bytes,
        "last_build_at": last_build_at,
        "last_build_age": last_build_age,
        "embed_backend": (
            os.environ.get("CAIRN_EMBED_BACKEND", "local").strip().lower()
            or "local"
        ),
        "hash_fallback": is_hash_fallback(),
        "ann_configured": (
            os.environ.get("CAIRN_ANN_BACKEND", "sqlite-vec").strip().lower()
            or "sqlite-vec"
        ),
        "ann_backend_enabled": ann_backend_enabled(),
        "ann_model": model,
        "ann_embedding_rows": embedding_rows,
        "ann_index_exists": index_present,
        "ann_index_rows": index_rows,
        "reranker_available": reranker_available(),
    }


def get_recent_memories(knowledge_dir: str, limit: int = 20) -> List[dict]:
    """Recent memories, newest-first, each with type and title (FR-009).

    Reads the OKF bundle's ``memory/`` namespace directly; an unreadable
    concept file is skipped, never fatal.
    """
    bundle = OKFBundle(knowledge_dir)
    entries: List[dict] = []
    for cid in bundle.list_concepts(prefix="memory/"):
        try:
            concept = bundle.read_concept(cid)
        except Exception:
            continue
        entries.append(
            {
                "id": cid,
                "type": concept.extensions.get("memory_type") or concept.type,
                "title": concept.title or cid,
                "tier": concept.extensions.get("memory_tier", ""),
                "timestamp": concept.timestamp or "",
            }
        )
    entries.sort(key=lambda e: _parse_ts(e["timestamp"]) or _EPOCH, reverse=True)
    return entries[:limit]


def get_task_queue(knowledge_dir: str, status: Optional[str] = None) -> List[dict]:
    """LLM task-queue entries as plain dicts, optionally filtered by status
    (pending / in-progress / done / failed) (FR-009)."""
    return [
        {
            "id": t.id,
            "kind": t.task_kind,
            "status": t.status,
            "resource": t.resource,
            "assigned_to": t.assigned_to,
            "created_at": t.created_at,
            "claimed_at": t.claimed_at,
            "completed_at": t.completed_at,
        }
        for t in list_tasks(OKFBundle(knowledge_dir), status=status)
    ]


def list_history(
    conn: sqlite3.Connection,
    tool_name: Optional[str] = None,
    session_id: Optional[str] = None,
) -> List[dict]:
    """Tool-invocation history, newest-first (FR-005).

    ``tool_name`` / ``session_id`` are exact-match filters (None = no
    filter); a no-match filter is an empty list, never an error.
    ``invoked_at`` is a raw ``time.time()`` epoch float — the MCP sink
    writes it directly, so ordering is numeric and the value is returned
    verbatim, never parsed as ISO. Pre-migration rows carry NULL sizes;
    their token estimates are None (unknown), not 0. ``args_summary`` is
    returned as stored — already redacted and truncated at the write
    chokepoint (``MAX_ARGS_SUMMARY_CHARS``); this layer never expands it.
    """
    clauses: List[str] = []
    params: List[object] = []
    if tool_name is not None:
        clauses.append("tool_name = ?")
        params.append(tool_name)
    if session_id is not None:
        clauses.append("session_id = ?")
        params.append(session_id)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"""
        SELECT id, tool_name, session_id, invoked_at, duration_ms,
               status, error_message, req_chars, resp_chars, args_summary
        FROM tool_metrics{where}
        ORDER BY invoked_at DESC
        """,
        params,
    ).fetchall()

    return [
        {
            "id": row["id"],
            "tool_name": row["tool_name"],
            "session_id": row["session_id"],
            "invoked_at": row["invoked_at"],
            "duration_ms": row["duration_ms"],
            "status": row["status"],
            "error_message": row["error_message"],
            "req_chars": row["req_chars"],
            "resp_chars": row["resp_chars"],
            "est_req_tokens": (
                None if row["req_chars"] is None else row["req_chars"] // CHARS_PER_TOKEN
            ),
            "est_resp_tokens": (
                None if row["resp_chars"] is None else row["resp_chars"] // CHARS_PER_TOKEN
            ),
            "args_summary": row["args_summary"],
        }
        for row in rows
    ]


def get_tool_tokens(conn: sqlite3.Connection) -> List[dict]:
    """Per-tool estimated context-token aggregates, ranked by total desc
    (FR-006).

    The estimate is ``SUM(req_chars) // CHARS_PER_TOKEN`` plus
    ``SUM(resp_chars) // CHARS_PER_TOKEN`` — the same constant the bench
    suite uses, so dashboard and bench numbers stay comparable. Rows with
    NULL sizes (recorded before the size columns existed) contribute zero
    tokens but still count as calls; ``mean_tokens`` is ``total / calls``.
    """
    rows = conn.execute(
        """
        SELECT tool_name,
               COUNT(*) AS calls,
               SUM(req_chars) AS total_req_chars,
               SUM(resp_chars) AS total_resp_chars
        FROM tool_metrics
        GROUP BY tool_name
        """
    ).fetchall()

    entries: List[dict] = []
    for row in rows:
        est_req = (row["total_req_chars"] or 0) // CHARS_PER_TOKEN
        est_resp = (row["total_resp_chars"] or 0) // CHARS_PER_TOKEN
        total = est_req + est_resp
        entries.append(
            {
                "tool_name": row["tool_name"],
                "calls": row["calls"],
                "est_req_tokens": est_req,
                "est_resp_tokens": est_resp,
                "total_tokens": total,
                "mean_tokens": total / row["calls"],
            }
        )
    entries.sort(key=lambda e: (-e["total_tokens"], e["tool_name"]))
    return entries


# Seconds of inactivity that split a session into separate chains.
SESSION_GAP_S = 1800


def get_session_chains(conn: sqlite3.Connection) -> List[dict]:
    """Tool calls grouped per session as ordered chains (FR-007).

    A session's calls are ordered by ``invoked_at`` (a raw ``time.time()``
    epoch float — gaps are computed numerically, never parsed as ISO) and
    a new chain starts wherever consecutive calls are more than
    :data:`SESSION_GAP_S` seconds apart. Calls with NULL ``invoked_at``
    never split a chain — their distance is unknowable — and stay in the
    current one. The flat chain list orders sessions newest-activity-first
    (all-NULL sessions last) and chains within a session chronologically;
    a single-call session is still one chain.
    """
    rows = conn.execute(
        """
        SELECT id, tool_name, session_id, invoked_at, duration_ms, status
        FROM tool_metrics
        ORDER BY session_id, invoked_at
        """
    ).fetchall()

    grouped: Dict[object, List[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(row["session_id"], []).append(row)

    sessions: List[dict] = []
    for session_id, calls in grouped.items():
        chains: List[dict] = []
        last_ts: Optional[float] = None
        for row in calls:
            ts = row["invoked_at"]
            split = (
                bool(chains)
                and last_ts is not None
                and ts is not None
                and (ts - last_ts) > SESSION_GAP_S
            )
            if split or not chains:
                chains.append({"session_id": session_id, "calls": []})
            chain = chains[-1]
            chain["calls"].append(
                {
                    "id": row["id"],
                    "tool_name": row["tool_name"],
                    "invoked_at": ts,
                    "duration_ms": row["duration_ms"],
                    "status": row["status"],
                }
            )
            if ts is not None:
                last_ts = ts
        for chain in chains:
            timestamps = [
                c["invoked_at"] for c in chain["calls"] if c["invoked_at"] is not None
            ]
            chain["started_at"] = timestamps[0] if timestamps else None
            chain["ended_at"] = timestamps[-1] if timestamps else None
            chain["call_count"] = len(chain["calls"])
        sessions.append({"last_activity": last_ts, "chains": chains})

    sessions.sort(
        key=lambda s: (s["last_activity"] is not None, s["last_activity"] or 0.0),
        reverse=True,
    )
    chains_out: List[dict] = []
    for session in sessions:
        chains_out.extend(session["chains"])
    return chains_out


class MissingDatabaseError(FileNotFoundError):
    """The graph DB file does not exist — nothing read-only to open."""


def get_read_only_db(db_path: str | None = None) -> sqlite3.Connection:
    """Open the graph DB read-only (SQLite ``mode=ro`` URI).

    The file must already exist: a read-only open of a missing DB is an
    error for a writer to fix (``cairn init && cairn build``), never
    something the dashboard creates. Callers render the missing-DB state.
    """
    path = Path(db_path) if db_path else resolve_store().db
    if not path.exists():
        raise MissingDatabaseError(str(path))
    return get_db(db_path, read_only=True)
