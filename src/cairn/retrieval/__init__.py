"""Retrieval pipeline: composable Retriever / Fusion / Reranker stages."""
from .protocols import (
    Candidate,
    Retriever,
    Reranker,
    Fusion,
)
from .vector_scan import cosine_scan

__all__ = [
    "Candidate",
    "Retriever",
    "Reranker",
    "Fusion",
    "cosine_scan",
]
