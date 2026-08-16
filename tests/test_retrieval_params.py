"""RetrievalParams explicit injection (T003, D-008, FR-005).

``RetrievalParams`` is the frozen, explicit tunable object threaded
``run_evaluation -> semantic_search`` -- the injection channel the quality
sweep uses instead of mutating the environment (in-process env writes would
leak across lever combinations). Two contracts are pinned here:

* **defaults-off equivalence** -- ``params=None`` and ``RetrievalParams()``
  (every field ``None``) produce byte-identical result lists, because a
  ``None`` field resolves to today's exact value at every knob site;
* **knob turns** -- an injected field demonstrably changes behavior:
  ``dense_threshold=0.0`` admits a candidate the 0.3 default filters,
  ``rrf_weights`` reorders the fused ranking by flipping leg weights,
  ``rrf_k`` rescales the fused scores, the pool knobs cap the cosine
  scan and the brute-force fetch, and the rerank/gate fields reach
  their stages. T010 adds the NEW BM25-leg lever ``sparse_top_n``
  (rank-position cutoff before fusion) with its own filter and
  defaults-off-equivalence proofs.

Hermetic like tests/test_rerank_gating.py: the hash embedder gives
deterministic vectors (cosines quoted in the fixtures below were probed,
not guessed), and the brute cosine path is forced so sqlite-vec presence
is irrelevant.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

# Dep-free deterministic vectors for the whole module.
pytestmark = pytest.mark.usefixtures("hash_backend")


@pytest.fixture(autouse=True)
def _params_env(monkeypatch):
    """Deterministic knobs around every test.

    * brute scan forced (ANN presence must not change results);
    * no rerank enablement or margin env -- the gate tests that need them
      set CAIRN_RERANK explicitly (mirroring test_rerank_gating.py);
    * the persistent rerank auto-enable marker neutralized: on a dev
      machine with a real ``~/.cairn/rerank_enabled`` (i.e. a downloaded
      reranker), a process where cairn.paths resolved CAIRN_HOME before
      conftest's sandbox applied would otherwise run the REAL cross-encoder
      under these exact-order assertions (the test_rerank_gating.py
      discipline);
    * no CAIRN_FUSION override -- fusion defaults ON, the production path
      the equivalence contract must hold on. Tests isolating the cosine
      filter set it to "0" themselves.
    """
    from cairn.graph import reranker as rrk

    monkeypatch.setattr(
        rrk, "_rerank_marker_path", lambda: Path("/nonexistent/cairn-test-marker")
    )
    monkeypatch.setenv("CAIRN_ANN_BACKEND", "off")
    monkeypatch.delenv("CAIRN_RERANK", raising=False)
    monkeypatch.delenv("CAIRN_RERANK_MIN_MARGIN", raising=False)
    monkeypatch.delenv("CAIRN_FUSION", raising=False)
    yield


# ---------------------------------------------------------------------------
# Fixture corpus
#
# Three symbols probed under the hash backend (query "function alpha"):
#   alpha          -- chunk tokens {file, tmp, test, k, kt, function, alpha},
#                     cosine 0.4901 (>= 0.3); BM25 name/qual match.
#   alphaBulk      -- 80 unique docstring tokens flood the chunk: cosine
#                     0.0801 (< 0.3); BM25 name match ("alphaBulk" starts
#                     with the FTS prefix "alpha*").
#   vectorOnlyNode -- "alpha" lives only in the FILE PATH (chunk contains
#                     it; the FTS columns name/qualified_name/docstring do
#                     not): cosine 0.4197, BM25-invisible.
# ---------------------------------------------------------------------------


def _seed_corpus(conn) -> None:
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('t', 't', '/tmp/t')")
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES (1, 't', '/tmp/test/K.kt', 'kotlin')"
    )
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES (2, 't', '/tmp/alpha/V.kt', 'kotlin')"
    )
    long_doc = " ".join(f"w{i}" for i in range(80))
    rows = [
        (1, 1, "alpha", "alpha", None),
        (2, 1, "alphaBulk", "x.alphaBulk", long_doc),
        (3, 2, "vectorOnlyNode", "x.vectorOnlyNode", None),
    ]
    for sid, fid, name, qual, doc in rows:
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, qualified_name, docstring, line_start, line_end) "
            "VALUES (?, ?, ?, 'function', ?, ?, 1, 10)",
            (sid, fid, name, qual, doc),
        )
    conn.commit()


@pytest.fixture()
def seeded_db(fresh_db):
    """The three-symbol corpus, embedded with the deterministic hash backend."""
    from cairn.graph import embeddings as emb

    _seed_corpus(fresh_db)
    emb.embed_all(fresh_db)
    return fresh_db


def _seed_decisive(conn) -> None:
    """The gating fixture from test_rerank_gating.py: querying
    ``safeApiCall`` yields a wide fused margin (~0.53) plus an exact-name
    #1 -- the confidence gate's skip shape."""
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('test', 'test', '/tmp/test')")
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES (1, 'test', '/tmp/test/Api.kt', 'kotlin')"
    )
    rows = [
        (1, "safeApiCall", "xyz.safeApiCall", "Retries a network call with backoff."),
        (2, "formatDate", "xyz.formatDate", "Formats a date for display."),
        (3, "drawBorder", "xyz.drawBorder", "Draws a border around a view."),
        (4, "parseJsonConfig", "xyz.parseJsonConfig", "Parses the JSON config file."),
        (5, "sortItems", "xyz.sortItems", "Sorts items by their priority."),
        (6, "validateInput", "xyz.validateInput", "Validates user input fields."),
    ]
    for sid, name, qual, doc in rows:
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, qualified_name, docstring, line_start, line_end) "
            "VALUES (?, 1, ?, 'function', ?, ?, 1, 10)",
            (sid, name, qual, doc),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Dataclass contract
