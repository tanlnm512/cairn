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


# ---------------------------------------------------------------------------
# Rerank confidence gating (P0-2).
#
# Steady-state profiling showed ~95% of a `semantic_search` call's wall time
# is the optional cross-encoder rerank (predict on max(limit*5, 50) pairs).
# When the FUSED ranking is already decisive the rerank re-sorts a list whose
# answer it cannot improve, so the stage is skipped. The gate is deliberately
# simple and deterministic: a normalized margin over the RRF scores, plus an
# exact-name corroboration (the fused #1 must be an exact reference of the
# query).
#
# Calibration (bge-m3 embeddings + BAAI/bge-reranker-base over a copy of this
# repo's src/ tree, 63 agent-style queries; see the PR description for the
# full tables): at threshold 0.45 the gated population keeps top-1 agreement
# 1.00 (limit=10) / 0.94 (limit=20) with the reranked result on the
# production-code corpus (0.91 on a corpus that also includes test-name
# twins), skipping ~17-25% of calls (~70% of exact-name traffic -- the
# dominant agent query shape). BM25-#1 corroboration was measured and
# REJECTED: populations it admits only reach 0.73-0.76 top-1 agreement
# (fragment queries like "schema"/"bm25" where BM25's #1 is not the answer).
# The gate is disabled under hash (token-overlap) vectors: there the vector
# signal is token overlap only, and the measured top-1 agreement of skip
# populations drops to ~0.0 -- rerank is the only semantic component left,
# so it must run.
#
# Calibration basis: 0.45, with the numbers on
# record. At margins {0.30, 0.45, 0.60, 0.75} the gate skips 0/29 tune
# queries both at the shipped config and with query enrichment forced on:
# every corpus query is a natural-language question and none is an exact-name
# reference of its fused #1 -- the gate
# deliberately still sees the RAW query, so flipping enrichment cannot
# shift the corroboration -- which makes the skip-rate curve flat at zero
# across the entire margin axis. A margin cannot be re-calibrated on a
# population with no skip traffic; the agent-style calibration above remains
# the operative basis. Under the shipped config the margin axis is degenerate
# anyway: the BM25 leg is empty for sentence queries, RRF fuses a single
# list, and all margins collapse to the same constant. The
# margin-only hypothetical (corroboration dropped) was rejected: it would
# skip too many qualifying queries at low agreement -- the exact-name
# corroboration, not the margin, is what makes skips safe. The gate is
# pair-format-safe by construction: it reads the fused RRF ranking BEFORE
# the rerank call, so pair
# construction cannot shift its inputs. tests/test_rerank_gating.py pins
# this decision (TestCalibrationPin).
# ---------------------------------------------------------------------------

# Default for CAIRN_RERANK_MIN_MARGIN. See the calibration note above.
_DEFAULT_RERANK_MIN_MARGIN = 0.45


def _rerank_min_margin() -> float:
    """The confidence threshold above which rerank is skipped (0.0-1.0).

    ``CAIRN_RERANK_MIN_MARGIN`` overrides the calibrated default; values are
    clamped to [0, 1] because the signal is a ratio (1.0 effectively disables
    skipping -- a fused ranking never has a perfect margin -- and 0.0 skips
    on every fused call that passes the corroboration check).
    Unparseable values fall back to the default rather than raising: this is
    a latency knob, not correctness.
    """
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
    """Normalized top-to-edge margin of a fused (RRF) ranking, in [0, 1].

    ``margin = (score[0] - score[min(limit-1, len-1)]) / score[0]`` over the
    RRF scores the call actually produced. A single candidate is trivially
    decisive (1.0): rerank's only power is reordering the pool, and a one-item
    pool is already final. A non-positive top score (defensive; RRF scores
    are always positive) returns 0.0 so the gate never divides by zero or
    skips on a degenerate ranking.
    """
    if len(candidates) <= 1:
        return 1.0
    top = candidates[0].get("score") or 0.0
    if top <= 0.0:
        return 0.0
    edge = candidates[min(limit - 1, len(candidates) - 1)].get("score") or 0.0
    return (top - edge) / top


def _exact_name_hit(query: str, top: dict) -> bool:
    """Whether the query is an exact (case-insensitive) reference to `top`.

    The corroboration half of the confidence gate. Covers the agent idiom of
    querying a known symbol name verbatim (``"ApiFactory"`` or its qualified
    form) -- the one lexical shape that is conclusive on its own. Calibration
    showed BM25-#1 agreement is NOT a safe substitute (fragment queries where
    BM25's #1 is a module or same-token neighbor reach only ~0.75 top-1
    agreement after the skip), so the gate requires this stronger check.
    """
    q = query.strip().lower()
    if not q:
        return False
    name = (top.get("name") or "").strip().lower()
    qual = (top.get("qualified_name") or "").strip().lower()
    return q == name or q == qual or qual.endswith("." + q)


