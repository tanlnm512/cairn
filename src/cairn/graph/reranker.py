"""Cross-encoder reranking for semantic_search.

Second stage of a two-stage retrieval pipeline: the cosine/ANN scan in
`queries.semantic_search` is a *bi-encoder* (embeds query and candidate
independently -- cheap but blind to interactions); a *cross-encoder* scores
`(query, candidate)` jointly, more accurate but too slow to run against every
symbol, so it only ever sees a shortlist the cosine scan already narrowed down.

T016 (FR-004, D-005): the pair is (query, importance-ordered structured
candidate) — identity fields (kind, qualified name, path, signature,
docstring) first, stored chunk last — with the encoder window pinned at
`RERANK_MAX_LENGTH` and query-priority truncation (the query is never cut;
the candidate loses from its tail, which carries the least-important
content). Raw bge-reranker scores are unbounded logits: ordering uses them
directly, any thresholding must use the sigmoid-mapped
``rerank_score_norm``.

Off by default (`CAIRN_RERANK` unset), reuses the `sentence-transformers`
dependency from the `[semantic]` extra, and degrades to a no-op on any failure
rather than raising past this module.
"""
from __future__ import annotations

import logging
import math
import os
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_RERANK_MODEL = "BAAI/bge-reranker-base"

# D-005 / FR-004 (T016): the pair budget, pinned explicitly rather than
# inherited from whatever the installed sentence-transformers resolves.
# Probed in this install (sentence-transformers 5.6.1): a bare
# CrossEncoder("BAAI/bge-reranker-base") already resolves max_length=512
# (tokenizer model_max_length 512), so this is a pin against future drift
# (model/config upgrade silently changing the effective window), not a
# behavior change. The bge-reranker-base model card lists 512 as the max.
RERANK_MAX_LENGTH = 512

# Special tokens the pair encoding spends outside the two text bodies
# ([CLS] query [SEP] candidate [SEP] for BERT-style encoders).
_PAIR_SPECIAL_TOKENS = 3

# The labeled section headers a chunk_for_symbol chunk can carry (variant
# B/C shapes; see embeddings.chunk_for_symbol). Used only to *read* the
# Signature/Docstring sections out of the stored chunk -- the chunk itself
# is always kept intact in the pair tail, so a mis-parse can never lose
# information, only fail to promote a field to the head.
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
        # max_length pinned explicitly (D-005): today the default resolves to
        # 512 in this install, but relying on the implicit resolution means a
        # sentence-transformers/config upgrade could silently change the
        # truncation window and shift every rerank score with no code diff.
        _RERANKER_CACHE[model_name] = CrossEncoder(
            model_name, max_length=RERANK_MAX_LENGTH
        )
    return _RERANKER_CACHE[model_name]


