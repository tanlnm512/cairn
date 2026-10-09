"""Read-only data access for the dashboard."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from html import escape as html_escape
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from cairn.okf.concept import OKFConcept

from cairn.bench.agent_suite import CHARS_PER_TOKEN
from cairn.dashboard.markdown import render_markdown_with_toc
from cairn.dashboard.tokenizer import (
    HEURISTIC_MODE,
    active_tokenizer_mode,
    estimate_tokens,
)
from cairn.graph.ann_index import (
    ann_backend_enabled,
    configured_backend,
    index_exists,
    index_row_count,
)
from cairn.graph.embeddings import (
    backend_name,
    current_model,
    embed_count,
    is_hash_fallback,
)
from cairn.graph.reranker import reranker_available
from cairn.graph.schema import get_db
from cairn.knowledge.store import normalize_doc_id, resolve_knowledge_doc
from cairn.llm.tasks import list_tasks
from cairn.okf.bundle import OKFBundle
from cairn.wiki.manifest import split_page_key
from cairn.paths import resolve_store
from cairn.telemetry.sink import retention_policy
from cairn.utils.git import get_repo_head
from cairn.viz import query as viz_query

GRAPH_SCOPES = ("symbol", "module", "impact", "deps", "repo")


def list_projects(conn: sqlite3.Connection) -> List[dict]:
    """Return project counts, freshness, and embedding status with workspace-relative paths."""
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
    include_tests: bool = False,
) -> Dict:
    """Dispatch to a viz scope; empty filters draw capped overviews, not empty graphs."""
    if scope == "symbol":
        name = (focus or "").strip()
        if not name:
            return viz_query.get_symbol_overview(conn, include_tests=include_tests)
        return viz_query.get_symbol_graph(conn, name, 1 if depth is None else depth)
    if scope == "module":
        return viz_query.get_module_graph(conn, focus or "", include_tests=include_tests)
    if scope == "impact":
        name = (focus or "").strip()
        if not name:
            return viz_query.get_symbol_overview(
                conn, include_tests=include_tests, scope="impact"
            )
        return viz_query.get_impact_graph(conn, name, 3 if depth is None else depth)
    if scope == "deps":
        return viz_query.get_deps_graph(conn)
    if scope == "repo":
        return viz_query.get_repo_graph(conn, repo or "", max_nodes=30)
    raise ValueError(f"unknown graph scope: {scope!r}")


INSPECT_NEIGHBOR_CAP = 10
INSPECT_IMPACT_CAP = 15


def inspect_symbol(conn: sqlite3.Connection, name: str) -> Dict:
    """Return one deterministic symbol panel payload with resolved neighbors and honest truncation."""
    if not name or not name.strip():
        return {"found": False, "name": name}
    name = name.strip()
    from ..graph.queries import impact_analysis
    from ..graph.tests import filter_tests

    cur = conn.cursor()
    row = cur.execute(
        "SELECT s.id, s.name, s.kind, s.qualified_name, s.line_start, s.docstring, "
        "f.path AS file, f.repo_id "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.name = ? ORDER BY f.path ASC, s.id ASC LIMIT 1",
        (name,),
    ).fetchone()
    if row is None:
        return {"found": False, "name": name}

    same_name_count = cur.execute(
        "SELECT COUNT(*) FROM symbols WHERE name = ?", (name,)
    ).fetchone()[0]

    def _neighbors(sql: str) -> tuple:
        fetched = cur.execute(sql, (row["id"],)).fetchall()
        truncated = len(fetched) > INSPECT_NEIGHBOR_CAP
        return (
            [
                {
                    "name": r["name"],
                    "kind": r["kind"],
                    "file": r["file"],
                    "edge_kind": r["ekind"],
                }
                for r in fetched[:INSPECT_NEIGHBOR_CAP]
            ],
            truncated,
        )

    cap = INSPECT_NEIGHBOR_CAP + 1
    callers, callers_truncated = _neighbors(
        f"SELECT s.name, s.kind, f.path AS file, e.kind AS ekind "
        f"FROM edges e JOIN symbols s ON e.source_id = s.id "
        f"JOIN files f ON s.file_id = f.id "
        f"WHERE e.target_id = ? LIMIT {cap}"
    )
    callees, callees_truncated = _neighbors(
        f"SELECT t.name, t.kind, f.path AS file, e.kind AS ekind "
        f"FROM edges e JOIN symbols t ON e.target_id = t.id "
        f"JOIN files f ON t.file_id = f.id "
        f"WHERE e.source_id = ? LIMIT {cap}"
    )

    impact = impact_analysis(conn, name, max_depth=3)
    affected = filter_tests(impact["impacted"])
    impact_view = {
        "total": impact["total"],
        "truncated": impact["truncated"],
        "top": [
            {"symbol": r["symbol"], "file": r["file"], "depth": r["depth"]}
            for r in impact["impacted"][:INSPECT_IMPACT_CAP]
        ],
        "affected_tests": [
            {"symbol": t["symbol"], "file": t["file"]}
            for t in affected[:INSPECT_IMPACT_CAP]
        ],
        "affected_tests_total": len(affected),
    }
    return {
        "found": True,
        "symbol": {
            "name": row["name"],
            "kind": row["kind"],
            "qualified_name": row["qualified_name"],
            "file": row["file"],
            "repo": row["repo_id"],
            "line_start": row["line_start"],
            "docstring": row["docstring"],
        },
        "same_name_count": same_name_count,
        "callers": callers,
        "callers_truncated": callers_truncated,
        "callees": callees,
        "callees_truncated": callees_truncated,
        "impact": impact_view,
    }


CANDIDATES_LIMIT = 10


def symbol_candidates(
    conn: sqlite3.Connection, name: str, limit: int = CANDIDATES_LIMIT
) -> Dict:
    """Return exact-name matches with disambiguating context and a truncation flag."""
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


SUGGEST_LIMIT = 10


def symbol_suggest(
    conn: sqlite3.Connection, prefix: str, limit: int = SUGGEST_LIMIT
) -> Dict:
    """Return escaped prefix matches, shortest names first, for search typeahead."""
    if not prefix or not prefix.strip():
        return {"matches": [], "truncated": False}
    if limit < 1:
        limit = 1
    escaped = (
        prefix.strip()
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    fetched = conn.execute(
        """
        SELECT s.name AS name, s.kind AS kind, f.path AS file, f.repo_id AS repo_id
        FROM symbols s
        LEFT JOIN files f ON s.file_id = f.id
        WHERE s.name LIKE ? ESCAPE '\\'
        ORDER BY LENGTH(s.name) ASC, s.name ASC, f.path ASC, f.repo_id ASC, s.id ASC
        LIMIT ?
        """,
        [escaped + "%", limit + 1],
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
    return {"matches": matches, "truncated": len(fetched) > limit}


def parse_ts(value) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp (concept or ``build_runs``) to an aware
    UTC datetime, or None when missing/unparseable."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _age_str(started_at) -> Optional[str]:
    """Format build age exactly as doctor's freshness check."""
    dt = parse_ts(started_at)
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