def _vectors_carry_token_overlap_only(hash_fallback_flag: bool) -> bool:
    """True when this call's embeddings are hash (token-overlap) vectors.

    Covers BOTH hash modes: the silent local-backend fallback (the caller's
    ``is_hash_fallback()`` flag) and an explicit ``CAIRN_EMBED_BACKEND=hash``
    (the documented dep-free smoke-test mode, which ``is_hash_fallback``
    deliberately does not flag because the user chose it). For the rerank
    gate the distinction doesn't matter -- the vectors carry no semantic
    signal either way, and calibration measured ~0.0 top-1 agreement between
    skip populations and the cross-encoder under hash vectors.
    """
    if hash_fallback_flag:
        return True
    return os.environ.get("CAIRN_EMBED_BACKEND", "").strip().lower() == "hash"


def _fused_confident(
    query: str,
    candidates: List[dict],
    limit: int,
    min_margin: Optional[float] = None,
) -> bool:
    """Whether the fused ranking is decisive enough to skip the rerank stage.

    Two conditions, both required:

    * margin -- the fused #1 leads the last-returned slot by at least
      ``CAIRN_RERANK_MIN_MARGIN`` (normalized RRF-score ratio), AND
    * corroboration -- the fused #1 is an exact-name hit for the query.
      A wide margin alone says the rank fusion found a stable #1, not that
      the #1 is the right answer; the exact-name check supplies the lexical
      evidence that the query was *about* that symbol.

    ``min_margin`` is the explicit per-call override of the margin
    (``RetrievalParams.gate_min_margin``, pre-clamped by the caller); ``None``
    keeps the env/default resolution.
    """
    margin = _rerank_min_margin() if min_margin is None else min_margin
    if _fused_margin(candidates, limit) < margin:
        return False
    return _exact_name_hit(query, candidates[0])


def _mapping_rows(cursor) -> list:
    """Normalize fetched rows to mapping access regardless of ``row_factory``.

    A bare ``sqlite3.connect()`` (no ``Row`` factory) yields plain tuples;
    this module reads rows by column name (``r["vec"]``, ``r["symbol_id"]``),
    so a bare connection used to raise ``TypeError`` inside the retrieval
    path and the search silently degraded to the FTS fallback (found while
    minting the DS-v1 quality baseline: a quality run through a bare
    connection measured recall 0.0). Normalizing once at each fetch boundary
    makes any caller's connection shape safe. Row-connection rows pass
    through untouched (zero copies on the standard path).
    """
    rows = cursor.fetchall()
    if not rows or not isinstance(rows[0], tuple):
        return rows
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, r)) for r in rows]


