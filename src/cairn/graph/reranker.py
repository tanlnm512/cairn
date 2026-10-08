"""Cross-encoder reranking for semantic_search."""
from __future__ import annotations

import logging
import math
import os
import threading
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_RERANK_MODEL = "BAAI/bge-reranker-base"

# Pair budget pinned explicitly (not inherited from the installed
# tokenizer config) so the effective truncation window cannot silently
# shift every rerank score.
RERANK_MAX_LENGTH = 512

# Special tokens the pair encoding spends outside the two text bodies
# ([CLS] query [SEP] candidate [SEP] for BERT-style encoders).
_PAIR_SPECIAL_TOKENS = 3

# Section extraction only promotes fields; the full chunk remains in the tail.
_CHUNK_SECTION_LABELS = (
    "File:",
    "Enclosing Scope:",
    "Imports:",
    "Signature:",
    "Parameters:",
    "Return Type:",
    "Docstring:",
    "Body:",
)

# Concurrent reranker loads share one cache lock.
_RERANKER_CACHE: dict = {}
_RERANKER_CACHE_LOCK = threading.Lock()


def _rerank_marker_path():
    """Return the persistent marker that enables reranking after a successful download."""
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
    """Whether the rerank stage should run at all."""
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
    """True iff sentence-transformers' CrossEncoder can be imported right now."""
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
        from huggingface_hub import _CACHED_NO_EXIST, try_to_load_from_cache
    except ImportError:
        return False
    m_name = model_name or current_rerank_model()
    # CrossEncoder models store config.json at the repo root like embedders.
    result = try_to_load_from_cache(m_name, "config.json")
    return result is not None and result is not _CACHED_NO_EXIST


def download_reranker_model(model_name: Optional[str] = None) -> bool:
    """Download the reranker's weights into the local HuggingFace cache if absent."""
    import subprocess
    import sys

    m_name = model_name or current_rerank_model()
    if reranker_model_is_cached(m_name):
        print(f"Reranker model '{m_name}' is already cached — skipping download.")
        return True

    print(f"Downloading reranker '{m_name}' weights into local cache...")
    # Constructing the CrossEncoder IS the download (weights land in the HF
    # cache). max_length is a runtime-only knob (_get_reranker pins it) and
    # does not change what gets fetched.
    code = (
        "from sentence_transformers import CrossEncoder; "
        f"CrossEncoder({m_name!r})"
    )
    try:
        from .embeddings import _lib_pythonpath, _run_subprocess_with_progress

        _run_subprocess_with_progress(
            [sys.executable, "-c", code],
            f"Downloading {m_name}",
            env={**os.environ, "PYTHONPATH": _lib_pythonpath()},
        )
    except subprocess.CalledProcessError:
        # The helper already printed the child's captured output above (the
        # HF error -- or a ModuleNotFoundError when sentence-transformers
        # isn't importable anywhere the child can see).
        print(
            f"Failed to download reranker model '{m_name}' (see the output "
            "above)"
        )
        return False
    print(f"Reranker model '{m_name}' downloaded successfully.")
    return True


def _get_reranker():
    model_name = current_rerank_model()
    model = _RERANKER_CACHE.get(model_name)
    if model is None:
        with _RERANKER_CACHE_LOCK:
            model = _RERANKER_CACHE.get(model_name)
            if model is None:
                from sentence_transformers import CrossEncoder

                # Single-model cache: a model-name change evicts the stale entry.
                if _RERANKER_CACHE and next(iter(_RERANKER_CACHE)) != model_name:
                    _RERANKER_CACHE.clear()
                # Pin max_length so dependency defaults cannot shift scores.
                model = CrossEncoder(model_name, max_length=RERANK_MAX_LENGTH)
                _RERANKER_CACHE[model_name] = model
    return model


def _sigmoid(x: float) -> float:
    """Numerically stable logistic function: unbounded logit -> [0, 1]."""
    if x >= 0.0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def _extract_chunk_section(chunk: str, label: str) -> str:
    """Best-effort read of one labeled section out of a stored chunk."""
    lines = chunk.splitlines()
    collected: List[str] = []
    inside = False
    for line in lines:
        stripped = line.strip()
        is_label_line = any(
            stripped.startswith(l) for l in _CHUNK_SECTION_LABELS
        )
        if is_label_line:
            inside = stripped.startswith(label)
            if inside:
                collected.append(stripped[len(label):].strip())
        elif inside:
            collected.append(line)
    return "\n".join(part for part in collected if part).strip()


