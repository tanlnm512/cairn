"""RM3-style pseudo-relevance-feedback query expansion."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Optional

__all__ = ["ExpansionResult", "expand"]

# DF-signal row: (symbol_df, n_symbols) from the term_df table.
_DfRow = tuple[int, int]

# IDF applied when no corpus DF signal exists for a token: df_lookup is
# None, the lookup misses the token, or the row violates the
# 1 <= symbol_df <= n_symbols contract. Must stay positive so feedback
# frequency still ranks terms in the degraded mode.
_UNIFORM_IDF = 1.0


def _unicode61_tokens(text: str):
    """Yield the unicode61 tokenization of ``text``.

    Local mirror of ``schema._unicode61_tokens`` (kept here so this
    module stays import-pure of the DB layer and of files other agents
    own in this wave): the two MUST tokenize identically, because
    expansion tokens are looked up against the term_df vocabulary that
    schema's copy builds. Case-folds and splits on non-alphanumeric
    runs, matching the ``tokenize='unicode61'`` declaration on
    ``symbols_fts``.
    """
    cur: list[str] = []
    for ch in text.lower():
        if ch.isalnum():
            cur.append(ch)
        elif cur:
            yield "".join(cur)
            cur = []
    if cur:
        yield "".join(cur)


def _idf(df_row: Optional[_DfRow]) -> float:
    """Corpus-aware IDF for one ``(symbol_df, n_symbols)`` row.

    ``ln(n_symbols / symbol_df)`` -- zero for a token in every symbol,
    growing as the token gets rarer. Rows outside the
    ``1 <= symbol_df <= n_symbols`` contract resolve to the uniform IDF
    (degraded-but-usable, never raises).
    """
    if df_row is None:
        return _UNIFORM_IDF
    symbol_df, n_symbols = df_row
    if symbol_df < 1 or n_symbols < 1 or symbol_df > n_symbols:
        return _UNIFORM_IDF
    return math.log(n_symbols / symbol_df)


@dataclass(frozen=True)
class ExpansionResult:
    """Result of pseudo-relevance feedback query expansion."""

    terms: tuple[str, ...]
    weights: tuple[float, ...]
    dense_query: str


def expand(
    query: str,
    feedback_docs: Iterable[Optional[str]],
    *,
    df_lookup: Optional[Callable[[str], Optional[_DfRow]]] = None,
    fb_terms: int = 10,
    fb_lambda: float = 0.5,
) -> ExpansionResult:
    """Deterministically expand ``query`` with RM3-style feedback terms.

    Pure and hermetic: no LLM, network, randomness, time, or
    environment reads; the corpus DF signal arrives only via
    ``df_lookup``. See the module docstring for the full parameter,
    df_lookup, and consumer contracts and the algorithm's five steps.
    """
    if not 0.0 <= fb_lambda <= 1.0:
        raise ValueError(f"fb_lambda must be within [0, 1], got {fb_lambda!r}")

    def _empty() -> ExpansionResult:
        return ExpansionResult(terms=(), weights=(), dense_query=query)

    if fb_terms <= 0:
        return _empty()

    query_tokens = frozenset(_unicode61_tokens(query))

    # Steps 1-2: per-token feedback-document frequency. A token counts
    # once per feedback document that contains it (distinct within the
    # doc), so duplicate tokens inside one doc do not inflate weight.
    fb_df: dict[str, int] = {}
    for doc in feedback_docs:
        if not doc:
            continue  # bm25-only candidates carry empty chunk text
        for token in set(_unicode61_tokens(doc)):
            fb_df[token] = fb_df.get(token, 0) + 1

    # Step 3: candidates are tokens the query does not already carry.
    candidates = {t: n for t, n in fb_df.items() if t not in query_tokens}
    if not candidates:
        return _empty()

    weights = {
        token: n * _idf(df_lookup(token) if df_lookup is not None else None)
        for token, n in candidates.items()
    }
    max_weight = max(weights.values())
    if max_weight <= 0.0:
        # Every candidate sits in every symbol (idf 0 across the board):
        # no corpus-aware signal left to expand with.
        return _empty()

    # Steps 4-5: RM3 drift cap, then deterministic order and cut.
    cap = (1.0 - fb_lambda) * max_weight
    kept = sorted(
        (token for token, w in weights.items() if w >= cap),
        key=lambda token: (-weights[token], token),
    )[:fb_terms]
    return ExpansionResult(
        terms=tuple(kept),
        weights=tuple(weights[token] for token in kept),
        dense_query=f"{query} {' '.join(kept)}" if kept else query,
    )
