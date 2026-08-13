"""Semantic (embedding-based) symbol search.

Two-stage retrieval:
  1. Cosine scan (or sqlite-vec ANN) over the embeddings table, optionally
     blended with BM25 via Reciprocal Rank Fusion.
  2. Optional cross-encoder rerank stage.

Imports vector math from ``vector_math``, BM25 search from ``lexical``, and
1-hop traversal from ``traversal`` for the ``include_callers=True`` enrichment.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from typing import List, Tuple

from .lexical import search_symbols
from .traversal import get_callers, get_callees

logger = logging.getLogger(__name__)


def _candidates_from_ann_hits(
    conn: sqlite3.Connection, ann_hits: List[Tuple[str, float]], threshold: float
) -> List[dict]:
    """Turn ``ann_index.ann_query``'s (symbol_id, score) pairs into the same
    candidate dict shape the brute-force scan produces.

    Re-applies ``threshold`` (the vec0 MATCH query has no threshold concept of
    its own — it just returns the nearest k).
    """
    ids = [sid for sid, score in ann_hits if score >= threshold]
    if not ids:
        return []
    score_by_id = {sid: score for sid, score in ann_hits}
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT e.symbol_id, e.chunk, "
        "s.name, s.kind, s.qualified_name, f.path AS file_path, f.repo_id AS repo "
        "FROM embeddings e "
        "JOIN symbols s ON e.symbol_id = s.id "
        "JOIN files f ON s.file_id = f.id "
        f"WHERE e.symbol_id IN ({placeholders})",
        tuple(ids),
    ).fetchall()
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


def semantic_search(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 20,
    threshold: float = 0.3,
    include_callers: bool = False,
) -> List[dict]:
    """Return top-k symbols by cosine similarity to a natural-language query.

    The semantic counterpart to ``search_symbols``: matches *meaning*
    (synonyms, paraphrases, cross-language concepts) rather than tokens.

    Loads the query embedding via ``embeddings.embed_query`` then
    cosine-compares it against every stored vector for the current model;
    only candidates with cosine ``score >= threshold`` survive.

    By default (``CAIRN_FUSION`` unset or not ``"0"``) the surviving vector
    candidates are blended with a BM25 list via Reciprocal Rank Fusion. When
    fusion is on, each result's ``score`` is the RRF rank score (small, e.g.
    0.01-0.02), not the cosine value; a query with zero cosine hits above
    ``threshold`` can still return BM25-only results
    (``provenance="bm25"``). Set ``CAIRN_FUSION=0`` for comparable cosine
    scores.

    When ``CAIRN_RERANK=1``, this becomes a two-stage retrieve-then-rerank
    pipeline: a wider candidate pool (``max(limit * 5, 50)``) is cross-encoder
    re-scored and truncated to ``limit`` by the rerank score. Degrades to plain
    ordering on any failure (check the ``reranked`` field).

    When ``CAIRN_ANN_BACKEND=sqlite-vec`` and an index exists for the current
    model, the candidate pool comes from a native ANN query instead of the
    brute-force scan (transparent fallback).

    When ``include_callers=True``, each result is enriched with a small
    ``"callers"``/``"callees"`` neighbor list (1-hop, precise resolution only,
    capped at 5 each). Off by default: adds up to ``2 * limit`` extra graph
    queries.

    Every result carries ``provenance`` (``"semantic"``, ``"bm25"``, or
    ``"fused(bm25+semantic)"``) and ``score``. Returns
    ``[{"id", "name", "kind", "qualified_name", "file_path", "repo", "score",
    "chunk", "provenance", "reranked"}]`` (plus ``"callers"``/``"callees"`` when
    requested) sorted by score (or rerank_score) descending.
    """
    from cairn.graph import embeddings as emb
    from cairn.graph import reranker as rrk
    from cairn.graph import ann_index as ann

    # Under the dep-free hash fallback the embedding carries only token-overlap
    # signal, so annotate provenance strings to surface the degradation.
    _hash = emb.is_hash_fallback()
    _sem_prov = "semantic (hash backend)" if _hash else "semantic"
    _fused_prov = "fused(bm25+semantic, hash)" if _hash else "fused(bm25+semantic)"

    rerank_on = rrk.rerank_enabled()
    # When reranking, retrieve a wider shortlist for the cross-encoder to
    # re-sort; plain cosine ordering slices to exactly `limit`.
    pool_size = max(limit * 5, 50) if rerank_on else limit

    model = emb.current_model()
    q_blob, q_dim = emb.embed_query(query)

    candidates = None
    ann_enabled = ann.ann_backend_enabled()
    if ann_enabled:
        ann_hits = ann.ann_query(conn, model, q_blob, pool_size)
        if ann_hits is not None:
            # ANN path available and an index exists for this model.
            candidates = _candidates_from_ann_hits(conn, ann_hits, threshold)

    if candidates is None:
        # Brute-force cosine scan fallback. Hard-cap the candidate pool so the
        # fetchall() can't grow unbounded with corpus size.
        #
        # Surface the degradation once when sqlite-vec was *expected* but is
        # unavailable. When ann_enabled is False it's either that or an
        # explicit CAIRN_ANN_BACKEND=off (the helper stays silent on the
        # opt-out). When ann_enabled is True we only land here pre-rebuild (no
        # index built yet -- a normal setup state, not a degradation) or after
        # a load failure that already warned inside try_load, so we don't warn
        # in that branch.
        if not ann_enabled:
            ann.warn_ann_fallback_once(logger, context="semantic_search")
        brute_force_limit = 50000
        rows = conn.execute(
            "SELECT e.symbol_id, e.vec, e.chunk, e.dim, "
            "s.name, s.kind, s.qualified_name, f.path AS file_path, f.repo_id AS repo "
            "FROM embeddings e "
            "JOIN symbols s ON e.symbol_id = s.id "
            "JOIN files f ON s.file_id = f.id "
            "WHERE e.model = ? "
            "LIMIT ?",
            (model, brute_force_limit),
        ).fetchall()
        if not rows:
            return []

        # Prefer numpy for the scan; fall back to pure Python. Both produce
        # identical cosine scores. Shared via cairn.retrieval.cosine_scan.
        from cairn.retrieval import cosine_scan

        triples = [(r["vec"], r["dim"], r) for r in rows]
        scored = cosine_scan(q_blob, q_dim, triples, threshold)
        candidates = []
        for score, r in scored[:pool_size]:
            candidates.append(
                {
                    "id": r["symbol_id"],
                    "name": r["name"],
                    "kind": r["kind"],
                    "qualified_name": r["qualified_name"],
                    "file_path": r["file_path"],
                    "repo": r["repo"],
                    "score": round(score, 4),
                    "chunk": r["chunk"],
                    "provenance": _sem_prov,
                    "reranked": False,
                }
            )

    # RRF Hybrid fusion.
    fusion_enabled = os.environ.get("CAIRN_FUSION", "1") != "0"
    if fusion_enabled and candidates is not None:
        try:
            from cairn.graph.fusion import rrf_fuse

            # Fetch BM25 candidates. search_symbols returns sqlite3.Row,
            # which has no .get() — convert to dict at this boundary so the
            # shared .get("id") access below works on both BM25 rows and the
            # candidate dicts (which are already plain dicts).
            bm25_raw = [dict(r) for r in search_symbols(conn, query, limit=30)]
            bm25_map = {}
            bm25_ids = []
            for r in bm25_raw:
                sid = r.get("id")
                if sid:
                    bm25_map[sid] = r
                    bm25_ids.append(sid)

            vec_map = {}
            vec_ids = []
            for r in candidates:
                sid = r.get("id")
                if sid:
                    vec_map[sid] = r
                    vec_ids.append(sid)

            fused_rank = rrf_fuse([bm25_ids, vec_ids], k=60)
            fused_candidates = []
            for doc_id, fused_score in fused_rank:
                in_bm25 = doc_id in bm25_map
                in_vec = doc_id in vec_map

                if in_bm25 and in_vec:
                    base = dict(vec_map[doc_id])
                    base["provenance"] = _fused_prov
                elif in_vec:
                    base = dict(vec_map[doc_id])
                    base["provenance"] = _sem_prov
                else:
                    b_item = bm25_map[doc_id]
                    base = {
                        "id": b_item.get("id"),
                        "name": b_item.get("name"),
                        "kind": b_item.get("kind"),
                        "qualified_name": b_item.get("qualified_name"),
                        "file_path": b_item.get("file_path"),
                        "repo": b_item.get("repo"),
                        "score": 0.0,
                        "chunk": "",
                        "provenance": "bm25",
                        "reranked": False,
                    }
                base["score"] = round(fused_score, 4)
                fused_candidates.append(base)

            candidates = fused_candidates
        except Exception:
            # Degrade to vector-only rather than failing the search, but log
            # at WARNING (not debug): this path was once silently broken by a
            # .get()-on-Row AttributeError swallowed here, so a future regression
            # must be visible. The debug-level exc_info still gives the traceback.
            logger.warning("RRF fusion degraded to vector-only", exc_info=True)

    if rerank_on:
        final, reranked = rrk.rerank(query, candidates, limit)
        for item in final:
            item["reranked"] = reranked
            if "rerank_score" in item:
                item["rerank_score"] = round(item["rerank_score"], 4)
    else:
        final = candidates[:limit]

    if include_callers:
        _attach_callers(conn, final)

    return final


def _attach_callers(conn: sqlite3.Connection, results: List[dict], neighbor_limit: int = 5) -> None:
    """Mutates each result dict in place, adding a small 1-hop neighbor list.

    Precise resolution only (``fuzzy=False``). Missing/errored lookups degrade
    to an empty list per result, never raise.
    """
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