# ---------------------------------------------------------------------------


class TestRetrievalParamsContract:
    def test_all_fields_default_none(self):
        """The None-means-default contract: an all-None object carries no
        opinion about any knob, which is what makes it behaviorally inert."""
        from cairn.graph.semantic import RetrievalParams

        p = RetrievalParams()
        for field in dataclasses.fields(p):
            assert getattr(p, field.name) is None, field.name

    def test_frozen_immutable(self):
        from cairn.graph.semantic import RetrievalParams

        p = RetrievalParams(dense_threshold=0.1)
        with pytest.raises(dataclasses.FrozenInstanceError):
            p.dense_threshold = 0.9
        assert p.dense_threshold == 0.1

    def test_hashable(self):
        """Frozen + eq gives hashability: a params object can key result
        caches / dedupe sweep combinations."""
        from cairn.graph.semantic import RetrievalParams

        assert hash(RetrievalParams(rrf_weights=(1.0, 0.0))) == hash(
            RetrievalParams(rrf_weights=(1.0, 0.0))
        )
        assert RetrievalParams() != RetrievalParams(rrf_k=10)


# ---------------------------------------------------------------------------
# Defaults-off equivalence (the FR-005 defaults-preserving contract)
# ---------------------------------------------------------------------------


class TestDefaultsOffEquivalence:
    @pytest.mark.parametrize("fusion", ["0", "1"])
    @pytest.mark.parametrize("query", ["function alpha", "alpha"])
    def test_empty_params_object_is_byte_identical(
        self, monkeypatch, seeded_db, fusion, query
    ):
        """params=None and RetrievalParams() (all-None fields) must return
        identical result lists -- same ids, scores, provenance -- on both
        the fusion path (RRF scores, the production default) and the pure
        cosine path, for an identifier-shaped and a sentence query."""
        from cairn.graph.semantic import RetrievalParams, semantic_search

        monkeypatch.setenv("CAIRN_FUSION", fusion)
        plain = semantic_search(seeded_db, query, limit=10)
        injected = semantic_search(seeded_db, query, limit=10, params=RetrievalParams())
        assert injected == plain

    def test_partially_set_object_leaves_unset_knobs_at_defaults(
        self, monkeypatch, seeded_db
    ):
        """Setting ONE field must not perturb the others: threshold=0.0
        with everything else None behaves exactly like the scalar
        threshold=0.0 call (the arg it overrides)."""
        from cairn.graph.semantic import RetrievalParams, semantic_search

        monkeypatch.setenv("CAIRN_FUSION", "0")
        via_arg = semantic_search(seeded_db, "function alpha", limit=10, threshold=0.0)
        via_params = semantic_search(
            seeded_db,
            "function alpha",
            limit=10,
            params=RetrievalParams(dense_threshold=0.0),
        )
        assert via_params == via_arg


# ---------------------------------------------------------------------------
# Knob turns (an injected field demonstrably changes behavior)
# ---------------------------------------------------------------------------


class TestDenseThresholdKnob:
    def test_threshold_zero_admits_a_candidate_the_default_filters(
        self, monkeypatch, seeded_db
    ):
        """alphaBulk's cosine is 0.0801: the 0.3 default filters it out;
        dense_threshold=0.0 admits it. Fusion off isolates the cosine
        filter (BM25-only candidates bypass it)."""
        from cairn.graph.semantic import RetrievalParams, semantic_search

        monkeypatch.setenv("CAIRN_FUSION", "0")
        default = semantic_search(seeded_db, "function alpha", limit=10)
        widened = semantic_search(
            seeded_db,
            "function alpha",
            limit=10,
            params=RetrievalParams(dense_threshold=0.0),
        )

        default_names = [r["name"] for r in default]
        widened_names = [r["name"] for r in widened]
        assert default_names == ["alpha", "vectorOnlyNode"]
        assert "alphaBulk" not in default_names, "fixture drift: 0.3 no longer filters"
        assert widened_names == ["alpha", "vectorOnlyNode", "alphaBulk"]

    def test_higher_threshold_filters_a_candidate_the_default_admits(
        self, monkeypatch, seeded_db
    ):
        from cairn.graph.semantic import RetrievalParams, semantic_search

        monkeypatch.setenv("CAIRN_FUSION", "0")
        tightened = semantic_search(
            seeded_db,
            "function alpha",
            limit=10,
            params=RetrievalParams(dense_threshold=0.45),
        )
        # vectorOnlyNode (0.4197) drops below the injected 0.45.
        assert [r["name"] for r in tightened] == ["alpha"]