# The health probes pay one-time imports (sentence-transformers above all),
# so their results are cached process-wide, prewarmed from a daemon thread
# at app startup, and revalidated in the background no more often than this.
PROBE_TTL_S = 300.0

# How long a request that beats the prewarm waits for the first population
# before serving unknown probe values -- well under the 200ms probe budget,
# and never enough to pay a slow import inside the request.
PROBE_WARM_WAIT_S = 0.05

_probe_cond = threading.Condition()
_probe_cache: Optional[Dict[str, object]] = None
_probe_cached_at: float = 0.0
_probe_refreshing = False


def _run_probes() -> Dict[str, object]:
    """The import-paying health probes, keyed as get_health reports them;
    a probe whose backend is unconfigured degrades to None, never raises."""

    def _safe(call):
        try:
            return call()
        except Exception:
            return None

    return {
        "hash_fallback": _safe(is_hash_fallback),
        "ann_backend_enabled": _safe(ann_backend_enabled),
        "ann_model": _safe(current_model),
        "reranker_available": _safe(reranker_available),
    }


def _publish_probes(values: Optional[Dict[str, object]]) -> None:
    """Publish probe results (None: end the in-flight refresh, keep the
    previous cache) and wake requests waiting on the first population."""
    global _probe_cache, _probe_cached_at, _probe_refreshing
    with _probe_cond:
        if values is not None:
            _probe_cache = values
            _probe_cached_at = time.monotonic()
        _probe_refreshing = False
        _probe_cond.notify_all()


def _revalidate_probes() -> None:
    """Compute the probes in the background (startup prewarm or refresh of a
    stale cache); on failure the previous values keep serving and the next
    request past the TTL retries."""
    try:
        _publish_probes(_run_probes())
    except Exception:
        _publish_probes(None)


def _serve_probes(
    max_wait_s: float = PROBE_WARM_WAIT_S,
) -> Optional[Dict[str, object]]:
    """Return cached health probes using bounded cold waits and background revalidation."""
    global _probe_refreshing
    with _probe_cond:
        if _probe_cache is not None:
            if (
                not _probe_refreshing
                and time.monotonic() - _probe_cached_at > PROBE_TTL_S
            ):
                _probe_refreshing = True
                threading.Thread(
                    target=_revalidate_probes,
                    name="cairn-probe-revalidate",
                    daemon=True,
                ).start()
            return _probe_cache
        if _probe_refreshing:
            _probe_cond.wait(max_wait_s)
            return _probe_cache
        _probe_refreshing = True
    try:
        values = _run_probes()
    except Exception:
        _publish_probes(None)
        raise
    _publish_probes(values)
    return values


def prewarm_probes() -> None:
    """Set the synchronous probe flag and populate its cache from a daemon thread."""
    global _probe_refreshing
    with _probe_cond:
        if _probe_cache is not None or _probe_refreshing:
            return
        _probe_refreshing = True
    threading.Thread(
        target=_revalidate_probes,
        name="cairn-probe-prewarm",
        daemon=True,
    ).start()


