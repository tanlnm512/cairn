"""Shared query-tokenization helpers.

Provides a base stop-word list plus a split-on-non-alphanumeric /
filter-stop-words / dedup loop. Callers that need extra behavior (CamelCase
symbol extraction, stemming, extra domain stop words) layer that on top of
`simple_tokenize` rather than re-copying it.
"""
from __future__ import annotations

import re
from typing import Iterable, List, Optional, Set

# Common English words filtered from query tokens before matching. Shared
# baseline; callers may extend with domain-specific stop words (see
# src/compass/router.py's ROUTER_EXTRA_STOP_WORDS).
BASE_STOP_WORDS = frozenset({
    "the", "how", "does", "what", "is", "where", "when", "why", "this", "that",
    "are", "can", "work", "for", "with", "from", "into", "about", "which",
    "their", "there", "will", "would", "should", "could", "have", "been",
    "some", "just", "only", "also", "did", "not", "but", "and", "or",
    "all", "any", "use", "used", "using", "our", "my", "we", "do", "if",
    "has", "had", "was", "were", "get", "got", "make", "made", "way",
})


def simple_tokenize(
    text: str, stop_words: Optional[Iterable[str]] = None, min_len: int = 3
) -> List[str]:
    """Split on non-alphanumeric boundaries, lowercase, filter stop words.

    Returns a deduplicated, order-preserving list of tokens >= min_len chars.
    """
    stop: Set[str] = set(stop_words) if stop_words is not None else set(BASE_STOP_WORDS)
    tokens: List[str] = []
    seen = set()
    for tok in re.split(r"[^A-Za-z0-9]+", text.lower()):
        tok = tok.strip()
        if len(tok) >= min_len and tok not in stop and tok not in seen:
            tokens.append(tok)
            seen.add(tok)
    return tokens
