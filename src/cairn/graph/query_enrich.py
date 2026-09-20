"""Deterministic query enrichment for the retrieval legs."""
from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["EnrichedQuery", "ENRICH_DF_MAX_FRACTION", "enrich"]

# Hard document-frequency cutoff: a query term whose
# symbol_df/n_symbols prevalence EXCEEDS this fraction is dropped from the
# appended identifier tail and the sparse term list. Scikit-learn's max_df
# convention: strictly greater than 0.90 drops, exactly 0.90 keeps. 0.90 is
# the shipped default that code and docs document.
ENRICH_DF_MAX_FRACTION = 0.90

# --- Extraction regexes (compiled once; pure functions of the input string).

# Backticked spans: the user's explicit code references. Non-greedy so two
# spans in one query each match their own content.
_BACKTICK_RE = re.compile(r"`([^`]+)`")

# Candidate identifier tokens in prose: a word of letters/digits/underscores
# (leading letter or underscore), optionally dot-qualified any number of
# times (``yarl.URL.build``). Digits-only runs are not candidates.
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")

# camelCase boundary splits, applied to a purely alphanumeric word:
#   1. ALLCAPS run followed by Cap+lower:  HTTPServer -> HTTP Server
#   2. lower/digit followed by upper:      parseURL -> parse URL
_CAMEL_ACRONYM_BOUNDARY = re.compile(r"([A-Z]+)([A-Z][a-z])")
_CAMEL_LOWER_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])")

# Separator split inside one candidate token: underscores, dots, and any
# other non-alphanumeric character.
_SEPARATOR_RE = re.compile(r"[^A-Za-z0-9]+")

# Stopword set -- see the module docstring's "Stopwords" section for why it
# is this small and why nothing else is trimmed. frozenset for O(1) lookups;
# iteration order can never leak because it is only probed, never iterated.
_STOPWORDS = frozenset(
    {
        # Grammar / question scaffolding.
        "a", "an", "the", "and", "or", "but", "if", "then", "than", "so",
        "of", "for", "to", "in", "on", "at", "by", "with", "from", "into",
        "onto", "over", "under", "is", "are", "be", "been", "was", "were",
        "am", "do", "does", "did", "doing", "have", "has", "had", "having",
        "i", "we", "you", "he", "she", "it", "they", "me", "us", "him",
        "her", "my", "our", "your", "his", "its", "their", "this", "that",
        "these", "those", "there", "here", "when", "where", "why", "how",
        "what", "which", "who", "whom", "whose", "will", "would", "can",
        "could", "shall", "should", "may", "might", "must", "not", "no",
        "vs", "versus",
        # Code-question nouns: they name the KIND of construct asked about
        # and match nothing useful in symbol names.
        "function", "functions", "class", "classes", "method", "methods",
        "handler", "handlers", "module", "modules", "variable", "variables",
        "constant", "constants", "file", "files",
    }
)


@dataclass(frozen=True)
class EnrichedQuery:
    """Enriched query representation containing dense and sparse variants alongside extracted identifiers."""

    dense_query: str
    sparse_query: str
    identifiers: tuple[str, ...]


def _camel_split(word: str) -> list[str]:
    """Split one alphanumeric word on camelCase boundaries.

    ALLCAPS runs stay whole (``URL``); ``parseUnencodedURL`` ->
    ``["parse", "Unencoded", "URL"]``; ``HTTPServer`` -> ``["HTTP",
    "Server"]``. Words with no boundaries come back as a single element.
    """
    if not word:
        return []
    spaced = _CAMEL_ACRONYM_BOUNDARY.sub(r"\1 \2", word)
    spaced = _CAMEL_LOWER_BOUNDARY.sub(r"\1 \2", spaced)
    return spaced.split(" ")


def _sub_tokens(candidate: str) -> list[str]:
    """Split one candidate token into its identifier sub-tokens.

    Separators (``_``, ``.``, anything non-alphanumeric) split first, then
    camelCase boundaries within each part: ``yarl.URL.build`` ->
    ``["yarl", "URL", "build"]``; ``split_url`` -> ``["split", "url"]``.
    """
    parts = [p for p in _SEPARATOR_RE.split(candidate) if p]
    out: list[str] = []
    for part in parts:
        out.extend(_camel_split(part))
    return out


def _is_identifier_shaped(candidate: str) -> bool:
    """True iff a prose candidate token looks like code, not English.

    Backticked chunks never come through here (backticks are identifiers
    by fiat -- the user marked them as code). A candidate is code-ish if
    it has a separator (``split_url``, ``yarl.URL.build``), a camelCase
    compound shape, an ALLCAPS run of >= 2 letters (``URL``), or
    letter+digit adjacency (``utf8``, ``v4``). A plain word -- even
    sentence-capitalized like ``Where`` -- is prose, not an identifier.
    """
    if "_" in candidate or "." in candidate:
        return True
    subs = _sub_tokens(candidate)
    if len(subs) > 1:
        return True  # camelCase compound (parseUnencodedURL et al.)
    tok = subs[0] if subs else ""
    if not tok:
        return False
    if tok.isalpha() and tok.isupper() and len(tok) >= 2:
        return True  # ALLCAPS acronym kept whole (URL, HTTP)
    has_digit = any(c.isdigit() for c in tok)
    has_alpha = any(c.isalpha() for c in tok)
    return has_digit and has_alpha