def get_health(conn: sqlite3.Connection, db_path: Optional[str] = None) -> Dict:
    """Return health display data while degrading unavailable reads to null/zero."""
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

    probes = _serve_probes() or {}
    model = probes.get("ann_model")
    model_name = model if isinstance(model, str) else None
    try:
        embedding_rows = embed_count(conn)
    except sqlite3.Error:
        embedding_rows = 0
    try:
        # With no embeddings or resolved model, index state is unknown—not missing.
        index_present: Optional[bool] = None
        index_rows: Optional[int] = None
        if embedding_rows and model_name:
            index_present = index_exists(conn, model_name)
            if index_present:
                index_rows = index_row_count(conn, model_name)
    except sqlite3.Error:
        index_present, index_rows = None, None

    tool_metrics_rows: Optional[int] = None
    try:
        tool_metrics_rows = conn.execute(
            "SELECT COUNT(*) AS n FROM tool_metrics"
        ).fetchone()["n"]
    except sqlite3.Error:
        pass

    return {
        "db_size_bytes": db_size_bytes,
        "last_build_at": last_build_at,
        "last_build_age": last_build_age,
        # Precedence via the embeddings resolver (env > config file >
        # "local"), not a bare env read — data.py already imports the
        # embeddings module at load, so this adds no import cost here.
        "embed_backend": backend_name(),
        "hash_fallback": probes.get("hash_fallback"),
        "ann_configured": configured_backend(),
        "ann_backend_enabled": probes.get("ann_backend_enabled"),
        "ann_model": model,
        "ann_embedding_rows": embedding_rows,
        "ann_index_exists": index_present,
        "ann_index_rows": index_rows,
        "reranker_available": probes.get("reranker_available"),
        **retention_policy(),
        "tool_metrics_rows": tool_metrics_rows,
    }


# sqlite-vec ANN internals (``vec0`` virtual tables, shadows, ``vecmv_*``):
# their module is not loaded on the dashboard's connection.
_ANN_TABLE_PREFIXES = ("vec_", "vecmv_")


def get_database_schema(conn: sqlite3.Connection) -> Dict:
    """Return the user schema as table nodes plus declared and inferred FK edges."""
    tables = sorted(
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        if not row[0].startswith("sqlite_")
        and "_fts" not in row[0]
        and not row[0].startswith(_ANN_TABLE_PREFIXES)
    )

    def _q(identifier: str) -> str:
        return '"' + identifier.replace('"', '""') + '"'

    meta: Dict[str, Dict] = {}
    skipped: List[Dict[str, str]] = []
    for name in tables:
        try:
            columns = [
                {"name": r[1], "type": r[2] or "", "pk": bool(r[5])}
                for r in conn.execute(f"PRAGMA table_info({_q(name)})")
            ]
            rows = conn.execute(f"SELECT COUNT(*) FROM {_q(name)}").fetchone()[0]
        except sqlite3.Error as e:
            skipped.append({"name": name, "reason": f"{type(e).__name__}: {e}"})
            continue
        meta[name] = {"columns": columns, "rows": rows}
    tables = [name for name in tables if name in meta]

    declared: set = set()
    edges: Dict[Tuple[str, str], Dict] = {}
    for name in tables:
        for fk in conn.execute(f"PRAGMA foreign_key_list({_q(name)})"):
            target, column = fk[2], fk[3]
            if target not in meta:
                continue
            declared.add((name, column))
            edge = edges.setdefault(
                (name, target),
                {"from": name, "to": target, "kind": "fk", "columns": []},
            )
            edge["columns"].append(column)

    for name in tables:
        for column in meta[name]["columns"]:
            col = column["name"]
            if not col.endswith("_id") or col == "id" or (name, col) in declared:
                continue
            base = col[: -len("_id")]
            target = base + "s" if base + "s" in meta else (base if base in meta else None)
            if not target or target == name:
                continue
            edge = edges.setdefault(
                (name, target),
                {
                    "from": name,
                    "to": target,
                    "kind": "inferred",
                    "columns": [],
                },
            )
            edge["columns"].append(col)

    return {
        "tables": [
            {
                "name": name,
                "rows": meta[name]["rows"],
                "columns": meta[name]["columns"],
            }
            for name in tables
        ],
        "edges": [edges[key] for key in sorted(edges)],
        "skipped": skipped,
    }


def get_recent_memories(
    knowledge_dir: str, limit: int = 20, memory_type: Optional[str] = None
) -> List[dict]:
    """Return readable recent memories, newest first, filtered before the limit."""
    bundle = OKFBundle(knowledge_dir)
    entries: List[dict] = []
    for cid in bundle.list_concepts(prefix="memory/"):
        try:
            concept = bundle.read_concept(cid)
        except Exception:
            continue
        entry_type = concept.extensions.get("memory_type") or concept.type
        if memory_type is not None and entry_type != memory_type:
            continue
        tier, slug = _split_doc_id(cid)
        entries.append(
            {
                "id": cid,
                "type": entry_type,
                "title": concept.title or cid,
                "tier": tier,
                "slug": slug,
                "timestamp": concept.timestamp or "",
            }
        )
    entries.sort(key=lambda e: parse_ts(e["timestamp"]) or EPOCH, reverse=True)
    return entries[:limit]


def _memory_neighbor(bundle: "OKFBundle", memory_id: str) -> dict:
    """A supersedes-chain neighbor as ``{id, tier, slug, title}`` — the
    detail href's parts plus a link label. An unreadable neighbor keeps
    its bare id as the title, so the chain row still renders."""
    tier, slug = _split_doc_id(memory_id)
    try:
        title = bundle.read_concept(memory_id).title or memory_id
    except Exception:
        title = memory_id
    return {"id": memory_id, "tier": tier, "slug": slug, "title": title}


