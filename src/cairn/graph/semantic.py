"""Semantic (embedding-based) symbol search."""
from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .lexical import search_symbols, search_symbols_terms
from .prf import expand as prf_expand
from .query_enrich import enrich as enrich_query
from .search_pipeline import (
    SearchAdapters,
    SearchContext,
    _ms_bucket as _ms_bucket,
    _n_results_bucket as _n_results_bucket,
    run_search,
)
from .traversal import get_callers, get_callees

logger = logging.getLogger(__name__)


# Skip rerank only with a decisive margin and an exact raw-query name.

_DEFAULT_RERANK_MIN_MARGIN = 0.45


def _rerank_min_margin() -> float:
    """Return the clamped rerank-skip margin threshold in [0, 1]."""
    raw = os.environ.get("CAIRN_RERANK_MIN_MARGIN", "")
    if not raw:
        return _DEFAULT_RERANK_MIN_MARGIN
    try:
        val = float(raw)
    except ValueError:
        logger.debug("unparseable CAIRN_RERANK_MIN_MARGIN=%r, using default", raw)
        return _DEFAULT_RERANK_MIN_MARGIN
    return min(max(val, 0.0), 1.0)


def _fused_margin(candidates: List[dict], limit: int) -> float:
    """Normalized top-to-edge margin of a fused (RRF) ranking, in [0, 1]."""
    if len(candidates) <= 1:
        return 1.0
    top = candidates[0].get("score") or 0.0
    if top <= 0.0:
        return 0.0
    edge = candidates[min(limit - 1, len(candidates) - 1)].get("score") or 0.0
    return (top - edge) / top


def _exact_name_hit(query: str, top: dict) -> bool:
    """Whether the query is an exact (case-insensitive) reference to `top`."""
    q = query.strip().lower()
    if not q:
        return False
    name = (top.get("name") or "").strip().lower()
    qual = (top.get("qualified_name") or "").strip().lower()
    return q == name or q == qual or qual.endswith("." + q)


def _vectors_carry_token_overlap_only(hash_fallback_flag: bool) -> bool:
    """True when this call's embeddings are hash (token-overlap) vectors."""
    if hash_fallback_flag:
        return True
    return os.environ.get("CAIRN_EMBED_BACKEND", "").strip().lower() == "hash"


def _fused_confident(
    query: str,
    candidates: List[dict],
    limit: int,
    min_margin: Optional[float] = None,
) -> bool:
    """Whether the fused ranking is decisive enough to skip the rerank stage."""
    margin = _rerank_min_margin() if min_margin is None else min_margin
    if _fused_margin(candidates, limit) < margin:
        return False
    return _exact_name_hit(query, candidates[0])


def _mapping_rows(cursor) -> list:
    """Normalize fetched rows to mapping access regardless of ``row_factory``."""
    rows = cursor.fetchall()
    if not rows or not isinstance(rows[0], tuple):
        return rows
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, r)) for r in rows]


def _candidates_from_ann_hits(
    conn: sqlite3.Connection, ann_hits: List[Tuple[str, float]], threshold: float
) -> List[dict]:
    """Convert ANN hits to thresholded, best-score candidate dictionaries."""
    # A symbol qualifies once at its best threshold-passing score.
    score_by_id: dict = {}
    for sid, score in ann_hits:
        if sid not in score_by_id or score > score_by_id[sid]:
            score_by_id[sid] = score
    ids = [sid for sid, score in score_by_id.items() if score >= threshold]
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    rows = _mapping_rows(
        conn.execute(
            f"SELECT e.symbol_id, e.chunk, "
            "s.name, s.kind, s.qualified_name, f.path AS file_path, f.repo_id AS repo "
            "FROM embeddings e "
            "JOIN symbols s ON e.symbol_id = s.id "
            "JOIN files f ON s.file_id = f.id "
            f"WHERE e.symbol_id IN ({placeholders})",
            tuple(ids),
        )
    )
    by_id = {r["symbol_id"]: r for r in rows}
    candidates = []
    for sid in ids:
        r = by_id.get(sid)
        if r is None:
            continue  # symbol/metadata vanished between index build and query
        candidates.append(
            {
                "id": r["symbol_id"],
                "name": r["name"],
                "kind": r["kind"],
                "qualified_name": r["qualified_name"],
                "file_path": r["file_path"],
                "repo": r["repo"],
                "score": round(score_by_id[sid], 4),
                "chunk": r["chunk"],
                "provenance": "semantic",
                "reranked": False,
            }
        )
    return candidates


