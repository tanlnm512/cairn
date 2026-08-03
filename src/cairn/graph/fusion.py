"""Reciprocal Rank Fusion (RRF) for hybrid retrieval.

Combines multiple ranked lists (e.g. BM25 lexical + vector semantic search)
into a single consensus ranking without depending on score scales.

Formula: score(d) = sum_i w_i / (k + rank_i(d)) where rank is 1-based.
"""
from __future__ import annotations

from typing import List, Tuple, Optional, Dict


def rrf_fuse(
    rankings: List[List[str]],
    k: int = 60,
    weights: Optional[List[float]] = None,
) -> List[Tuple[str, float]]:
    """Fuse multiple lists of document IDs using Reciprocal Rank Fusion.

    :param rankings: List of ranked ID lists (each list ordered best to worst).
    :param k: RRF constant (default 60, standard in industry).
    :param weights: Optional relative weight for each input list. Default 1.0 each.
    :return: List of (doc_id, fused_score) sorted descending by fused_score.
    """
    if not rankings:
        return []

    if weights is None:
        weights = [1.0] * len(rankings)
    elif len(weights) != len(rankings):
        raise ValueError("Length of weights must match length of rankings")

    scores: Dict[str, float] = {}

    for ranking, weight in zip(rankings, weights):
        for rank_1based, doc_id in enumerate(ranking, start=1):
            if not doc_id:
                continue
            rrf_val = weight / (k + rank_1based)
            scores[doc_id] = scores.get(doc_id, 0.0) + rrf_val

    # Sort descending by score, tie-break by doc_id
    sorted_scores = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return sorted_scores
