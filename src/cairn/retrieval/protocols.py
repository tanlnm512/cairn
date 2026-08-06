"""Retriever / Reranker / Fusion protocol trio.

The retrieval pipeline is three composable stages:

  retrieve → fuse → rerank

This module defines the three protocols as the swappable seams. Concrete
fusion (Reciprocal Rank Fusion) and cross-encoder reranking live in
``graph.fusion`` / ``graph.reranker`` and are wired directly by
``graph.semantic``. The protocols are narrow so a new provider plugs in by
implementing one class.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Protocol, Tuple, runtime_checkable


@dataclass
class Candidate:
    """A single retrieved item flowing through the pipeline.

    ``id`` is the join key used by fusion; ``score`` is the retrieval score
    (cosine, then possibly an RRF rank score after fusion, then a rerank score
    after reranking). ``payload`` carries domain-specific fields from the
    producing retriever.
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