def _candidates_from_ann_hits(
    conn: sqlite3.Connection, ann_hits: List[Tuple[str, float]], threshold: float
) -> List[dict]:
    """Turn ``ann_index.ann_query``'s (symbol_id, score) pairs into the same
    candidate dict shape the brute-force scan produces.

    Re-applies ``threshold`` (the vec0 MATCH query has no threshold concept of
    its own — it just returns the nearest k). Dedups per symbol by MAX score:
    a symbol reaches the hit list once per vector when its
    table holds multiple rows for it, and must surface exactly once at its
    best score. At one row per symbol max equals the only score, so the
    single-vector behavior is unchanged.
    """
    # Max-score dedup per symbol: a symbol qualifies iff its BEST vector
    # clears the threshold, appears exactly once, and carries that best
    # score. (A last-wins merge would let a later below-threshold hit
    # overwrite a passing score, and a multi-hit
    # symbol would appear once per hit.)
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
    """Merge two already-deduped ANN candidate lists into one ranked list.

    Under ``params.multivector`` the same symbol can hit
    both the ``vec_`` leg and the ``vecmv_`` leg -- once each, since
    ``_candidates_from_ann_hits`` already max-dedups within a leg -- and
    must surface exactly ONCE in the merged list, at its best (max) score.
    Both inputs are score-descending; the result is too, via a
    stable sort that keeps ``base`` ahead of ``extra`` on exact score ties,
    so the merge is deterministic. Candidate metadata (``chunk`` etc.) is
    the base-table display text (``_candidates_from_ann_hits`` joins
    ``embeddings``); only the SCORE is the max across vectors.
    """
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
    """Immutable per-call retrieval tunables for ``semantic_search``.

    Fields default to None, which preserves standard retrieval behavior.
    Non-None fields override corresponding scalar arguments and environment settings.
    """

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
    """Build a memoized per-term DF lookup over the persisted ``term_df``.

    The ``enrich_idf`` boundary half: ``semantic_search`` (and only it --
    ``enrich`` stays pure) calls this when ``params.enrich_idf`` is truthy
    and hands the returned callable to ``enrich`` as ``df_lookup``.

    Contract of the returned ``lookup(token)``:

    * ``token`` is the CASE-FOLDED key (the caller -- ``enrich``'s
      ubiquity predicate -- lower-cases before the call, matching the
      FTS5 unicode61 case-folding ``term_df`` keys were built with);
    * one indexed ``SELECT symbol_df, n_symbols FROM term_df WHERE
      token = ?`` per DISTINCT token, memoized in a per-lookup dict, so a
      ``semantic_search`` call costs O(#distinct query tokens) SELECTs --
      each a ``token`` PRIMARY-KEY probe,
      never a table scan;
    * returns ``None`` for an absent token ("no DF data": the term keeps
      full weight) or the ``(symbol_df, n_symbols)`` 2-tuple; the
      drop/keep decision (the 0.90 cutoff) belongs to ``enrich``, not
      here -- this is a read-only view over the connection's corpus.
    """
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

    ``rerank`` is a per-call override of the rerank stage: ``None`` (default)
    is auto -- the stage runs when enabled AND the fused ranking is not
    already decisive (see below); ``True`` forces the stage past the
    confidence gate when it is enabled (``CAIRN_RERANK=0`` still wins); ``False``
    never reranks regardless of enablement. Confidence gating (auto mode
    only): when the fused RRF ranking's #1 leads the last-returned slot by a
    normalized margin >= ``CAIRN_RERANK_MIN_MARGIN`` (default 0.45, calibrated)
    AND the #1 is an exact-name hit for the query, the expensive cross-encoder
    pass is skipped because it cannot change the answer -- the fused order,
    scores, and provenance are returned as-is (``reranked=False``) and a
    ``rerank_skipped`` telemetry event records the skip. The gate only applies
    to fused rankings from a real embed backend (``CAIRN_FUSION=0`` or the
    hash backend -- fallback or explicit -- keep today's
    always-rerank-when-enabled behavior).

    ``params`` is an optional frozen
    :class:`RetrievalParams` carrying explicit retrieval tunables (dense
    threshold, RRF k/weights, pool sizes, sparse fetch limit and top-N
    cutoff, rerank/gate overrides). ``params=None`` -- and every ``None`` field of a passed
    object -- preserves today's exact behavior; the eval/sweep path injects
    combinations through this object rather than mutating the environment.
    ``params.enrich=True`` computes query enrichment ONCE at
    this boundary and feeds both legs from it: the single ``embed_query``
    call embeds the enriched ``dense_query`` (original + identifiers), and
    the sparse (BM25) fetch goes through enrichment's term mode. The
    confidence gate keeps seeing the raw query;
    the rerank pair's query side is the enriched ``dense_query`` --
    which equals the raw query whenever enrichment is off.
    ``params.enrich_idf=True`` additionally injects the per-term
    ``term_df`` DF lookup into that one ``enrich`` call (one indexed
    SELECT per distinct query token); off/``None`` builds no
    lookup and issues no ``term_df`` SELECT. ``params.multivector=True``
    widens the dense leg to the parallel ``embeddings_mv``
    vectors: the brute scan UNIONs the mv rows (same model) beside
    ``embeddings`` and the ANN leg queries the ``vecmv_`` index beside
    ``vec_``, each leg consolidating every symbol to ONE entry at its MAX
    score across vectors; the mv leg is strictly additive -- a missing
    ``vecmv_`` index leaves the base candidates unchanged, never errors.
    Off/``None`` never reads ``embeddings_mv``: identical SQL, identical
    ANN calls, byte-identical results. ``params.prf=True``
    re-runs the full pass (both legs + fusion) ONCE at the post-fusion
    seam with an RM3-expanded query -- costing a SECOND ``embed_query``
    call, the explicit flag-gated exception to the one-call
    doctrine, budget-accounted by REPLACING the rerank stage (never
    stacked: the stage is forced off on PRF combos regardless of
    ``rerank``/``CAIRN_RERANK``). An empty expansion skips the second
    pass. Off/``None`` runs no second pass: byte-identical results, one
    ``embed_query`` call. Flags
    the function still does not know are ignored, never errors.

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
    requested) sorted by score (or rerank_score) descending. When a hard
    dense-leg embed failure falls to an active ladder rung, every
    result additionally carries ``"degraded": "embedding-backend"`` and a
    ``"hint"`` remediation line; results are otherwise unchanged in shape.
    """
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
