"""Retriever / Reranker / Fusion protocol trio."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Protocol, Tuple, runtime_checkable


@dataclass
class Candidate:
    """Retrieved item flowing through the search and ranking pipeline."""

    id: str
    score: float = 0.0
    payload: dict = field(default_factory=dict)
    provenance: str = "semantic"
    reranked: bool = False
    rerank_score: Optional[float] = None


@runtime_checkable
class Retriever(Protocol):
    """Protocol for generating a ranked candidate list for a query."""

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
    """Protocol for scoring and reordering candidate items against a query."""

    def rerank(
        self,
        query: str,
        candidates: List[Candidate],
        limit: int,
    ) -> Tuple[List[Candidate], bool]:
        ...


@runtime_checkable
class Fusion(Protocol):
    """Protocol for combining multiple ranked candidate lists into one."""

    def fuse(
        self,
        rankings: List[List[str]],
        *,
        k: int = 60,
        weights: Optional[List[float]] = None,
    ) -> List[Tuple[str, float]]:
        ...
