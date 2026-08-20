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


CANDIDATES_LIMIT = 10


def symbol_candidates(
    conn: sqlite3.Connection, name: str, limit: int = CANDIDATES_LIMIT
) -> Dict:
    """Every exact-name symbol match with disambiguating context (FR-002).

    Each match carries ``name``, ``kind``, the defining ``file`` path and
    its ``repo_id`` — the context that lets a caller disambiguate instead
    of taking the viz layer's silent LIMIT-1 pick
    (``get_symbol_graph``'s focal lookup). The match is exact
    (``symbols.name = ?``, parameterized — a name is data, never SQL) and
    ordered by file path then repo_id (symbol id as the final tiebreaker)
    so identical queries return identical lists. A dangling ``file_id``
    yields ``file``/``repo_id`` None rather than dropping the symbol.
    Rows are capped at ``limit`` (bounds below 1 clamp to 1); the
    limit+1 over-fetch decides ``truncated`` so the cap stays visible in
    the response (FR-005 honesty). An empty or whitespace-only ``name``
    short-circuits to ``{"matches": [], "truncated": False}`` without
    touching the database.
    """
    if not name or not name.strip():
        return {"matches": [], "truncated": False}
    if limit < 1:
        limit = 1
    fetched = conn.execute(
        """
        SELECT s.name AS name, s.kind AS kind, f.path AS file, f.repo_id AS repo_id
        FROM symbols s
        LEFT JOIN files f ON s.file_id = f.id
        WHERE s.name = ?
        ORDER BY f.path ASC, f.repo_id ASC, s.id ASC
        LIMIT ?
        """,
        [name, limit + 1],
    ).fetchall()
    matches = [
        {
            "name": row["name"],
            "kind": row["kind"],
            "file": row["file"],
            "repo_id": row["repo_id"],
        }
        for row in fetched[:limit]
    ]
    # The over-fetched (limit + 1)-th row proves more same-name symbols
    # exist; the matches list itself stays at the cap.
    return {"matches": matches, "truncated": len(fetched) > limit}


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


HISTORY_PAGE_SIZE = 50


def _parse_history_cursor(cursor: Optional[str]) -> Optional[tuple]:
    """Decode a page cursor ``"<invoked_at>,<id>"`` into ``(float, int)``;
    None when absent or unparseable — a caller's cursor is a hint, never
    an error."""
    if not cursor:
        return None
    ts, sep, row_id = cursor.partition(",")
    if not sep:
        return None
    try:
        return float(ts), int(row_id)
    except ValueError:
        return None


