"""Explicit stage composition for semantic symbol search."""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Sequence,
    Tuple,
)

from . import ann_index as ann
from . import fusion
from . import reranker as rrk

logger = logging.getLogger(__name__)

Candidates = List[Dict[str, Any]]
SparseTerms = Sequence[str]
DenseRetriever = Callable[["SearchContext"], Optional[Candidates]]
SparseRetriever = Callable[["SearchContext", SparseTerms], List[Dict[str, Any]]]
FusedCandidateBuilder = Callable[
    [
        "SearchContext",
        Dict[Any, Dict[str, Any]],
        Dict[Any, Dict[str, Any]],
        List[Tuple[str, float]],
    ],
    Candidates,
]
ConfidenceGate = Callable[["SearchContext"], bool]
ResultEnricher = Callable[["SearchContext"], None]
PrfExpander = Callable[..., Any]
DfLookupFactory = Callable[[sqlite3.Connection], Callable[[str], Optional[Tuple[int, int]]]]
StageRecovery = Callable[["SearchContext"], None]


@dataclass
class SearchContext:
    """State shared by one semantic-search run.

    ``params`` is the caller-owned ``RetrievalParams``; it remains duck-typed
    here to avoid a reverse dependency on the composition entry point.
    """

    conn: sqlite3.Connection
    query: str
    limit: int
    threshold: float
    rerank_override: Optional[bool]
    params: Any
    adapters: "SearchAdapters"
    ann_index: "AnnIndexGateway" = field(default_factory=lambda: AnnIndexGateway())
    include_callers: bool = False
    fusion_enabled: bool = True
    hash_backend: bool = False
    backend: str = "brute"
    model: str = ""
    semantic_provenance: str = "semantic"
    fused_provenance: str = "fused(bm25+semantic)"
    gate_margin_override: Optional[float] = None
    started_at: float = field(default_factory=time.perf_counter)
    dense_query: str = ""
    enriched: Any = None
    extra_sparse_terms: Sequence[str] = ()
    dense_candidates: Optional[Candidates] = None
    candidates: Optional[Candidates] = None
    results: Candidates = field(default_factory=list)
    rerank_on: bool = False
    reranker_available: bool = False
    ann_enabled: bool = False
    dense_lost: bool = False
    dense_pass_failed: bool = False
    ann_used: bool = False
    fusion_used: bool = False
    fusion_degraded: bool = False
    rerank_used: bool = False
    rerank_degraded: bool = False
    telemetry_recorded: bool = False
    dense_ladder_evaluated: bool = False


@dataclass(frozen=True)
class AnnIndexGateway:
    """Boundary for the native ANN index used by dense retrieval."""

    def enabled(self) -> bool:
        return ann.ann_backend_enabled()

    def query(
        self,
        conn: sqlite3.Connection,
        model: str,
        query_blob: bytes,
        k: int,
        source: str = "embeddings",
    ) -> Optional[List[Tuple[str, float]]]:
        if source == "embeddings":
            return ann.ann_query(conn, model, query_blob, k)
        return ann.ann_query(conn, model, query_blob, k, source=source)


@dataclass(frozen=True)
class SearchAdapters:
    """Injected graph operations that are specific to the search corpus."""

    dense_retrieve: DenseRetriever
    sparse_retrieve: SparseRetriever
    query_enricher: Callable[..., Any]
    prf_expander: PrfExpander
    build_fused_candidates: FusedCandidateBuilder
    confidence_gate: ConfidenceGate
    enrich_results: ResultEnricher
    dense_failure: StageRecovery
    apply_degradation: ResultEnricher
    df_lookup_factory: Optional[DfLookupFactory] = None


@dataclass(frozen=True)
class SearchStage:
    """One named stage in the semantic-search pipeline."""

    name: str
    run: Callable[[SearchContext], None]


def _ms_bucket(ms: float) -> str:
    for bound, label in ((10.0, "0-10ms"), (100.0, "10-100ms"), (1000.0, "100-1000ms")):
        if ms < bound:
            return label
    return ">1000ms"


def _n_results_bucket(n: int) -> str:
    if n <= 0:
        return "0"
    for bound, label in ((5, "1-5"), (10, "6-10"), (50, "11-50")):
        if n <= bound:
            return label
    return ">50"


def _sparse_terms(
    context: SearchContext, extra_sparse_terms: Sequence[str]
) -> List[str]:
    sparse_terms: List[str] = []
    if context.enriched is not None:
        sparse_terms.extend(context.enriched.sparse_query.split())
    sparse_terms.extend(extra_sparse_terms)
    return sparse_terms