class TestRRFWeightsKnob:
    """Fusion ON, query ``alpha`` (single token, so BOTH legs are
    non-empty -- a sentence query degenerates to an empty BM25 leg via
    today's quoted-phrase FTS defect, the T007 survey finding).

    Leg memberships: BM25 = [alpha, alphaBulk] (FTS prefix ``alpha*`` +
    LIKE substring both hit the names); vector pool (threshold 0.0) =
    [alpha, vectorOnlyNode, alphaBulk] by cosine. Swapping the (dense,
    sparse) weights flips which leg orders the tail -- vectorOnlyNode and
    alphaBulk trade places, and the leg-excluded candidate scores 0.0.
    """

    @staticmethod
    def _order(seeded_db, dense_w, sparse_w):
        from cairn.graph.semantic import RetrievalParams, semantic_search

        res = semantic_search(
            seeded_db,
            "alpha",
            limit=10,
            params=RetrievalParams(
                dense_threshold=0.0, rrf_weights=(dense_w, sparse_w)
            ),
        )
        return [(r["name"], r["score"]) for r in res]

    def test_pure_dense_orders_by_vector_leg(self, seeded_db):
        order = self._order(seeded_db, 1.0, 0.0)
        assert [name for name, _ in order] == [
            "alpha",
            "vectorOnlyNode",
            "alphaBulk",
        ]

    def test_pure_sparse_orders_by_bm25_leg(self, seeded_db):
        order = self._order(seeded_db, 0.0, 1.0)
        assert [name for name, _ in order] == ["alpha", "alphaBulk", "vectorOnlyNode"]
        # vectorOnlyNode is BM25-invisible: a zero dense weight zeroes its
        # only contributing leg.
        by_name = dict(order)
        assert by_name["vectorOnlyNode"] == 0.0
        assert by_name["alphaBulk"] > 0.0  # genuine BM25-leg contribution

    def test_weights_flip_the_tail_order(self, seeded_db):
        """The knob-turn proof: same fixture, same scores formula, only the
        weight tuple differs -- vectorOnlyNode and alphaBulk swap ranks."""
        dense_order = [n for n, _ in self._order(seeded_db, 1.0, 0.0)]
        sparse_order = [n for n, _ in self._order(seeded_db, 0.0, 1.0)]
        assert dense_order != sparse_order
        assert dense_order.index("vectorOnlyNode") < dense_order.index("alphaBulk")
        assert sparse_order.index("alphaBulk") < sparse_order.index("vectorOnlyNode")


class TestRRFKKnob:
    def test_k_rescales_fused_scores(self, seeded_db):
        """k=1 instead of the hard-coded 60: the pure-dense top score
        becomes 1/(1+1) = 0.5 instead of 1/(60+1) ~ 0.0164 -- the constant
        demonstrably reaches rrf_fuse."""
        from cairn.graph.semantic import RetrievalParams, semantic_search

        params = RetrievalParams(
            dense_threshold=0.0, rrf_weights=(1.0, 0.0), rrf_k=1
        )
        res = semantic_search(seeded_db, "function alpha", limit=10, params=params)
        by_name = {r["name"]: r["score"] for r in res}
        # vec ranks 1/2/3 -> 1/2, 1/3, 1/4.
        assert by_name["alpha"] == 0.5
        assert by_name["vectorOnlyNode"] == 0.3333
        assert by_name["alphaBulk"] == 0.25


# ---------------------------------------------------------------------------
# Scan-side pool knobs (T010): rerank_pool reaches BOTH computed-pool
# branches; dense_pool reaches the brute-force SQL fetch cap.
# ---------------------------------------------------------------------------


