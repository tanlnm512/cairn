"""Semantic (embedding-based) symbol search.

Two-stage retrieval:
  1. Cosine scan (or sqlite-vec ANN) over the embeddings table, optionally
     blended with BM25 via Reciprocal Rank Fusion.
  2. Optional cross-encoder rerank stage, skipped when the fused (RRF) ranking
     is already decisive (``_fused_confident`` -- see the gating note on
     ``semantic_search``).

Imports vector math from ``vector_math``, BM25 search from ``lexical``, and
1-hop traversal from ``traversal`` for the ``include_callers=True`` enrichment.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .lexical import search_symbols
from .traversal import get_callers, get_callees

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Telemetry bucketing (spec §6.4 -- enums/buckets only, no free text/paths).
#
# `semantic_search` emits one `semantic_backend` event per call on its return
# path; wall-time and result-count are collapsed to fixed low-cardinality tags
# so the `events` table can't grow an unbounded distinct-value set. Both
# helpers are pure O(1); `emit()` itself is best-effort and never raises.
# ---------------------------------------------------------------------------

_MS_BUCKETS = (
    (10.0, "0-10ms"),
    (100.0, "10-100ms"),
    (1000.0, "100-1000ms"),
)
_MS_BUCKET_MAX = ">1000ms"

_N_BUCKETS = (
    (5, "1-5"),
    (10, "6-10"),
    (50, "11-50"),
)
_N_BUCKET_ZERO = "0"
_N_BUCKET_MAX = ">50"


def _ms_bucket(ms: float) -> str:
    """Bucket a wall-clock duration (ms) into a fixed low-cardinality tag."""
    for bound, label in _MS_BUCKETS:
        if ms < bound:
            return label
    return _MS_BUCKET_MAX


def _n_results_bucket(n: int) -> str:
    """Bucket a result count into a fixed low-cardinality tag (0 handled first)."""
    if n <= 0:
        return _N_BUCKET_ZERO
    for bound, label in _N_BUCKETS:
        if n <= bound:
            return label
    return _N_BUCKET_MAX


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

    ``min_margin`` (D-008) is the explicit per-call override of the margin
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
    its own — it just returns the nearest k).
    """
    ids = [sid for sid, score in ann_hits if score >= threshold]
    if not ids:
        return []
    score_by_id = {sid: score for sid, score in ann_hits}
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


