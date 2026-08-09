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
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_RERANK_MODEL = "BAAI/bge-reranker-base"

# Cache the loaded CrossEncoder so repeated calls within a process (e.g. one
# long-lived MCP server) don't reload weights on every semantic_search call.
_RERANKER_CACHE: dict = {}


def _rerank_marker_path():
    """Persistent marker file (<CAIRN_HOME>/rerank_enabled) recording that
    `cairn download-reranker` succeeded and reranking should be auto-enabled.

    A CLI process cannot export an env var into its parent shell, so successful
    pre-download writes this marker and `rerank_enabled()` honors it as if
    CAIRN_RERANK=1 had been set. Imported lazily so importing this module never
    forces paths.CAIRN_HOME resolution (which would pin the home dir at import
    time and break tests that relocate it).
    """
    from ..paths import CAIRN_HOME
    return CAIRN_HOME / "rerank_enabled"


def set_rerank_enabled_persistently():
    """Write the auto-enable marker. Called after a successful download-reranker."""
    try:
        marker = _rerank_marker_path()
        marker.parent.mkdir(parents=True, exist_ok=True)
        # Write the resolved model name so a later model switch is detectable.
        marker.write_text(current_rerank_model() + "\n")
    except OSError as exc:
        logger.debug("could not write rerank marker: %s", exc)


def rerank_enabled() -> bool:
    """Whether the rerank stage should run at all.

    Enabled if ANY of:
      - CAIRN_RERANK is set to a truthy value (1/true/on), OR
      - the persistent auto-enable marker exists (written by a successful
        `cairn download-reranker`).
    Disabled if CAIRN_RERANK is set to a falsy value (0/false/off) — this
    explicit OFF always wins, even if the marker exists, so users have a hard
    kill switch.
    """
    env = os.environ.get("CAIRN_RERANK", "").strip().lower()
    if env in ("0", "false", "off"):
        return False
    if env in ("1", "true", "on"):
        return True
    # Env unset: honor the persistent marker if present.
    try:
        return _rerank_marker_path().exists()
    except Exception:
        return False


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


def reranker_model_is_cached(model_name: Optional[str] = None) -> bool:
    """Whether the reranker's weights are present in the local HuggingFace cache."""
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        return False
    m_name = model_name or current_rerank_model()
    # CrossEncoder models store config.json at the repo root like embedders.
    return try_to_load_from_cache(m_name, "config.json") is not None


def download_reranker_model(model_name: Optional[str] = None) -> bool:
    """Download the reranker's weights into the local HuggingFace cache if absent.

    Returns True if the model is available locally after the call (already
    cached, or successfully downloaded). Mirrors ``embeddings.download_model``
    so ``cairn download-reranker`` and ``cairn embed --download-model`` share
    a shape. Does not require ``CAIRN_RERANK=1`` — pre-fetching the weights
    should not depend on the feature being enabled at download time.
    """
    m_name = model_name or current_rerank_model()
    if reranker_model_is_cached(m_name):
        print(f"Reranker model '{m_name}' is already cached — skipping download.")
        return True
    try:
        from sentence_transformers import CrossEncoder  # noqa: F401
    except ImportError:
        print(
            f"Cannot download reranker '{m_name}': sentence-transformers is not "
            f"installed. {install_hint()}"
        )
        return False
    try:
        print(f"Downloading reranker '{m_name}' weights into local cache...")
        # Constructing a CrossEncoder fetches the weights into the HF cache.
        CrossEncoder(m_name)
        print(f"Reranker model '{m_name}' downloaded successfully.")
        return True
    except Exception as exc:
        print(f"Failed to download reranker model '{m_name}': {exc}")
        return False


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
    (disabled, uninstalled, model not cached, or a `predict()` exception): falls
    back to ``candidates[:limit]`` unchanged with ``reranked=False`` — i.e. the
    hybrid (vector + BM25 + RRF) order is returned as-is. On success, each
    returned dict gains a ``"rerank_score"`` float and the list is truncated
    to ``limit`` by that score, descending.

    The model-cache check is proactive (before `_get_reranker`) so a missing
    or evicted model logs once at info and returns the hybrid fallback, rather
    than attempting a network download mid-query or crashing.
    """
    if not candidates:
        return candidates[:limit], False
    if not rerank_enabled() or not reranker_available():
        return candidates[:limit], False
    # Proactive guard: is the configured model actually cached locally? If not,
    # fall back to the hybrid order rather than blocking on a download or
    # surfacing a load error. (Auto-enable via the download marker guarantees a
    # model was cached at enable time, but the cache can be evicted later.)
    m_name = current_rerank_model()
    if not reranker_model_is_cached(m_name):
        logger.info(
            "rerank enabled but model '%s' is not cached locally; falling back "
            "to hybrid order. Run `cairn download-reranker` to fetch it.",
            m_name,
        )
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
        logger.debug("rerank failed, returning hybrid order", exc_info=True)
        return candidates[:limit], False