class TestPoolKnobs:
    """Fusion off isolates the dense scan -- BM25-only candidates bypass
    the pool entirely, so result counts pin exactly what the cosine scan
    sliced to."""

    def test_rerank_pool_caps_the_scan_rerank_off(self, monkeypatch, seeded_db):
        """The rerank-off branch computes pool_size = limit (10 >= corpus),
        so the default returns all three above-threshold candidates;
        rerank_pool=2 replaces that computed size and the scan keeps only
        the top-2 cosines (alpha 0.4901, vectorOnlyNode 0.4197)."""
        from cairn.graph.semantic import RetrievalParams, semantic_search

        monkeypatch.setenv("CAIRN_FUSION", "0")
        default = semantic_search(
            seeded_db,
            "function alpha",
            limit=10,
            params=RetrievalParams(dense_threshold=0.0),
        )
        capped = semantic_search(
            seeded_db,
            "function alpha",
            limit=10,
            params=RetrievalParams(dense_threshold=0.0, rerank_pool=2),
        )
        assert len(default) == 3
        assert [r["name"] for r in capped] == ["alpha", "vectorOnlyNode"]

    def test_rerank_pool_overrides_the_wide_rerank_branch(
        self, armed_gate, monkeypatch
    ):
        """The rerank-ON branch computes pool_size = max(limit*5, 50) -- a
        deliberately wider pool the override must ALSO replace (both
        branches, per the field contract). The recorder sees the candidate
        list the stage was handed. Expected default: 5 of the 6 symbols --
        formatDate's signed-dim hash-vector collision with the query puts
        its cosine below 0.0 (the hash embedder hashes tokens to signed
        dims, so token-disjoint texts are not guaranteed non-negative),
        while the four exactly-0.0 rows pass a 0.0 threshold."""
        from cairn.graph.semantic import RetrievalParams, semantic_search

        conn, rec = armed_gate
        monkeypatch.setenv("CAIRN_FUSION", "0")
        semantic_search(
            conn,
            "safeApiCall",
            limit=5,
            params=RetrievalParams(dense_threshold=0.0),
        )
        assert rec.calls[0]["n_candidates"] == 5
        rec.calls.clear()
        semantic_search(
            conn,
            "safeApiCall",
            limit=5,
            params=RetrievalParams(dense_threshold=0.0, rerank_pool=2),
        )
        assert rec.calls[0]["n_candidates"] == 2

    def test_dense_pool_caps_the_brute_force_fetch(self, monkeypatch, seeded_db):
        """dense_pool is the SQL LIMIT on the brute-force embedding fetch
        (hard-coded 50000 today): LIMIT 1 means exactly one embedding row
        is ever scanned, so exactly one candidate survives -- regardless of
        which row the planner picks (count is planner-independent)."""
        from cairn.graph.semantic import RetrievalParams, semantic_search

        monkeypatch.setenv("CAIRN_FUSION", "0")
        default = semantic_search(
            seeded_db,
            "function alpha",
            limit=10,
            params=RetrievalParams(dense_threshold=0.0),
        )
        capped = semantic_search(
            seeded_db,
            "function alpha",
            limit=10,
            params=RetrievalParams(dense_threshold=0.0, dense_pool=1),
        )
        assert len(default) == 3
        assert len(capped) == 1
        assert capped[0]["name"] in {"alpha", "alphaBulk", "vectorOnlyNode"}


class TestSparseLimitKnob:
    def test_sparse_limit_reaches_search_symbols(self, monkeypatch, seeded_db):
        """A spy on the module-level ``search_symbols`` reference (what
        semantic.py's fusion block calls) sees the BM25 fetch limit: the
        hard-coded 30 without params, the injected value with them."""
        from cairn.graph import semantic as semantic_mod
        from cairn.graph.semantic import RetrievalParams, semantic_search

        seen = []
        real = semantic_mod.search_symbols

        def spy(conn, pattern, kind=None, limit=100):
            seen.append(limit)
            return real(conn, pattern, kind=kind, limit=limit)

        monkeypatch.setattr(semantic_mod, "search_symbols", spy)
        semantic_search(seeded_db, "function alpha", limit=10)
        semantic_search(
            seeded_db,
            "function alpha",
            limit=10,
            params=RetrievalParams(sparse_limit=7),
        )
        assert seen == [30, 7]


# ---------------------------------------------------------------------------
# The T010 NEW lever: sparse_top_n -- BM25-leg rank-position cutoff
# ---------------------------------------------------------------------------


