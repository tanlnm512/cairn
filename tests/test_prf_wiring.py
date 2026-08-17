"""T016 (FR-004): the PRF wiring at the post-fusion seam in semantic_search.

Pins the boundary contract of the second pass (D-001/D-003/D-012):

* flag-off byte-equivalence -- ``params=None``, ``RetrievalParams()``, and
  a flag-off object carrying the new knobs are all identical, with exactly
  ONE ``embed_query`` per call and zero ``term_df`` SELECTs (TC-016);
* flag-on full-path wiring -- the fused top-k's text feeds
  ``prf.expand`` (with T013's ``term_df`` lookup and the resolved
  D-002 knobs), the expanded ``dense_query`` gets the call's SECOND
  ``embed_query`` (the D-012 doctrine exception, at most one extra), the
  expansion terms join the sparse leg, and the second pass's candidates
  replace the first's;
* PRF replaces the rerank stage, never stacks (TC-019, wiring level): a
  PRF combo never reaches ``rrk.rerank`` even with ``CAIRN_RERANK=1``
  and an explicit ``rerank=True``;
* offline determinism (TC-017) and the degenerate empty-expansion
  fallback (second pass skipped: zero extra embeds, first-pass results).

The corpus is two python-ish symbols under the hash backend plus
pure-sparse weights (``dense_threshold=0.99`` empties the dense leg;
BM25-only candidates bypass the threshold, so the results ARE the sparse
leg): querying ``gizmo`` fetches only ``gizmo_polish`` on the first
pass. BM25-only candidates carry ``chunk=""``, so their feedback text is
prf.py's documented fallback ``name + " " + qualified_name`` --
``gizmo_polish mod.gizmo_polish`` -- whose only non-query, non-ubiquitous
token is ``polish`` (df 1/2). That expansion term prefix-fetches
``polisher_rig`` (its name token ``polisher`` starts with ``polish``) on
the second pass -- the wiring-level proof that the expansion is actually
consulted. A dense-leg variant (default threshold) exercises the chunk
feedback path.
"""

from __future__ import annotations

import socket
import sqlite3
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("hash_backend")

# Pure-sparse retrieval knobs (same trick as test_semantic_enrichment):
# the dense leg is emptied so the fused list IS the BM25 leg.
SPARSE_ONLY = dict(dense_threshold=0.99, rrf_weights=(0.0, 1.0))

# Every query shape the boundary sees: telegraphic, sentence, mixed.
_PROBES = [
    "gizmo",
    "how do I build a gizmo from scratch",
    "gizmo_maker callers",
    "widget polisher",
]


def _seed_prf_corpus(conn: sqlite3.Connection) -> None:
    """Two functions whose FTS tokens overlap only through prefixes.

    ``gizmo_polish``'s name/qualified tokens carry the rare token
    ``polish`` (df 1/2; ``mod`` is ubiquitous at 2/2); ``polisher_rig``'s
    name carries ``polisher`` -- so the PRF expansion term ``polish``
    prefix-fetches the second symbol on the second pass, while the raw
    ``gizmo`` query can never reach it.
    """
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('t', 't', '/tmp/t')")
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES (1, 't', '/tmp/t/m.py', 'python')"
    )
    rows = [
        (1, "gizmo_polish", "mod.gizmo_polish", "Builds gizmos."),
        (2, "polisher_rig", "mod.polisher_rig", "Shines items."),
    ]
    for sid, name, qual, doc in rows:
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, qualified_name, docstring, line_start, line_end) "
            "VALUES (?, 1, ?, 'function', ?, ?, 1, 10)",
            (sid, name, qual, doc),
        )
    conn.commit()


@pytest.fixture()
def prf_db(fresh_db, monkeypatch):
    """The two-symbol corpus, embedded (term_df rebuilt by the embed
    pass), under deterministic retrieval knobs: brute scan forced, no
    rerank enablement/marker, fusion left at its ON default."""
    from cairn.graph import embeddings as emb
    from cairn.graph import reranker as rrk

    monkeypatch.setattr(
        rrk, "_rerank_marker_path", lambda: Path("/nonexistent/cairn-t016-marker")
    )
    monkeypatch.setenv("CAIRN_ANN_BACKEND", "off")
    monkeypatch.delenv("CAIRN_RERANK", raising=False)
    monkeypatch.delenv("CAIRN_RERANK_MIN_MARGIN", raising=False)
    monkeypatch.delenv("CAIRN_FUSION", raising=False)
    _seed_prf_corpus(fresh_db)
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