def get_memory_detail(knowledge_dir: str, memory_id: str) -> Optional[dict]:
    """Return the memory detail payload, or None for an unknown/non-memory id."""
    if not memory_id.startswith("memory/"):
        return None
    bundle = OKFBundle(knowledge_dir)
    try:
        concept = bundle.read_concept(memory_id)
    except Exception:
        return None
    html, toc = render_markdown_with_toc(concept.body or "")
    tier, slug = _split_doc_id(memory_id)
    extensions = concept.extensions
    supersedes_raw = extensions.get("memory_supersedes") or []
    superseded_by_raw = extensions.get("memory_superseded_by")
    return {
        "id": memory_id,
        "tier": tier,
        "slug": slug,
        "type": extensions.get("memory_type") or concept.type,
        "title": concept.title or memory_id,
        "description": concept.description or "",
        "status": extensions.get("memory_status") or tier,
        "score": extensions.get("memory_score"),
        "signals": extensions.get("memory_signals") or {},
        "tags": list(concept.tags),
        "session_origin": extensions.get("session_origin") or "",
        "recorded": concept.timestamp or "",
        "valid_from": extensions.get("valid_from") or "",
        "valid_until": extensions.get("valid_until") or "",
        "is_latest": bool(extensions.get("memory_is_latest", True)),
        "promotion_history": extensions.get("promotion_history") or [],
        "supersedes": [_memory_neighbor(bundle, m) for m in supersedes_raw],
        "superseded_by": (
            _memory_neighbor(bundle, str(superseded_by_raw))
            if superseded_by_raw
            else None
        ),
        "html": html,
        "toc": toc,
    }


def get_task_queue(knowledge_dir: str, status: Optional[str] = None) -> List[dict]:
    """LLM task-queue entries as plain dicts, optionally filtered by status
    (pending / in-progress / done / failed)."""
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




def _knowledge_link_counts(conn: Optional[sqlite3.Connection]) -> Dict[str, int]:
    """Return distinct bidirectional link counts, zero on stores without the index."""
    if conn is None:
        return {}
    present = conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type = 'table' "
        "AND name = 'knowledge_edges'"
    ).fetchone()[0]
    if not present:
        return {}
    neighbors: Dict[str, set] = {}
    for doc_id, related_id in conn.execute(
        "SELECT doc_id, related_id FROM knowledge_edges"
    ).fetchall():
        neighbors.setdefault(doc_id, set()).add(related_id)
        neighbors.setdefault(related_id, set()).add(doc_id)
    return {doc_id: len(seen) for doc_id, seen in neighbors.items()}


def _split_doc_id(doc_id: str) -> Tuple[str, str]:
    """Split a concept id into family and slug while keeping malformed rows renderable."""
    parts = doc_id.split("/")
    if len(parts) < 3:
        return parts[-1], ""
    return parts[1], "/".join(parts[2:])


def list_knowledge_docs(
    conn: Optional[sqlite3.Connection],
    knowledge_dir: str,
    family: Optional[str] = None,
    status: Optional[str] = None,
    tag: Optional[str] = None,
) -> Dict:
    """Return knowledge docs once with corpus-wide filter counts before filtering."""
    bundle = OKFBundle(knowledge_dir)
    link_counts = _knowledge_link_counts(conn)
    docs: List[dict] = []
    families: Dict[str, int] = {}
    statuses: Dict[str, int] = {}
    for cid in bundle.list_concepts(prefix="knowledge/"):
        try:
            concept = bundle.read_concept(cid)
        except Exception:
            continue
        doc_id = normalize_doc_id(bundle, cid)
        doc_family, slug = _split_doc_id(doc_id)
        doc_status = concept.extensions.get("doc_status") or "active"
        families[doc_family] = families.get(doc_family, 0) + 1
        statuses[doc_status] = statuses.get(doc_status, 0) + 1
        if family is not None and doc_family != family:
            continue
        if status is not None and doc_status != status:
            continue
        if tag is not None and tag not in concept.tags:
            continue
        docs.append(
            {
                "id": doc_id,
                "family": doc_family,
                "slug": slug,
                "title": concept.title or doc_id,
                "status": doc_status,
                "tags": list(concept.tags),
                "links": link_counts.get(doc_id, 0),
                "source": concept.extensions.get("doc_source", ""),
                "updated": concept.timestamp or "",
            }
        )
    docs.sort(key=lambda d: (d["family"], d["title"]))
    return {
        "docs": docs,
        "families": families,
        "statuses": statuses,
        "total": sum(families.values()),
    }


def get_knowledge_doc(knowledge_dir: str, doc_id: str) -> Optional[dict]:
    """Return one knowledge doc and rendered body, or None outside the namespace."""
    bundle = OKFBundle(knowledge_dir)
    try:
        concept = resolve_knowledge_doc(bundle, doc_id)
    except ValueError:
        return None
    html, toc = render_markdown_with_toc(concept.body or "")
    bare_id = normalize_doc_id(bundle, concept.concept_id)
    family, slug = _split_doc_id(bare_id)
    return {
        "id": bare_id,
        "family": family,
        "slug": slug,
        "title": concept.title or bare_id,
        "status": concept.extensions.get("doc_status") or "active",
        "tags": list(concept.tags),
        "source": concept.extensions.get("doc_source", ""),
        "resource": concept.resource or "",
        "updated": concept.timestamp or "",
        "html": html,
        "toc": toc,
    }


def _knowledge_table_present(conn: Optional[sqlite3.Connection], table: str) -> bool:
    """True when ``table`` exists on this connection. A store predating
    the relationship index renders empty panels, never an error."""
    if conn is None:
        return False
    present = conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return bool(present and present[0])


def _group_related(related: List[dict]) -> List[dict]:
    """``related_docs`` rows grouped under their relation, groups sorted
    by relation name, rows keeping the CLI's order within a group — the
    panel renders exactly the rows ``cairn knowledge related`` prints."""
    groups: Dict[str, List[dict]] = {}
    for row in related:
        groups.setdefault(row["relation"], []).append(row)
    return [
        {"relation": relation, "rows": rows}
        for relation, rows in sorted(groups.items())
    ]