class TestSparseTopNKnob:
    """A rank-position cutoff on the BM25 candidate list before fusion
    (NOT a score threshold: SQLite FTS5's bm25() rank is negative with
    better = more negative, and the LIKE-fallback rows carry no rank at
    all -- see the RetrievalParams field doc).

    Query ``alpha`` (both legs non-empty): BM25 = [alpha, alphaBulk];
    vector pool at threshold 0.0 = [alpha, vectorOnlyNode, alphaBulk].
    Equal weights, k=60. Default fused scores: alpha 2/61 = 0.0328,
    alphaBulk 1/62 + 1/63 = 0.032, vectorOnlyNode 1/62 = 0.0161 --
    alphaBulk's bm25 rank-2 term pushes it ABOVE vectorOnlyNode. Cutting
    the bm25 tail at N=1 removes alphaBulk's only sparse contribution;
    its bare vec rank-3 term (1/63 = 0.0159) falls below
    vectorOnlyNode's 1/62, so the two trade places.
    """

    @staticmethod
    def _run(seeded_db, **fields):
        from cairn.graph.semantic import RetrievalParams, semantic_search

        params = RetrievalParams(dense_threshold=0.0, **fields)
        res = semantic_search(seeded_db, "alpha", limit=10, params=params)
        return [(r["name"], r["score"]) for r in res]

    def test_default_keeps_the_full_bm25_leg(self, seeded_db):
        """Baseline (lever off): today's behavior -- the whole fetched
        BM25 list reaches rrf_fuse and alphaBulk outranks vectorOnlyNode
        on its rank-2 sparse term."""
        order = self._run(seeded_db)
        assert [n for n, _ in order] == ["alpha", "alphaBulk", "vectorOnlyNode"]
        by_name = dict(order)
        assert by_name["alpha"] == 0.0328
        assert by_name["alphaBulk"] == 0.032
        assert by_name["vectorOnlyNode"] == 0.0161
        assert by_name["alphaBulk"] > by_name["vectorOnlyNode"]

    def test_top_n_one_drops_the_bm25_tail(self, seeded_db):
        """The filter proof: same fixture, same query, ONLY sparse_top_n
        differs -- alphaBulk loses its bm25 rank-2 term (score collapses
        to the bare vec rank-3 term 1/63 = 0.0159) and drops below
        vectorOnlyNode."""
        default_scores = dict(self._run(seeded_db))
        cut = self._run(seeded_db, sparse_top_n=1)
        assert [n for n, _ in cut] == ["alpha", "vectorOnlyNode", "alphaBulk"]
        by_name = dict(cut)
        assert by_name["alphaBulk"] == 0.0159
        assert by_name["alphaBulk"] < default_scores["alphaBulk"]

    def test_top_n_zero_empties_the_sparse_leg(self, seeded_db):
        """N=0 is the sweep's sparse-off point: alpha keeps only its vec
        rank-1 term (1/61 = 0.0164) and the order is pure-vector."""
        cut = self._run(seeded_db, sparse_top_n=0)
        assert [n for n, _ in cut] == ["alpha", "vectorOnlyNode", "alphaBulk"]
        assert dict(cut)["alpha"] == 0.0164

    def test_negative_clamps_to_zero(self, seeded_db):
        """Documented clamp: a negative N is a harness bug, not an error
        worth failing a sweep run over -- and plain slicing with -3 would
        silently keep the WORST 3-by-tail instead of a cutoff."""
        assert self._run(seeded_db, sparse_top_n=-3) == self._run(
            seeded_db, sparse_top_n=0
        )

    def test_cuts_do_not_touch_the_dense_leg(self, seeded_db):
        """The lever is scoped to the sparse leg: the vector-only
        candidate vectorOnlyNode survives every cut with its score
        unchanged (always the vec rank-2 term 1/62)."""
        for n in (None, 1, 0):
            assert dict(self._run(seeded_db, sparse_top_n=n))[
                "vectorOnlyNode"
            ] == 0.0161

    def test_defaults_off_equivalence_on_the_fixture(self, seeded_db):
        """The FR-005 defaults-preserving contract on THIS lever's fixture:
        an all-None params object is byte-identical to no params."""
        from cairn.graph.semantic import RetrievalParams, semantic_search

        plain = semantic_search(seeded_db, "alpha", limit=10)
        injected = semantic_search(
            seeded_db, "alpha", limit=10, params=RetrievalParams()
        )
        assert injected == plain


# ---------------------------------------------------------------------------
# The T008 lever: enrich -- sparse-leg term mode (FR-001)
#
# With params.enrich=True the BM25 fetch consumes query_enrich's term list
# through lexical.search_symbols_terms (OR of quoted prefixes) instead of
# the raw sentence through search_symbols (ONE quoted phrase -- the
# empty-BM25 defect). Dense leg + gate interplay are T009's; here only the
# fetch input changes.
# ---------------------------------------------------------------------------


