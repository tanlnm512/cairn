"""Federated semantic search across every registered workspace store."""
from __future__ import annotations

import contextlib
import sqlite3
import threading
from dataclasses import dataclass, field, replace
from typing import Dict, Iterator, List, Tuple

from cairn.graph.schema import get_db
from cairn.graph.fusion import rrf_fuse
from cairn.paths import StorePaths, _load_registry, resolve_store

# StorePaths layout (paths.register_workspace): db and knowledge live
# directly under the store home.
_DB_FILENAME = ".kg"
_KNOWLEDGE_DIRNAME = ".knowledge"

# Serializes the process-global embed_query swap in _shared_query_embed.
_shared_embed_lock = threading.Lock()


@dataclass
class FederatedResult:
    """Aggregated outcome of a federated search across registered stores."""

    query: str
    hits: List[dict] = field(default_factory=list)
    dropped: List[str] = field(default_factory=list)
    states: Dict[str, str] = field(default_factory=dict)


def iter_stores() -> Iterator[Tuple[str, StorePaths]]:
    """Yield ``(workspace_path, store_paths)`` for every registry entry.

    ``resolve_store``'s CAIRN_DB/CAIRN_KNOWLEDGE overrides pin the active
    process's single store; fan-out derives each entry's layout from its own
    store home instead.
    """
    for ws_path in sorted(_load_registry()):
        store = resolve_store(ws_path)
        yield ws_path, replace(
            store,
            db=store.home / _DB_FILENAME,
            knowledge=store.home / _KNOWLEDGE_DIRNAME,
        )


def federated_search(
    query: str, limit: int = 20, shared_embed: bool = False
) -> FederatedResult:
    """Search every registered workspace store and fuse the rankings.

    Per store: ``semantic_search`` runs on a read-only connection; a store
    with no embedding rows for the current model contributes its
    ``search_symbols`` BM25 ranking instead (``provenance="bm25"``). The
    cross-store merge is rank-level only (``rrf_fuse``) -- per-store scores
    are never compared. ``limit`` bounds each store's fetch and the merged
    ranking. Every registry entry is classified ``ok`` / ``missing`` /
    ``locked`` / ``unindexed`` and reported in ``states``; entries that are
    not ``ok`` are named in ``dropped`` and never abort the query.

    ``shared_embed`` opts into shared-backend serving: when every reachable
    store's embedding rows carry exactly the current model stamp, the query
    is embedded once and the vector reused across those stores; any other
    stamp mix keeps per-store embedding with rank-level fusion. Off by
    default; never changes which hits callers get, only how many times the
    query is embedded.
    """
    if not query or not query.strip():
        raise ValueError("query must not be empty")
    with _shared_query_embed(shared_embed and _stamps_share_backend()):
        rankings: List[List[str]] = []
        hits_by_doc: dict = {}
        states: Dict[str, str] = {}
        dropped: List[str] = []
        for ws_path, store in iter_stores():
            state = _classify_store(store)
            if state != "ok":
                states[ws_path] = state
                dropped.append(ws_path)
                continue
            try:
                store_hits = _search_store(store, query, limit)
            except Exception:
                states[ws_path] = "locked"
                dropped.append(ws_path)
                continue
            states[ws_path] = "ok"
            ranking = []
            for hit in store_hits:
                doc_id = f"{store.home.name}:{hit['id']}"
                hit["workspace"] = ws_path
                hits_by_doc[doc_id] = hit
                ranking.append(doc_id)
            rankings.append(ranking)
        hits = []
        for doc_id, fused_score in rrf_fuse(rankings):
            hit = hits_by_doc[doc_id]
            hit["score"] = round(fused_score, 4)
            hits.append(hit)
        return FederatedResult(
            query=query, hits=hits[:limit], dropped=dropped, states=states
        )


def _stamps_share_backend() -> bool:
    """True when every reachable store's embedding rows carry exactly the
    current model stamp.

    A store without embedding rows is lexical-only and constrains nothing;
    a foreign stamp, ambiguous multi-stamp rows, or an unreadable db keeps
    per-store serving. Never raises and never writes.
    """
    from cairn.graph import embeddings as emb

    model = emb.current_model()
    for _, store in iter_stores():
        if _classify_store(store) != "ok":
            continue
        try:
            conn = get_db(str(store.db), read_only=True)
            try:
                rows = conn.execute(
                    "SELECT DISTINCT model FROM embeddings"
                ).fetchall()
            finally:
                conn.close()
        except sqlite3.OperationalError:
            return False
        stamps = {row["model"] for row in rows}
        if stamps and stamps != {model}:
            return False
    return True


@contextlib.contextmanager
def _shared_query_embed(enabled: bool):
    """Serve every query embed in the scope from one memoized vector.

    ``semantic_search`` reaches the backend only through the process-global
    ``embeddings.embed_query``, so enabling the share swaps that seam for a
    memoizing wrapper; the swap is lock-serialized and always restored.
    """
    if not enabled:
        yield
        return
    from cairn.graph import embeddings as emb

    with _shared_embed_lock:
        memo: dict = {}
        original = emb.embed_query

        def embed_once(text, _embed=original, _memo=memo):
            if text not in _memo:
                _memo[text] = _embed(text)
            return _memo[text]

        emb.embed_query = embed_once
        try:
            yield
        finally:
            emb.embed_query = original


def _classify_store(store: StorePaths) -> str:
    """``missing`` / ``unindexed`` / ``ok`` from the filesystem alone.

    A store home that is absent is ``missing``; a home without a db file is
    ``unindexed``. A db that exists but cannot be queried classifies
    ``locked`` at query time. Never raises and never writes.
    """
    if not store.home.is_dir():
        return "missing"
    if not store.db.is_file():
        return "unindexed"
    return "ok"


def _search_store(store: StorePaths, query: str, limit: int) -> List[dict]:
    """One store's ranked hits: hybrid semantic search, or BM25-only when
    the store has no embedding rows for the current model."""
    from cairn.graph import embeddings as emb
    from cairn.graph.lexical import search_symbols
    from cairn.graph.semantic import semantic_search

    conn = get_db(str(store.db), read_only=True)
    try:
        if _has_embeddings(conn, emb.current_model()):
            return semantic_search(conn, query, limit=limit)
        return [_lexical_hit(row) for row in search_symbols(conn, query, limit=limit)]
    finally:
        conn.close()


def _has_embeddings(conn: sqlite3.Connection, model: str) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM embeddings WHERE model = ? LIMIT 1", (model,)
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    return row is not None


def _lexical_hit(row: sqlite3.Row) -> dict:
    """Shape one ``search_symbols`` row like a ``semantic_search`` bm25 hit."""
    return {
        "id": row["id"],
        "name": row["name"],
        "kind": row["kind"],
        "qualified_name": row["qualified_name"],
        "file_path": row["file_path"],
        "repo": row["repo"],
        "score": 0.0,
        "chunk": "",
        "provenance": "bm25",
        "reranked": False,
    }
