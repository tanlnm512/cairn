"""``run_sweep_kfold`` — the per-fold lever sweep over the seeded rotation.

FR-001's sweep half: the harness runs the SAME lever grid once per fold of
``kfold_partitions``, each time through the unchanged ``evaluate_on`` seam
(``purpose="selection"``, selection ids = all minus the fold's, the fold's
own ids as the flat ``held_out_ids`` iterable). Two things must hold:

* **Rotation discipline** — every query is held out exactly once, serves as
  selection material in all other folds, and never appears in its own
  fold's selection results (D-009: the per-fold results are the rotation's
  building blocks; the pooled significance basis is the consumer's job).
* **The guard extends per fold (TC-003)** — a tampered request naming any
  fold's held-out ids aborts at that fold's turn with ``HeldOutError``
  before that fold's retrieval runs, whether the breach surfaces in the
  very first fold or after earlier folds were already consumed; nothing is
  returned for the run (no results table emitted).

The fixture corpus is the three-symbol probed corpus from
``test_eval_sweep.py`` (deterministic under the hash embed backend); the
six-query ground truth rotates over its three symbols. Structural
assertions derive the expected folds through ``kfold_partitions`` itself
(regenerate-and-compare), never hard-coded membership.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import cairn.eval as eval_mod
from cairn.eval import (
    ALL_LEVERS_OFF,
    KFOLD_SWEEP_SCHEMA,
    HeldOutError,
    format_sweep_json,
    kfold_partitions,
    load_ground_truth,
    run_sweep_kfold,
)
from cairn.graph.semantic import RetrievalParams

# Dep-free deterministic vectors for the whole module.
pytestmark = pytest.mark.usefixtures("hash_backend")


@pytest.fixture(autouse=True)
def _kfold_env(monkeypatch):
    """Deterministic retrieval knobs around every test (the
    test_eval_sweep.py discipline: rerank hard-off, brute cosine forced,
    fusion off — the exact-order determinism the byte-equality test needs).
    """
    from cairn.graph import reranker as rrk

    monkeypatch.setattr(
        rrk, "_rerank_marker_path", lambda: Path("/nonexistent/cairn-kfold-marker")
    )
    monkeypatch.setenv("CAIRN_RERANK", "0")
    monkeypatch.setenv("CAIRN_ANN_BACKEND", "off")
    monkeypatch.setenv("CAIRN_FUSION", "0")
    yield


# ---------------------------------------------------------------------------
# Fixture corpus + ground truth (the three-symbol probed corpus)
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
def kfold_db(fresh_db):
    """The three-symbol corpus, embedded with the deterministic hash backend."""
    from cairn.graph import embeddings as emb

    _seed_corpus(fresh_db)
    emb.embed_all(fresh_db)
    return fresh_db


KFOLD_QUERIES = [
    {
        "query_id": "l1-alpha-1",
        "level": "L1",
        "kind": "definition",
        "text": "function alpha",
        "rationale": "primary target rank-1 in every config",
    },
    {
        "query_id": "l1-alpha-2",
        "level": "L1",
        "kind": "definition",
        "text": "alpha function",
        "rationale": "same target, different phrasing",
    },
    {
        "query_id": "l1-alpha-3",
        "level": "L1",
        "kind": "definition",
        "text": "alpha",
        "rationale": "grade-1 context expectation",
    },
    {
        "query_id": "l1-bulk-1",
        "level": "L1",
        "kind": "definition",
        "text": "alphaBulk words",
        "rationale": "long-doc symbol",
    },
    {
        "query_id": "l1-node-1",
        "level": "L1",
        "kind": "definition",
        "text": "vector only node",
        "rationale": "second-file target",
    },
    {
        "query_id": "l1-node-2",
        "level": "L1",
        "kind": "definition",
        "text": "node vector",
        "rationale": "second-file target, different phrasing",
    },
]
KFOLD_EXPECTATIONS = [
    ("l1-alpha-1", "K.kt#alpha", 2),
    ("l1-alpha-2", "K.kt#alpha", 2),
    ("l1-alpha-3", "K.kt#alpha", 1),
    ("l1-bulk-1", "K.kt#alphaBulk", 2),
    ("l1-node-1", "V.kt#vectorOnlyNode", 2),
    ("l1-node-2", "V.kt#vectorOnlyNode", 2),
]


@pytest.fixture()
def gt_dir(tmp_path):
    directory = tmp_path / "ground_truth"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "queries.jsonl").write_text(
        "".join(json.dumps(q) + "\n" for q in KFOLD_QUERIES), encoding="utf-8"
    )
    lines = ["query_id\tsymbol_id\tgrade"]
    lines += [f"{qid}\t{sym}\t{grade}" for qid, sym, grade in KFOLD_EXPECTATIONS]
    (directory / "expectations.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return directory


@pytest.fixture()
def gt_queries(gt_dir):
    return load_ground_truth(gt_dir)


def _all_ids():
    return sorted(q["query_id"] for q in KFOLD_QUERIES)


def _folds():
    """The expected rotation, derived through kfold_partitions itself."""
    return kfold_partitions(_all_ids())


def _combos():
    """A trivial grid: two dense-threshold points (plus the implicit
    all-levers-off row the harness prepends)."""
    return [
        {"name": "wide", "params": RetrievalParams(dense_threshold=0.0)},
        {"name": "tight", "params": RetrievalParams(dense_threshold=0.45)},
    ]


def _fake_timer_factory():
    """A deterministic clock: every call advances 7/1000 seconds, so every
    measured query takes exactly 7.0 ms."""
    ticks = iter(range(0, 7_000_000, 7))
    return lambda: next(ticks) / 1000.0


# ---------------------------------------------------------------------------
# The rotation document
# ---------------------------------------------------------------------------


class TestRunSweepKfoldDocument:
    def test_document_shape_and_per_fold_keying(self, kfold_db, gt_queries):
        doc = run_sweep_kfold(kfold_db, gt_queries, combos=_combos())
        assert doc["schema"] == KFOLD_SWEEP_SCHEMA
        assert doc["dataset"] == {
            "name": "ground-truth",
            "version": "1",
            "fold_seed": eval_mod.DEFAULT_SPLIT_SEED,
            "k_folds": 5,
            "split": "kfold",
            "metric": "recall_at_10",
            "n_queries": 6,
        }
        folds = doc["folds"]
        assert [entry["fold"] for entry in folds] == [0, 1, 2, 3, 4]
        for entry in folds:
            assert set(entry["held_out_ids"]) <= set(_all_ids())
            assert entry["rows"][0]["combo"] == ALL_LEVERS_OFF  # integrity row first
            assert [row["combo"] for row in entry["rows"]] == [
                ALL_LEVERS_OFF,
                "wide",
                "tight",
            ]
            assert set(entry["reports"]) == {ALL_LEVERS_OFF, "wide", "tight"}
            assert entry["baseline"]["combo"] == ALL_LEVERS_OFF
            assert entry["baseline"]["metric"] == "recall_at_10"

    def test_fold_membership_matches_kfold_partitions(self, kfold_db, gt_queries):
        doc = run_sweep_kfold(kfold_db, gt_queries, combos=_combos())
        for entry, expected in zip(doc["folds"], _folds()):
            assert entry["held_out_ids"] == sorted(expected)

    def test_rotation_holds_out_every_id_exactly_once(self, kfold_db, gt_queries):
        doc = run_sweep_kfold(kfold_db, gt_queries, combos=_combos())
        held = [qid for entry in doc["folds"] for qid in entry["held_out_ids"]]
        assert sorted(held) == _all_ids()  # complete cover...
        assert len(held) == len(set(held))  # ...with no overlap

    def test_rows_cover_all_ids_minus_the_held_out_fold(self, kfold_db, gt_queries):
        doc = run_sweep_kfold(kfold_db, gt_queries, combos=_combos())
        for entry, expected in zip(doc["folds"], _folds()):
            selection = set(_all_ids()) - set(expected)
            for row in entry["rows"]:
                assert row["n_queries"] == len(selection)
            for report in entry["reports"].values():
                assert set(report["per_query"]) == selection
            assert set(entry["baseline"]["per_query"]) == selection

    def test_held_out_ids_never_appear_in_their_own_fold_results(
        self, kfold_db, gt_queries
    ):
        doc = run_sweep_kfold(kfold_db, gt_queries, combos=_combos())
        for entry in doc["folds"]:
            held = set(entry["held_out_ids"])
            for report in entry["reports"].values():
                assert not held & set(report["per_query"])
                assert not held & set(report["durations_ms"])

    def test_every_query_is_selection_material_in_all_other_folds(
        self, kfold_db, gt_queries
    ):
        doc = run_sweep_kfold(kfold_db, gt_queries, combos=_combos())
        owner = {
            qid: entry["fold"]
            for entry in doc["folds"]
            for qid in entry["held_out_ids"]
        }
        base_reports = [entry["reports"][ALL_LEVERS_OFF] for entry in doc["folds"]]
        for qid in _all_ids():
            served = sum(qid in report["per_query"] for report in base_reports)
            assert served == 4  # all folds except the one holding it out
            assert qid not in base_reports[owner[qid]]["per_query"]

    def test_seam_is_called_with_selection_minus_fold_and_held_out_fold(
        self, kfold_db, gt_queries, monkeypatch
    ):
        real_evaluate_on = eval_mod.evaluate_on
        calls = []

        def _recording_evaluate_on(conn, queries, **kwargs):
            calls.append(kwargs)
            return real_evaluate_on(conn, queries, **kwargs)

        monkeypatch.setattr(eval_mod, "evaluate_on", _recording_evaluate_on)
        run_sweep_kfold(kfold_db, gt_queries, combos=_combos())

        assert len(calls) == 5 * 3  # every combo in every fold, nothing else
        for kwargs in calls:
            assert kwargs["purpose"] == "selection"
            held = set(kwargs["held_out_ids"])
            assert held in [set(fold) for fold in _folds()]
            assert set(kwargs["ids"]) == set(_all_ids()) - held

    def test_rows_carry_the_sweep_row_contract(self, kfold_db, gt_queries):
        doc = run_sweep_kfold(kfold_db, gt_queries, combos=_combos())
        for entry in doc["folds"]:
            for row in entry["rows"]:
                assert {
                    "combo",
                    "recall_at_10",
                    "mrr",
                    "p95_ms",
                    "n_queries",
                    "db_mb",
                    "chunk_chars_max",
                    "chunk_chars_mean",
                } <= set(row)
                assert row["db_mb"] > 0  # in-memory: page_count * page_size
                assert row["chunk_chars_max"] > 0

    def test_empty_grid_runs_the_integrity_row_alone(self, kfold_db, gt_queries):
        doc = run_sweep_kfold(kfold_db, gt_queries, combos=[])
        for entry in doc["folds"]:
            assert [row["combo"] for row in entry["rows"]] == [ALL_LEVERS_OFF]

    def test_same_inputs_give_identical_document_bytes(self, kfold_db, gt_queries):
        first = run_sweep_kfold(
            kfold_db, gt_queries, combos=_combos(), timer=_fake_timer_factory()
        )
        second = run_sweep_kfold(
            kfold_db, gt_queries, combos=_combos(), timer=_fake_timer_factory()
        )
        assert format_sweep_json(first) == format_sweep_json(second)
        assert json.loads(format_sweep_json(first)) == first

    def test_fold_count_is_configurable(self, kfold_db, gt_queries):
        doc = run_sweep_kfold(kfold_db, gt_queries, combos=_combos(), k_folds=6)
        assert doc["dataset"]["k_folds"] == 6
        assert len(doc["folds"]) == 6
        for entry in doc["folds"]:
            assert len(entry["held_out_ids"]) == 1  # 6 ids over 6 folds
            for row in entry["rows"]:
                assert row["n_queries"] == 5

    def test_fold_count_below_the_floor_is_refused(self, kfold_db, gt_queries):
        for weak_k in (2, 4):
            with pytest.raises(ValueError, match="k >= 5"):
                run_sweep_kfold(kfold_db, gt_queries, combos=_combos(), k_folds=weak_k)

    def test_fewer_ids_than_folds_is_refused(self, kfold_db, gt_queries):
        with pytest.raises(ValueError, match="at least 5 distinct ids"):
            run_sweep_kfold(kfold_db, gt_queries[:4], combos=_combos())

    def test_unknown_metric_and_baseline_are_refused(self, kfold_db, gt_queries):
        with pytest.raises(ValueError, match="unknown metric"):
            run_sweep_kfold(kfold_db, gt_queries, combos=_combos(), metric="ndcg")
        with pytest.raises(ValueError, match="names no combo"):
            run_sweep_kfold(kfold_db, gt_queries, combos=_combos(), baseline="ghost")


# ---------------------------------------------------------------------------
# Held-out enforcement per fold (TC-003) — the seam IS the enforcement
# ---------------------------------------------------------------------------


class TestKfoldHeldOutGuard:
    def _recording_retrieval(self, monkeypatch):
        """Wrap evaluate_graded_query so every scored query id is recorded
        (retrieval itself keeps running — earlier folds must complete)."""
        real = eval_mod.evaluate_graded_query
        evaluated: list[str] = []

        def _recording(conn, bundle_root, graded, **kwargs):
            evaluated.append(graded.query_id)
            return real(conn, bundle_root, graded, **kwargs)

        monkeypatch.setattr(eval_mod, "evaluate_graded_query", _recording)
        return evaluated

    def test_request_touching_the_first_fold_aborts_before_any_retrieval(
        self, kfold_db, gt_queries, monkeypatch
    ):
        def _must_not_run(*args, **kwargs):
            raise AssertionError("retrieval ran before the held-out guard")

        monkeypatch.setattr(eval_mod, "evaluate_graded_query", _must_not_run)
        fold_zero = _folds()[0]
        with pytest.raises(HeldOutError) as excinfo:
            run_sweep_kfold(kfold_db, gt_queries, combos=_combos(), ids=fold_zero)
        message = str(excinfo.value)
        assert "purpose='selection'" in message
        for qid in fold_zero:
            assert qid in message  # every held-out id is named

    def test_request_touching_a_later_fold_aborts_mid_rotation(
        self, kfold_db, gt_queries, monkeypatch
    ):
        evaluated = self._recording_retrieval(monkeypatch)
        breached_id = _folds()[2][0]  # folds 0 and 1 run to completion first
        with pytest.raises(HeldOutError):
            run_sweep_kfold(kfold_db, gt_queries, combos=_combos(), ids=[breached_id])
        # The breached fold's id was legally selection material in folds 0
        # and 1 (the rotation really got under way)...
        assert evaluated == [breached_id] * 6  # 2 folds x 3 combos, nothing else
        # ...and nothing was ever returned for the run — the exception IS
        # the abort; fold 2's guard fired before its own retrieval ran.

    def test_request_spanning_folds_fires_at_the_earliest_owning_fold(
        self, kfold_db, gt_queries, monkeypatch
    ):
        evaluated = self._recording_retrieval(monkeypatch)
        folds = _folds()
        request = folds[1] + folds[3]
        with pytest.raises(HeldOutError) as excinfo:
            run_sweep_kfold(kfold_db, gt_queries, combos=_combos(), ids=request)
        # Fold 0 consumed every requested id as legal tuning material...
        assert set(evaluated) == set(folds[1]) | set(folds[3])
        assert len(evaluated) == len(request) * 3  # fold 0 x 3 combos, then abort
        # ...and fold 1's own turn still rejected them — consumption of an
        # earlier fold never launders a held-out read (the violation names
        # exactly fold 1's ids; fold 3's ids are not the trigger).
        message = str(excinfo.value)
        for qid in folds[1]:
            assert qid in message

    def test_full_set_request_aborts_at_the_first_fold(
        self, kfold_db, gt_queries, monkeypatch
    ):
        evaluated = self._recording_retrieval(monkeypatch)
        with pytest.raises(HeldOutError):
            run_sweep_kfold(kfold_db, gt_queries, combos=_combos(), ids=_all_ids())
        assert evaluated == []  # nothing scored anywhere: no table, no rows