class TestEnrichSparseLeg:
    """Query ``function alpha`` (the sentence shape): today's sparse leg is
    EMPTY (the phrase '"function alpha"*' matches no indexed token and the
    whole-sentence LIKE union neither), while enrichment yields the term
    list ``["alpha"]`` whose MATCH expression is exactly '"alpha"*' -- the
    same fetch the bare ``alpha`` query makes today."""

    @staticmethod
    def _run(seeded_db, **fields):
        from cairn.graph.semantic import RetrievalParams, semantic_search

        params = RetrievalParams(dense_threshold=0.0, rrf_weights=(0.0, 1.0), **fields)
        return [
            (r["name"], r["score"]) for r in semantic_search(seeded_db, "function alpha", limit=10, params=params)
        ]

    def test_off_is_today_behavior_and_flag_none_matches_no_params(self, seeded_db):
        """enrich=None (inside an otherwise-set object) and enrich=False are
        byte-identical to no params: the empty-BM25 defect status quo -- a
        pure-sparse weighting scores every candidate 0.0 because the leg is
        empty for a sentence query."""
        from cairn.graph.semantic import RetrievalParams, semantic_search

        plain = [
            (r["name"], r["score"])
            for r in semantic_search(
                seeded_db, "function alpha", limit=10,
                params=RetrievalParams(dense_threshold=0.0, rrf_weights=(0.0, 1.0)),
            )
        ]
        assert plain == self._run(seeded_db)  # enrich=None inside the object
        assert plain == self._run(seeded_db, enrich=False)
        assert all(score == 0.0 for _, score in plain), "fixture drift: BM25 leg unexpectedly non-empty"

    def test_on_bm25_leg_contributes_for_sentence_queries(self, seeded_db):
        """The defect FIXED under the flag: with enrich=True the same
        sentence's BM25 leg carries [alpha, alphaBulk] (the '"alpha"*'
        prefix matches both names), so pure-sparse weighting produces
        genuine leg scores -- alpha 1/61, alphaBulk 1/62 -- while
        vectorOnlyNode stays 0.0 (BM25-invisible)."""
        order = self._run(seeded_db, enrich=True)
        assert [n for n, _ in order] == ["alpha", "alphaBulk", "vectorOnlyNode"]
        by_name = dict(order)
        assert by_name["alpha"] == 0.0164
        assert by_name["alphaBulk"] == 0.0161
        assert by_name["vectorOnlyNode"] == 0.0
        assert by_name["alphaBulk"] > by_name["vectorOnlyNode"]  # leg contributes

    def test_on_equals_the_bare_term_query_today(self, seeded_db):
        """Equivalence anchor: enrich=True on the sentence reproduces exactly
        what today's code does for the single-token query ``alpha`` -- the
        enrichment's term fetch builds the identical MATCH expression
        ('"alpha"*'), so every downstream score matches."""
        from cairn.graph.semantic import RetrievalParams, semantic_search

        params = RetrievalParams(dense_threshold=0.0, rrf_weights=(0.0, 1.0))
        bare = [
            (r["name"], r["score"])
            for r in semantic_search(seeded_db, "alpha", limit=10, params=params)
        ]
        assert self._run(seeded_db, enrich=True) == bare

    def test_terms_flow_through_search_symbols_terms_not_search_symbols(
        self, monkeypatch, seeded_db
    ):
        """Wiring proof: under the flag the sparse fetch goes to
        search_symbols_terms with the enrichment's term list; the string
        path (search_symbols) is not called at all."""
        from cairn.graph import semantic as semantic_mod
        from cairn.graph.semantic import RetrievalParams, semantic_search

        term_calls = []
        str_calls = []
        real_terms = semantic_mod.search_symbols_terms
        real_str = semantic_mod.search_symbols

        def spy_terms(conn, terms, kind=None, limit=100):
            term_calls.append({"terms": list(terms), "limit": limit})
            return real_terms(conn, terms, kind=kind, limit=limit)

        def spy_str(conn, pattern, kind=None, limit=100):
            str_calls.append(pattern)
            return real_str(conn, pattern, kind=kind, limit=limit)

        monkeypatch.setattr(semantic_mod, "search_symbols_terms", spy_terms)
        monkeypatch.setattr(semantic_mod, "search_symbols", spy_str)
        semantic_search(
            seeded_db,
            "function alpha",
            limit=10,
            params=RetrievalParams(dense_threshold=0.0, enrich=True),
        )
        assert term_calls == [{"terms": ["alpha"], "limit": 30}]
        assert str_calls == []

    def test_sparse_limit_reaches_the_term_mode_fetch(self, monkeypatch, seeded_db):
        """T010's limit threading covers the term path too: the fetch-limit
        spy sees the hard-coded 30 without params and the injected 7 with
        them (the fetch limit, not the display limit)."""
        from cairn.graph import semantic as semantic_mod
        from cairn.graph.semantic import RetrievalParams, semantic_search

        seen = []
        real = semantic_mod.search_symbols_terms

        def spy(conn, terms, kind=None, limit=100):
            seen.append(limit)
            return real(conn, terms, kind=kind, limit=limit)

        monkeypatch.setattr(semantic_mod, "search_symbols_terms", spy)
        semantic_search(
            seeded_db, "function alpha", limit=10, params=RetrievalParams(enrich=True)
        )
        semantic_search(
            seeded_db,
            "function alpha",
            limit=10,
            params=RetrievalParams(enrich=True, sparse_limit=7),
        )
        assert seen == [30, 7]

    def test_all_stopword_query_keeps_the_raw_query_fetch(
        self, monkeypatch, seeded_db
    ):
        """EnrichedQuery's documented boundary: when every token is a
        stopword (``where is the function``) the sparse leg must NOT search
        for the empty string -- it falls back to today's raw-query fetch
        through search_symbols."""
        from cairn.graph import semantic as semantic_mod
        from cairn.graph.semantic import RetrievalParams, semantic_search

        term_calls = []
        str_calls = []
        real_terms = semantic_mod.search_symbols_terms
        real_str = semantic_mod.search_symbols

        monkeypatch.setattr(
            semantic_mod,
            "search_symbols_terms",
            lambda conn, terms, kind=None, limit=100: (
                term_calls.append(list(terms)) or real_terms(conn, terms, kind=kind, limit=limit)
            ),
        )

        def spy_str(conn, pattern, kind=None, limit=100):
            str_calls.append(pattern)
            return real_str(conn, pattern, kind=kind, limit=limit)

        monkeypatch.setattr(semantic_mod, "search_symbols", spy_str)
        semantic_search(
            seeded_db,
            "where is the function",
            limit=10,
            params=RetrievalParams(dense_threshold=0.0, enrich=True),
        )
        assert term_calls == []
        assert str_calls == ["where is the function"]