def _rank_inputs(candidates: Candidates) -> tuple[Dict[Any, Dict[str, Any]], List[str]]:
    ranked: Dict[Any, Dict[str, Any]] = {}
    ids: List[str] = []
    for candidate in candidates:
        symbol_id = candidate.get("id")
        if symbol_id:
            ranked[symbol_id] = candidate
            ids.append(symbol_id)
    return ranked, ids


def _rrf_options(
    context: SearchContext, bm25_ids: List[str]
) -> tuple[int, Optional[List[float]], List[str]]:
    rrf_k = 60
    weights: Optional[List[float]] = None
    if context.params is not None:
        if getattr(context.params, "rrf_k", None) is not None:
            rrf_k = context.params.rrf_k
        raw_weights = getattr(context.params, "rrf_weights", None)
        if raw_weights is not None:
            weights = [raw_weights[1], raw_weights[0]]
        sparse_top_n = getattr(context.params, "sparse_top_n", None)
        if sparse_top_n is not None:
            top_n = max(sparse_top_n, 0)
            if len(bm25_ids) > top_n:
                bm25_ids = bm25_ids[:top_n]
    return rrf_k, weights, bm25_ids


def query_stage(context: SearchContext) -> None:
    """Prepare query text and rerank gating through the existing enricher."""
    params = context.params
    context.fusion_enabled = os.environ.get("CAIRN_FUSION", "1") != "0"
    rerank_override = context.rerank_override
    if params is not None and getattr(params, "rerank", None) is not None:
        rerank_override = params.rerank
        context.rerank_override = rerank_override
    rerank_on = rrk.rerank_enabled() if rerank_override is not False else False
    if params is not None and bool(getattr(params, "prf", False)):
        rerank_on = False
    context.rerank_on = rerank_on
    context.reranker_available = rrk.reranker_available() if rerank_on else False

    if params is None or not getattr(params, "enrich", False):
        context.dense_query = context.query
        return

    df_lookup = None
    if getattr(params, "enrich_idf", False) and context.adapters.df_lookup_factory:
        df_lookup = context.adapters.df_lookup_factory(context.conn)
    if df_lookup is None:
        context.enriched = context.adapters.query_enricher(context.query)
    else:
        context.enriched = context.adapters.query_enricher(
            context.query, df_lookup=df_lookup
        )
    context.dense_query = context.enriched.dense_query


def retrieval_stage(context: SearchContext) -> None:
    """Retrieve the dense pool through the injected dense-leg adapter."""
    try:
        context.ann_enabled = context.ann_index.enabled()
        context.dense_candidates = context.adapters.dense_retrieve(context)
    except Exception:
        try:
            context.adapters.dense_failure(context)
        except Exception:
            pass
        context.dense_candidates = []
        context.dense_lost = True
        context.dense_pass_failed = True
    context.candidates = context.dense_candidates


def fusion_stage(context: SearchContext) -> None:
    """Fuse sparse and dense rankings with the existing RRF implementation."""
    if context.candidates is None or context.dense_pass_failed:
        return
    _fuse_candidates(context, ())
    if getattr(context.params, "prf", False) and context.candidates:
        _apply_prf(context)


def _fuse_candidates(
    context: SearchContext, extra_sparse_terms: Sequence[str]
) -> None:
    if context.candidates is None or not context.fusion_enabled:
        return
    try:
        sparse_terms = _sparse_terms(context, extra_sparse_terms)
        sparse_candidates = context.adapters.sparse_retrieve(context, sparse_terms)
        bm25_map, bm25_ids = _rank_inputs(sparse_candidates)
        dense_map, dense_ids = _rank_inputs(context.candidates)
        rrf_k, weights, bm25_ids = _rrf_options(context, bm25_ids)

        fused_rank = fusion.rrf_fuse(
            [bm25_ids, dense_ids], k=rrf_k, weights=weights
        )
        context.candidates = context.adapters.build_fused_candidates(
            context, bm25_map, dense_map, fused_rank
        )
        context.fusion_used = True
    except Exception:
        context.fusion_degraded = True
        logger.warning("RRF fusion degraded to vector-only", exc_info=True)