def _doc_chain(conn: Optional[sqlite3.Connection], knowledge_dir: str, doc_id: str) -> List[dict]:
    """Return the ordered supersede chain, or [] when unavailable or malformed."""
    if conn is None or not _knowledge_table_present(conn, "knowledge_edges"):
        return []
    from cairn.knowledge.index import supersede_chain

    try:
        chain = supersede_chain(conn, OKFBundle(knowledge_dir), doc_id)
    except Exception:
        return []
    for member in chain:
        member["family"], member["slug"] = _split_doc_id(member["concept_id"])
    return chain


def _doc_refs(conn: Optional[sqlite3.Connection], doc_id: str, store_key: str) -> List[dict]:
    """The doc's stored code refs with resolution status; symbol refs
    deep-link into the graph (the wiki sources' seam), file refs render
    as plain code."""
    if conn is None or not _knowledge_table_present(conn, "knowledge_doc_refs"):
        return []
    from urllib.parse import quote

    store_suffix = f"&store={quote(store_key, safe='')}" if store_key else ""
    refs: List[dict] = []
    for ref, ref_kind, verified in conn.execute(
        "SELECT ref, ref_kind, verified FROM knowledge_doc_refs "
        "WHERE doc_id = ? ORDER BY ref_kind, ref",
        (doc_id,),
    ).fetchall():
        refs.append({
            "ref": ref,
            "ref_kind": ref_kind,
            "verified": bool(verified),
            "href": _symbol_graph_href(ref, store_suffix)
            if ref_kind == "symbol"
            else "",
        })
    return refs


def _staged_manifest_reference(
    workspace: Optional[str], doc_id: str
) -> dict:
    """Return the staged manifest reference and matching row without raising."""
    if not workspace:
        return {"path": "", "exists": False, "row": None}
    path = Path(workspace) / ".cairn" / "ingest-outbox" / "manifest.json"
    reference = {"path": str(path), "exists": path.exists(), "row": None}
    if not reference["exists"]:
        return reference
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return reference
    for row in manifest.get("rows") or []:
        if isinstance(row, dict) and row.get("concept_id") == doc_id:
            reference["row"] = row
            break
    return reference


def _doc_provenance(doc: dict, workspace: Optional[str]) -> dict:
    """Return ingest provenance, preferring a staged manifest row over recorded resource."""
    manifest = _staged_manifest_reference(workspace, doc["id"])
    repo, source_path = "", ""
    resource = (doc.get("resource") or "").strip()
    if resource:
        repo = resource.partition("/")[0]
        source_path = resource
    row = manifest["row"]
    if row:
        repo = str(row.get("repo") or repo)
        source_path = str(row.get("source_path") or source_path)
    return {
        "doc_source": doc.get("source") or "",
        "repo": repo,
        "source_path": source_path,
        "manifest": manifest,
        "staged": bool(resource or row),
    }


def get_knowledge_doc_detail(
    conn: Optional[sqlite3.Connection],
    knowledge_dir: str,
    doc_id: str,
    workspace: Optional[str] = None,
    store_key: str = "",
) -> Optional[dict]:
    """Return knowledge identity, body, relationships, refs, and provenance."""
    doc = get_knowledge_doc(knowledge_dir, doc_id)
    if doc is None:
        return None
    related: List[dict] = []
    if conn is not None and _knowledge_table_present(conn, "knowledge_edges"):
        from cairn.knowledge.index import related_docs

        related = related_docs(conn, OKFBundle(knowledge_dir), doc["id"])
        for neighbor in related:
            neighbor["family"], neighbor["slug"] = _split_doc_id(
                neighbor["doc_id"]
            )
    doc["related"] = related
    doc["related_groups"] = _group_related(related)
    doc["chain"] = _doc_chain(conn, knowledge_dir, doc["id"])
    doc["refs"] = _doc_refs(conn, doc["id"], store_key)
    doc["provenance"] = _doc_provenance(doc, workspace)
    return doc


def get_knowledge_graph(
    conn: Optional[sqlite3.Connection], knowledge_dir: str
) -> Dict:
    """Return resolvable knowledge/memory nodes and deduplicated relationship edges."""
    bundle = OKFBundle(knowledge_dir)
    nodes: List[dict] = []
    memories: Dict[str, "OKFConcept"] = {}
    for cid in bundle.list_concepts(prefix="knowledge/"):
        try:
            concept = bundle.read_concept(cid)
        except Exception:
            continue
        doc_id = normalize_doc_id(bundle, cid)
        family, _slug = _split_doc_id(doc_id)
        nodes.append(
            {
                "id": doc_id,
                "title": concept.title or doc_id,
                "family": family,
                "status": concept.extensions.get("doc_status") or "active",
                "kind": "doc",
            }
        )
    for cid in bundle.list_concepts(prefix="memory/"):
        try:
            concept = bundle.read_concept(cid)
        except Exception:
            continue
        memories[cid] = concept
        tier, _slug = _split_doc_id(cid)
        nodes.append(
            {
                "id": cid,
                "title": concept.title or cid,
                "family": tier,
                "status": concept.extensions.get("memory_status") or tier,
                "kind": "memory",
            }
        )
    nodes.sort(key=lambda n: n["id"])
    node_ids = {n["id"] for n in nodes}
    edges: List[dict] = []
    seen = set()

    def add_edge(source: str, target: str, relation: str, kind: str) -> None:
        key = (source, target, relation, kind)
        if key in seen or source not in node_ids or target not in node_ids:
            return
        seen.add(key)
        edges.append(
            {
                "source": source,
                "target": target,
                "relation": relation,
                "kind": kind,
            }
        )

    if conn is not None and _knowledge_table_present(conn, "knowledge_edges"):
        for doc_id, related_id, relation, kind in conn.execute(
            "SELECT doc_id, related_id, relation, kind FROM knowledge_edges "
            "ORDER BY doc_id, related_id, relation, kind"
        ).fetchall():
            add_edge(doc_id, related_id, relation, kind)
    # Memory supersedes pairs drawn two-way, matching the index's mirrored
    # supersede rows: the newer's declaration plus the older's back-pointer
    # cover chains whose either half lost its file.
    for cid, concept in memories.items():
        extensions = concept.extensions
        pairs = [
            (cid, older)
            for older in (extensions.get("memory_supersedes") or [])
        ]
        superseded_by = extensions.get("memory_superseded_by")
        if superseded_by:
            pairs.append((str(superseded_by), cid))
        for newer, older in pairs:
            add_edge(newer, older, "supersedes", "extracted")
            add_edge(older, newer, "superseded-by", "extracted")
    return {
        "nodes": nodes,
        "edges": edges,
        "metadata": {"node_count": len(nodes), "edge_count": len(edges)},
    }