# ---------------------------------------------------------------------------
# Rerank / gate knobs (recorder proves the stage ran or was skipped)
# ---------------------------------------------------------------------------


class _RerankRecorder:
    """Stand-in for rrk.rerank (the test_rerank_gating.py pattern)."""

    def __init__(self):
        self.calls = []

    def __call__(self, query, candidates, limit):
        self.calls.append(
            {"query": query, "limit": limit, "n_candidates": len(candidates)}
        )
        out = [dict(c) for c in candidates[:limit]]
        for item in out:
            item["rerank_score"] = 0.5
        return out, True


@pytest.fixture()
def armed_gate(monkeypatch, fresh_db):
    """The decisive fixture with the rerank stage enabled and the recorder
    installed; returns (conn, recorder)."""
    from cairn.graph import embeddings as emb
    from cairn.graph import reranker as rrk
    from cairn.graph import semantic as semantic_mod

    _seed_decisive(fresh_db)
    emb.embed_all(fresh_db)
    monkeypatch.setattr(rrk, "_rerank_marker_path", lambda: Path("/nonexistent/marker"))
    monkeypatch.setattr(emb, "is_hash_fallback", lambda: False)
    # Arm the gate despite the hash env (mirrors test_rerank_gating.py).
    monkeypatch.setattr(
        semantic_mod, "_vectors_carry_token_overlap_only", lambda flag: False
    )
    monkeypatch.setenv("CAIRN_RERANK", "1")
    rec = _RerankRecorder()
    monkeypatch.setattr(rrk, "rerank", rec)
    return fresh_db, rec


class TestRerankAndGateKnobs:
    def test_default_gate_skips_on_decisive_margin(self, armed_gate):
        """Baseline for the knob tests: the fused margin (~0.53) clears the
        0.45 default, so the stage is skipped even with a params object
        present (its gate field is None)."""
        from cairn.graph.semantic import RetrievalParams, semantic_search

        conn, rec = armed_gate
        semantic_search(
            conn,
            "safeApiCall",
            limit=5,
            params=RetrievalParams(dense_threshold=0.0),
        )
        assert rec.calls == []

    def test_gate_min_margin_unattainable_keeps_the_stage(self, armed_gate):
        """gate_min_margin=1.0 (a fused ranking never has a perfect margin)
        blocks the skip that the 0.45 default makes -- the gate knob turns."""
        from cairn.graph.semantic import RetrievalParams, semantic_search

        conn, rec = armed_gate
        semantic_search(
            conn,
            "safeApiCall",
            limit=5,
            params=RetrievalParams(dense_threshold=0.0, gate_min_margin=1.0),
        )
        assert len(rec.calls) == 1, "margin override must reach the gate"

    def test_gate_min_margin_zero_forces_the_skip(self, armed_gate, monkeypatch):
        """A tight-margin query the default (0.45) reranks skips under
        margin=0 -- the knob works in both directions. The tight fixture
        ties everywhere, so the gate is the only variable."""
        from cairn.graph import embeddings as emb
        from cairn.graph.semantic import RetrievalParams, semantic_search

        conn, rec = armed_gate
        # Re-seed the tight shape on the second file slot: identical names
        # and docstrings make every signal tie (margin ~0).
        conn.execute(
            "INSERT INTO files (id, repo_id, path, language) VALUES (2, 'test', '/tmp/test/W.kt', 'kotlin')"
        )
        for i in range(8):
            conn.execute(
                "INSERT INTO symbols (id, file_id, name, kind, qualified_name, docstring, line_start, line_end) "
                "VALUES (?, 2, 'worker', 'function', 'xyz.worker', "
                "'Process items from the queue.', 1, 10)",
                (10 + i,),
            )
        conn.commit()
        emb.embed_all(conn)

        query = "worker"
        # Default margin: no skip (margin ~0 < 0.45).
        semantic_search(conn, query, limit=5, params=RetrievalParams(dense_threshold=0.0))
        assert len(rec.calls) == 1
        rec.calls.clear()
        # Margin 0 + exact-name corroboration ("worker") -> skip.
        semantic_search(
            conn,
            query,
            limit=5,
            params=RetrievalParams(dense_threshold=0.0, gate_min_margin=0.0),
        )
        assert rec.calls == []

    def test_rerank_true_forces_past_the_gate(self, armed_gate):
        from cairn.graph.semantic import RetrievalParams, semantic_search

        conn, rec = armed_gate
        res = semantic_search(
            conn,
            "safeApiCall",
            limit=5,
            params=RetrievalParams(dense_threshold=0.0, rerank=True),
        )
        assert len(rec.calls) == 1
        assert all(r["reranked"] is True for r in res)

    def test_rerank_false_never_runs(self, armed_gate):
        from cairn.graph.semantic import RetrievalParams, semantic_search

        conn, rec = armed_gate
        res = semantic_search(
            conn,
            "safeApiCall",
            limit=5,
            params=RetrievalParams(dense_threshold=0.0, rerank=False),
        )
        assert rec.calls == []
        assert all(r["reranked"] is False for r in res)


