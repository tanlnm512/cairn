"""T007: deterministic query enrichment (FR-001 / D-001, TC-003/004/005).

Pure-function unit tests for ``cairn.graph.query_enrich.enrich``: the
extraction rules (backticks, camelCase, snake_case, dotted, ALLCAPS,
letter-digit), the no-identifier boundary, determinism, purity, the
hermetic-import guard, and the documented re-enrichment behavior.
"""
from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path

import pytest

from cairn.graph.query_enrich import EnrichedQuery, enrich

# The FR-001 defect sentence: today search_symbols folds this into the quoted
# FTS5 phrase '"where is the function that parses an unencoded URL string"*'
# (empty BM25); enrichment must decompose it into terms + identifiers.
SPEC_EXAMPLE = "where is the function that parses an unencoded URL string"


class TestExtractionRules:
    def test_spec_example_sentence(self) -> None:
        r = enrich(SPEC_EXAMPLE)
        # Only URL is identifier-shaped in this prose sentence (ALLCAPS,
        # >=2 letters); parses/unencoded/string are plain words -> terms only.
        assert r.identifiers == ("URL",)
        assert r.dense_query == SPEC_EXAMPLE + " URL"
        assert r.sparse_query == "parses unencoded URL string"

    def test_camel_case_splits(self) -> None:
        r = enrich("how does parseUnencodedURL handle quirks")
        assert r.identifiers == ("parse", "Unencoded", "URL")
        # Compound stays a term (FTS5 unicode61 keeps camelCase as one token,
        # so it can still exact-match the name), sub-tokens appended after.
        assert r.sparse_query == "parseUnencodedURL handle quirks parse Unencoded URL"
        assert r.dense_query == (
            "how does parseUnencodedURL handle quirks parse Unencoded URL"
        )

    def test_snake_case_splits(self) -> None:
        r = enrich("where is split_url called")
        assert r.identifiers == ("split", "url")
        assert r.sparse_query == "split_url called split url"
        assert r.dense_query == "where is split_url called split url"

    def test_backticked_span_is_verbatim_plus_subs(self) -> None:
        r = enrich("where is `parse_url` used")
        # Backticks are the only source of verbatim tokens; their sub-tokens
        # follow. Backtick identifiers come before any prose identifiers.
        assert r.identifiers == ("parse_url", "parse", "url")
        assert r.sparse_query == "used parse_url parse url"
        assert r.dense_query == "where is `parse_url` used parse_url parse url"

    def test_dotted_reference_splits(self) -> None:
        r = enrich("what does yarl.URL.build return")
        assert r.identifiers == ("yarl", "URL", "build")
        assert r.sparse_query == "yarl.URL.build return yarl URL build"
        assert r.dense_query == "what does yarl.URL.build return yarl URL build"

    def test_allcaps_acronym_kept_whole(self) -> None:
        r = enrich("how is HTTP retry handled")
        assert r.identifiers == ("HTTP",)
        assert r.sparse_query == "HTTP retry handled"
        assert r.dense_query == "how is HTTP retry handled HTTP"

    def test_acronym_inside_camel_case(self) -> None:
        r = enrich("HTTPServer config")
        # ALLCAPS run stays whole, then the Cap+lower part splits off.
        assert r.identifiers == ("HTTP", "Server")
        assert r.sparse_query == "HTTPServer config HTTP Server"

    def test_letter_digit_adjacency_is_code(self) -> None:
        r = enrich("how do I decode utf8 bytes")
        assert r.identifiers == ("utf8",)
        assert r.sparse_query == "decode utf8 bytes"

    def test_mixed_shapes(self) -> None:
        q = "Where does `ApiFactory` call build_client for the HTTPClient"
        r = enrich(q)
        # Backtick first (verbatim + subs), then prose candidates in order;
        # build_client is processed before HTTPClient so its lowercase
        # "client" wins the case-insensitive dedupe.
        assert r.identifiers == (
            "ApiFactory",
            "Api",
            "Factory",
            "build",
            "client",
            "HTTP",
        )
        assert r.sparse_query == (
            "call build_client HTTPClient ApiFactory Api Factory build client HTTP"
        )
        assert r.dense_query == q + " ApiFactory Api Factory build client HTTP"

    def test_plain_words_are_not_identifiers(self) -> None:
        # Sentence-capitalized "Where" and plain words are prose: terms, yes;
        # identifiers, no.
        r = enrich("Where are the parse helpers tested")
        assert r.identifiers == ()
        assert r.sparse_query == "parse helpers tested"


