"""RM3-style pseudo-relevance-feedback query expansion (FR-004 / D-001, D-003).

Why this module exists
----------------------
The first retrieval pass fuses a dense and a sparse leg (``rrf_fuse`` in
semantic.py). When the raw query is a natural-language sentence, both legs
see only sentence-shaped text; the terms that actually name the target
(``parseUnencodedURL`` lives in the corpus, not the query) reach neither
leg. Pseudo-relevance feedback (PRF) assumes the fused top-k already
contains the right symbols and mines their text for the vocabulary the
query is missing -- the RM3 route, with corpus-aware IDF weighting added
explicitly (research RQ3 x RQ1).

This module is the PURE half of FR-004: given the query and the feedback
documents' text, it deterministically selects expansion terms. It never
runs a search, never reads the DB, env, clock, or network, and calls no
LLM (TC-017). The wiring half (T016) takes this module's output and
re-runs the full pass (both legs + fusion) ONCE at the
``candidates = fused_candidates`` seam.

Algorithm (RM3-style, all knobs documented)
-------------------------------------------
1. Tokenize each feedback document; count in how many feedback documents
   each distinct token occurs (``fb_df``).
2. Score every candidate token by SUMMED corpus-aware IDF:
   ``weight(token) = fb_df(token) * idf(token)`` where
   ``idf = ln(n_symbols / symbol_df)`` (natural log, the standard
   formulation) from the injected DF signal. Terms absent from the
   lookup, and any mode without a lookup, get uniform ``idf = 1.0``
   (weight degrades to feedback-document frequency).
3. Drop tokens already in the query (case-folded token-set comparison).
4. Keep only tokens with ``weight >= (1 - fb_lambda) * max_weight`` --
   the RM3 drift cap: ``fb_lambda`` is the original query's weight in the
   RM3 mixture, so an expansion term must be worth at least the
   expansion budget's share of the strongest term to earn a place.
   ``max_weight`` is taken over the QUERY-DEDUPED candidates (a max over
   dropped query terms would shrink the cap with signal that never
   reaches the output).
5. Order by weight descending, ties broken by token ascending (byte
   deterministic, independent of feedback-document order), and keep the
   first ``fb_terms``.

Purity doctrine
---------------
Same contract as ``query_enrich.enrich``: a pure function of its
arguments -- no randomness, no time, no environment reads, no LLM, no
network (stdlib ``math`` only beyond the tokenizer). The DF signal is
INJECTED as a parameter; this module never touches the graph DB. Equal
inputs produce byte-identical outputs.

df_lookup contract (symmetric to T012's ``enrich(query, df_lookup=...)``)
------------------------------------------------------------------------
``df_lookup`` is a callable ``lowercase_token -> (symbol_df, n_symbols)
| None`` -- per-term indexed reads over the persisted ``term_df(token,
symbol_df, n_symbols)`` table (schema.py, T011). The key MUST already be
case-folded (unicode61): FTS5 vocabulary tokens are lowercase while
candidate text keeps casing, exactly the asymmetry documented for
``enrich``. Expected ``1 <= symbol_df <= n_symbols``; values outside that
contract, a ``None`` return (token not in the corpus vocabulary), and
``df_lookup=None`` itself all resolve to the uniform IDF above, so a
missing table degrades PRF to frequency-only selection instead of
raising. Lookup exceptions propagate (a DB fault is the caller's failure,
not degenerate feedback).

Consumer contract (T016 reads only this)
----------------------------------------
``expand(query, feedback_docs, *, df_lookup=None, fb_terms=10,
fb_lambda=0.5)`` takes:

``query``
    The FIRST-PASS dense query text (post-enrichment ``dense_query`` if
    enrichment is on). Only used for query-term exclusion and as the
    preserved prefix of the expanded dense text.
``feedback_docs``
    An ordered iterable of per-candidate TEXT strings: the top
    ``prf_docs`` entries of the fused candidate list, in rank order
    (order cannot change the output -- scoring is order-invariant -- but
    rank order is the contract). Recommended extraction per candidate:
    its ``chunk`` text, falling back to ``" ".join(name,
    qualified_name)`` for bm25-only candidates whose ``chunk`` is empty.
    Each element is one feedback document: duplicate texts count
    separately (the fused list is per-symbol, so the caller controls
    dedup). ``None``/empty entries contribute no tokens, never raise.
``fb_terms`` (default 10) and ``fb_lambda`` (default 0.5)
    The Anserini RM3 anchors (terms=10, lambda=0.5; docs=10 lives with
    the CALLER as the ``feedback_docs`` slice). The sweep grid (D-002)
    varies docs over {3, 10} with terms=10, lambda=0.5. ``fb_terms <= 0``
    yields an empty expansion; ``fb_lambda`` outside [0, 1] raises
    ``ValueError``.

Returns a frozen :class:`ExpansionResult`:

``terms``
    Ordered tuple of lowercase expansion tokens, query terms excluded.
``weights``
    Parallel tuple of the summed-IDF weights (selection evidence for the
    ablation rows; ordering matches ``terms``).
``dense_query``
    ``query + " " + " ".join(terms)`` for the second ``embed_query``
    call -- the original text always preserved as prefix (the
    never-loses-information contract). Equals ``query`` unchanged when
    no terms survive (PRF never manufactures signal out of nothing).

The sparse leg gains the same terms: T016 appends ``result.terms`` to
the first-pass sparse term list. Empty feedback yields
``terms == ()`` and ``dense_query == query`` -- never raises.
"""

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
    """Pure-function result of expanding one query from feedback docs.

    Attributes:
        terms: Ordered lowercase expansion tokens (weight descending,
            token ascending on ties), query tokens excluded.
        weights: Parallel summed-IDF weights, index-aligned with
            ``terms``.
        dense_query: The second-pass dense text: the original query with
            the expansion terms appended. Unchanged original when
            ``terms`` is empty.
    """

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

    Pure and hermetic (TC-017): no LLM, network, randomness, time, or
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