def _sigmoid(x: float) -> float:
    """Numerically stable logistic function: unbounded logit -> [0, 1].

    bge-reranker raw scores are unbounded logits (model card; research RQ4),
    so any thresholding/interpretation must go through this map. Ranking is
    unchanged (sigmoid is monotone) -- it exists so future score cutoffs and
    T017's distribution analysis see calibrated probabilities, never raw
    logits. The naive 1/(1+exp(-x)) overflows for x < ~-709; the two-branch
    form below is exact for all finite floats.
    """
    if x >= 0.0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def _extract_chunk_section(chunk: str, label: str) -> str:
    """Best-effort read of one labeled section out of a stored chunk.

    A section runs from its ``label`` line ("Signature: ...") to the next
    line carrying any known chunk label. Best-effort by design: a docstring
    that itself contains a line like "Signature: ..." truncates the read
    early, and any mis-parse is harmless -- the full chunk is still appended
    to the pair tail by _structured_candidate_text, so extraction failure
    can only fail to *promote* a field, never lose it.
    """
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
    """Build the D-005 structured candidate side of a rerank pair.

    Importance-ordered head first (kind + qualified name, file path,
    signature, docstring -- in that order), full stored chunk appended
    last. Because truncation eats the pair from the tail, whatever matters
    most is guaranteed to survive and the least-important content (chunk
    body, then the chunk's duplicate of the promoted fields) loses first.

    Field sourcing: kind/qualified_name/file_path come from the candidate
    dict itself (they are ground truth, and they exist even for BM25-only
    candidates whose ``chunk`` is empty -- today those rerank against the
    empty string). Signature and docstring have no dedicated candidate
    fields; they exist only inside the chunk text (variant B/C carry
    ``Signature:``/``Docstring:`` sections), so they are extracted
    best-effort and degrade to absence when the chunk lacks them.

    Graceful degradation: every field is optional; missing ones skip their
    line. A candidate with no identity fields and no chunk degrades to the
    empty string, exactly as the legacy flat format did.
    """
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
    """Query-priority truncation of one candidate text to the pair budget.

    CrossEncoder.predict truncates the (query, candidate) pair jointly
    (HF "longest_first"), which cannot express "never touch the query":
    near the boundary it will trim either side. So the candidate TEXT is
    pre-truncated here to the tokens left after the query, and the SDK's
    own truncation becomes a no-op. Measurement contract: the query
    reaches the cross-encoder verbatim; the candidate loses from its tail
    (the least-important end of the importance-ordered text).

    Tokenizer-based when the model exposes one; falls back to the chunker's
    own ~4 chars/token approximation otherwise (stub models, tokenizer
    access failures). A query that alone fills the window returns the text
    untruncated -- there is no budget to protect, and the SDK decides.
    """
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
            # Cut strategy, in order of fidelity:
            # 1. offset mapping (byte-exact prefix in one call) — supported
            #    by real fast tokenizers; silently absent in some installs
            #    (this repo's XLMRobertaTokenizer accepts the kwarg but
            #    returns no mapping), hence the KeyError catch;
            # 2. binary search on the char prefix — token count is monotone
            #    in char length, so the largest prefix within budget is a
            #    byte-exact cut (~log2(len) tokenizer calls, only on the
            #    rare oversized candidate);
            # 3. decode(ids[:budget]) — token-exact but NOT byte-exact
            #    (SentencePiece decoders drop newlines), kept as the last
            #    resort for tokenizers that only support encode/decode.
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
    structured: bool = True,
) -> Tuple[List[dict], bool]:
    """Rerank a candidate shortlist; returns (results, reranked).

    ``candidates`` must each have a ``"chunk"`` key. Non-fatal on any failure
    (disabled, uninstalled, model not cached, or a `predict()` exception): falls
    back to ``candidates[:limit]`` unchanged with ``reranked=False`` — i.e. the
    hybrid (vector + BM25 + RRF) order is returned as-is. On success, each
    returned dict gains a ``"rerank_score"`` float and the list is truncated
    to ``limit`` by that score, descending.

    T016 (FR-004, D-005) — pair format: with ``structured=True`` (default)
    the candidate side of each pair is the importance-ordered structured
    text (kind + qualified name, file path, signature, docstring, then the
    stored chunk — see `_structured_candidate_text`), pre-truncated with
    query priority to `RERANK_MAX_LENGTH` so the query always reaches the
    cross-encoder verbatim and only the candidate's tail loses tokens.
    ``structured=False`` reproduces the legacy flat format
    (raw chunk as the pair text, SDK-side joint truncation) for A/B
    measurement of the pair format alone.

    Scores: ``rerank_score`` stays the RAW logit (bge-reranker outputs are
    unbounded — ordering only, never threshold it directly); each result
    additionally carries ``rerank_score_norm``, the sigmoid-mapped [0, 1]
    value, for any future thresholding and T017's distribution analysis.
    Nothing in semantic.py's confidence gate consumes either field (the
    gate reads pre-rerank fused RRF scores), so both are purely additive.

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