class TestDedupeAndOrder:
    def test_case_insensitive_dedupe_keeps_first_casing(self) -> None:
        r = enrich("URL vs url_builder")
        # URL (ALLCAPS) extracted first; url_builder's "url" sub-token is a
        # case-insensitive dup and is dropped, "builder" is new.
        assert r.identifiers == ("URL", "builder")
        assert r.sparse_query == "URL url_builder builder"

    def test_repeated_token_appears_once_in_terms(self) -> None:
        r = enrich("retry retry backoff")
        assert r.sparse_query == "retry backoff"


class TestBoundaries:
    def test_no_identifier_query_keeps_original_dense(self) -> None:
        # TC-005 boundary: nothing identifier-shaped -> identifiers == (),
        # dense_query is the ORIGINAL unchanged (never loses information,
        # never manufactures emphasis), sparse is the stopword-trimmed terms.
        q = "where do we handle retries"
        r = enrich(q)
        assert r.identifiers == ()
        assert r.dense_query == q
        assert r.sparse_query == "handle retries"

    def test_nonsense_query_extracts_nothing_manufactured(self) -> None:
        # TC-005's giraffe probe: enrichment must not invent matches.
        q = "the function that teleports a giraffe to mars"
        r = enrich(q)
        assert r.identifiers == ()
        assert r.dense_query == q
        assert r.sparse_query == "teleports giraffe mars"

    def test_empty_string(self) -> None:
        r = enrich("")
        assert r == EnrichedQuery(dense_query="", sparse_query="", identifiers=())

    def test_whitespace_only(self) -> None:
        q = "   \t\n "
        r = enrich(q)
        assert r.identifiers == ()
        # Original preserved verbatim (no trimming -- no information loss).
        assert r.dense_query == q
        assert r.sparse_query == ""

    def test_all_stopwords_yields_empty_sparse(self) -> None:
        # Documented signal for the sparse leg: empty sparse_query means
        # "fall back to the raw query", not "search for nothing".
        q = "where is the function"
        r = enrich(q)
        assert r.identifiers == ()
        assert r.dense_query == q
        assert r.sparse_query == ""


class TestDeterminismPurityHermeticity:
    def test_two_calls_are_equal(self) -> None:
        # TC-003: same input, separate invocations -> identical results.
        for q in (SPEC_EXAMPLE, "how does `parse_url` differ from split_url", ""):
            assert enrich(q) == enrich(q)

    def test_input_string_not_mutated(self) -> None:
        q = SPEC_EXAMPLE
        before = str(q)
        enrich(q)
        assert q == before

    def test_result_is_frozen(self) -> None:
        r = enrich(SPEC_EXAMPLE)
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.dense_query = "tampered"  # type: ignore[misc]

    def test_module_imports_are_hermetic(self) -> None:
        # TC-004 doctrine guard: the enrichment path may not touch
        # randomness, time, the environment, or anything network-capable.
        # Assert via AST that only stdlib re/dataclasses are imported.
        src = Path(inspect.getsourcefile(enrich)).read_text()
        imported: set[str] = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        assert imported <= {"re", "dataclasses", "__future__"}


class TestReEnrichment:
    def test_re_enriching_dense_query_is_stable_except_growth(self) -> None:
        # Documented behavior: enrich is NOT idempotent (the identifier tail
        # is appended again), but the extracted identifiers and sparse terms
        # stabilize -- the appended tail is already split, and backticks
        # survive verbatim inside the preserved original text.
        q = "where is `parse_url` used"
        first = enrich(q)
        second = enrich(first.dense_query)
        assert second.identifiers == first.identifiers
        assert second.sparse_query == first.sparse_query
        assert second.dense_query.startswith(first.dense_query)
        # And it keeps growing, not oscillating: a third pass is stable too.
        third = enrich(second.dense_query)
        assert third.identifiers == first.identifiers
        assert third.sparse_query == first.sparse_query

    def test_dense_query_always_contains_original(self) -> None:
        # The never-lose-information invariant, across the shape battery.
        for q in (
            SPEC_EXAMPLE,
            "where do we handle retries",
            "how does parseUnencodedURL handle quirks",
            "",
            "  ",
        ):
            assert enrich(q).dense_query.startswith(q)
