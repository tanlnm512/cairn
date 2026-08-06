"""Retrieval pipeline: composable Retriever / Fusion / Reranker stages.

Public surface re-exported here so higher layers import the protocols from a
single place:

    from cairn.retrieval import Candidate, Retriever, Reranker, Fusion

The unified cosine-scan core lives in :mod:`vector_scan` and backs all three
retrieval paths (symbols / knowledge / memory).
"""
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
