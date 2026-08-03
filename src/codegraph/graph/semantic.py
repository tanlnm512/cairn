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
    candidate dict shape the brute-force scan produces, so both paths feed
    the same threshold-filter / rerank code below.

    ``ann_hits`` is already ANN-distance-ordered (most similar first); this
    only adds metadata (name/kind/qualified_name/file_path/repo/chunk) via a
    targeted IN query and re-applies ``threshold`` (the vec0 MATCH query has
    no threshold concept of its own — it just returns the nearest k).
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

    This is the semantic counterpart to ``search_symbols``: where FTS5 matches
    tokens, this matches *meaning* — synonyms, paraphrases, and cross-language
    concepts land close in embedding space even with zero shared tokens.

    Loads the query embedding via ``embeddings.embed_query`` (backend-selected),
    then cosine-compares it against every stored vector for the current model.
    The scan is O(n) over the embeddings table; at 50k chunks it's ~50ms with
    numpy and ~2s in pure Python — acceptable for interactive queries and
    avoids any native vector extension. Only candidates with cosine
    ``score >= threshold`` survive this stage.

    When ``CODEGRAPH_FUSION`` is unset or not ``"0"`` (**the default**), the
    surviving vector candidates are blended with a BM25 (``search_symbols``)
    candidate list via Reciprocal Rank Fusion (``src.graph.fusion.rrf_fuse``).
    This is intentional — it lets a strong lexical hit rank alongside a
    moderate semantic one — but it means each result's ``score`` is
    overwritten with the *RRF rank score* (``1/(k+rank)``: small, e.g.
    0.01-0.02, and tightly clustered by rank) instead of the cosine value
    computed above. ``threshold`` only gates which vector candidates enter
    the fusion; it does not gate the final result set, so a query with zero
    cosine hits above ``threshold`` can still return results sourced purely
    from BM25 (``provenance="bm25"``, initial ``score`` of ``0.0`` before the
    RRF rank score overwrites it). Set ``CODEGRAPH_FUSION=0`` to skip this
    stage and get comparable cosine scores back in ``score`` instead.

    When ``CODEGRAPH_RERANK=1`` (see ``src.graph.reranker``), this becomes a
    two-stage retrieve-then-rerank pipeline: the cosine scan above pulls a
    wider candidate pool (``max(limit * 5, 50)``) instead of exactly
    ``limit``, a cross-encoder re-scores that shortlist on ``(query, chunk)``
    pairs, and the result is truncated to ``limit`` by the *rerank* score
    instead of the cosine/fusion score. Reranking is opt-in and degrades to
    plain ordering on any failure (not installed, model load error, etc.) —
    check the ``reranked`` field on the result to know which path ran.

    Every result carries a ``provenance`` (``"semantic"``, ``"bm25"``, or
    ``"fused(bm25+semantic)"``) and a ``score`` so callers never mistake a
    fuzzy similarity hit for a grounded structural edge — the resolver's
    exact/ambiguous/unresolved contract stays the source of truth for
    *structural* queries. ``score`` is a comparable 0..1 cosine value only
    when ``CODEGRAPH_FUSION=0``; by default it's an RRF rank score, not a
    similarity measure (see above). ``reranked=True`` results also carry a
    ``rerank_score`` — not directly comparable to plain cosine ``score``
    values from a non-reranked call.

    When ``CODEGRAPH_ANN_BACKEND=sqlite-vec`` (see ``src.graph.ann_index``)
    and an index has been built for the current model (``cg embed
    --build-index``), the candidate pool comes from a native ANN query
    instead of the brute-force scan below. Falls back to the brute-force scan
    transparently if the extension can't load or no index exists yet for
    this model.

    When ``include_callers=True``, each result is enriched with a small
    ``"callers"``/``"callees"`` neighbor list (1-hop, precise resolution
    only, capped at 5 each) so the tool returns a small subgraph instead of a
    flat list — the join back to the graph that this chunk's own docstring
    says a semantic hit is "meant to be" (see ``chunk_for_symbol``), done
    automatically instead of requiring a separate ``get_callers`` call per
    hit. Off by default: adds up to ``2 * limit`` extra graph queries, real
    latency for a feature not every caller needs.

    Returns ``[{"id", "name", "kind", "qualified_name", "file_path", "repo",
    "score", "chunk", "provenance", "reranked"}]`` (plus ``"callers"``/
    ``"callees"`` when requested) sorted by score (or rerank_score, when
    reranked) descending. ``score`` is an RRF rank score unless
    ``CODEGRAPH_FUSION=0`` (see above) or ``reranked=True``.
    """
    from codegraph.graph import embeddings as emb
    from codegraph.graph import reranker as rrk
    from codegraph.graph import ann_index as ann

    rerank_on = rrk.rerank_enabled()
    # When reranking, retrieve a wider shortlist than the caller asked for --
    # the cross-encoder's job is to re-sort a candidate pool, not the whole
    # corpus. Plain cosine ordering (rerank off) keeps today's behavior of
    # slicing to exactly `limit` from the cosine scan.
    pool_size = max(limit * 5, 50) if rerank_on else limit

    model = emb.current_model()
    q_blob, q_dim = emb.embed_query(query)

    candidates = None
    if ann.ann_backend_enabled():
        ann_hits = ann.ann_query(conn, model, q_blob, pool_size)
        if ann_hits is not None:
            # ANN path available and an index exists for this model -- skip
            # the brute-force scan below entirely.
            candidates = _candidates_from_ann_hits(conn, ann_hits, threshold)
        # else: extension unavailable or no index built yet for this model --
        # candidates stays None, falls through to the brute-force scan below
        # exactly as if ANN were never enabled.

    if candidates is None:
        # Brute-force cosine scan fallback (used when the ANN index isn't
        # available). Hard-cap the candidate pool so the fetchall() can't grow
        # unbounded with corpus size -- the preferred path is the sqlite-vec
        # ANN index; this is just the safety-net fallback. 50k rows is ~50ms
        # with numpy / ~2s pure-Python, well past any interactive result set.
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

        # Prefer numpy for the scan (fast); fall back to pure Python if the
        # extra isn't installed. Both produce identical cosine scores. The
        # scan core itself is shared via codegraph.retrieval.cosine_scan so
        # symbols / knowledge / memory all use one implementation.
        from codegraph.retrieval import cosine_scan

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
                    "provenance": "semantic",
                    "reranked": False,
                }
            )

    # RRF Hybrid fusion.
    fusion_enabled = os.environ.get("CODEGRAPH_FUSION", "1") != "0"
    if fusion_enabled and candidates is not None:
        try:
            from codegraph.graph.fusion import rrf_fuse

            # Fetch BM25 candidates
            bm25_raw = search_symbols(conn, query, limit=30)
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
                    base["provenance"] = "fused(bm25+semantic)"
                elif in_vec:
                    base = dict(vec_map[doc_id])
                    base["provenance"] = "semantic"
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
            logger.debug("fusion degraded to vector-only", exc_info=True)
            pass  # Degrade gracefully to vector-only if fusion fails

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

    Precise resolution only (``get_callers``/``get_callees`` default,
    ``fuzzy=False``) -- a semantic hit enriched with imprecise neighbors would
    undercut the whole point of keeping fuzzy and structural provenance
    separate. Missing/errored lookups degrade to an empty list per result,
    never raise -- this is a nice-to-have enrichment, not core retrieval.
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