# ---------------------------------------------------------------------------
# Explicit retrieval tunables (D-008, FR-005)
#
# The sweep/eval path is in-process, so per-combo environment mutation would
# leak state across lever combinations and make results order-dependent.
# This frozen object is the explicit injection channel instead: every knob
# the quality sweep needs to turn rides through here, never through env.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetrievalParams:
    """Immutable per-call retrieval tunables for ``semantic_search`` (D-008).

    ``None``-means-default is the whole contract (FR-005's
    defaults-preserving rule): a ``None`` field resolves to exactly the value
    today's code uses — the function-arg default, the hard-coded constant, or
    the env-gated setting — so ``RetrievalParams()`` and ``params=None`` are
    behaviorally identical, and the sweep's all-levers-off row is today's
    retrieval, not an approximation of it.

    Precedence: a non-``None`` field overrides the corresponding scalar arg
    (``dense_threshold`` over ``threshold``, ``rerank`` over ``rerank``).
    Legitimate callers pass either the scalar or the object, never both;
    when both are set the field wins.

    Fields (each ``None`` resolves to today's value):

    * ``dense_threshold`` — cosine cutoff for vector candidates
      (``threshold`` arg default ``0.3``).
    * ``rrf_k`` — RRF constant (hard-coded ``60`` today).
    * ``rrf_weights`` — ``(dense, sparse)`` relative RRF weights. ``None``
      keeps ``rrf_fuse``'s equal weights. The FIELD order is
      ``(dense, sparse)`` while the call site fuses ``[bm25, vec]`` — the
      reorder happens at the call site, never in the caller.
    * ``sparse_limit`` — BM25 fetch size (hard-coded ``30`` today).
    * ``sparse_top_n`` — BM25-leg rank-position cutoff applied before
      fusion (T010's NEW lever): keep only the first N ids of the fetched
      BM25 list in ``search_symbols``' best-first order. ``None`` keeps
      today's behavior (the list as fetched, already capped by
      ``sparse_limit``); ``0`` empties the sparse leg (the sweep's
      sparse-off point); negative values clamp to ``0`` (see the wiring
      comment). A position cutoff, NOT a score threshold
      (``sparse_min_score``), by deliberate choice: SQLite FTS5's
      ``bm25()`` rank is NEGATIVE with better = more negative (inverted
      "min score" semantics), and ``search_symbols``' LIKE-fallback /
      substring-union rows (lexical.py) carry no ``rank`` column at all —
      a score filter would behave path-dependently. A position cutoff is
      scale-free and composes with RRF, which consumes ranks, not scores.
    * ``dense_pool`` — brute-force cosine scan fetch cap (hard-coded
      ``50000`` today; ignored on the native ANN path, which sizes itself
      by the rerank pool).
    * ``rerank_pool`` — candidate pool carried into the rerank stage
      (computed ``max(limit * 5, 50)`` when the stage is armed, else
      ``limit``). A non-``None`` value replaces the computed size in both
      branches.
    * ``rerank`` — the rerank-stage override, same semantics as the
      per-call ``rerank`` arg: ``None`` = auto (env-gated plus the
      confidence gate), ``True`` = force past the gate (``CAIRN_RERANK=0``
      still wins), ``False`` = never.
    * ``enrich`` — FORWARD-COMPAT (query enrichment, FR-001/T008):
      ``semantic_search`` does not read this flag today. It is carried so
      the sweep can express on/off combinations the moment enrichment
      lands; unknown-to-the-function flags are ignored, never errors.
    * ``gate_min_margin`` — rerank confidence-gate margin override
      (``None`` = env ``CAIRN_RERANK_MIN_MARGIN`` or the calibrated
      ``0.45``; a non-``None`` value is clamped to ``[0, 1]`` exactly like
      the env path).
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
    gate_min_margin: Optional[float] = None


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

    ``params`` (D-008, FR-005) is an optional frozen
    :class:`RetrievalParams` carrying explicit retrieval tunables (dense
    threshold, RRF k/weights, pool sizes, sparse fetch limit and top-N
    cutoff, rerank/gate overrides). ``params=None`` -- and every ``None`` field of a passed
    object -- preserves today's exact behavior; the eval/sweep path injects
    combinations through this object rather than mutating the environment.
    Flags the function does not know yet (e.g. ``enrich`` until FR-001
    lands) are ignored, never errors.

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
    # Lazy import mirrors metric_buffering.py's discipline to avoid any
    # boot-order cycle with the telemetry package. emit() is best-effort and
    # never raises (spec §5.6); the import is cached after the first call.
    # RERANK_SKIPPED comes from the events module directly -- it is not
    # re-exported at the cairn.telemetry package level.
    from cairn.telemetry import emit, SEMANTIC_BACKEND, EMPTY_RESULT
    from cairn.telemetry.events import RERANK_SKIPPED

    # --- Explicit tunable injection (D-008, FR-005) -------------------------
    # None-means-default: with params=None (or a None field) every knob keeps
    # today's exact value, so this block is a behavioral no-op for every
    # existing caller -- params=None and RetrievalParams() must be identical,
    # which is what the equivalence tests pin. A non-None field wins over the
    # scalar arg (see RetrievalParams' precedence note).
    _gate_margin_override: Optional[float] = None
    if params is not None:
        if params.dense_threshold is not None:
            threshold = params.dense_threshold
        if params.rerank is not None:
            rerank = params.rerank
        if params.gate_min_margin is not None:
            # Clamp like the env path (_rerank_min_margin): the signal is a
            # ratio, so out-of-range values are a harness bug, not an error
            # worth failing a sweep run over.
            _gate_margin_override = min(max(params.gate_min_margin, 0.0), 1.0)

    # Under the dep-free hash fallback the embedding carries only token-overlap
    # signal, so annotate provenance strings to surface the degradation.
    _hash = emb.is_hash_fallback()
    _sem_prov = "semantic (hash backend)" if _hash else "semantic"
    _fused_prov = "fused(bm25+semantic, hash)" if _hash else "fused(bm25+semantic)"

    # Per-call override on top of the env/marker enablement: False is a hard
    # off; True forces past the confidence gate but still respects CAIRN_RERANK=0
    # (the kill switch must win); None (default) leaves the gate in charge.
    # Computed BEFORE pool_size: the wider rerank pool must be fetched whenever
    # the stage might still run (the gate can only be evaluated after fusion).
    _rerank_enabled = rrk.rerank_enabled()
    rerank_on = _rerank_enabled if rerank is not False else False
    # Hoisted above the early-return path so the semantic_backend telemetry can
    # report it. Same expression explore.py / tools_graph.py use: fusion defaults
    # ON (anything other than the literal "0" leaves it on).
    fusion_enabled = os.environ.get("CAIRN_FUSION", "1") != "0"
    # Wall-clock start for the `ms` telemetry bucket; _ann_used flips the
    # backend tag to "ann" only when the native vec0 query actually produced
    # this call's candidates (not merely when it was enabled).
    _t0 = time.perf_counter()
    _ann_used = False
    # Execution-truth flags for the fusion/rerank stages: the attrs must report
    # what the call ACTUALLY did, not what it was configured to do. A
    # configured-but-degraded stage (RRF exception, reranker model not cached)
    # reports 0 for the stage and 1 for its *_degraded marker, so the
    # degradation is durable in the events table instead of invisible.
    # Assigned in the enclosing scope and read by the _finish closure (same
    # pattern as _ann_used).
    _fusion_used = False
    _fusion_degraded = False
    _rerank_used = False
    _rerank_degraded = False

    def _finish(results: List[dict]) -> List[dict]:
        """Emit `semantic_backend` (+ `empty_result` when empty); return results.

        Single funnel for both return paths so the event fires exactly once per
        call regardless of which branch produced the list. `backend` precedence
        is hash > ann > brute: the hash-embed fallback is the worst degradation
        (token-overlap vectors carry no real semantic signal), so a query that
        ran on hash vectors is tagged ``hash`` whether or not the cosine scan
        used the native ANN index. `fusion`/`rerank` report execution (the
        stage ran to completion / applied re-scoring), with paired
        `*_degraded` markers for a configured stage that failed mid-call.
        Cardinality is bounded to enums + fixed buckets (spec §6.4). emit()
        never raises; the wrap is belt-and-suspenders so a bucketing bug can't
        fail the search (spec §5.6).
        """
        try:
            elapsed_ms = (time.perf_counter() - _t0) * 1000.0
            backend = "hash" if _hash else ("ann" if _ann_used else "brute")
            emit(
                SEMANTIC_BACKEND,
                backend=backend,
                fusion=1 if _fusion_used else 0,
                fusion_degraded=1 if _fusion_degraded else 0,
                rerank=1 if _rerank_used else 0,
                rerank_degraded=1 if _rerank_degraded else 0,
                ms=_ms_bucket(elapsed_ms),
                n_results=_n_results_bucket(len(results)),
            )
            if not results:
                # empty_result carries only query_kind (spec §6.4 lists query_kind,
                # not backend); the per-backend view comes from correlating with
                # the semantic_backend event emitted on the same call.
                emit(EMPTY_RESULT, query_kind="semantic_search")
        except Exception:
            logger.debug("semantic_search telemetry emit failed", exc_info=True)
        return results

    # When reranking, retrieve a wider shortlist for the cross-encoder to
    # re-sort; plain cosine ordering slices to exactly `limit`.
    pool_size = max(limit * 5, 50) if rerank_on else limit
    if params is not None and params.rerank_pool is not None:
        # Explicit override of the computed pool size (both branches).
        pool_size = params.rerank_pool

    model = emb.current_model()
    q_blob, q_dim = emb.embed_query(query)

    candidates = None
    ann_enabled = ann.ann_backend_enabled()
    if ann_enabled:
        ann_hits = ann.ann_query(conn, model, q_blob, pool_size)
        if ann_hits is not None:
            # ANN path available and an index exists for this model.
            candidates = _candidates_from_ann_hits(conn, ann_hits, threshold)
            _ann_used = True

    if candidates is None:
        # Brute-force cosine scan fallback. Hard-cap the candidate pool so the
        # fetchall() can't grow unbounded with corpus size.
        #
        # Surface the degradation once when sqlite-vec was *expected* but is
        # unavailable. When ann_enabled is False it's either that or an
        # explicit CAIRN_ANN_BACKEND=off (the helper stays silent on the
        # opt-out). When ann_enabled is True, ann_query has already surfaced
        # its own once-guarded reason on every None path (load failure inside
        # try_load; the no-index state and query errors in ann_query), so
        # there is nothing left to warn about here.
        if not ann_enabled:
            ann.warn_ann_fallback_once(logger, context="semantic_search")
        brute_force_limit = 50000
        if params is not None and params.dense_pool is not None:
            brute_force_limit = params.dense_pool
        rows = _mapping_rows(
            conn.execute(
                "SELECT e.symbol_id, e.vec, e.chunk, e.dim, "
                "s.name, s.kind, s.qualified_name, f.path AS file_path, f.repo_id AS repo "
                "FROM embeddings e "
                "JOIN symbols s ON e.symbol_id = s.id "
                "JOIN files f ON s.file_id = f.id "
                "WHERE e.model = ? "
                "LIMIT ?",
                (model, brute_force_limit),
            )
        )
        if not rows:
            return _finish([])

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

    # RRF Hybrid fusion (fusion_enabled hoisted above for the early-return path).
    if fusion_enabled and candidates is not None:
        try:
            from cairn.graph.fusion import rrf_fuse

            # Fetch BM25 candidates. search_symbols returns sqlite3.Row,
            # which has no .get() — convert to dict at this boundary so the
            # shared .get("id") access below works on both BM25 rows and the
            # candidate dicts (which are already plain dicts).
            sparse_limit = 30
            if params is not None and params.sparse_limit is not None:
                sparse_limit = params.sparse_limit
            bm25_raw = [dict(r) for r in search_symbols(conn, query, limit=sparse_limit)]
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

            rrf_k = 60
            rrf_weights: Optional[List[float]] = None
            if params is not None:
                if params.rrf_k is not None:
                    rrf_k = params.rrf_k
                if params.rrf_weights is not None:
                    # Field order is (dense, sparse); rrf_fuse pairs
                    # weights[i] with rankings[i], and the rankings here are
                    # [bm25(sparse), vec(dense)] -- reorder at the boundary.
                    rrf_weights = [params.rrf_weights[1], params.rrf_weights[0]]
                if params.sparse_top_n is not None:
                    # Rank-position cutoff on the BM25 leg (T010's NEW
                    # lever): keep the first N ids in search_symbols'
                    # best-first order, dropping the tail before fusion.
                    # Negative N clamps to 0 rather than erroring (the
                    # gate_min_margin clamp doctrine: a harness bug must
                    # not fail a sweep run) -- plain slicing with a
                    # negative N would silently keep the WORST |N| matches
                    # instead, which is the opposite of a cutoff.
                    top_n = max(params.sparse_top_n, 0)
                    if len(bm25_ids) > top_n:
                        bm25_ids = bm25_ids[:top_n]
            fused_rank = rrf_fuse([bm25_ids, vec_ids], k=rrf_k, weights=rrf_weights)
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
            _fusion_used = True
        except Exception:
            # Degrade to vector-only rather than failing the search, but log
            # at WARNING (not debug): this path was once silently broken by a
            # .get()-on-Row AttributeError swallowed here, so a future regression
            # must be visible. The debug-level exc_info still gives the traceback.
            _fusion_degraded = True
            logger.warning("RRF fusion degraded to vector-only", exc_info=True)

    # Confidence gate (auto mode only -- `rerank=True` explicitly wants the
    # cross-encoder, `rerank=False` already turned the stage off above).
    # Scoped to successful RRF fusion: the margin is calibrated on RRF score
    # geometry (rank-sums in the ~0.016-0.033 band), which cosine scores from
    # the CAIRN_FUSION=0 path don't share; with fusion off or degraded the
    # stage keeps today's behavior. Also disabled under hash embed vectors
    # (either the silent fallback or explicit CAIRN_EMBED_BACKEND=hash):
    # token-overlap vectors make the fused ranking untrustworthy exactly
    # when the cross-encoder is the only semantic signal left (measured
    # top-1 agreement of skip populations drops to ~0.0 there). On skip,
    # `rerank_on=False` routes the call through the plain
    # `candidates[:limit]` return below, so scores and provenance stay
    # exactly the fused ones the call actually produced.
    if (
        rerank_on
        and rerank is None
        and not _vectors_carry_token_overlap_only(_hash)
        and _fusion_used
        and candidates
        and _fused_confident(query, candidates, limit, min_margin=_gate_margin_override)
    ):
        rerank_on = False
        try:
            # Durable signal for doctor/metrics aggregation; reason is a
            # fixed enum so the events table's cardinality stays bounded
            # (spec §6.4). emit() is best-effort and never raises; the
            # wrap mirrors ann_index.warn_ann_fallback_once's belt-and-
            # suspenders discipline.
            emit(RERANK_SKIPPED, reason="confident_margin")
        except Exception:
            pass
        logger.debug("rerank skipped: fused ranking decisive (margin gate)")

    if rerank_on:
        final, reranked = rrk.rerank(query, candidates, limit)
        # `reranked` is the cross-encoder's own outcome flag: False means it
        # degraded to returning the hybrid order unchanged (disabled, model
        # not cached, or a predict() failure -- see reranker.rerank). Report
        # the execution truth, not the rerank_on config.
        _rerank_used = reranked
        _rerank_degraded = not reranked
        for item in final:
            item["reranked"] = reranked
            if "rerank_score" in item:
                item["rerank_score"] = round(item["rerank_score"], 4)
    else:
        final = candidates[:limit]

    if include_callers:
        _attach_callers(conn, final)

    return _finish(final)


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
