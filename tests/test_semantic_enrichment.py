"""Phase 4: optional graph enrichment (include_callers) on semantic_search.

Verifies the opt-in 1-hop caller/callee attachment, and that it's a true
no-op (no "callers"/"callees" keys at all) when not requested -- existing
callers of semantic_search must see zero shape change by default.

The T013 section (FR-003 / D-005) pins the ``enrich_idf`` boundary: the
DF lookup is built at the ``semantic_search`` seam (never inside
``enrich``), flag-off is byte-identical to today, flag-on demonstrably
drops a corpus-ubiquitous token through the full path, and the per-query
cost is one indexed ``term_df`` SELECT per distinct case-folded token.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

# Apply the shared hash-backend fixture to every test in this module
pytestmark = pytest.mark.usefixtures("hash_backend")


def _seed_call_graph(conn: sqlite3.Connection) -> None:
    """UserRepo.run() calls Profile.displayName() -- a real 1-hop edge to enrich with."""
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('test', 'test', '/tmp/test')")
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES (1, 'test', '/tmp/test/Profile.kt', 'kotlin')"
    )
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES (2, 'test', '/tmp/test/UserRepo.kt', 'kotlin')"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, docstring, line_start, line_end) "
        "VALUES (1, 1, 'displayName', 'method', 'Profile.displayName', 'Returns display name.', 1, 3)"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
        "VALUES (2, 2, 'run', 'method', 'UserRepo.run', 1, 5)"
    )
    conn.execute(
        "INSERT INTO edges (id, source_id, target_id, target_name, kind, line) "
        "VALUES ('e1', '2', '1', 'displayName', 'call', 3)"
    )
    conn.commit()


def _conn_with_call_graph(fresh_db) -> sqlite3.Connection:
    _seed_call_graph(fresh_db)
    return fresh_db


def test_include_callers_false_by_default_has_no_extra_keys(fresh_db):
    from cairn.graph import embeddings as emb
    from cairn.graph.queries import semantic_search

    conn = _conn_with_call_graph(fresh_db)
    emb.embed_all(conn)

    results = semantic_search(conn, "displayName", limit=5, threshold=-1.0)
    assert results
    assert "callers" not in results[0]
    assert "callees" not in results[0]


def test_include_callers_true_attaches_1hop_neighbors(fresh_db):
    from cairn.graph import embeddings as emb
    from cairn.graph.queries import semantic_search

    conn = _conn_with_call_graph(fresh_db)
    emb.embed_all(conn)

    results = semantic_search(
        conn, "displayName", limit=5, threshold=-1.0, include_callers=True
    )
    assert results
    hit = next(r for r in results if r["name"] == "displayName")
    assert "callers" in hit and "callees" in hit
    caller_names = [c["name"] for c in hit["callers"]]
    assert "run" in caller_names, f"expected UserRepo.run as a caller, got {caller_names}"


def test_include_callers_degrades_to_empty_lists_for_symbol_with_no_edges(fresh_db):
    from cairn.graph import embeddings as emb
    from cairn.graph.queries import semantic_search

    conn = _conn_with_call_graph(fresh_db)
    emb.embed_all(conn)

    results = semantic_search(
        conn, "run", limit=5, threshold=-1.0, include_callers=True
    )
    hit = next(r for r in results if r["name"] == "run")
    assert hit["callers"] == []  # nothing calls UserRepo.run in this fixture


# ---------------------------------------------------------------------------
# T013 (FR-003, D-005): the enrich_idf boundary -- DF lookup injection at
# the semantic_search seam.
#
# The corpus-side DF signal enters exactly at ``_enriched = enrich_query``
# (semantic.py): when params.enrich_idf is truthy AND enrichment is
# active, the boundary builds a per-term lookup over the persisted
# term_df table (T011) and injects it into the ONE enrich() call --
# enrich itself stays pure (TC-015). Flag-off must be byte-identical to
# today (TC-016-style defaults doctrine); the per-query cost is bounded
# by the query's distinct token count (TC-014's documented bound).
# ---------------------------------------------------------------------------

# The L1-D03 sentence shape plus identifier/plain probes -- every kind of
# text the boundary sees.
_PROBES = [
    "what handles `parseUnencodedURL` when building the outgoing request",
    "where is the function that parses an unencoded URL string",
    "displayName",
    "UserRepo.run callers",
]


def _seed_idf_corpus(conn: sqlite3.Connection) -> None:
    """Three symbols under the hash backend:

    * parseUnencodedURL -- docstring "Parse URL." (the standalone FTS
      token ``url``; name is one camelCase unicode61 token);
    * buildOutgoingRequest -- prose decoy, no url token;
    * urlEncoder -- matches the FTS prefix ``"url"*`` through its name,
      so a surviving ``url`` term fetches it into the sparse leg.

    embed_all rebuilds term_df naturally (T011): df(url) = 1/3 here --
    kept. Tests override rows explicitly to set prevalence.
    """
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('t', 't', '/tmp/t')")
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES (1, 't', '/tmp/src/Net.kt', 'kotlin')"
    )
    rows = [
        (1, "parseUnencodedURL", "net.parseUnencodedURL", "Parse URL."),
        (2, "buildOutgoingRequest", "net.buildOutgoingRequest", "Builds the outgoing request."),
        (3, "urlEncoder", "net.urlEncoder", "Encodes URLs."),
    ]
    for sid, name, qual, doc in rows:
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, qualified_name, docstring, line_start, line_end) "
            "VALUES (?, 1, ?, 'function', ?, ?, 1, 10)",
            (sid, name, qual, doc),
        )
    conn.commit()


def _set_df(conn: sqlite3.Connection, token: str, symbol_df: int, n_symbols: int) -> None:
    """Override one term_df row to pin an exact prevalence."""
    conn.execute(
        "INSERT OR REPLACE INTO term_df (token, symbol_df, n_symbols) VALUES (?, ?, ?)",
        (token, symbol_df, n_symbols),
    )
    conn.commit()


@pytest.fixture()
def idf_db(fresh_db, monkeypatch):
    """The three-symbol corpus, embedded (term_df rebuilt by the embed
    pass), under deterministic retrieval knobs: brute scan forced, no
    rerank enablement/marker, fusion left at its ON default -- the
    production path the equivalence contracts must hold on."""
    from cairn.graph import embeddings as emb
    from cairn.graph import reranker as rrk

    monkeypatch.setattr(
        rrk, "_rerank_marker_path", lambda: Path("/nonexistent/cairn-t013-marker")
    )
    monkeypatch.setenv("CAIRN_ANN_BACKEND", "off")
    monkeypatch.delenv("CAIRN_RERANK", raising=False)
    monkeypatch.delenv("CAIRN_RERANK_MIN_MARGIN", raising=False)
    monkeypatch.delenv("CAIRN_FUSION", raising=False)
    _seed_idf_corpus(fresh_db)
    emb.embed_all(fresh_db)
    return fresh_db


class _StatementRecorder:
    """Collect every SQL statement this connection executes (the sqlite3
    trace callback) for the ``with`` block's duration."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.statements: list[str] = []

    def __enter__(self):
        self.conn.set_trace_callback(self.statements.append)
        return self

    def __exit__(self, *exc):
        self.conn.set_trace_callback(None)

    def term_df_selects(self) -> list[str]:
        return [s for s in self.statements if "FROM term_df" in s]


