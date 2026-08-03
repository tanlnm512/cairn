"""Retriever / Reranker / Fusion protocol trio.

The retrieval pipeline is conceptually three composable stages:

  retrieve → fuse → rerank

This module defines the three protocols as the swappable seams; concrete
providers live in their own modules and the default wiring composes them via
:func:`run_pipeline` below.

The protocols are intentionally narrow so a future provider (Cohere rerank,
pgvector retriever, a learned-fusion model) plugs in by implementing one
class -- no edits to the call sites.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional, Protocol, Tuple, runtime_checkable


@dataclass
class Candidate:
    """A single retrieved item flowing through the pipeline.

    The minimal shared shape across symbols / knowledge / memory pipelines.
    ``id`` is the join key used by fusion; ``score`` is the retrieval score
    (cosine, then possibly an RRF rank score after fusion, then a rerank score
    after reranking). ``payload`` carries whatever domain-specific fields the
    producing retriever attached (name/kind/file_path for symbols, title/
    doc_type for knowledge, the OKFConcept for memory).
    """

    id: str
    score: float = 0.0
    payload: dict = field(default_factory=dict)
    provenance: str = "semantic"
    reranked: bool = False
    rerank_score: Optional[float] = None


@runtime_checkable
class Retriever(Protocol):
    """Stage 1: produce a ranked candidate list for a query.

    Implementations are responsible for the embed + scan + threshold pass.
    Different retrievers scan different tables (embeddings vs
    knowledge_embeddings vs in-memory concepts) but all return Candidates.
    """

    def retrieve(
        self,
        query: str,
        *,
        limit: int = 20,
        threshold: float = 0.3,
    ) -> List[Candidate]:
        ...


@runtime_checkable
class Reranker(Protocol):
    """Stage 3 (optional): re-score a shortlist on (query, chunk) pairs.

    A cross-encoder reranker trades latency for precision: it pulls a wider
    candidate pool from the retriever and re-sorts it. Degrades to the input
    order on any failure (model not installed, load error) -- the second return
    value tells the caller whether reranking actually ran.
    """

    def rerank(
        self,
        query: str,
        candidates: List[Candidate],
        limit: int,
    ) -> Tuple[List[Candidate], bool]:
        ...


@runtime_checkable
class Fusion(Protocol):
    """Stage 2 (optional): combine multiple ranked lists into one.

    The default (RRF) fuses lexical + semantic rankings without depending on
    score scales. A future learned-fusion provider can implement this too.
    """

    def fuse(
        self,
        rankings: List[List[str]],
        *,
        k: int = 60,
        weights: Optional[List[float]] = None,
    ) -> List[Tuple[str, float]]:
        ...


# --- default concrete providers -------------------------------------------

class RRFUnorderedFusion(Fusion):
    """Default Fusion wrapping ``graph.fusion.rrf_fuse``.

    Lazy-imports ``rrf_fuse`` so importing this protocols module never drags
    in the graph layer eagerly (keeps the embeddings stack opt-in for callers
    that only want the protocol types).
    """

    def fuse(
        self,
        rankings: List[List[str]],
        *,
        k: int = 60,
        weights: Optional[List[float]] = None,
    ) -> List[Tuple[str, float]]:
        from ..graph.fusion import rrf_fuse

        return rrf_fuse(rankings, k=k, weights=weights)


class CrossEncoderReranker(Reranker):
    """Default Reranker wrapping the existing ``graph.reranker`` module.

    Routes through ``rerank_enabled`` / ``rerank`` so the env-var gate
    (``CODEGRAPH_RERANK=1``) and model-name override
    (``CODEGRAPH_RERANK_MODEL``) keep working unchanged. A future external
    provider (e.g. Cohere) would replace this class behind the same protocol.
    """

    def rerank(
        self,
        query: str,
        candidates: List[Candidate],
        limit: int,
    ) -> Tuple[List[Candidate], bool]:
        # The cross-encoder reranker consumes/returns plain dicts. Adapt
        # Candidates to dicts and back so this provider slots in without
        # touching the cross-encoder implementation.
        from ..graph import reranker as rrk

        as_dicts = [_candidate_to_dict(c) for c in candidates]
        ranked_dicts, reranked = rrk.rerank(query, as_dicts, limit)
        out = [_dict_to_candidate(d) for d in ranked_dicts]
        return out, reranked


# --- dict/Candidate adapters ----------------------------------------------

def _candidate_to_dict(c: Candidate) -> dict:
    d = {"id": c.id, "score": c.score, "provenance": c.provenance,
         "reranked": c.reranked}
    d.update(c.payload)
    if c.rerank_score is not None:
        d["rerank_score"] = c.rerank_score
    return d


def _dict_to_candidate(d: dict) -> Candidate:
    known = {"id", "score", "provenance", "reranked", "rerank_score"}
    payload = {k: v for k, v in d.items() if k not in known}
    return Candidate(
        id=d.get("id"),
        score=d.get("score", 0.0),
        payload=payload,
        provenance=d.get("provenance", "semantic"),
        reranked=d.get("reranked", False),
        rerank_score=d.get("rerank_score"),
    )
