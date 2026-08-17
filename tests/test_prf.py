"""T015: pure RM3-style PRF expansion (FR-004 / D-001, D-003, TC-017).

Pure-function unit tests for ``cairn.graph.prf.expand``: IDF-weighted
term selection and ordering, query-term exclusion, the RM3 (1-lambda)
drift cap with its exact boundary, top-fb_terms cut, degenerate feedback
(never raises), the df_lookup contract (lowercase keys, uniform-IDF
fallbacks), determinism, and the no-LLM/no-network hermeticity guard.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import math
import socket
from pathlib import Path

import pytest

from cairn.graph.prf import ExpansionResult, expand


def _lookup(table: dict, n_symbols: int = 100):
    """df_lookup over an in-memory table: token -> (symbol_df, n_symbols)."""

    def _df(token: str):
        df = table.get(token)
        return None if df is None else (df, n_symbols)

    return _df


# n=100 corpus: ln(100/df) IDFs -- ln100 ~= 4.605, ln50 ~= 3.912,
# ln10 ~= 2.303, ln5 ~= 1.609, ln1 = 0.
DF_RARE = {"rare": 1, "also": 1, "lessthan": 2}
DF_ORDER = {"alpha": 10, "beta": 10, "gamma": 20, "delta": 100}


class TestTermSelection:
    def test_idf_weighted_order_with_tiebreak(self) -> None:
        # lambda=1.0 -> cap 0 keeps every candidate, exposing the full
        # weight ordering: alpha (3 docs x ln10), beta (2 x ln10), gamma
        # (ln5), delta (idf 0). alpha/beta-ties and beta-vs-gamma prove
        # higher summed IDF ranks first; the alpha==beta weight tie is
        # broken by token ascending.
        r = expand(
            "search",
            ["alpha beta gamma", "alpha beta", "alpha delta"],
            df_lookup=_lookup(DF_ORDER),
            fb_lambda=1.0,
        )
        assert r.terms == ("alpha", "beta", "gamma", "delta")
        assert r.weights == (
            pytest.approx(3 * math.log(10)),
            pytest.approx(2 * math.log(10)),
            pytest.approx(math.log(5)),
            pytest.approx(0.0),
        )
        assert len(r.weights) == len(r.terms)

    def test_fb_terms_cuts_after_ordering(self) -> None:
        r = expand(
            "search",
            ["alpha beta gamma", "alpha beta", "alpha delta"],
            df_lookup=_lookup(DF_ORDER),
            fb_lambda=1.0,
            fb_terms=2,
        )
        assert r.terms == ("alpha", "beta")

    def test_per_doc_frequency_not_raw_frequency(self) -> None:
        # A token repeated 4x inside ONE doc counts once (fb_df=1) while
        # a token in 2 docs counts twice: under raw frequency "echo" (4
        # occurrences) would outrank "signal" (2); per-document summed
        # IDF puts signal first.
        r = expand(
            "q",
            ["echo echo echo echo", "signal", "signal"],
            df_lookup=_lookup({"echo": 1, "signal": 1}),
            fb_lambda=1.0,
        )
        # w(signal) = 2*ln100 > w(echo) = 1*ln100.
        assert r.terms == ("signal", "echo")


class TestQueryExclusion:
    def test_query_terms_dropped_case_folded(self) -> None:
        # Query carries "Rare"/"SEARCH"; feedback has "rare" -- excluded
        # under case-folded comparison in both directions.
        r = expand(
            "Rare SEARCH",
            ["rare also beta"],
            df_lookup=_lookup({"rare": 1, "also": 1, "beta": 1}),
        )
        assert "rare" not in r.terms
        assert r.terms == ("also", "beta")  # equal weights -> alphabetical

    def test_all_feedback_terms_in_query_yields_empty(self) -> None:
        r = expand("parse url", ["parse", "url parse"], df_lookup=_lookup({}))
        assert r.terms == ()
        assert r.weights == ()
        assert r.dense_query == "parse url"


class TestLambdaCap:
    def test_boundary_term_at_exactly_cap_is_kept(self) -> None:
        # rare: fb=2, idf=ln100 -> w = 2*ln100 = max. also: fb=1, idf=ln100
        # -> w = ln100 == (1-0.5)*max EXACTLY in binary floating point
        # (doubling/halving are exact), so >= keeps it. lessthan: fb=1,
        # idf=ln50 < cap -> dropped.
        r = expand(
            "search",
            ["rare also", "rare", "lessthan"],
            df_lookup=_lookup(DF_RARE),
            fb_lambda=0.5,
        )
        assert r.terms == ("rare", "also")

    def test_cap_filters_below_threshold(self) -> None:
        # Same scenario at the default lambda: beta survives the
        # 0.5*max cap, gamma (ln5) and delta (idf 0) do not.
        r = expand(
            "search",
            ["alpha beta gamma", "alpha beta", "alpha delta"],
            df_lookup=_lookup(DF_ORDER),
            fb_lambda=0.5,
        )
        assert r.terms == ("alpha", "beta")

    def test_lambda_zero_keeps_only_max(self) -> None:
        r = expand(
            "search",
            ["alpha beta gamma", "alpha beta", "alpha delta"],
            df_lookup=_lookup(DF_ORDER),
            fb_lambda=0.0,
        )
        assert r.terms == ("alpha",)

    def test_lambda_out_of_range_raises(self) -> None:
        for bad in (1.5, -0.1):
            with pytest.raises(ValueError, match="fb_lambda"):
                expand("q", ["a"], fb_lambda=bad)


class TestDfLookupContract:
    def test_lookup_receives_lowercase_keys(self) -> None:
        calls: list[str] = []

        def _df(token: str):
            calls.append(token)
            return (1, 100)

        expand("q", ["ParseURL parse_url"], df_lookup=_df)
        # Call ORDER follows fb_df insertion order (set iteration, not
        # under contract) -- the contract is the KEYS: unicode61
        # case-folded, camelCase/separator compounds split by the
        # tokenizer, never mixed-case.
        assert sorted(calls) == ["parse", "parseurl", "url"]
        assert all(k == k.lower() for k in calls)

    def test_missing_token_falls_back_to_uniform_idf(self) -> None:
        # "unseen" is absent from the lookup -> uniform idf 1.0, so it
        # ranks by feedback frequency alone, below known rare terms.
        r = expand(
            "q",
            ["known unseen", "known unseen"],
            df_lookup=_lookup({"known": 1}),
            fb_lambda=1.0,
        )
        assert r.terms == ("known", "unseen")
        assert r.weights == (
            pytest.approx(2 * math.log(100)),
            pytest.approx(2 * 1.0),
        )

    def test_none_lookup_is_uniform_idf(self) -> None:
        # df_lookup=None degrades to frequency-only selection: every
        # token gets idf 1.0. alpha (2 docs) first; beta/gamma tie at 1
        # -> alphabetical. beta/gamma sit exactly AT the 0.5*max cap and
        # are kept (the boundary is inclusive).
        r = expand("q", ["alpha beta", "alpha gamma"])
        assert r.terms == ("alpha", "beta", "gamma")
        assert r.weights == (pytest.approx(2.0), pytest.approx(1.0), pytest.approx(1.0))

    def test_malformed_rows_fall_back_to_uniform_idf(self) -> None:
        # df < 1, n < 1, and df > n all violate the lookup contract and
        # resolve to uniform idf 1.0 rather than raising or logging.
        def _df(token: str):
            return {"zero": (0, 100), "neg": (1, 0), "over": (200, 100)}[token]

        r = expand("q", ["zero neg over"], df_lookup=_df, fb_lambda=0.5)
        assert r.terms == ("neg", "over", "zero")  # all weight 1.0 == cap
        assert r.weights == (pytest.approx(1.0),) * 3

    def test_all_idf_zero_yields_empty(self) -> None:
        # Every candidate token sits in every symbol: idf 0 across the
        # board, max_weight 0 -> no corpus-aware signal -> empty terms.
        r = expand("q", ["a b", "a c"], df_lookup=lambda _t: (100, 100))
        assert r.terms == ()
        assert r.dense_query == "q"


class TestDegenerateFeedback:
    def test_empty_docs_never_raises(self) -> None:
        r = expand("parse url", [], df_lookup=_lookup({}))
        assert r == ExpansionResult((), (), "parse url")

    def test_none_and_blank_docs_contribute_nothing(self) -> None:
        # bm25-only candidates carry chunk=""; a wiring slip may pass
        # None. Both are empty feedback documents, not errors.
        r = expand("q", [None, "", "   "], df_lookup=_lookup({}))
        assert r.terms == ()
        assert r.dense_query == "q"

    def test_fb_terms_zero_yields_empty(self) -> None:
        r = expand("q", ["a b"], fb_terms=0)
        assert r.terms == ()
        assert r.dense_query == "q"

    def test_empty_query_with_feedback_still_expands(self) -> None:
        r = expand("", ["alpha"], df_lookup=_lookup({"alpha": 1}))
        assert r.terms == ("alpha",)
        assert r.dense_query == " alpha"


class TestResultContract:
    def test_dense_query_is_original_plus_terms(self) -> None:
        r = expand("search", ["beta alpha", "alpha"], df_lookup=_lookup(DF_ORDER))
        assert r.dense_query == "search " + " ".join(r.terms)

    def test_result_is_frozen(self) -> None:
        r = expand("q", ["a"], df_lookup=_lookup({"a": 1}))
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.terms = ()  # type: ignore[misc]

    def test_generator_input_is_consumed_once(self) -> None:
        # feedback_docs may be any ordered iterable, single-pass.
        r = expand("q", (d for d in ["alpha beta"]), df_lookup=_lookup(DF_ORDER))
        assert "alpha" in r.terms


class TestDeterminismHermeticity:
    def test_two_calls_byte_identical(self) -> None:
        # TC-017: same inputs -> identical results, with and without a
        # DF signal. dataclass equality compares fields; the string
        # fields pin byte-identity.
        for docs in (["alpha beta gamma", "alpha beta", "alpha delta"], []):
            for lk in (None, _lookup(DF_ORDER)):
                first = expand("search", docs, df_lookup=lk)
                second = expand("search", docs, df_lookup=lk)
                assert first == second
                assert first.dense_query == second.dense_query

    def test_doc_order_permutation_same_terms(self) -> None:
        # Scoring is order-invariant: rank order is the contract, not an
        # input to the outcome.
        docs = ["alpha beta gamma", "alpha beta", "alpha delta"]
        assert expand("search", docs, df_lookup=_lookup(DF_ORDER)).terms == (
            expand("search", reversed(docs), df_lookup=_lookup(DF_ORDER)).terms
        )

    def test_no_env_or_network_dependence(self) -> None:
        # TC-017 offline guard: env mutations must not change the output
        # and no network call may be attempted.
        docs = ["alpha beta", "alpha gamma"]
        baseline = expand("q", docs, df_lookup=_lookup(DF_ORDER))

        real_socket = socket.socket

        def _no_network(*_a, **_k):
            raise AssertionError("expand() attempted a network call")

        try:
            socket.socket = _no_network  # type: ignore[assignment,misc]
            import os

            for key, val in (
                ("CAIRN_LLM_PROVIDER", "openai"),
                ("CAIRN_API_KEY", "secret"),
                ("CAIRN_PRF_TERMS", "999"),
                ("OPENAI_API_KEY", "secret"),
            ):
                os.environ[key] = val
            again = expand("q", docs, df_lookup=_lookup(DF_ORDER))
        finally:
            socket.socket = real_socket  # type: ignore[assignment,misc]
            import os

            for key in (
                "CAIRN_LLM_PROVIDER",
                "CAIRN_API_KEY",
                "CAIRN_PRF_TERMS",
                "OPENAI_API_KEY",
            ):
                os.environ.pop(key, None)

        assert again == baseline

    def test_module_imports_are_hermetic(self) -> None:
        # Standing guard (TC-017 doctrine): only stdlib math, typing,
        # dataclasses, and collections.abc may be imported -- no random,
        # time, os, sqlite3, or anything network/LLM-capable.
        src = Path(inspect.getsourcefile(expand)).read_text()
        imported: set[str] = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        assert imported <= {
            "__future__",
            "math",
            "collections.abc",
            "dataclasses",
            "typing",
        }