def _install_embed_spy(monkeypatch):
    """Count and capture every embed_query text, wrapping the real
    (deterministic, hash-backend) function so results stay live."""
    from cairn.graph import embeddings as emb_mod

    calls: list[str] = []
    real = emb_mod.embed_query

    def spy(text):
        calls.append(text)
        return real(text)

    monkeypatch.setattr(emb_mod, "embed_query", spy)
    return calls


def _install_fetch_spies(monkeypatch):
    """Capture the raw-query sparse fetches and the term-mode sparse
    fetches (first pass uses the raw form with enrich off; the PRF second
    pass uses term mode once terms exist)."""
    from cairn.graph import semantic as semantic_mod

    raw_calls: list[str] = []
    real_raw = semantic_mod.search_symbols

    def raw_spy(conn, query, kind=None, limit=100):
        raw_calls.append(query)
        return real_raw(conn, query, kind=kind, limit=limit)

    term_calls: list[list[str]] = []
    real_terms = semantic_mod.search_symbols_terms

    def terms_spy(conn, terms, kind=None, limit=100):
        term_calls.append(list(terms))
        return real_terms(conn, terms, kind=kind, limit=limit)

    monkeypatch.setattr(semantic_mod, "search_symbols", raw_spy)
    monkeypatch.setattr(semantic_mod, "search_symbols_terms", terms_spy)
    return raw_calls, term_calls


def _install_expand_spy(monkeypatch):
    """Capture every prf_expand invocation (args + the real result),
    wrapping the real pure function."""
    from cairn.graph import semantic as semantic_mod

    captured: list[dict] = []
    real = semantic_mod.prf_expand

    def spy(query, feedback_docs, *, df_lookup=None, fb_terms=10, fb_lambda=0.5):
        result = real(
            query,
            feedback_docs,
            df_lookup=df_lookup,
            fb_terms=fb_terms,
            fb_lambda=fb_lambda,
        )
        captured.append(
            {
                "query": query,
                "feedback_docs": list(feedback_docs),
                "df_lookup": df_lookup,
                "fb_terms": fb_terms,
                "fb_lambda": fb_lambda,
                "result": result,
            }
        )
        return result

    monkeypatch.setattr(semantic_mod, "prf_expand", spy)
    return captured


class _RerankRecorder:
    """Stand-in for rrk.rerank that records calls (the spy for TC-019's
    never-runs proof); mirrors the real success contract."""

    def __init__(self):
        self.calls = []

    def __call__(self, query, candidates, limit):
        self.calls.append({"query": query, "n": len(candidates), "limit": limit})
        out = [dict(c) for c in candidates[:limit]]
        for item in out:
            item["rerank_score"] = 0.5
        return out, True


class TestPrfFlagOffEquivalence:
    """TC-016: the lever ships flag-off. params=None, RetrievalParams(),
    and a flag-off object that ALSO carries the new knobs are all
    byte-identical, one embed_query per call, no term_df read."""

    @pytest.mark.parametrize("probe", _PROBES)
    def test_flag_off_is_byte_identical_across_all_param_shapes(
        self, monkeypatch, prf_db, probe
    ):
        from cairn.graph.semantic import RetrievalParams, semantic_search

        embed_calls = _install_embed_spy(monkeypatch)

        plain = semantic_search(prf_db, probe, limit=10)
        empty_obj = semantic_search(
            prf_db, probe, limit=10, params=RetrievalParams()
        )
        flag_off_with_knobs = semantic_search(
            prf_db,
            probe,
            limit=10,
            params=RetrievalParams(
                prf=False, prf_docs=1, prf_terms=2, prf_lambda=0.9
            ),
        )
        assert empty_obj == plain
        assert flag_off_with_knobs == plain
        # One call per semantic_search, never two: the second embed is the
        # flag-gated D-012 exception and the flag is off.
        assert embed_calls == [probe, probe, probe]

    def test_flag_off_issues_zero_term_df_selects(self, prf_db):
        from cairn.graph.semantic import RetrievalParams, semantic_search

        with _StatementRecorder(prf_db) as rec:
            semantic_search(prf_db, "gizmo", limit=10)
            semantic_search(prf_db, "gizmo", limit=10, params=RetrievalParams())
            semantic_search(
                prf_db,
                "gizmo",
                limit=10,
                params=RetrievalParams(prf=False, prf_docs=1),
            )
        assert rec.term_df_selects() == []

    def test_knobs_without_the_flag_are_inert(self, monkeypatch, prf_db):
        from cairn.graph.semantic import RetrievalParams, semantic_search

        embed_calls = _install_embed_spy(monkeypatch)
        knobs_only = semantic_search(
            prf_db,
            "gizmo",
            limit=10,
            params=RetrievalParams(
                prf_docs=1, prf_terms=1, prf_lambda=0.1, **SPARSE_ONLY
            ),
        )
        sparse_plain = semantic_search(
            prf_db, "gizmo", limit=10, params=RetrievalParams(**SPARSE_ONLY)
        )
        assert knobs_only == sparse_plain
        assert embed_calls == ["gizmo", "gizmo"]