def _merge_ann_candidates(base: List[dict], extra: List[dict]) -> List[dict]:
    """Merge two already-deduped ANN candidate lists into one ranked list."""
    best: dict = {}
    for cand in base:
        best[cand["id"]] = cand
    for cand in extra:
        cur = best.get(cand["id"])
        if cur is None or cand["score"] > cur["score"]:
            best[cand["id"]] = cand
    return sorted(best.values(), key=lambda c: c["score"], reverse=True)


# ---------------------------------------------------------------------------
# Retrieval tunables
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetrievalParams:
    """Immutable per-call retrieval tunables for ``semantic_search``."""

    dense_threshold: Optional[float] = None
    rrf_k: Optional[int] = None
    rrf_weights: Optional[Tuple[float, float]] = None
    sparse_limit: Optional[int] = None
    sparse_top_n: Optional[int] = None
    dense_pool: Optional[int] = None
    rerank_pool: Optional[int] = None
    rerank: Optional[bool] = None
    enrich: Optional[bool] = None
    enrich_idf: Optional[bool] = None
    gate_min_margin: Optional[float] = None
    multivector: Optional[bool] = None
    prf: Optional[bool] = None
    prf_docs: Optional[int] = None
    prf_terms: Optional[int] = None
    prf_lambda: Optional[float] = None


def _term_df_lookup(conn: sqlite3.Connection):
    """Build a memoized per-term DF lookup over the persisted ``term_df``."""
    cache: dict = {}

    def lookup(token: str):
        if token not in cache:
            row = conn.execute(
                "SELECT symbol_df, n_symbols FROM term_df WHERE token = ?",
                (token,),
            ).fetchone()
            cache[token] = None if row is None else (row[0], row[1])
        return cache[token]

    return lookup


def _evaluate_dense_ladder_once(context) -> None:
    """Evaluate the embedding fallback ladder once for one search."""
    from cairn.graph import embed_ladder

    if not context.dense_ladder_evaluated:
        context.dense_ladder_evaluated = True
        embed_ladder.evaluate_ladder(context.conn)


def _dense_embed_guarded(context, text: str):
    """Return ``(blob, dim)``, or ``None`` when the dense leg must be empty."""
    from cairn.graph import embeddings as emb

    try:
        return emb.embed_query(text)
    except Exception:
        pass
    try:
        _evaluate_dense_ladder_once(context)
        from cairn.graph import embed_ladder

        state = embed_ladder.ladder_state()
        if state is not None and state.active and state.adopted_model:
            return emb.embed_query(text)
    except Exception:
        pass
    return None


