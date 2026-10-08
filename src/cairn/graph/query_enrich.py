"""Deterministic query enrichment for the retrieval legs."""
from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["EnrichedQuery", "ENRICH_DF_MAX_FRACTION", "enrich"]

# Prevalence strictly above the cutoff drops; exactly-at keeps.
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

# Stopword set: grammar/question scaffolding only -- content terms must
# survive into the matching legs. frozenset for O(1) lookups;
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
    """Split one alphanumeric word on camelCase boundaries."""
    if not word:
        return []
    spaced = _CAMEL_ACRONYM_BOUNDARY.sub(r"\1 \2", word)
    spaced = _CAMEL_LOWER_BOUNDARY.sub(r"\1 \2", spaced)
    return spaced.split(" ")


def _sub_tokens(candidate: str) -> list[str]:
    """Split one candidate token into its identifier sub-tokens."""
    parts = [p for p in _SEPARATOR_RE.split(candidate) if p]
    out: list[str] = []
    for part in parts:
        out.extend(_camel_split(part))
    return out


def _is_identifier_shaped(candidate: str) -> bool:
    """True iff a prose candidate token looks like code, not English."""
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
    """Build a memoized case-folded ubiquity test from an injected lookup."""

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
    """Deterministically enrich one query for the dense and sparse legs."""
    identifiers: list[str] = []
    seen_ids: set[str] = set()

    def _add_identifier(token: str) -> None:
        key = token.lower()
        if key and key not in seen_ids:
            seen_ids.add(key)
            identifiers.append(token)

    # Backticked spans contribute verbatim chunks and split sub-tokens.
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

    # Ubiquitous terms leave both sparse source loops.
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

    # The dense query keeps the original text and appends each survivor once.
    tail = [ident for ident in identifiers if not is_ubiquitous(ident)]
    dense_query = query if not tail else f"{query} {' '.join(tail)}"

    return EnrichedQuery(
        dense_query=dense_query,
        sparse_query=" ".join(terms),
        identifiers=tuple(identifiers),
    )