class TestPrfWiring:
    """Flag-on: the expansion is consulted and consumed, the second full
    pass runs exactly once, and its candidates replace the first's."""

    def test_second_pass_embeds_expanded_text_once(self, monkeypatch, prf_db):
        from cairn.graph.semantic import RetrievalParams, semantic_search

        embed_calls = _install_embed_spy(monkeypatch)
        captured = _install_expand_spy(monkeypatch)
        results = semantic_search(
            prf_db,
            "gizmo",
            limit=10,
            params=RetrievalParams(prf=True, **SPARSE_ONLY),
        )
        assert results, "second pass must return results"
        assert len(captured) == 1, "exactly one expansion per call"
        expansion = captured[0]["result"]
        assert expansion.terms, "the corpus must yield a non-empty expansion"
        # Exactly TWO embeds: the first pass on the raw query, the second
        # (the D-012 exception) on the expanded dense text -- never more.
        assert embed_calls == ["gizmo", expansion.dense_query]
        assert expansion.dense_query.startswith("gizmo ")
        assert set(expansion.dense_query.split()[1:]) == set(expansion.terms)

    def test_feedback_docs_are_the_fused_top_candidate_texts(
        self, monkeypatch, prf_db
    ):
        from cairn.graph.semantic import RetrievalParams, semantic_search

        # The flag-off first pass pins what the fused top-k is here.
        off = semantic_search(
            prf_db, "gizmo", limit=10, params=RetrievalParams(**SPARSE_ONLY)
        )
        assert [r["name"] for r in off] == ["gizmo_polish"]
        # BM25-only candidates carry chunk="" -- their feedback text is
        # prf.py's documented fallback: name + " " + qualified_name.
        top = off[0]
        assert top["chunk"] == ""
        expected_fb = f"{top['name']} {top['qualified_name']}"

        captured = _install_expand_spy(monkeypatch)
        semantic_search(
            prf_db,
            "gizmo",
            limit=10,
            params=RetrievalParams(prf=True, prf_docs=10, **SPARSE_ONLY),
        )
        assert len(captured) == 1
        # prf.py's consumer contract: the caller slices the top prf_docs
        # candidate TEXTS, in rank order.
        assert captured[0]["query"] == "gizmo"
        assert captured[0]["feedback_docs"] == [expected_fb]

    def test_feedback_docs_use_the_chunk_when_present(self, monkeypatch, prf_db):
        """Dense-leg hits carry real chunks -- those feed the expansion
        verbatim (the recommended extraction, no fallback)."""
        from cairn.graph.semantic import RetrievalParams, semantic_search

        off = semantic_search(prf_db, "gizmo", limit=10)  # dense leg live
        assert off and off[0]["chunk"], "dense hits carry chunks"
        top_chunk = off[0]["chunk"]

        captured = _install_expand_spy(monkeypatch)
        on = semantic_search(
            prf_db, "gizmo", limit=10, params=RetrievalParams(prf=True)
        )
        assert len(captured) == 1
        assert captured[0]["feedback_docs"][0] == top_chunk
        assert on, "full-path PRF returns results"

    def test_expansion_terms_join_the_sparse_leg_of_the_second_pass(
        self, monkeypatch, prf_db
    ):
        from cairn.graph.semantic import RetrievalParams, semantic_search

        raw_calls, term_calls = _install_fetch_spies(monkeypatch)
        captured = _install_expand_spy(monkeypatch)
        semantic_search(
            prf_db,
            "gizmo",
            limit=10,
            params=RetrievalParams(prf=True, **SPARSE_ONLY),
        )
        expansion = captured[0]["result"]
        # First pass: the raw-query sparse fetch. Second pass: term mode
        # carrying exactly the expansion terms (enrich off adds none).
        assert raw_calls == ["gizmo"]
        assert term_calls == [list(expansion.terms)]

    def test_prf_surfaces_the_symbol_the_first_pass_missed(self, prf_db):
        from cairn.graph.semantic import RetrievalParams, semantic_search

        off = semantic_search(
            prf_db, "gizmo", limit=10, params=RetrievalParams(**SPARSE_ONLY)
        )
        on = semantic_search(
            prf_db,
            "gizmo",
            limit=10,
            params=RetrievalParams(prf=True, **SPARSE_ONLY),
        )
        # The raw query can never fetch polisher_rig; the polish
        # expansion term prefix-fetches it (df 1/2 keeps it above the
        # lambda cap) -- the expansion demonstrably changed the retrieval.
        assert "polisher_rig" not in [r["name"] for r in off]
        assert "polisher_rig" in [r["name"] for r in on]
        assert "gizmo_polish" in [r["name"] for r in on]

    def test_df_lookup_is_term_df_backed_and_knobs_thread_through(
        self, monkeypatch, prf_db
    ):
        from cairn.graph.semantic import RetrievalParams, semantic_search

        captured = _install_expand_spy(monkeypatch)
        semantic_search(
            prf_db,
            "gizmo",
            limit=10,
            params=RetrievalParams(
                prf=True, prf_docs=1, prf_terms=1, prf_lambda=0.9, **SPARSE_ONLY
            ),
        )
        call = captured[0]
        # None-means-default resolves to the D-002 anchors at the boundary.
        assert call["fb_terms"] == 1
        assert call["fb_lambda"] == 0.9
        # The DF signal is T013's builder over the persisted term_df table
        # (both symbols carry the qualified-name token ``mod``).
        assert call["df_lookup"] is not None
        assert call["df_lookup"]("mod") == (2, 2)
        assert call["df_lookup"]("no-such-token") is None
        # fb_terms=1 caps the expansion at one term.
        assert len(call["result"].terms) == 1

    def test_df_lookup_reads_are_visible_and_bounded(self, prf_db):
        from cairn.graph.semantic import RetrievalParams, semantic_search

        with _StatementRecorder(prf_db) as rec:
            semantic_search(
                prf_db,
                "gizmo",
                limit=10,
                params=RetrievalParams(prf=True, **SPARSE_ONLY),
            )
        # The expansion's lookups are indexed PK probes over term_df (the
        # memoized per-lookup dict keeps repeats to one SELECT per token).
        assert rec.term_df_selects(), "PRF-on must read term_df"
        assert all(
            "WHERE token = " in s for s in rec.term_df_selects()
        ), "per-term indexed probes, never a scan"