def _ubiquity_predicate(df_lookup):
    """Build a memoized case-folded ubiquity test from an injected lookup.

    Returns a predicate ``is_ubiquitous(token) -> bool`` that is True iff
    the corpus marks ``token`` as ubiquitous (prevalence strictly greater
    than :data:`ENRICH_DF_MAX_FRACTION`). The lookup is called with the
    CASE-FOLDED token and is invoked at most once per distinct case-folded
    token (memoized), so a query costs O(#distinct tokens) lookups.
    With ``df_lookup`` None the predicate is constantly False
    (no lookup is ever made).
    """

    cache: dict[str, bool] = {}

    def is_ubiquitous(token: str) -> bool:
        key = token.lower()
        if key not in cache:
            info = df_lookup(key) if df_lookup is not None else None
            if info is None:
                cache[key] = False  # unknown term: no data, no penalty
            else:
                symbol_df, n_symbols = info
                cache[key] = n_symbols > 0 and (
                    symbol_df / n_symbols > ENRICH_DF_MAX_FRACTION
                )
        return cache[key]

    return is_ubiquitous


def enrich(query: str, df_lookup=None) -> EnrichedQuery:
    """Deterministically enrich one query for the dense and sparse legs.

    Pure function of ``query`` (and, when given, of the injected
    ``df_lookup``): no randomness, time, environment, LLM, or network --
    the DF signal is INJECTED, never fetched.
    See the module docstring for the full consumer contract and
    extraction rules.

    ``df_lookup`` -- the injected per-corpus document-frequency lookup
    (the caller at the ``semantic_search`` boundary builds it from
    the persisted ``term_df`` table). Contract:

    * a CALLABLE taking one ``str`` and returning ``None`` or a 2-tuple
      ``(symbol_df, n_symbols)`` of non-negative ints, where ``symbol_df``
      is the number of distinct symbols whose indexed text contains the
      token and ``n_symbols`` the total symbol count;
    * it receives the CASE-FOLDED token (``token.lower()``) -- ``term_df``
      keys are FTS5 unicode61 case-folded while enrich tokens keep casing,
      so the passed key matches the table directly;
    * a term whose ``symbol_df / n_symbols`` is STRICTLY greater than
      ``ENRICH_DF_MAX_FRACTION`` (0.90; exactly 0.90 keeps) is dropped
      from ``dense_query``'s appended identifier tail and from
      ``sparse_query`` -- never from the original text prefix and never
      from the ``identifiers`` extraction record;
    * ``None`` return, absent key, or ``n_symbols <= 0`` means "no DF
      data": the term keeps full weight;
    * it is called at most once per distinct case-folded token per
      ``enrich`` call (memoized; O(#distinct query tokens) bound);
    * ``df_lookup=None`` (the default) disables filtering entirely:
      byte-identical to the single-argument behavior.

    Boundary: a query with no extractable identifiers returns
    ``identifiers == ()`` and ``dense_query == query`` (the original,
    unmodified) -- enrichment never manufactures matches out of nothing.
    The same holds when every extracted identifier is DF-dropped: the
    dense query falls back to the original with NO appended tail (an
    empty tail would only add a trailing space).
    """
    identifiers: list[str] = []
    seen_ids: set[str] = set()

    def _add_identifier(token: str) -> None:
        key = token.lower()
        if key and key not in seen_ids:
            seen_ids.add(key)
            identifiers.append(token)

    # 1. Backticked spans first: explicit code references. Their content
    #    contributes the VERBATIM chunk plus its split sub-tokens. A span
    #    with internal whitespace (an expression like `x == y`) is split on
    #    whitespace and each letter-bearing chunk is treated as if it had
    #    been backticked on its own.
    working = _BACKTICK_RE.sub(" ", query)
    for span in _BACKTICK_RE.findall(query):
        for chunk in span.split():
            if not any(c.isalpha() for c in chunk):
                continue  # `==`, `->`, numbers-only: no identifier content
            _add_identifier(chunk)
            for sub in _sub_tokens(chunk):
                _add_identifier(sub)

    # 2. Prose candidates: identifier-shaped ones contribute their split
    #    sub-tokens (no verbatim form -- only backticks earn that).
    candidates = _TOKEN_RE.findall(working)
    for candidate in candidates:
        if _is_identifier_shaped(candidate):
            for sub in _sub_tokens(candidate):
                _add_identifier(sub)

    # 3. Sparse terms: the query's non-stopword tokens in query order
    #    (compounds included verbatim -- FTS5 unicode61 keeps camelCase as
    #    one token, so the compound itself can still exact-match a name),
    #    then the identifier tokens not already present, stopword-trimmed.
    #    DF filtering: corpus-ubiquitous terms (prevalence
    #    strictly > ENRICH_DF_MAX_FRACTION) are dropped from BOTH source
    #    loops -- the dilution fix must not merely move a term from one
    #    loop to the other.
    is_ubiquitous = _ubiquity_predicate(df_lookup)
    terms: list[str] = []
    seen_terms: set[str] = set()
    for token in candidates:
        key = token.lower()
        if key in _STOPWORDS or key in seen_terms:
            continue
        if is_ubiquitous(token):
            continue
        seen_terms.add(key)
        terms.append(token)
    for ident in identifiers:
        key = ident.lower()
        if key in _STOPWORDS or key in seen_terms:
            continue
        if is_ubiquitous(ident):
            continue
        seen_terms.add(key)
        terms.append(ident)

    # 4. Dense query: original plus each identifier once. The embedder sees
    #    name-shaped tokens in isolation; nothing from the original text is
    #    ever dropped. DF-dropped identifiers are simply not appended (the
    #    original-text prefix still contains the term if the user typed it
    #    -- the prefix contract); an all-dropped tail means NO tail at all.
    tail = [ident for ident in identifiers if not is_ubiquitous(ident)]
    dense_query = query if not tail else f"{query} {' '.join(tail)}"

    return EnrichedQuery(
        dense_query=dense_query,
        sparse_query=" ".join(terms),
        identifiers=tuple(identifiers),
    )