def _install_boundary_spies(monkeypatch):
    """Spy on the two boundary consumers: what embed_query received (the
    dense leg's input) and what search_symbols_terms received (the sparse
    leg's term list). Wraps the real functions so results stay live."""
    from cairn.graph import embeddings as emb_mod
    from cairn.graph import semantic as semantic_mod

    embed_calls: list[str] = []
    real_embed = emb_mod.embed_query

    def embed_spy(text):
        embed_calls.append(text)
        return real_embed(text)

    term_calls: list[list[str]] = []
    real_terms = semantic_mod.search_symbols_terms

    def terms_spy(conn, terms, kind=None, limit=100):
        term_calls.append(list(terms))
        return real_terms(conn, terms, kind=kind, limit=limit)

    monkeypatch.setattr(emb_mod, "embed_query", embed_spy)
    monkeypatch.setattr(semantic_mod, "search_symbols_terms", terms_spy)
    return embed_calls, term_calls


class TestEnrichIdfFlagOffEquivalence:
    """The FR-005 defaults-preserving contract on the new field: with
    enrich_idf None (inside an object) or explicit False, the enriched
    outputs and the retrieval results are byte-identical to the DF-blind
    enrich(query) call -- T012's TC-015 None-lookup equivalence chains
    that to the pre-FR-003 bytes -- and not one term_df SELECT runs."""

    @pytest.mark.parametrize("probe", _PROBES)
    def test_flag_off_matches_the_pure_enrich_call_byte_for_byte(
        self, monkeypatch, idf_db, probe
    ):
        from cairn.graph import semantic as semantic_mod
        from cairn.graph.query_enrich import enrich
        from cairn.graph.semantic import RetrievalParams, semantic_search

        captured = []
        real = semantic_mod.enrich_query

        def spy(query, df_lookup=None):
            out = real(query) if df_lookup is None else real(query, df_lookup)
            captured.append((df_lookup, out))
            return out

        monkeypatch.setattr(semantic_mod, "enrich_query", spy)

        unset = semantic_search(
            idf_db, probe, limit=10, params=RetrievalParams(enrich=True)
        )
        off = semantic_search(
            idf_db,
            probe,
            limit=10,
            params=RetrievalParams(enrich=True, enrich_idf=False),
        )
        assert off == unset  # retrieval results byte-identical
        assert len(captured) == 2
        for df_lookup, out in captured:
            assert df_lookup is None  # the DF-blind single-argument form
            assert out == enrich(probe)  # byte-identical enriched output

    def test_flag_off_issues_zero_term_df_selects(self, idf_db):
        from cairn.graph.semantic import RetrievalParams, semantic_search

        with _StatementRecorder(idf_db) as rec:
            semantic_search(idf_db, _PROBES[1], limit=10)
            semantic_search(
                idf_db, _PROBES[1], limit=10, params=RetrievalParams()
            )
            semantic_search(
                idf_db, _PROBES[1], limit=10, params=RetrievalParams(enrich=True)
            )
            semantic_search(
                idf_db,
                _PROBES[1],
                limit=10,
                params=RetrievalParams(enrich=True, enrich_idf=False),
            )
        assert rec.term_df_selects() == []

    @pytest.mark.parametrize("probe", _PROBES)
    def test_empty_object_still_equals_no_params(self, idf_db, probe):
        from cairn.graph.semantic import RetrievalParams, semantic_search

        plain = semantic_search(idf_db, probe, limit=10)
        injected = semantic_search(
            idf_db, probe, limit=10, params=RetrievalParams()
        )
        assert injected == plain

    def test_enrich_idf_alone_is_inert_without_enrich(self, idf_db):
        """enrich_idf=True with enrichment off does nothing: no lookup,
        no SELECTs, byte-identical results to the plain call."""
        from cairn.graph.semantic import RetrievalParams, semantic_search

        _set_df(idf_db, "url", 3, 3)  # ubiquitous -- must not matter
        with _StatementRecorder(idf_db) as rec:
            alone = semantic_search(
                idf_db, "URL helper", limit=10, params=RetrievalParams(enrich_idf=True)
            )
            plain = semantic_search(idf_db, "URL helper", limit=10)
        assert alone == plain
        assert rec.term_df_selects() == []


