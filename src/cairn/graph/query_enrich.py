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

Purity / idempotence
--------------------
``enrich`` depends only on its input string. Re-enriching an already
enriched ``dense_query`` adds no NEW identifier sub-tokens (the appended
tail is already split, and backticks survive verbatim in the original
text), but it is NOT idempotent: the appended identifier tail is appended
again, so ``dense_query`` grows. This is benign by construction --
enrichment is applied exactly once at the ``semantic_search`` boundary
(T009), never nested.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["EnrichedQuery", "enrich"]

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
            the raw query").
        identifiers: Ordered, case-insensitively deduped identifier tokens
            extracted from the query (first occurrence's casing kept).
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


def enrich(query: str) -> EnrichedQuery:
    """Deterministically enrich one query for the dense and sparse legs.

    Pure function of ``query``: no randomness, time, environment, LLM, or
    network (TC-003/TC-004 doctrine). See the module docstring for the
    full consumer contract and extraction rules.

    Boundary (TC-005): a query with no extractable identifiers returns
    ``identifiers == ()`` and ``dense_query == query`` (the original,
    unmodified) -- enrichment never manufactures matches out of nothing.
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
    terms: list[str] = []
    seen_terms: set[str] = set()
    for token in candidates:
        key = token.lower()
        if key in _STOPWORDS or key in seen_terms:
            continue
        seen_terms.add(key)
        terms.append(token)
    for ident in identifiers:
        key = ident.lower()
        if key in _STOPWORDS or key in seen_terms:
            continue
        seen_terms.add(key)
        terms.append(ident)

    # 4. Dense query: original plus each identifier once. The embedder sees
    #    name-shaped tokens in isolation; nothing from the original text is
    #    ever dropped.
    dense_query = query if not identifiers else f"{query} {' '.join(identifiers)}"

    return EnrichedQuery(
        dense_query=dense_query,
        sparse_query=" ".join(terms),
        identifiers=tuple(identifiers),
    )