# ---------------------------------------------------------------------------
# Eval-layer threading (run_evaluation -> ... -> semantic_search)
# ---------------------------------------------------------------------------


def _write_graded_dir(tmp_path):
    """One graded L1 query whose primary target is alphaBulk -- the symbol
    only retrieval finds once the dense threshold widens."""
    d = tmp_path / "ground_truth"
    d.mkdir(parents=True, exist_ok=True)
    (d / "queries.jsonl").write_text(
        json.dumps(
            {
                "query_id": "l1-bulk",
                "level": "L1",
                "kind": "definition",
                "text": "function alpha",
                "rationale": "threshold knob fixture",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (d / "expectations.tsv").write_text(
        "query_id\tsymbol_id\tgrade\nl1-bulk\tK.kt#alphaBulk\t2\n", encoding="utf-8"
    )
    return d


class TestEvalThreading:
    def test_graded_path_threads_params_to_semantic_search(
        self, monkeypatch, fresh_db, tmp_path
    ):
        """A spy on cairn.graph.semantic.semantic_search (the object
        cairn.graph.queries lazily re-resolves) sees the exact params
        object on every call run_evaluation makes."""
        import cairn.eval as eval_mod
        from cairn.graph.semantic import RetrievalParams

        seen = []

        def spy(conn, query, **kw):
            seen.append({"query": query, "params": kw.get("params")})
            return []

        monkeypatch.setattr("cairn.graph.semantic.semantic_search", spy)
        params = RetrievalParams(dense_threshold=0.0)
        eval_mod.run_evaluation(
            fresh_db, queries_path=_write_graded_dir(tmp_path), params=params
        )
        assert seen == [{"query": "function alpha", "params": params}]

    def test_yaml_path_threads_params_to_semantic_search(
        self, monkeypatch, fresh_db, tmp_path
    ):
        import cairn.eval as eval_mod
        from cairn.graph.semantic import RetrievalParams

        seen = []

        def spy(conn, query, **kw):
            seen.append({"query": query, "params": kw.get("params")})
            return []

        monkeypatch.setattr("cairn.graph.semantic.semantic_search", spy)
        qyaml = tmp_path / "queries.yaml"
        qyaml.write_text(
            "- query: function alpha\n  corpus: L1\n  expect: [alpha]\n",
            encoding="utf-8",
        )
        params = RetrievalParams(rrf_k=7)
        eval_mod.run_evaluation(fresh_db, queries_path=qyaml, params=params)
        assert seen == [{"query": "function alpha", "params": params}]

    def test_run_evaluation_reports_identical_with_empty_params(
        self, monkeypatch, seeded_db, tmp_path
    ):
        """End-to-end defaults-off equivalence at the harness boundary."""
        from cairn.graph.semantic import RetrievalParams

        monkeypatch.setenv("CAIRN_FUSION", "0")
        gt = _write_graded_dir(tmp_path)
        plain = eval_run(seeded_db, gt, None)
        injected = eval_run(seeded_db, gt, RetrievalParams())
        assert injected == plain

    def test_injected_threshold_changes_the_report(
        self, monkeypatch, seeded_db, tmp_path
    ):
        """The knob-turn proof at the harness boundary: alphaBulk (cosine
        0.0801) is invisible at the 0.3 default (recall 0.0) and found at
        rank 3 under dense_threshold=0.0 (recall 1.0, MRR 1/3). Fusion off
        keeps the BM25 leg from masking the threshold."""
        from cairn.graph.semantic import RetrievalParams

        monkeypatch.setenv("CAIRN_FUSION", "0")
        gt = _write_graded_dir(tmp_path)
        default = eval_run(seeded_db, gt, None)
        widened = eval_run(seeded_db, gt, RetrievalParams(dense_threshold=0.0))

        assert default["L1"]["recall_at_10"] == 0.0
        assert default["L1"]["mrr"] == 0.0
        assert widened["L1"]["recall_at_10"] == 1.0
        assert widened["L1"]["mrr"] == 0.3333


def eval_run(conn, gt_dir, params):
    import cairn.eval as eval_mod

    return eval_mod.run_evaluation(
        conn, queries_path=gt_dir, corpus_filter="L1", params=params
    )