class TestEnrichIdfWiring:
    """Flag-on: the lookup is built from term_df at the boundary and the
    0.90 cutoff fires through the full semantic_search path (both legs).

    Query ``URL helper`` under pure-sparse weights (dense_threshold=0.99
    empties the dense leg; BM25-only candidates bypass the threshold, so
    the results ARE the sparse leg): enrichment extracts the identifier
    URL, so DF-blind the sparse terms are [URL, helper] and the
    ``"url"*`` prefix fetches urlEncoder; with url ubiquitous the terms
    are [helper] alone and urlEncoder is gone from the results.
    """

    SPARSE_ONLY = dict(dense_threshold=0.99, rrf_weights=(0.0, 1.0))

    def test_ubiquitous_token_dropped_from_both_legs(self, monkeypatch, idf_db):
        from cairn.graph.semantic import RetrievalParams, semantic_search

        _set_df(idf_db, "url", 3, 3)  # prevalence 1.0 > 0.90
        embed_calls, term_calls = _install_boundary_spies(monkeypatch)
        semantic_search(
            idf_db,
            "URL helper",
            limit=10,
            params=RetrievalParams(enrich=True, enrich_idf=True, **self.SPARSE_ONLY),
        )
        # Dense leg: the appended identifier tail is gone -- the all-dropped
        # tail means NO tail, the raw query verbatim (EnrichedQuery contract).
        assert embed_calls == ["URL helper"]
        # Sparse leg: the term list carries no url token.
        assert term_calls == [["helper"]]

    def test_ubiquitous_token_leaves_the_result_list(self, idf_db):
        from cairn.graph.semantic import RetrievalParams, semantic_search

        off = semantic_search(
            idf_db,
            "URL helper",
            limit=10,
            params=RetrievalParams(enrich=True, **self.SPARSE_ONLY),
        )
        assert "urlEncoder" in [r["name"] for r in off]

        _set_df(idf_db, "url", 3, 3)
        on = semantic_search(
            idf_db,
            "URL helper",
            limit=10,
            params=RetrievalParams(enrich=True, enrich_idf=True, **self.SPARSE_ONLY),
        )
        assert "urlEncoder" not in [r["name"] for r in on]

    def test_cutoff_boundary_flows_through_the_real_table(self, monkeypatch, idf_db):
        """Exactly 0.90 keeps, strictly above drops -- pinned through the
        real term_df rows, not a fake callable (the boundary must build
        the lookup from the table)."""
        from cairn.graph.semantic import RetrievalParams, semantic_search

        embed_calls, _ = _install_boundary_spies(monkeypatch)

        def run():
            semantic_search(
                idf_db,
                "URL helper",
                limit=10,
                params=RetrievalParams(enrich=True, enrich_idf=True, **self.SPARSE_ONLY),
            )
            return list(embed_calls)

        _set_df(idf_db, "url", 90, 100)  # exactly 0.90: keeps full weight
        assert run() == ["URL helper URL"]
        _set_df(idf_db, "url", 91, 100)  # strictly above: dropped
        embed_calls.clear()
        assert run() == ["URL helper"]

    def test_rare_and_absent_tokens_keep_full_weight(self, monkeypatch, idf_db):
        """No-data-never-penalizes at the boundary: a rare term (natural
        df 1/3) and an absent row (lookup None) both keep the term."""
        from cairn.graph.semantic import RetrievalParams, semantic_search

        embed_calls, term_calls = _install_boundary_spies(monkeypatch)
        params = RetrievalParams(enrich=True, enrich_idf=True, **self.SPARSE_ONLY)

        # Rare: df(url) = 1/3 <= 0.90 (the natural rebuild already wrote
        # exactly this row; set it explicitly to pin the intent).
        _set_df(idf_db, "url", 1, 3)
        semantic_search(idf_db, "URL helper", limit=10, params=params)
        assert embed_calls == ["URL helper URL"]
        assert term_calls == [["URL", "helper"]]

        # Absent: deleted row -> lookup returns None -> term kept.
        idf_db.execute("DELETE FROM term_df WHERE token = 'url'")
        idf_db.commit()
        embed_calls.clear()
        term_calls.clear()
        semantic_search(idf_db, "URL helper", limit=10, params=params)
        assert embed_calls == ["URL helper URL"]
        assert term_calls == [["URL", "helper"]]

    def test_flag_on_results_deterministic(self, idf_db):
        """TC-014 flavor: the injected signal is a pure read of the local
        table -- same query, same table, byte-identical results twice."""
        from cairn.graph.semantic import RetrievalParams, semantic_search

        _set_df(idf_db, "url", 3, 3)
        params = RetrievalParams(enrich=True, enrich_idf=True)
        first = semantic_search(idf_db, _PROBES[1], limit=10, params=params)
        second = semantic_search(idf_db, _PROBES[1], limit=10, params=params)
        assert first == second


