"""Cross-encoder reranking for semantic_search.

Second stage of a two-stage retrieval pipeline: the cosine/ANN scan in
`queries.semantic_search` is a *bi-encoder* (embeds query and candidate
independently -- cheap but blind to interactions); a *cross-encoder* scores
`(query, candidate)` jointly, more accurate but too slow to run against every
symbol, so it only ever sees a shortlist the cosine scan already narrowed down.

Off by default (`CAIRN_RERANK` unset), reuses the `sentence-transformers`
dependency from the `[semantic]` extra, and degrades to a no-op on any failure
rather than raising past this module.
"""
from __future__ import annotations

import logging
import os
from typing import List, Tuple

logger = logging.getLogger(__name__)

DEFAULT_RERANK_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

# Cache the loaded CrossEncoder so repeated calls within a process (e.g. one
# long-lived MCP server) don't reload weights on every semantic_search call.
_RERANKER_CACHE: dict = {}


def rerank_enabled() -> bool:
    """Whether the rerank stage should run at all.

    Opt-in via CAIRN_RERANK=1.
    """
    return os.environ.get("CAIRN_RERANK", "").strip().lower() in ("1", "true", "on")


def current_rerank_model() -> str:
    return os.environ.get("CAIRN_RERANK_MODEL", DEFAULT_RERANK_MODEL)


def reranker_available() -> bool:
    """True iff sentence-transformers' CrossEncoder can be imported right now.

    Does not attempt to load the model itself (that can still fail later);
    only answers "is the capability installed at all".
    """
    try:
        from sentence_transformers import CrossEncoder  # noqa: F401

        return True
    except ImportError:
        return False


def install_hint() -> str:
    return (
        "Reranking requires the 'semantic' extra (same dependency as local "
        "embeddings). Install it with: pip install 'cairn-intel[semantic]', then "
        "set CAIRN_RERANK=1."
    )


def _get_reranker():
    model_name = current_rerank_model()
    if model_name not in _RERANKER_CACHE:
        from sentence_transformers import CrossEncoder

        # Single-model cache: a model-name change evicts the stale entry.
        if _RERANKER_CACHE and next(iter(_RERANKER_CACHE)) != model_name:
            _RERANKER_CACHE.clear()
        _RERANKER_CACHE[model_name] = CrossEncoder(model_name)
    return _RERANKER_CACHE[model_name]


def rerank(query: str, candidates: List[dict], limit: int) -> Tuple[List[dict], bool]:
    """Rerank a candidate shortlist; returns (results, reranked).

    ``candidates`` must each have a ``"chunk"`` key. Non-fatal on any failure
    (disabled, uninstalled, or a `predict()` exception): falls back to
    ``candidates[:limit]`` unchanged with ``reranked=False``. On success, each
    returned dict gains a ``"rerank_score"`` float and the list is truncated
    to ``limit`` by that score, descending.
    """
    if not candidates:
        return candidates[:limit], False
    if not rerank_enabled() or not reranker_available():
        return candidates[:limit], False
    try:
        model = _get_reranker()
        pairs = [(query, c.get("chunk") or "") for c in candidates]
        scores = model.predict(pairs)
        ranked = sorted(
            zip(candidates, scores), key=lambda pair: -float(pair[1])
        )
        out = []
        for cand, score in ranked[:limit]:
            reranked_cand = dict(cand)
            reranked_cand["rerank_score"] = float(score)
            out.append(reranked_cand)
        return out, True
    except Exception:
        # Never let a reranker problem take down semantic search.
        logger.debug("rerank failed, returning unranked", exc_info=True)
        return candidates[:limit], False