def _memory_inspect(
    bundle: "OKFBundle", memory_id: str
) -> Optional[dict]:
    """Return memory identity and readable supersedes neighbors without phantoms."""
    try:
        concept = bundle.read_concept(memory_id)
    except Exception:
        return None
    tier, slug = _split_doc_id(memory_id)
    extensions = concept.extensions
    related: List[dict] = []

    def add_neighbor(neighbor_id: str, relation: str, direction: str) -> None:
        try:
            neighbor = bundle.read_concept(neighbor_id)
        except Exception:
            return  # deleted between captures: never a phantom neighbor
        neighbor_tier, neighbor_slug = _split_doc_id(neighbor_id)
        related.append(
            {
                "doc_id": neighbor_id,
                "title": neighbor.title or neighbor_id,
                "relation": relation,
                "kind": "extracted",
                "direction": direction,
                "family": neighbor_tier,
                "slug": neighbor_slug,
            }
        )

    for older in extensions.get("memory_supersedes") or []:
        add_neighbor(str(older), "supersedes", "outgoing")
    superseded_by = extensions.get("memory_superseded_by")
    if superseded_by:
        add_neighbor(str(superseded_by), "superseded-by", "incoming")
    return {
        "found": True,
        "kind": "memory",
        "id": memory_id,
        "family": tier,
        "slug": slug,
        "title": concept.title or memory_id,
        "status": extensions.get("memory_status") or tier,
        "tags": list(concept.tags),
        "related": related,
        "related_groups": _group_related(related),
    }


def get_knowledge_graph_inspect(
    conn: Optional[sqlite3.Connection], knowledge_dir: str, doc_id: str
) -> Optional[dict]:
    """Return one knowledge/memory inspect payload, None for an unknown id."""
    bundle = OKFBundle(knowledge_dir)
    if doc_id.startswith("memory/"):
        return _memory_inspect(bundle, doc_id)
    try:
        concept = resolve_knowledge_doc(bundle, doc_id)
    except ValueError:
        return None
    bare_id = normalize_doc_id(bundle, concept.concept_id)
    family, slug = _split_doc_id(bare_id)
    related: List[dict] = []
    if conn is not None and _knowledge_table_present(conn, "knowledge_edges"):
        from cairn.knowledge.index import related_docs

        related = related_docs(conn, bundle, bare_id)
        for neighbor in related:
            neighbor["family"], neighbor["slug"] = _split_doc_id(
                neighbor["doc_id"]
            )
    return {
        "found": True,
        "kind": "doc",
        "id": bare_id,
        "family": family,
        "slug": slug,
        "title": concept.title or bare_id,
        "status": concept.extensions.get("doc_status") or "active",
        "tags": list(concept.tags),
        "related": related,
        "related_groups": _group_related(related),
    }


HISTORY_PAGE_SIZE = 50


def _recorded_sha(concept: Optional["OKFConcept"]) -> Optional[str]:
    """The page content's provenance sha — the concept extension alone. A
    page with no content has no sha: the plan kind keeps no provenance, so
    staleness never verdicts on non-existent content."""
    if concept is None:
        return None
    return concept.extensions.get("commit_sha") or None


def _wiki_rows(
    knowledge_dir: str, repo: Optional[str] = None, page_id: Optional[str] = None
):
    """Yield manifest rows with concepts, cached HEADs, write chains, and bundle."""
    from cairn.wiki.lifecycle import page_chains, read_page_concept
    from cairn.wiki.manifest import load_manifest

    bundle = OKFBundle(knowledge_dir)
    chains = page_chains(bundle)
    heads: Dict[str, Optional[str]] = {}
    for key, row in load_manifest(knowledge_dir)["pages"].items():
        key_repo, key_page = split_page_key(key)
        if repo and key_repo != repo:
            continue
        if page_id and key_page != page_id:
            continue
        concept = read_page_concept(bundle, key_repo, key_page)
        if key_repo not in heads:
            heads[key_repo] = get_repo_head(key_repo)
        yield (
            key_repo,
            key_page,
            row,
            concept,
            heads[key_repo],
            chains.get(key, []),
            bundle,
        )