class TestEnrichIdfCostBound:
    """The documented D-005 bound: one indexed term_df SELECT per
    DISTINCT case-folded query token, memoized per call, never a scan."""

    def test_one_select_per_distinct_token(self, idf_db):
        from cairn.graph.semantic import RetrievalParams, semantic_search

        _set_df(idf_db, "url", 3, 3)
        # Non-stopword distinct candidate tokens of the L1-D03 sentence:
        # parses, unencoded, url, string (identifiers add no new key).
        with _StatementRecorder(idf_db) as rec:
            semantic_search(
                idf_db,
                _PROBES[1],
                limit=10,
                params=RetrievalParams(enrich=True, enrich_idf=True),
            )
        selects = rec.term_df_selects()
        assert len(selects) == 4
        # The trace callback reports the expanded statements: exactly the
        # four distinct case-folded tokens, one probe each, all through
        # the same PK-equality shape.
        assert selects == [
            "SELECT symbol_df, n_symbols FROM term_df WHERE token = 'parses'",
            "SELECT symbol_df, n_symbols FROM term_df WHERE token = 'unencoded'",
            "SELECT symbol_df, n_symbols FROM term_df WHERE token = 'url'",
            "SELECT symbol_df, n_symbols FROM term_df WHERE token = 'string'",
        ]

    def test_repeated_tokens_are_memoized(self, idf_db):
        from cairn.graph.semantic import RetrievalParams, semantic_search

        # "URL parsing of URL strings": five candidate tokens, four
        # non-stopword occurrences, three DISTINCT keys (url, parsing,
        # strings) -- memoization must hold it to three SELECTs.
        with _StatementRecorder(idf_db) as rec:
            semantic_search(
                idf_db,
                "URL parsing of URL strings",
                limit=10,
                params=RetrievalParams(enrich=True, enrich_idf=True),
            )
        assert len(rec.term_df_selects()) == 3

    def test_lookup_memoizes_per_instance_and_misses_return_none(self, idf_db):
        from cairn.graph.semantic import _term_df_lookup

        _set_df(idf_db, "url", 2, 3)
        lookup = _term_df_lookup(idf_db)
        with _StatementRecorder(idf_db) as rec:
            assert lookup("url") == (2, 3)
            assert lookup("url") == (2, 3)  # served from the memo
            assert lookup("missing") is None
            assert lookup("missing") is None
        assert len(rec.term_df_selects()) == 2  # url + missing, once each
        # A fresh lookup instance memoizes independently.
        lookup2 = _term_df_lookup(idf_db)
        with _StatementRecorder(idf_db) as rec2:
            assert lookup2("url") == (2, 3)
        assert len(rec2.term_df_selects()) == 1

    def test_lookup_probes_the_primary_key_not_a_scan(self, idf_db):
        """The per-term fetch is an index probe (token is term_df's
        PRIMARY KEY), so the per-query cost is O(#tokens) indexed
        lookups, never a table scan."""
        plan = idf_db.execute(
            "EXPLAIN QUERY PLAN SELECT symbol_df, n_symbols FROM term_df WHERE token = ?",
            ("url",),
        ).fetchall()
        detail = " ".join(str(r[3]) for r in plan)
        # A TEXT PRIMARY KEY is enforced by SQLite's automatic unique
        # index: the probe surfaces as SEARCH ... USING INDEX
        # sqlite_autoindex_term_df_1 (token=?), never a SCAN.
        assert "SEARCH term_df USING INDEX sqlite_autoindex_term_df_1" in detail
        assert "SCAN" not in detail.upper()