def _dense_retrieve(context):
    """Retrieve dense candidates through ANN or the cosine-scan fallback."""
    pool_size = max(context.limit * 5, 50) if context.rerank_on else context.limit
    params = context.params
    if params is not None and params.rerank_pool is not None:
        pool_size = params.rerank_pool

    pair = _dense_embed_guarded(context, context.dense_query)
    if pair is None:
        context.dense_lost = True
        return []

    candidates = None
    q_blob, q_dim = pair
    if context.ann_enabled:
        ann_hits = context.ann_index.query(
            context.conn, context.model, q_blob, pool_size
        )
        if ann_hits is not None:
            candidates = _candidates_from_ann_hits(
                context.conn, ann_hits, context.threshold
            )
            context.ann_used = True
            if params is not None and params.multivector:
                mv_hits = context.ann_index.query(
                    context.conn,
                    context.model,
                    q_blob,
                    pool_size,
                    source="embeddings_mv",
                )
                if mv_hits is not None:
                    candidates = _merge_ann_candidates(
                        candidates,
                        _candidates_from_ann_hits(
                            context.conn, mv_hits, context.threshold
                        ),
                    )

    if candidates is None:
        if not context.ann_enabled:
            from cairn.graph import ann_index as ann

            ann.warn_ann_fallback_once(logger, context="semantic_search")
        brute_force_limit = 50000
        if params is not None and params.dense_pool is not None:
            brute_force_limit = params.dense_pool
        if params is not None and params.multivector:
            rows = _mapping_rows(
                context.conn.execute(
                    "SELECT symbol_id, vec, chunk, dim, name, kind, "
                    "qualified_name, file_path, repo FROM ("
                    "SELECT e.symbol_id, e.vec, e.chunk, e.dim, "
                    "s.name, s.kind, s.qualified_name, f.path AS file_path, f.repo_id AS repo "
                    "FROM embeddings e "
                    "JOIN symbols s ON e.symbol_id = s.id "
                    "JOIN files f ON s.file_id = f.id "
                    "WHERE e.model = ? "
                    "UNION ALL "
                    "SELECT m.symbol_id, m.vec, m.chunk, m.dim, "
                    "s.name, s.kind, s.qualified_name, f.path AS file_path, f.repo_id AS repo "
                    "FROM embeddings_mv m "
                    "JOIN symbols s ON m.symbol_id = s.id "
                    "JOIN files f ON s.file_id = f.id "
                    "WHERE m.model = ?"
                    ") LIMIT ?",
                    (context.model, context.model, brute_force_limit),
                )
            )
        else:
            rows = _mapping_rows(
                context.conn.execute(
                    "SELECT e.symbol_id, e.vec, e.chunk, e.dim, "
                    "s.name, s.kind, s.qualified_name, f.path AS file_path, f.repo_id AS repo "
                    "FROM embeddings e "
                    "JOIN symbols s ON e.symbol_id = s.id "
                    "JOIN files f ON s.file_id = f.id "
                    "WHERE e.model = ? "
                    "LIMIT ?",
                    (context.model, brute_force_limit),
                )
            )
        if not rows:
            return None

        from cairn.retrieval import cosine_scan

        triples = [(row["vec"], row["dim"], row) for row in rows]
        scored = cosine_scan(q_blob, q_dim, triples, context.threshold)
        if params is not None and params.multivector:
            best = {}
            for score, row in scored:
                symbol_id = row["symbol_id"]
                if symbol_id not in best or score > best[symbol_id][0]:
                    best[symbol_id] = (score, row)
            scored = list(best.values())
        candidates = []
        for score, row in scored[:pool_size]:
            candidates.append(
                {
                    "id": row["symbol_id"],
                    "name": row["name"],
                    "kind": row["kind"],
                    "qualified_name": row["qualified_name"],
                    "file_path": row["file_path"],
                    "repo": row["repo"],
                    "score": round(score, 4),
                    "chunk": row["chunk"],
                    "provenance": context.semantic_provenance,
                    "reranked": False,
                }
            )
    return candidates


def _sparse_retrieve(context, sparse_terms):
    """Fetch lexical candidates in term mode or raw-query mode."""
    sparse_limit = 30
    if context.params is not None and context.params.sparse_limit is not None:
        sparse_limit = context.params.sparse_limit
    if sparse_terms:
        rows = search_symbols_terms(context.conn, sparse_terms, limit=sparse_limit)
    else:
        rows = search_symbols(context.conn, context.query, limit=sparse_limit)
    return [dict(row) for row in rows]


def _build_fused_candidates(context, bm25_map, dense_map, fused_rank):
    """Materialize fused candidates and tag each candidate's source."""
    fused = []
    for doc_id, fused_score in fused_rank:
        in_bm25 = doc_id in bm25_map
        in_dense = doc_id in dense_map
        if in_bm25 and in_dense:
            base = dict(dense_map[doc_id])
            base["provenance"] = context.fused_provenance
        elif in_dense:
            base = dict(dense_map[doc_id])
            base["provenance"] = context.semantic_provenance
        else:
            item = bm25_map[doc_id]
            base = {
                "id": item.get("id"),
                "name": item.get("name"),
                "kind": item.get("kind"),
                "qualified_name": item.get("qualified_name"),
                "file_path": item.get("file_path"),
                "repo": item.get("repo"),
                "score": 0.0,
                "chunk": "",
                "provenance": "bm25",
                "reranked": False,
            }
        base["score"] = round(fused_score, 4)
        fused.append(base)
    return fused