def _apply_prf(context: SearchContext) -> None:
    """Replace fused candidates once with RM3 pseudo-relevance feedback."""
    params = context.params
    candidates = context.candidates
    if candidates is None:
        return
    fb_n = params.prf_docs if params.prf_docs is not None else 10
    feedback_docs = [
        candidate.get("chunk")
        or " ".join(
            part
            for part in (candidate.get("name"), candidate.get("qualified_name"))
            if part
        )
        for candidate in candidates[: max(fb_n, 0)]
    ]
    df_lookup = (
        context.adapters.df_lookup_factory(context.conn)
        if context.adapters.df_lookup_factory
        else None
    )
    expansion = context.adapters.prf_expander(
        context.dense_query,
        feedback_docs,
        df_lookup=df_lookup,
        fb_terms=params.prf_terms if params.prf_terms is not None else 10,
        fb_lambda=params.prf_lambda if params.prf_lambda is not None else 0.5,
    )
    if not expansion.terms:
        return

    original_query = context.dense_query
    context.dense_query = expansion.dense_query
    try:
        context.candidates = context.adapters.dense_retrieve(context)
        if context.candidates is not None:
            _fuse_candidates(context, expansion.terms)
    except Exception:
        try:
            context.adapters.dense_failure(context)
        except Exception:
            pass
        context.candidates = []
        context.dense_lost = True
    finally:
        context.dense_query = original_query


def rerank_stage(context: SearchContext) -> None:
    """Apply confidence gating and the existing cross-encoder reranker."""
    if context.candidates is None:
        return
    if (
        context.rerank_on
        and context.rerank_override is None
        and context.adapters.confidence_gate(context)
    ):
        context.rerank_on = False
        _emit_rerank_skipped()
        logger.debug("rerank skipped: fused ranking decisive (margin gate)")

    if context.rerank_on:
        final, reranked = rrk.rerank(context.dense_query, context.candidates, context.limit)
        context.rerank_used = reranked
        context.rerank_degraded = not reranked
        for item in final:
            item["reranked"] = reranked
            rerank_score = item.get("rerank_score")
            if rerank_score is not None:
                item["rerank_score"] = round(rerank_score, 4)
                normalized = item.get("rerank_score_norm")
                if normalized is not None:
                    item["rerank_score_norm"] = round(normalized, 4)
        context.results = final
    else:
        context.results = context.candidates[: context.limit]


def enrichment_stage(context: SearchContext) -> None:
    """Add result-level graph enrichment without replacing ranked results."""
    if context.include_callers:
        context.adapters.enrich_results(context)


def telemetry_stage(context: SearchContext) -> None:
    """Emit semantic backend and empty-result events exactly once."""
    if context.telemetry_recorded:
        return
    try:
        from cairn.telemetry import EMPTY_RESULT, SEMANTIC_BACKEND, emit

        elapsed_ms = (time.perf_counter() - context.started_at) * 1000.0
        emit(
            SEMANTIC_BACKEND,
            backend="hash"
            if context.hash_backend
            else ("ann" if context.ann_used else "brute"),
            fusion=1 if context.fusion_used else 0,
            fusion_degraded=1 if context.fusion_degraded else 0,
            rerank=1 if context.rerank_used else 0,
            rerank_degraded=1 if context.rerank_degraded else 0,
            ms=_ms_bucket(elapsed_ms),
            n_results=_n_results_bucket(len(context.results)),
        )
        if not context.results:
            emit(EMPTY_RESULT, query_kind="semantic_search")
        context.telemetry_recorded = True
    except Exception:
        logger.debug("semantic_search telemetry emit failed", exc_info=True)


def result_assembly_stage(context: SearchContext) -> None:
    """Apply additive degradation markers after result enrichment."""
    if context.dense_lost:
        context.adapters.apply_degradation(context)


def _emit_rerank_skipped() -> None:
    try:
        from cairn.telemetry import emit
        from cairn.telemetry.events import RERANK_SKIPPED

        emit(RERANK_SKIPPED, reason="confident_margin")
    except Exception:
        pass


SEARCH_STAGES: Tuple[SearchStage, ...] = (
    SearchStage("query", query_stage),
    SearchStage("retrieval", retrieval_stage),
    SearchStage("fusion", fusion_stage),
    SearchStage("rerank", rerank_stage),
    SearchStage("enrichment", enrichment_stage),
    SearchStage("telemetry", telemetry_stage),
    SearchStage("result_assembly", result_assembly_stage),
)


def run_search(
    context: SearchContext, stages: Sequence[SearchStage] = SEARCH_STAGES
) -> Candidates:
    """Run the ordered search stages and return assembled results."""
    for stage in stages:
        stage.run(context)
    return context.results