class TestPrfReplacesRerank:
    """TC-019 (wiring level): a PRF combo never reaches the rerank stage
    -- D-012's replaces-not-stacks doctrine."""

    def test_rerank_never_runs_on_prf_combo_even_when_armed(
        self, monkeypatch, prf_db
    ):
        from cairn.graph import reranker as rrk
        from cairn.graph.semantic import RetrievalParams, semantic_search

        monkeypatch.setenv("CAIRN_RERANK", "1")

        # Control: without PRF the armed stage DOES run (the hash backend
        # disables the confidence gate, so nothing else could skip it) --
        # proving the spy and enablement are live.
        control_rec = _RerankRecorder()
        monkeypatch.setattr(rrk, "rerank", control_rec)
        control = semantic_search(
            prf_db, "gizmo", limit=10, params=RetrievalParams(**SPARSE_ONLY)
        )
        assert len(control_rec.calls) == 1, "control: the stage must run without PRF"
        assert any(r.get("rerank_score") for r in control)

        # Treatment 1: PRF + the stage armed by env.
        prf_rec = _RerankRecorder()
        monkeypatch.setattr(rrk, "rerank", prf_rec)
        on = semantic_search(
            prf_db, "gizmo", limit=10, params=RetrievalParams(prf=True, **SPARSE_ONLY)
        )
        assert prf_rec.calls == [], "PRF combo must never reach the rerank stage"
        assert on, "results still returned"
        assert all(r["reranked"] is False for r in on)
        assert all("rerank_score" not in r for r in on)

        # Treatment 2: the caller ALSO sets rerank=True -- PRF wins, the
        # stage is still skipped (documented resolution, never an error).
        both_rec = _RerankRecorder()
        monkeypatch.setattr(rrk, "rerank", both_rec)
        both = semantic_search(
            prf_db,
            "gizmo",
            limit=10,
            rerank=True,
            params=RetrievalParams(prf=True, **SPARSE_ONLY),
        )
        assert both_rec.calls == [], "PRF wins over an explicit rerank=True"
        assert both == on, "explicit rerank=True changes nothing under PRF"