def get_wiki_pages(knowledge_dir: str, repo: Optional[str] = None) -> List[dict]:
    """Return manifest pages with derived lifecycle, promotion, and staleness state."""
    from cairn.wiki.lifecycle import derived_state, staleness as wiki_staleness

    pages: List[dict] = []
    for key_repo, page_id, row, concept, head, chain, bundle in _wiki_rows(
        knowledge_dir, repo=repo
    ):
        pages.append(
            {
                "repo": key_repo,
                "page_id": page_id,
                "title": row.get("title") or page_id,
                "description": row.get("description") or "",
                "state": derived_state(bundle, key_repo, page_id, chain),
                "promoted": concept is not None,
                "staleness": wiki_staleness(_recorded_sha(concept), head),
            }
        )
    return pages


def _symbol_graph_href(symbol: str, store_suffix: str) -> str:
    """Deep link to the symbol's graph view (the existing /graph seam:
    scope=symbol&focus=..., store riding last when selected)."""
    from urllib.parse import quote

    return f"/graph?scope=symbol&focus={quote(symbol, safe='')}{store_suffix}"


def _wiki_ref_map(concept, row: dict, store_suffix: str) -> dict:
    """Map only verified symbol refs to graph links using escaped span keys."""
    refs: Dict[str, str] = {}
    for entry in concept.sources or []:
        symbol = entry.get("symbol") if isinstance(entry, dict) else None
        if symbol:
            key = html_escape(str(symbol), quote=False)
            refs.setdefault(
                key, _symbol_graph_href(str(symbol), store_suffix)
            )
    seeds = row.get("seeds") or {}
    for symbol in seeds.get("symbols") or []:
        key = html_escape(str(symbol), quote=False)
        refs.setdefault(
            key, _symbol_graph_href(str(symbol), store_suffix)
        )
    return refs