def list_history(
    conn: sqlite3.Connection,
    tool_name: Optional[str] = None,
    session_id: Optional[str] = None,
    source: Optional[str] = None,
    since: Optional[float] = None,
    before: Optional[str] = None,
    after: Optional[str] = None,
    limit: int = HISTORY_PAGE_SIZE,
) -> Dict:
    """One bounded page of tool-invocation history, newest-first (FR-001).

    ``tool_name`` / ``session_id`` / ``source`` are exact-match filters
    (None = no filter); a no-match filter is an empty page, never an
    error.
    ``since`` (epoch seconds, None = all time) windows the page — and the
    neighbor probes that decide ``next``/``prev`` — to ``invoked_at >=
    since``; rows with NULL ``invoked_at`` predate windowing and never
    match a window (FR-002, FR-006). Paging is
    keyset on ``(invoked_at, id)``: ``before`` yields the page strictly
    older than that cursor, ``after`` the page strictly newer, presented in
    the same newest-first order. A cursor is the opaque
    ``"<invoked_at>,<id>"`` string a prior result's ``next``/``prev``
    carried; an unparseable cursor is ignored (no filter), never an error.
    The result carries ``rows`` (at most ``limit`` row dicts), ``next``
    (cursor of the older page, None when exhausted) and ``prev`` (cursor
    of the newer page, None when none). ``invoked_at`` is a raw
    ``time.time()`` epoch float — the MCP sink writes it directly, so
    ordering is numeric and the value is returned verbatim, never parsed
    as ISO. Pre-migration rows carry NULL sizes; their token estimates are
    None (unknown), not 0. ``args_summary`` is returned as stored —
    already redacted and truncated at the write chokepoint
    (``MAX_ARGS_SUMMARY_CHARS``); this layer never expands it.
    """
    if limit < 1:
        limit = 1
    before_key = _parse_history_cursor(before)
    after_key = _parse_history_cursor(after)
    backward = after_key is not None

    filter_clauses: List[str] = []
    filter_params: List[object] = []
    if tool_name is not None:
        filter_clauses.append("tool_name = ?")
        filter_params.append(tool_name)
    if session_id is not None:
        filter_clauses.append("session_id = ?")
        filter_params.append(session_id)
    if source is not None:
        # FR-002: 'cli' vs 'mcp' (the column default) — exact match, no
        # allow-list, same discipline as tool/session.
        filter_clauses.append("source = ?")
        filter_params.append(source)
    if since is not None:
        # NULL invoked_at never satisfies the comparison: pre-windowing
        # rows only ever surface on all-time (since=None) pages.
        filter_clauses.append("invoked_at >= ?")
        filter_params.append(since)

    clauses = list(filter_clauses)
    params = list(filter_params)
    if before_key is not None:
        clauses.append("(invoked_at, id) < (?, ?)")
        params.extend(before_key)
    if after_key is not None:
        clauses.append("(invoked_at, id) > (?, ?)")
        params.extend(after_key)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    # The over-fetched (limit + 1)-th row proves a further page exists in
    # the fetch direction; NULL invoked_at never satisfies either row-value
    # comparison, so such rows only ever appear on a cursorless first page.
    direction = "ASC, id ASC" if backward else "DESC, id DESC"
    fetched = conn.execute(
        f"""
        SELECT id, tool_name, session_id, source, invoked_at, duration_ms,
               status, error_message, req_chars, resp_chars, args_summary
        FROM tool_metrics{where}
        ORDER BY invoked_at {direction}
        LIMIT ?
        """,
        [*params, limit + 1],
    ).fetchall()
    page = fetched[:limit]
    if backward:
        page = list(reversed(page))

    rows = [
        {
            "id": row["id"],
            "tool_name": row["tool_name"],
            "session_id": row["session_id"],
            "source": row["source"],
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
        for row in page
    ]

    def has_neighbor(edge: dict, comparison: str) -> bool:
        probe_clauses = filter_clauses + [f"(invoked_at, id) {comparison} (?, ?)"]
        probe_where = f" WHERE {' AND '.join(probe_clauses)}"
        return (
            conn.execute(
                f"SELECT 1 FROM tool_metrics{probe_where} LIMIT 1",
                [*filter_params, edge["invoked_at"], edge["id"]],
            ).fetchone()
            is not None
        )

    next_cursor = None
    prev_cursor = None
    if rows:
        newest, oldest = rows[0], rows[-1]
        # Forward fetches learn "more older rows exist" from the over-fetch;
        # a backward fetch only proves newer rows, so it probes instead.
        more_older = len(fetched) > limit if not backward else has_neighbor(oldest, "<")
        if more_older:
            next_cursor = f"{oldest['invoked_at']},{oldest['id']}"
        if has_neighbor(newest, ">"):
            prev_cursor = f"{newest['invoked_at']},{newest['id']}"

    return {"rows": rows, "next": next_cursor, "prev": prev_cursor}


def get_tool_tokens(
    conn: sqlite3.Connection, since: Optional[float] = None
) -> List[dict]:
    """Per-tool estimated context-token aggregates, ranked by total desc
    (FR-006).

    The estimate is ``SUM(req_chars) // CHARS_PER_TOKEN`` plus
    ``SUM(resp_chars) // CHARS_PER_TOKEN`` — the same constant the bench
    suite uses, so dashboard and bench numbers stay comparable. Rows with
    NULL sizes (recorded before the size columns existed) contribute zero
    tokens but still count as calls; ``mean_tokens`` is ``total / calls``.
    ``since`` (epoch seconds, None = all time) computes calls, sums — and
    therefore aggregates and ranking — within the window only (FR-003);
    rows with NULL ``invoked_at`` predate windowing and never match a
    window.
    """
    where = " WHERE invoked_at >= ?" if since is not None else ""
    rows = conn.execute(
        f"""
        SELECT tool_name,
               COUNT(*) AS calls,
               SUM(req_chars) AS total_req_chars,
               SUM(resp_chars) AS total_resp_chars
        FROM tool_metrics{where}
        GROUP BY tool_name
        """,
        [since] if since is not None else [],
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

# Bounds for the chains view (FR-004): chains rendered at once, and calls
# kept per chain before the expand affordance takes over.
CHAINS_MAX_CHAINS = 20
CHAINS_CALLS_PER_CHAIN = 25


def get_session_chains(
    conn: sqlite3.Connection,
    since: Optional[float] = None,
    session_id: Optional[str] = None,
    max_chains: int = CHAINS_MAX_CHAINS,
    calls_per_chain: int = CHAINS_CALLS_PER_CHAIN,
    expand: Optional[str] = None,
) -> Dict:
    """Tool calls grouped per session as ordered chains, bounded for
    rendering (FR-004, FR-007).

    A session's calls are ordered by ``invoked_at`` (a raw ``time.time()``
    epoch float — gaps are computed numerically, never parsed as ISO) and
    a new chain starts wherever consecutive calls are more than
    :data:`SESSION_GAP_S` seconds apart. Calls with NULL ``invoked_at``
    never split a chain — their distance is unknowable — and stay in the
    current one. Sessions order newest-activity-first (all-NULL sessions
    last) and chains within a session chronologically; a single-call
    session is still one chain. ``since`` (epoch seconds, None = all
    time) windows the rows before grouping (FR-002): sessions and chains
    with no in-window calls vanish from the output entirely, and NULL
    ``invoked_at`` calls predate windowing and never match a window — an
    empty window is an empty result, never an error. ``session_id``
    (exact value, None = no filter) reads only that session's rows
    (FR-002): it composes with the window predicate in the same WHERE,
    and a no-match session is the empty wrapper, never an error.

    The flat chain list is capped at ``max_chains`` after flattening
    (newest sessions' chains first); each chain keeps only its newest
    ``calls_per_chain`` calls — ``started_at`` then reflects the first
    included call while ``ended_at`` and ``call_count`` keep the full
    chain's truth. Every chain carries ``shown_calls`` and
    ``truncated_calls`` (call_count > shown_calls); the returned wrapper
    carries ``chains``, ``total_chains`` (chains in the windowed result
    before the cap) and ``truncated`` (total_chains > len(chains)). The
    chains of the session whose id equals ``expand`` (exact value match)
    are exempt from the per-chain cap — the chain-list cap still applies.
    Bounds below 1 are clamped to 1, never an error.
    """
    if max_chains < 1:
        max_chains = 1
    if calls_per_chain < 1:
        calls_per_chain = 1
    clauses: List[str] = []
    params: List[object] = []
    if session_id is not None:
        clauses.append("session_id = ?")
        params.append(session_id)
    if since is not None:
        # NULL invoked_at never satisfies the comparison: pre-windowing
        # rows only ever surface on all-time (since=None) chains.
        clauses.append("invoked_at >= ?")
        params.append(since)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"""
        SELECT id, tool_name, session_id, invoked_at, duration_ms, status
        FROM tool_metrics{where}
        ORDER BY session_id, invoked_at
        """,
        params,
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
    all_chains: List[dict] = []
    for session in sessions:
        all_chains.extend(session["chains"])

    chains_out: List[dict] = all_chains[:max_chains]
    for chain in chains_out:
        calls = chain["calls"]
        expanded = expand is not None and chain["session_id"] == expand
        if not expanded and len(calls) > calls_per_chain:
            # Chains are chronological: the newest tail is what renders.
            calls = calls[-calls_per_chain:]
        chain["calls"] = calls
        chain["shown_calls"] = len(calls)
        chain["truncated_calls"] = chain["call_count"] > len(calls)
        if chain["truncated_calls"]:
            timestamps = [
                c["invoked_at"] for c in calls if c["invoked_at"] is not None
            ]
            chain["started_at"] = timestamps[0] if timestamps else None
    return {
        "chains": chains_out,
        "total_chains": len(all_chains),
        "truncated": len(all_chains) > len(chains_out),
    }


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
