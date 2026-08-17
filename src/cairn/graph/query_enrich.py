"""Deterministic query enrichment for the retrieval legs (FR-001 / D-001).

Why this module exists
----------------------
Today the raw query string reaches BOTH retrieval legs unchanged:

* the dense leg embeds it verbatim (``embeddings.embed_query`` at
  semantic.py:405), so an identifier named inside a sentence
  (``parseUnencodedURL``) is never shown to the embedder as a standalone
  name-shaped token it could match against name-bearing chunks;
* the sparse leg hands the whole sentence to ``search_symbols``
  (semantic.py:477), whose ``_pattern_to_fts`` folds ANY multi-token input
  into ONE quoted FTS5 phrase -- the sentence
  ``where is the function that parses an unencoded URL string`` becomes
  ``"where is the function that parses an unencoded URL string"*``, an
  exact phrase-prefix that matches no symbol name, so BM25 returns ``[]``
  for every sentence-shaped query (the empty-BM25 defect).

``enrich`` is the pure fix-point for both: it extracts identifier-like
tokens (backticked spans, camelCase, snake_case, dotted references,
ALLCAPS acronyms) and stopword-trims a term set, returning one
:class:`EnrichedQuery` the wiring tasks (T008/T009) feed to each leg.

Doctrine (TC-003/TC-004): enrichment is deterministic (no randomness, no
time, no environment reads), hermetic (no LLM, no network, stdlib ``re``
only), and never loses information: ``dense_query`` ALWAYS contains the
full original text as its prefix.

Consumer contract
-----------------
``dense_query``
    The original query with each extracted identifier appended once. The
    embedder then sees name-shaped tokens in isolation, which is how they
    appear inside the composed chunks it must match (qualified names,
    signatures). Format is ``<original> <id1> <id2> ...`` -- space-joined
    so the result stays one natural text blob for the single
    ``embed_query`` call (latency doctrine: enrichment must never add a
    second embedding call). When no identifiers are found the original is
    returned UNCHANGED (boundary, TC-005: enrichment must not manufacture
    signal out of nothing).

``sparse_query``
    A whitespace-separated term string for the BM25 leg: the query's
    non-stopword tokens in query order, then the extracted identifier
    tokens not already present. IMPORTANT plumbing note for T008 (verified
    empirically against ``_pattern_to_fts`` as of this writing): passing a
    MULTI-TOKEN string through today's ``search_symbols`` still yields one
    quoted phrase -- ``_pattern_to_fts("parse url")`` -> ``'"parse url"*'``
    -- so joining with spaces alone does NOT fix the defect. The terms are
    therefore exposed as clean whitespace-separable tokens (and as the
    ``identifiers`` tuple) so the T008 term-mode path can build an
    OR-style per-token MATCH expression. This module keeps NO FTS
    knowledge; T008 decides the plumbing. An empty ``sparse_query`` means
    "every token was a stopword" -- the sparse leg should fall back to the
    raw query, not search for the empty string.

``identifiers``
    Ordered tuple of extracted identifier tokens: backticked spans first
    (verbatim span, then its split sub-tokens), then remaining
    identifier-shaped candidates left-to-right (split sub-tokens only).
    Deduped case-insensitively, first occurrence's casing preserved
    (``URL`` stays ``URL``; a later ``url`` is dropped). Kept UNFILTERED by
    stopwords -- this tuple is the structural extraction record (T009
    keeps the raw query for ``_exact_name_hit``; this tuple is what the
    dense append drew from); only ``sparse_query`` is stopword-trimmed.

Identifier extraction rules
---------------------------
* Backticked spans (`` `parse_url` ``) are extracted FIRST and are the
  only source of VERBATIM tokens (the user explicitly marked them as
  code); their snake_case/camelCase/dotted sub-tokens are also added.
* camelCase boundaries split: ``parseUnencodedURL`` -> parse, Unencoded,
  URL.
* snake_case / dotted / other non-alphanumeric separators split:
  ``split_url`` -> split, url; ``yarl.URL.build`` -> yarl, URL, build.
* ALLCAPS runs (>= 2 letters) are kept whole: URL, HTTP.
* Letter+digit adjacency counts as code-ish: utf8, v4, base64.
* A plain lowercase (or sentence-capitalized) word is NOT an identifier
  (``unencoded`` alone is prose); it still reaches ``sparse_query`` as a
  term unless it is a stopword.

Stopwords
---------
Intentionally TINY and hand-curated: pure grammar words plus the
code-question nouns ("function", "class", "method", ...) that name the
KIND of construct being asked about and match nothing useful in symbol
names. Over-trimming is the real risk (a dropped token can no longer
match a real symbol/docstring word), so anything not in this list stays a
term. No stemming: ``parses`` stays ``parses`` (deterministic, hermetic;
prefix handling belongs to the T008 term-mode FTS expression, not here).

IDF-aware filtering (FR-003 / D-004 / D-005)
---------------------------------------------
An optional ``df_lookup`` argument lets the caller INJECT the corpus-side
document-frequency signal, keeping ``enrich`` pure (the signal arrives as
an argument; nothing is fetched from env/graph/DB inside -- see the
purity section). Terms whose ``symbol_df / n_symbols`` prevalence is
STRICTLY greater than :data:`ENRICH_DF_MAX_FRACTION` (0.90, the
scikit-learn ``max_df`` convention: a term appearing in more than 90% of
the corpus's symbols carries no discriminative signal) are dropped from
the appended identifier tail and from the sparse term list. Three things
are deliberately NOT touched:

* the ``dense_query`` prefix -- the original query text is preserved
  verbatim (the never-lose-information contract; D-004's "original query
  text never modified" consequence);
* the ``identifiers`` tuple -- it stays the UNFILTERED extraction record
  (extraction is corpus-independent; only what the two legs consume is
  filtered);
* unknown terms -- a lookup miss (``None``) or zero ``n_symbols`` means
  "no DF data", and no data never penalizes a term.

Lookup keys are CASE-FOLDED (``token.lower()``) before the call because
the persisted ``term_df`` keys come from FTS5's unicode61 tokenizer,
which case-folds, while enrich's extracted tokens keep their casing
(``URL`` stays ``URL``; the lookup receives ``url``). Exactly 0.90 keeps
the term (the cut is strictly-greater, the documented TC-011 boundary).
With ``df_lookup=None`` (the default) the filtering is inert and the
output is byte-identical to the pre-FR-003 behavior (TC-015 regression
guard).

Purity / idempotence
--------------------
``enrich`` depends only on its input string (and, when given, on the
injected ``df_lookup`` callable, which the caller supplies as a pure
read-only DB/index view -- D-005). Re-enriching an already enriched
``dense_query`` adds no NEW identifier sub-tokens (the appended tail is
already split, and backticks survive verbatim in the original text), but
it is NOT idempotent: the appended identifier tail is appended again, so
``dense_query`` grows. This is benign by construction -- enrichment is
applied exactly once at the ``semantic_search`` boundary (T009), never
nested.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["EnrichedQuery", "ENRICH_DF_MAX_FRACTION", "enrich"]

# Hard document-frequency cutoff for FR-003 (D-004): a query term whose
# symbol_df/n_symbols prevalence EXCEEDS this fraction is dropped from the
# appended identifier tail and the sparse term list. Scikit-learn's max_df
# convention: strictly greater than 0.90 drops, exactly 0.90 keeps. The value
# is the shipped default (TC-011 pins it); T014's ablation sweeps 0.75-0.95
# around it but 0.90 is what code and docs document.
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
    """Result of enriching one natural-language query.

    Attributes:
        dense_query: Text for the embedding leg -- the original query with
            each extracted identifier appended once (original text always
            preserved as the prefix; never loses information).
        sparse_query: Whitespace-separated BM25 term string
            (stopword-trimmed; empty means "all stopwords -- fall back to
            the raw query"). With ``df_lookup`` given, also DF-filtered
            (corpus-ubiquitous terms dropped).
        identifiers: Ordered, case-insensitively deduped identifier tokens
            extracted from the query (first occurrence's casing kept).
            Stays the UNFILTERED extraction record even under DF filtering
            -- only ``dense_query``'s appended tail and ``sparse_query``
            consume the filter.
    """

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
    token (memoized), so a query costs O(#distinct tokens) lookups -- the
    D-005 bound. With ``df_lookup`` None the predicate is constantly False
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
    ``df_lookup``): no randomness, time, environment, LLM, or network
    (TC-003/TC-004 doctrine) -- the DF signal is INJECTED, never fetched
    (D-005). See the module docstring for the full consumer contract and
    extraction rules.

    ``df_lookup`` -- the injected per-corpus document-frequency lookup
    (FR-003; the caller at the ``semantic_search`` boundary builds it from
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
      ``enrich`` call (memoized; D-005's O(#distinct query tokens) bound);
    * ``df_lookup=None`` (the default) disables filtering entirely:
      byte-identical to the pre-FR-003 single-argument behavior
      (TC-015).

    Boundary (TC-005): a query with no extractable identifiers returns
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
    #    DF filtering (FR-003/D-004): corpus-ubiquitous terms (prevalence
    #    strictly > ENRICH_DF_MAX_FRACTION) are dropped from BOTH source
    #    loops -- the dilution fix must not merely move a term from one
    #    loop to the other (TC-010).
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
