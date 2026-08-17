"""T007: deterministic query enrichment (FR-001 / D-001, TC-003/004/005).

T012 adds the FR-003 / D-004 / D-005 coverage: the injected ``df_lookup``
(0.90 hard cutoff), the L1-D03 'URL' repro fixed deterministically
(TC-010), the threshold boundary on both sides (TC-011), rare-term
survival (TC-012), and the None-lookup purity equivalence regression
guard (TC-015).

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

from cairn.graph.query_enrich import ENRICH_DF_MAX_FRACTION, EnrichedQuery, enrich

# The FR-001 defect sentence: today search_symbols folds this into the quoted
# FTS5 phrase '"where is the function that parses an unencoded URL string"*'
# (empty BM25); enrichment must decompose it into terms + identifiers.
SPEC_EXAMPLE = "where is the function that parses an unencoded URL string"

# The L1-D03 regression text (survey FR-003 verify command, verbatim): the
# one-command repro whose enrichment output today pins ('URL',).
L1_D03 = (
    "Where is the function that parses an already-encoded URL string "
    "without re-quoting?"
)


def _lookup(table: dict[str, tuple[int, int]]):
    """Build a df_lookup callable from a case-folded-key -> (df, n) table."""

    def df_lookup(key: str):
        return table.get(key)

    return df_lookup


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


class TestDfLookupL1D03:
    """TC-010: the L1-D03 'URL' repro, fixed deterministically."""

    def test_ubiquitous_url_dropped_from_both_legs(self) -> None:
        # 'url' marked ubiquitous at 91/100 (> 0.90): dropped from the
        # sparse term list AND from the appended identifier tail (the only
        # identifier is URL, so the dense query falls back to the original
        # with NO tail). The dense PREFIX keeps the user's original text
        # verbatim (the never-modify contract) -- the full-weight emphasis
        # is what disappears, from both legs, not moved between them.
        r = enrich(L1_D03, df_lookup=_lookup({"url": (91, 100)}))
        assert r.identifiers == ("URL",)  # extraction record stays unfiltered
        assert r.dense_query == L1_D03  # no appended tail
        assert "URL" not in r.sparse_query.split()
        assert r.sparse_query == "parses already encoded string without re quoting"

    def test_repro_is_deterministic_across_runs_and_lookup_instances(self) -> None:
        # Run the survey's one-command repro shape twice with independently
        # built lookups marking 'url' ubiquitous: byte-identical output.
        r1 = enrich(L1_D03, df_lookup=_lookup({"url": (91, 100)}))
        r2 = enrich(L1_D03, df_lookup=_lookup({"url": (91, 100)}))
        assert r1 == r2
        r3 = enrich(L1_D03, df_lookup=_lookup({"url": (9999, 10000)}))
        assert r1 == r3  # any prevalence > 0.90 gives the same answer

    def test_none_lookup_repro_unchanged(self) -> None:
        # TC-015: the survey repro's output with no lookup is byte-identical
        # to today's -- ('URL',) with the appended tail and URL term intact.
        r = enrich(L1_D03)
        assert r.identifiers == ("URL",)
        assert r.dense_query == L1_D03 + " URL"
        assert "URL" in r.sparse_query.split()

    def test_partial_filter_keeps_the_rest(self) -> None:
        # Only 'url' is ubiquitous; every other term/identifier keeps full
        # weight exactly where the legacy path put it.
        base = enrich("how does parseUnencodedURL handle quirks")
        r = enrich(
            "how does parseUnencodedURL handle quirks",
            df_lookup=_lookup({"url": (95, 100)}),
        )
        assert r.identifiers == base.identifiers  # ("parse", "Unencoded", "URL")
        assert r.sparse_query == "parseUnencodedURL handle quirks parse Unencoded"
        assert r.dense_query == (
            "how does parseUnencodedURL handle quirks parse Unencoded"
        )  # tail keeps parse/Unencoded, drops URL


class TestDfLookupThresholdBoundary:
    """TC-011: behavior switches exactly at the documented 0.90 cut."""

    def test_documented_threshold_value(self) -> None:
        # The shipped, documented value (scikit-learn max_df convention).
        assert ENRICH_DF_MAX_FRACTION == 0.90

    @pytest.mark.parametrize(
        ("symbol_df", "n_symbols", "kept"),
        [
            (89, 100, True),  # just below -> full weight
            (90, 100, True),  # exactly at the cut -> kept (strictly-greater drop)
            (9, 10, True),  # exactly 0.90 via a different denominator
            (900, 1000, True),  # exactly 0.90 at scale
            (91, 100, False),  # just above -> dropped
            (9001, 10000, False),  # just above at scale
        ],
    )
    def test_prevalence_cut(self, symbol_df: int, n_symbols: int, kept: bool) -> None:
        q = "how is HTTP retry handled"
        r = enrich(q, df_lookup=_lookup({"http": (symbol_df, n_symbols)}))
        if kept:
            assert r.sparse_query == "HTTP retry handled"
            assert r.dense_query == q + " HTTP"
        else:
            assert r.sparse_query == "retry handled"
            assert r.dense_query == q  # no tail, prefix untouched

    def test_no_df_data_keeps_term(self) -> None:
        # Unknown key (None) and empty-corpus rows (n_symbols == 0) both
        # mean "no data": never a penalty.
        q = "how is HTTP retry handled"
        for info in (None, (0, 0), (5, 0)):
            table = {} if info is None else {"http": info}
            r = enrich(q, df_lookup=_lookup(table))
            assert r.sparse_query == "HTTP retry handled"
            assert r.dense_query == q + " HTTP"

    def test_all_ubiquitous_terms_yield_empty_sparse_fallback_signal(self) -> None:
        # The documented sparse-leg signal survives filtering: empty
        # sparse_query means "fall back to the raw query".
        r = enrich("where is the URL", df_lookup=_lookup({"url": (100, 100)}))
        assert r.sparse_query == ""
        assert r.dense_query == "where is the URL"


class TestDfLookupRareTermSurvival:
    """TC-012: discriminative terms keep full weight."""

    def test_rare_terms_byte_identical_to_no_lookup(self) -> None:
        # Rare identifiers (2/1000, 3/5000) survive at FULL weight: the
        # enriched output equals the no-lookup legacy output byte for byte.
        q = "how do I decode utf8 bytes with `split_url`"
        lookup = _lookup({"utf8": (2, 1000), "url": (3, 5000), "split": (1, 5000)})
        assert enrich(q, df_lookup=lookup) == enrich(q)

    def test_rare_survives_alongside_ubiquitous_dropped(self) -> None:
        # One rare + one ubiquitous in the same query: the repair
        # suppresses ubiquity, not specificity.
        q = "retry HTTP via `backoff_policy`"
        r = enrich(q, df_lookup=_lookup({"http": (99, 100)}))
        # HTTP stays in the extraction record (prose ALLCAPS is extracted);
        # it is dropped from the tail and the sparse terms.
        assert r.identifiers == ("backoff_policy", "backoff", "policy", "HTTP")
        assert r.sparse_query == "retry via backoff_policy backoff policy"
        assert r.dense_query == q + " backoff_policy backoff policy"


class TestDfLookupPurityEquivalence:
    """TC-015: enrich stays pure; default behavior is byte-identical."""

    PROBES = [
        L1_D03,
        SPEC_EXAMPLE,
        "how does `parse_url` differ from split_url",
        "Where does `ApiFactory` call build_client for the HTTPClient",
        "where do we handle retries",
        "",
        "   \t\n ",
        "where is the function",
    ]

    @pytest.mark.parametrize("q", PROBES)
    def test_none_and_empty_and_all_miss_lookups_equal_legacy(self, q: str) -> None:
        legacy = enrich(q)
        assert enrich(q, df_lookup=None) == legacy
        assert enrich(q, df_lookup=_lookup({})) == legacy
        assert enrich(q, df_lookup=lambda key: None) == legacy

    @pytest.mark.parametrize("q", PROBES)
    def test_dense_prefix_untouched_under_filtering(self, q: str) -> None:
        # The never-lose-information invariant holds even when terms are
        # DF-dropped: the original text is always the dense prefix.
        r = enrich(q, df_lookup=lambda key: (100, 100))  # everything ubiquitous
        assert r.dense_query.startswith(q)
        assert r.identifiers == enrich(q).identifiers

    def test_deterministic_repeats_with_lookup(self) -> None:
        lookup = _lookup({"url": (91, 100), "parse": (50, 100)})
        for q in self.PROBES[:4]:
            assert enrich(q, df_lookup=lookup) == enrich(q, df_lookup=lookup)

    def test_lookup_called_once_per_distinct_casefolded_token(self) -> None:
        # D-005's O(#distinct query tokens) bound: the memo means repeated
        # occurrences (any casing) trigger exactly one lookup per key.
        calls: list[str] = []

        def counting(key: str):
            calls.append(key)
            return None

        enrich("url URL url parse_url", df_lookup=counting)
        assert calls.count("url") == 1
        assert sorted(set(calls)) == sorted(calls)  # no key probed twice
        assert set(calls) == {"url", "parse_url", "parse"}

    def test_lookup_not_invoked_when_omitted(self) -> None:
        # The default path stays a pure function of the string: the None
        # default never dereferences a lookup (structurally impossible --
        # no lookup object is conjured from env or globals).
        assert enrich(SPEC_EXAMPLE, df_lookup=None) == enrich(SPEC_EXAMPLE)