def get_wiki_page(
    knowledge_dir: str,
    page_id: str,
    repo: Optional[str] = None,
    store_key: str = "",
) -> Optional[dict]:
    """Return one promoted wiki page with rendered body, refs, and staleness."""
    from urllib.parse import quote

    from cairn.wiki.lifecycle import derived_state, staleness as wiki_staleness

    for key_repo, key_page, row, concept, head, chain, bundle in _wiki_rows(
        knowledge_dir, repo=repo, page_id=page_id
    ):
        if concept is None:
            continue
        store_suffix = f"&store={quote(store_key, safe='')}" if store_key else ""
        ref_hrefs = _wiki_ref_map(concept, row, store_suffix)
        html, toc = render_markdown_with_toc(concept.body, ref_hrefs)
        return {
            "repo": key_repo,
            "page_id": page_id,
            "title": row.get("title") or concept.title or page_id,
            "state": derived_state(bundle, key_repo, key_page, chain),
            "html": html,
            "toc": toc,
            "ref_hrefs": ref_hrefs,
            "sources": concept.sources or [],
            "staleness": wiki_staleness(_recorded_sha(concept), head),
        }
    return None


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
    """Return one newest-first history page using filters, windows, and keyset cursors."""
    if limit < 1:
        limit = 1
    before_key = _parse_history_cursor(before)
    after_key = _parse_history_cursor(after)
    backward = after_key is not None

    filter_clauses: List[str] = []
    filter_params: List[object] = []
    if tool_name is not None:
        # Prefix match treats typed text literally; stored names are namespaced.
        filter_clauses.append("substr(tool_name, 1, length(?)) = ?")
        filter_params.extend([tool_name, tool_name])
    if session_id is not None:
        filter_clauses.append("session_id = ?")
        filter_params.append(session_id)
    if source is not None:
        # 'cli' vs 'mcp' (the column default) — exact match, no
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

    divisor, _ = _estimate_divisor(conn, filter_clauses, filter_params)
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
                None if row["req_chars"] is None else row["req_chars"] // divisor
            ),
            "est_resp_tokens": (
                None if row["resp_chars"] is None else row["resp_chars"] // divisor
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
            # A NULL invoked_at never satisfies the keyset comparison, so a
            # cursor built from it re-serves this page forever; stop instead.
            if oldest["invoked_at"] is not None:
                next_cursor = f"{oldest['invoked_at']},{oldest['id']}"
        if has_neighbor(newest, ">"):
            prev_cursor = f"{newest['invoked_at']},{newest['id']}"

    return {"rows": rows, "next": next_cursor, "prev": prev_cursor}


# Exact mode calibrates from bounded window samples; heuristic mode stays constant.
CALIBRATION_SAMPLE_LIMIT = 200
CALIBRATION_MIN_CHARS = 1000


def _estimate_divisor(
    conn: sqlite3.Connection, clauses: List[str], params: List[object]
) -> Tuple[int, bool]:
    """Return the window-aware chars-per-token divisor and whether it was calibrated."""
    if active_tokenizer_mode() == HEURISTIC_MODE:
        return CHARS_PER_TOKEN, False
    sample = conn.execute(
        "SELECT args_summary FROM tool_metrics WHERE "
        + " AND ".join(
            [*clauses, "args_summary IS NOT NULL", "args_summary != ''"]
        )
        + " LIMIT ?",
        [*params, CALIBRATION_SAMPLE_LIMIT],
    ).fetchall()
    total_chars = sum(len(row["args_summary"]) for row in sample)
    if total_chars < CALIBRATION_MIN_CHARS:
        return CHARS_PER_TOKEN, False
    total_tokens = sum(estimate_tokens(row["args_summary"]) for row in sample)
    return max(round(total_chars / total_tokens), 1), True


class TokenEstimates(list):
    """List of per-tool token estimates annotated with calibration metadata."""

    token_mode: str
    calibrated: bool
    chars_per_token: int

    def __init__(
        self,
        entries: List[dict],
        token_mode: str,
        calibrated: bool,
        chars_per_token: int,
    ) -> None:
        super().__init__(entries)
        self.token_mode = token_mode
        self.calibrated = calibrated
        self.chars_per_token = chars_per_token


def get_tool_tokens(
    conn: sqlite3.Connection, since: Optional[float] = None
) -> TokenEstimates:
    """Return per-tool token aggregates with mode context and unknown-safe truncation counts."""
    clauses: List[str] = []
    params: List[object] = []
    if since is not None:
        clauses.append("invoked_at >= ?")
        params.append(since)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    divisor, calibrated = _estimate_divisor(conn, clauses, params)
    rows = conn.execute(
        f"""
        SELECT tool_name,
               COUNT(*) AS calls,
               SUM(req_chars) AS total_req_chars,
               SUM(resp_chars) AS total_resp_chars,
               COUNT(truncated_from_chars) AS truncated_calls,
               SUM(CASE WHEN truncated_from_chars IS NOT NULL
                        THEN truncated_from_chars
                             - COALESCE(truncated_to_chars, truncated_from_chars)
                   END) AS truncated_chars
        FROM tool_metrics{where}
        GROUP BY tool_name
        """,
        params,
    ).fetchall()

    entries: List[dict] = []
    for row in rows:
        est_req = (row["total_req_chars"] or 0) // divisor
        est_resp = (row["total_resp_chars"] or 0) // divisor
        total = est_req + est_resp
        has_evidence = bool(row["truncated_calls"])
        entries.append(
            {
                "tool_name": row["tool_name"],
                "calls": row["calls"],
                "est_req_tokens": est_req,
                "est_resp_tokens": est_resp,
                "total_tokens": total,
                "mean_tokens": total / row["calls"],
                "truncated_calls": row["truncated_calls"] if has_evidence else None,
                "truncated_chars": row["truncated_chars"] if has_evidence else None,
            }
        )
    entries.sort(key=lambda e: (-e["total_tokens"], e["tool_name"]))
    return TokenEstimates(
        entries,
        token_mode=active_tokenizer_mode(),
        calibrated=calibrated,
        chars_per_token=divisor,
    )


# Seconds of inactivity that split a session into separate chains.
SESSION_GAP_S = 1800

# Bounds for the chains view: chains rendered at once, and calls
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
    """Return bounded session chains split only on known timestamp gaps."""
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
    for sid, calls in grouped.items():
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
                chains.append({"session_id": sid, "calls": []})
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


COMMUNITY_HUB_CAP = 3


def get_communities(conn: sqlite3.Connection) -> List[dict]:
    """Persisted communities with size and top hubs in stored id order; [] when none or the tables predate the store."""
    if not _knowledge_table_present(conn, "communities"):
        return []
    rows = conn.execute(
        """
        SELECT c.id, c.label, c.size,
               s.qualified_name AS qualified_name,
               sc.structural_degree AS structural_degree
        FROM communities c
        JOIN symbol_communities sc ON sc.community_id = c.id
        JOIN symbols s ON s.id = sc.symbol_id
        JOIN files f ON f.id = s.file_id
        ORDER BY c.id, sc.structural_degree DESC, f.path, s.qualified_name
        """
    ).fetchall()
    communities: List[dict] = []
    by_id: Dict[int, dict] = {}
    for row in rows:
        community = by_id.get(row["id"])
        if community is None:
            community = {
                "id": row["id"],
                "label": row["label"],
                "size": row["size"],
                "hubs": [],
            }
            by_id[row["id"]] = community
            communities.append(community)
        if len(community["hubs"]) < COMMUNITY_HUB_CAP:
            community["hubs"].append(
                {
                    "qualified_name": row["qualified_name"],
                    "structural_degree": row["structural_degree"],
                }
            )
    return communities


def get_community_members(
    conn: sqlite3.Connection, community_id: int
) -> Optional[dict]:
    """One community with its degree-ranked member symbols; None when the id persists no membership."""
    if not _knowledge_table_present(conn, "communities"):
        return None
    row = conn.execute(
        "SELECT id, label, size FROM communities WHERE id = ?", (community_id,)
    ).fetchone()
    if row is None:
        return None
    members = [
        {
            "qualified_name": member["qualified_name"],
            "path": member["path"],
            "structural_degree": member["structural_degree"],
        }
        for member in conn.execute(
            """
            SELECT s.qualified_name AS qualified_name,
                   f.path AS path,
                   sc.structural_degree AS structural_degree
            FROM symbol_communities sc
            JOIN symbols s ON s.id = sc.symbol_id
            JOIN files f ON f.id = s.file_id
            WHERE sc.community_id = ?
            ORDER BY sc.structural_degree DESC, f.path, s.qualified_name
            """,
            (community_id,),
        )
    ]
    return {
        "id": row["id"],
        "label": row["label"],
        "size": row["size"],
        "members": members,
    }


class MissingDatabaseError(FileNotFoundError):
    """The graph DB file does not exist — nothing read-only to open."""


def get_read_only_db(db_path: str | None = None) -> sqlite3.Connection:
    """Open an existing graph DB read-only; raise MissingDatabaseError rather than create it."""
    path = Path(db_path) if db_path else resolve_store().db
    if not path.exists():
        raise MissingDatabaseError(str(path))
    return get_db(db_path, read_only=True)