def _confidence_gate(context) -> bool:
    """Reject rerank skipping for sparse, degraded, or hash-backed rankings."""
    return bool(
        not _vectors_carry_token_overlap_only(context.hash_backend)
        and context.fusion_used
        and context.candidates
        and _fused_confident(
            context.query,
            context.candidates,
            context.limit,
            min_margin=context.gate_margin_override,
        )
    )


def _enrich_results(context) -> None:
    _attach_callers(context.conn, context.results)


def _dense_failure(context) -> None:
    try:
        _evaluate_dense_ladder_once(context)
    except Exception:
        pass


def _apply_degradation(context) -> None:
    from cairn.graph import embed_ladder

    if context.dense_lost and embed_ladder.degradation_active():
        hint = embed_ladder.degradation_footnote()
        for item in context.results:
            item["degraded"] = "embedding-backend"
            item["hint"] = hint


def _query_enricher(query, df_lookup=None):
    if df_lookup is None:
        return enrich_query(query)
    return enrich_query(query, df_lookup=df_lookup)


def _prf_expander(
    dense_query, feedback_docs, df_lookup=None, fb_terms=10, fb_lambda=0.5
):
    return prf_expand(
        dense_query,
        feedback_docs,
        df_lookup=df_lookup,
        fb_terms=fb_terms,
        fb_lambda=fb_lambda,
    )


def _search_adapters():
    return SearchAdapters(
        dense_retrieve=_dense_retrieve,
        sparse_retrieve=_sparse_retrieve,
        query_enricher=_query_enricher,
        prf_expander=_prf_expander,
        build_fused_candidates=_build_fused_candidates,
        confidence_gate=_confidence_gate,
        enrich_results=_enrich_results,
        dense_failure=_dense_failure,
        apply_degradation=_apply_degradation,
        df_lookup_factory=_term_df_lookup,
    )


def semantic_search(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 20,
    threshold: float = 0.3,
    include_callers: bool = False,
    rerank: Optional[bool] = None,
    params: Optional[RetrievalParams] = None,
) -> List[dict]:
    """Return top-k symbols by cosine similarity to a natural-language query."""
    from cairn.graph import embeddings as emb

    if params is not None and params.dense_threshold is not None:
        threshold = params.dense_threshold
    gate_margin_override = None
    if params is not None and params.gate_min_margin is not None:
        gate_margin_override = min(max(params.gate_min_margin, 0.0), 1.0)

    hash_backend = emb.is_hash_fallback()
    context = SearchContext(
        conn=conn,
        query=query,
        limit=limit,
        threshold=threshold,
        rerank_override=rerank,
        params=params,
        adapters=_search_adapters(),
        include_callers=include_callers,
        hash_backend=hash_backend,
        model=emb.current_model(),
        semantic_provenance=(
            "semantic (hash backend)" if hash_backend else "semantic"
        ),
        fused_provenance=(
            "fused(bm25+semantic, hash)" if hash_backend else "fused(bm25+semantic)"
        ),
        gate_margin_override=gate_margin_override,
    )
    return run_search(context)


def _attach_callers(conn: sqlite3.Connection, results: List[dict], neighbor_limit: int = 5) -> None:
    """Mutates each result dict in place, adding a small 1-hop neighbor list."""
    for item in results:
        name = item.get("name")
        if not name:
            item["callers"] = []
            item["callees"] = []
            continue
        try:
            callers = get_callers(conn, name, limit=neighbor_limit)
        except Exception:
            callers = []
        try:
            callees = get_callees(conn, name, limit=neighbor_limit)
        except Exception:
            callees = []
        item["callers"] = [
            {"name": c["caller_name"], "kind": c["caller_kind"], "file_path": c["file_path"]}
            for c in callers
        ]
        item["callees"] = [
            {"name": c["callee_name"], "kind": c["callee_kind"], "file_path": c["file_path"]}
            for c in callees
        ]