class TestPrfDeterminismAndDegenerate:
    """TC-017 + the bounded fallback: offline determinism, and the
    degenerate empty-expansion path."""

    def test_two_runs_byte_identical_with_network_disabled(
        self, monkeypatch, prf_db
    ):
        from cairn.graph.semantic import RetrievalParams, semantic_search

        def _no_network(*_a, **_k):
            raise AssertionError("PRF wiring attempted a network call")

        monkeypatch.setattr(socket, "socket", _no_network)
        monkeypatch.setenv("CAIRN_LLM_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "secret")
        params = RetrievalParams(prf=True, **SPARSE_ONLY)
        first = semantic_search(prf_db, "gizmo", limit=10, params=params)
        second = semantic_search(prf_db, "gizmo", limit=10, params=params)
        assert first == second
        assert first, "non-degenerate corpus must return results"

    def test_empty_expansion_skips_the_second_pass(self, monkeypatch, prf_db):
        from cairn.graph.semantic import RetrievalParams, semantic_search

        embed_calls = _install_embed_spy(monkeypatch)
        captured = _install_expand_spy(monkeypatch)

        plain = semantic_search(
            prf_db, "gizmo", limit=10, params=RetrievalParams(**SPARSE_ONLY)
        )
        assert embed_calls == ["gizmo"]
        embed_calls.clear()

        # prf_docs=0 clamps to empty feedback -> empty expansion -> the
        # bounded fallback: NO second pass (zero extra embeds), results
        # byte-identical to flag-off.
        degenerate = semantic_search(
            prf_db,
            "gizmo",
            limit=10,
            params=RetrievalParams(prf=True, prf_docs=0, **SPARSE_ONLY),
        )
        assert len(captured) == 1
        assert captured[0]["result"].terms == ()
        assert captured[0]["result"].dense_query == "gizmo"
        assert embed_calls == ["gizmo"]
        assert degenerate == plain

    def test_negative_prf_docs_clamp_to_empty_not_worst_tail(
        self, monkeypatch, prf_db
    ):
        from cairn.graph.semantic import RetrievalParams, semantic_search

        captured = _install_expand_spy(monkeypatch)
        semantic_search(
            prf_db,
            "gizmo",
            limit=10,
            params=RetrievalParams(prf=True, prf_docs=-3, **SPARSE_ONLY),
        )
        # A plain [:n] slice with n=-3 would keep all-but-3 candidates;
        # the clamp yields empty feedback (the harness-bug doctrine).
        assert captured[0]["feedback_docs"] == []
        assert captured[0]["result"].terms == ()


class TestPrfParamsAdditive:
    """The additive-field doctrine on the four new fields."""

    def test_new_fields_default_to_none(self):
        from cairn.graph.semantic import RetrievalParams

        p = RetrievalParams()
        assert p.prf is None
        assert p.prf_docs is None
        assert p.prf_terms is None
        assert p.prf_lambda is None

    def test_out_of_range_lambda_propagates_expand_contract(self, prf_db):
        """prf_lambda outside [0, 1] is prf.expand's ValueError (its
        published contract), surfaced unchanged by the boundary."""
        from cairn.graph.semantic import RetrievalParams, semantic_search

        with pytest.raises(ValueError, match="fb_lambda"):
            semantic_search(
                prf_db,
                "gizmo",
                limit=10,
                params=RetrievalParams(prf=True, prf_lambda=1.5, **SPARSE_ONLY),
            )