def _structured_candidate_text(c: dict) -> str:
    """Build the structured candidate side of a rerank pair."""
    kind = (c.get("kind") or "").strip()
    qname = (c.get("qualified_name") or c.get("name") or "").strip()
    path = (c.get("file_path") or "").strip()
    chunk = c.get("chunk") or ""

    parts: List[str] = []
    header = f"{kind} {qname}".strip()
    if header:
        parts.append(header)
    if path:
        parts.append(f"File: {path}")
    sig = _extract_chunk_section(chunk, "Signature:")
    if sig:
        parts.append(f"Signature: {sig}")
    doc = _extract_chunk_section(chunk, "Docstring:")
    if doc:
        parts.append(f"Docstring: {doc}")
    if chunk:
        # The chunk stays intact in the tail: it carries the context the
        # head doesn't (enclosing scope, imports, parameters, body), and
        # keeping it whole means extraction can never lose information.
        parts.append(chunk)
    return "\n".join(parts)


def _truncate_candidate(model, query: str, text: str) -> str:
    """Query-priority truncation of one candidate text to the pair budget."""
    tokenizer = getattr(model, "tokenizer", None)
    if tokenizer is not None:
        try:
            q_ids = tokenizer(query, add_special_tokens=False)["input_ids"]
            budget = RERANK_MAX_LENGTH - len(q_ids) - _PAIR_SPECIAL_TOKENS
            if budget <= 0:
                return text
            encoded = tokenizer(text, add_special_tokens=False)
            t_ids = encoded["input_ids"]
            if len(t_ids) <= budget:
                return text
            # Prefer byte-exact prefix cuts; decode only as a last resort.
            try:
                offsets = tokenizer(
                    text, add_special_tokens=False, return_offsets_mapping=True
                )["offsets_mapping"]
                for i in range(min(budget, len(offsets)) - 1, -1, -1):
                    end = offsets[i][1]
                    if end > 0:
                        return text[:end]
            except Exception:
                logger.debug("offset-based cut unavailable; prefix search")
            try:
                lo, hi = 0, len(text)
                while lo < hi:
                    mid = (lo + hi + 1) // 2
                    prefix_ids = tokenizer(
                        text[:mid], add_special_tokens=False
                    )["input_ids"]
                    if len(prefix_ids) <= budget:
                        lo = mid
                    else:
                        hi = mid - 1
                return text[:lo]
            except Exception:
                logger.debug("prefix search failed; decode fallback")
            return tokenizer.decode(t_ids[:budget]).strip()
        except Exception:
            logger.debug(
                "tokenizer-based truncation failed; char fallback",
                exc_info=True,
            )
    # Char-approximation fallback (~4 chars/token, the same heuristic
    # chunk_for_symbol uses to bound chunk size).
    budget_chars = (
        (RERANK_MAX_LENGTH - _PAIR_SPECIAL_TOKENS) * 4 - len(query)
    )
    if budget_chars <= 0 or len(text) <= budget_chars:
        return text
    return text[:budget_chars]


def rerank(
    query: str,
    candidates: List[dict],
    limit: int,
    structured: bool = False,
) -> Tuple[List[dict], bool]:
    """Rerank a candidate shortlist; returns (results, reranked)."""
    if not candidates:
        return candidates[:limit], False
    if not rerank_enabled() or not reranker_available():
        return candidates[:limit], False
    # An uncached model falls back without downloading.
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
        if structured:
            pair_texts = [
                _truncate_candidate(
                    model, query, _structured_candidate_text(c)
                )
                for c in candidates
            ]
        else:
            # Legacy flat format for A/B: raw chunk, SDK truncation.
            pair_texts = [c.get("chunk") or "" for c in candidates]
        pairs = [(query, text) for text in pair_texts]
        scores = model.predict(pairs)
        ranked = sorted(
            zip(candidates, scores), key=lambda pair: -float(pair[1])
        )
        out = []
        for cand, score in ranked[:limit]:
            reranked_cand = dict(cand)
            raw = float(score)
            reranked_cand["rerank_score"] = raw
            reranked_cand["rerank_score_norm"] = _sigmoid(raw)
            out.append(reranked_cand)
        return out, True
    except Exception:
        # Never let a reranker problem take down semantic search.
        logger.debug("rerank failed, returning hybrid order", exc_info=True)
        return candidates[:limit], False
