"""Sweep harness core tests (T004, FR-005, D-007).

``run_sweep`` is the lever-ablation engine: it evaluates every
``{"name", "params"}`` combo on the TUNE split through the guarded
``evaluate_on`` seam (held-out enforcement is inherited, not re-implemented)
and emits the ``cairn-quality-sweep/1`` table that lands in
benchmarks/quality/ablation.json (AC1; committing is T024's job).

Hermetic like tests/test_retrieval_params.py: the hash embedder gives
deterministic vectors and the retrieval knobs are pinned (rerank hard-off,
brute cosine, fusion off) so the dense-threshold lever's effect is the
probed, machine-independent one.

Probed fixture (query ``function alpha``; cosines from the hash backend):
  alpha          0.4901  -> survives every threshold used here, rank 1
  vectorOnlyNode 0.4197  -> default-visible, dropped by threshold 0.45
  alphaBulk      0.0801  -> default-filtered, admitted by threshold 0.0

so the three configs exercise three distinct orderings/memberships and the
per-query (recall@10, MRR) table below is derived, not guessed:

    config          l1-alpha    l1-node        l1-bulk
    default(0.3)    (1.0, 1.0)  (1.0, 0.5)     (0.0, 0.0)
    wide(0.0)       (1.0, 1.0)  (1.0, 0.5)     (1.0, 0.3333)
    tight(0.45)     (1.0, 1.0)  (0.0, 0.0)     (0.0, 0.0)

Contracts pinned:

* valid D-007 schema + row shape over a trivial grid, integrity row first;
* tune-only enforcement -- tampered ids raise ``HeldOutError`` via the seam;
* ground-truth files byte-identical after a sweep (TC-025 read-only);
* determinism -- same inputs, identical canonical table bytes;
* p95 present and finite; ``evaluate_full_set`` (full set, no split);
* the seam threads ``params`` and reports per-query ``durations_ms``.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pytest

import cairn.eval as eval_mod
from cairn.eval import (
    ALL_LEVERS_OFF,
    DEFAULT_SPLIT_SEED,
    SWEEP_SCHEMA,
    HeldOutError,
    evaluate_full_set,
    evaluate_on,
    format_sweep_json,
    load_ground_truth,
    run_sweep,
    split_queries,
)
from cairn.graph.semantic import RetrievalParams

# Dep-free deterministic vectors for the whole module.
pytestmark = pytest.mark.usefixtures("hash_backend")


@pytest.fixture(autouse=True)
def _sweep_env(monkeypatch):
    """Deterministic retrieval knobs around every test (the
    test_retrieval_params.py discipline).

    * rerank hard-off (``CAIRN_RERANK=0`` -- the kill switch beats the auto
      path AND any dev-machine rerank marker, which would otherwise load a
      real cross-encoder under these exact-order assertions);
    * brute cosine forced (sqlite-vec presence must not change results);
    * fusion off isolates the dense-threshold lever the probed fixture
      table was measured under.
    """
    from cairn.graph import reranker as rrk

    monkeypatch.setattr(
        rrk, "_rerank_marker_path", lambda: Path("/nonexistent/cairn-sweep-marker")
    )
    monkeypatch.setenv("CAIRN_RERANK", "0")
    monkeypatch.setenv("CAIRN_ANN_BACKEND", "off")
    monkeypatch.setenv("CAIRN_FUSION", "0")
    yield


# ---------------------------------------------------------------------------
# Fixture corpus + ground truth
# ---------------------------------------------------------------------------


def _seed_corpus(conn) -> None:
    """The three-symbol corpus from test_retrieval_params.py (probed)."""
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
def sweep_db(fresh_db):
    """The three-symbol corpus, embedded with the deterministic hash backend."""
    from cairn.graph import embeddings as emb

    _seed_corpus(fresh_db)
    emb.embed_all(fresh_db)
    return fresh_db


SWEEP_QUERIES = [
    {
        "query_id": "l1-alpha",
        "level": "L1",
        "kind": "definition",
        "text": "function alpha",
        "rationale": "rank-1 primary target in every config",
    },
    {
        "query_id": "l1-node",
        "level": "L1",
        "kind": "definition",
        "text": "function alpha",
        "rationale": "default-visible, tight-threshold-filtered",
    },
    {
        "query_id": "l1-bulk",
        "level": "L1",
        "kind": "definition",
        "text": "function alpha",
        "rationale": "invisible at the 0.3 default, found once widened",
    },
]
SWEEP_EXPECTATIONS = [
    ("l1-alpha", "K.kt#alpha", 2),
    ("l1-node", "V.kt#vectorOnlyNode", 2),
    ("l1-bulk", "K.kt#alphaBulk", 2),
]

#: (recall_at_10, mrr) per query per config -- the probed table above.
EXPECTED_BY_CONFIG = {
    ALL_LEVERS_OFF: {
        "l1-alpha": (1.0, 1.0),
        "l1-node": (1.0, 0.5),
        "l1-bulk": (0.0, 0.0),
    },
    "wide": {
        "l1-alpha": (1.0, 1.0),
        "l1-node": (1.0, 0.5),
        "l1-bulk": (1.0, 1 / 3),
    },
    "tight": {
        "l1-alpha": (1.0, 1.0),
        "l1-node": (0.0, 0.0),
        "l1-bulk": (0.0, 0.0),
    },
}


def _write_ground_truth(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "queries.jsonl").write_text(
        "".join(json.dumps(q) + "\n" for q in SWEEP_QUERIES), encoding="utf-8"
    )
    lines = ["query_id\tsymbol_id\tgrade"]
    lines += [f"{qid}\t{sym}\t{grade}" for qid, sym, grade in SWEEP_EXPECTATIONS]
    (directory / "expectations.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return directory


@pytest.fixture()
def gt_dir(tmp_path):
    return _write_ground_truth(tmp_path / "ground_truth")


@pytest.fixture()
def gt_queries(gt_dir):
    return load_ground_truth(gt_dir)


def _combos():
    """A trivial grid: two dense-threshold points (plus the implicit
    all-levers-off row the harness prepends)."""
    return [
        {"name": "wide", "params": RetrievalParams(dense_threshold=0.0)},
        {"name": "tight", "params": RetrievalParams(dense_threshold=0.45)},
    ]


def _expected_metrics(config: str, tune) -> tuple:
    values = EXPECTED_BY_CONFIG[config]
    recall = sum(values[q][0] for q in tune) / len(tune)
    mrr = sum(values[q][1] for q in tune) / len(tune)
    return round(recall, 4), round(mrr, 4)


def _fake_timer_factory():
    """A deterministic clock: every call advances 7/1000 seconds, so every
    measured query takes exactly 7.0 ms."""
    ticks = iter(range(0, 7_000_000, 7))
    return lambda: next(ticks) / 1000.0


# ---------------------------------------------------------------------------
# The sweep grid + D-007 schema
# ---------------------------------------------------------------------------


class TestRunSweep:
    def test_trivial_grid_emits_valid_schema(self, sweep_db, gt_queries):
        doc = run_sweep(sweep_db, gt_queries, combos=_combos())

        assert set(doc) == {"schema", "dataset", "rows", "baseline"}
        assert doc["schema"] == SWEEP_SCHEMA == "cairn-quality-sweep/1"

        tune, validate = split_queries(gt_queries)
        assert doc["dataset"] == {
            "name": "ground-truth",
            "version": "1",
            "split_seed": DEFAULT_SPLIT_SEED,
            "split": "tune",
            "metric": "recall_at_10",
            "n_queries": len(tune),
        }

        # Integrity row first, caller combos in order.
        assert [row["combo"] for row in doc["rows"]] == [
            ALL_LEVERS_OFF,
            "wide",
            "tight",
        ]
        for row in doc["rows"]:
            assert set(row) == {"combo", "recall_at_10", "mrr", "p95_ms", "n_queries"}
            assert row["n_queries"] == len(tune)

        # Metrics are the probed per-config means over the tune membership.
        by_combo = {row["combo"]: row for row in doc["rows"]}
        for config in (ALL_LEVERS_OFF, "wide", "tight"):
            recall, mrr = _expected_metrics(config, tune)
            assert by_combo[config]["recall_at_10"] == recall, config
            assert by_combo[config]["mrr"] == mrr, config
        assert not set(validate) & set(by_combo)

    def test_custom_dataset_identity_rides_along(self, sweep_db, gt_queries):
        doc = run_sweep(
            sweep_db,
            gt_queries,
            combos=[],
            dataset_name="DS-v1",
            dataset_version="t2",
            metric="mrr",
        )
        assert doc["dataset"]["name"] == "DS-v1"
        assert doc["dataset"]["version"] == "t2"
        assert doc["dataset"]["metric"] == "mrr"
        assert doc["baseline"]["metric"] == "mrr"

    def test_implicit_integrity_row_added_only_when_absent(self, sweep_db, gt_queries):
        # Absent -> prepended first (covered above); present -> the caller's
        # params-None combo IS the integrity row, never evaluated twice.
        combos = [{"name": "incumbent", "params": None}] + _combos()
        doc = run_sweep(sweep_db, gt_queries, combos=combos)
        assert [row["combo"] for row in doc["rows"]] == ["incumbent", "wide", "tight"]
        # The default baseline is the params-None row, whichever it is named.
        assert doc["baseline"]["combo"] == "incumbent"

    def test_empty_grid_yields_the_integrity_row_alone(self, sweep_db, gt_queries):
        # T006's shape: the sweep entrypoint at the all-levers-off baseline.
        doc = run_sweep(sweep_db, gt_queries, combos=[])
        assert [row["combo"] for row in doc["rows"]] == [ALL_LEVERS_OFF]

    def test_generator_queries_are_materialized_once(self, sweep_db, gt_queries):
        # A generator is a legal (and easy-to-misuse) input: every combo must
        # see the full set, not just the first consumer.
        doc = run_sweep(sweep_db, iter(gt_queries), combos=_combos())
        assert [row["n_queries"] for row in doc["rows"]] == [2, 2, 2]

    def test_rows_only_ever_cover_the_tune_split(self, sweep_db, gt_queries):
        tune, validate = split_queries(gt_queries)
        doc = run_sweep(sweep_db, gt_queries, combos=_combos())
        assert all(row["n_queries"] == len(tune) for row in doc["rows"])
        assert set(doc["baseline"]["per_query"]) == set(tune)
        assert not set(doc["baseline"]["per_query"]) & set(validate)

    def test_tune_subset_selection_is_supported(self, sweep_db, gt_queries):
        tune, _validate = split_queries(gt_queries)
        doc = run_sweep(sweep_db, gt_queries, combos=_combos(), ids=[tune[0]])
        assert all(row["n_queries"] == 1 for row in doc["rows"])
        assert set(doc["baseline"]["per_query"]) == {tune[0]}

    # ------------------------------------------------------------------
    # Held-out enforcement (the seam IS the enforcement)
    # ------------------------------------------------------------------

    def test_tampered_ids_touching_validate_raise_via_the_seam(
        self, sweep_db, gt_queries
    ):
        tune, validate = split_queries(gt_queries)
        with pytest.raises(HeldOutError) as excinfo:
            run_sweep(sweep_db, gt_queries, combos=_combos(), ids=tune + validate)
        message = str(excinfo.value)
        assert "purpose='selection'" in message
        assert validate[0] in message  # the held-out id is named
        assert "FR-006" in message

    def test_guard_fires_before_any_retrieval_runs(self, sweep_db, gt_queries, monkeypatch):
        def _must_not_run(*args, **kwargs):
            raise AssertionError("retrieval ran before the held-out guard")

        monkeypatch.setattr(eval_mod, "evaluate_graded_query", _must_not_run)
        tune, validate = split_queries(gt_queries)
        with pytest.raises(HeldOutError):
            run_sweep(sweep_db, gt_queries, combos=_combos(), ids=validate)

    # ------------------------------------------------------------------
    # Read-only guarantee (TC-025)
    # ------------------------------------------------------------------

    def test_ground_truth_files_are_byte_identical_after_a_sweep(
        self, sweep_db, gt_dir, gt_queries
    ):
        def _digest(path):
            return hashlib.sha256(path.read_bytes()).hexdigest()

        before = {
            p.name: _digest(p) for p in sorted(gt_dir.iterdir()) if p.is_file()
        }
        assert set(before) == {"queries.jsonl", "expectations.tsv"}

        run_sweep(sweep_db, gt_queries, combos=_combos())

        after = {p.name: _digest(p) for p in sorted(gt_dir.iterdir()) if p.is_file()}
        assert after == before  # same files, same bytes: the loader is read-only

    # ------------------------------------------------------------------
    # Determinism + timing
    # ------------------------------------------------------------------

    def test_same_inputs_give_identical_table_bytes(self, sweep_db, gt_queries):
        first = run_sweep(sweep_db, gt_queries, combos=_combos(), timer=_fake_timer_factory())
        second = run_sweep(sweep_db, gt_queries, combos=_combos(), timer=_fake_timer_factory())
        assert format_sweep_json(first) == format_sweep_json(second)
        # ...and the serialization round-trips to the document itself.
        assert json.loads(format_sweep_json(first)) == first

    def test_deterministic_timer_pins_the_p95_column(self, sweep_db, gt_queries):
        doc = run_sweep(sweep_db, gt_queries, combos=_combos(), timer=_fake_timer_factory())
        # Every fake tick advances 7/1000 s -> every query measures 7.0 ms
        # exactly, whatever the interpolation percentile lands on.
        for row in doc["rows"]:
            assert row["p95_ms"] == 7.0

    def test_p95_is_present_and_finite_with_a_real_clock(self, sweep_db, gt_queries):
        doc = run_sweep(sweep_db, gt_queries, combos=_combos())
        for row in doc["rows"]:
            assert isinstance(row["p95_ms"], float)
            assert math.isfinite(row["p95_ms"])
            assert row["p95_ms"] >= 0.0

    def test_format_sweep_json_is_canonical(self, sweep_db, gt_queries):
        doc = run_sweep(sweep_db, gt_queries, combos=_combos(), timer=_fake_timer_factory())
        text = format_sweep_json(doc)
        assert text.endswith("\n")
        assert format_sweep_json(json.loads(text)) == text  # idempotent bytes
        # Keys sorted: the canonical order is not insertion order.
        assert text.index('"baseline"') < text.index('"schema"')

    # ------------------------------------------------------------------
    # Baseline pairing data (the downstream validate-guard input)
    # ------------------------------------------------------------------

    def test_default_baseline_is_the_integrity_row(self, sweep_db, gt_queries):
        tune, _validate = split_queries(gt_queries)
        doc = run_sweep(sweep_db, gt_queries, combos=_combos())
        assert doc["baseline"]["combo"] == ALL_LEVERS_OFF
        assert set(doc["baseline"]["per_query"]) == set(tune)
        for qid, value in doc["baseline"]["per_query"].items():
            assert value == EXPECTED_BY_CONFIG[ALL_LEVERS_OFF][qid][0]

    def test_explicit_baseline_names_any_combo(self, sweep_db, gt_queries):
        tune, _validate = split_queries(gt_queries)
        doc = run_sweep(sweep_db, gt_queries, combos=_combos(), baseline="wide")
        assert doc["baseline"]["combo"] == "wide"
        for qid, value in doc["baseline"]["per_query"].items():
            assert value == EXPECTED_BY_CONFIG["wide"][qid][0]

    # ------------------------------------------------------------------
    # Loud failures
    # ------------------------------------------------------------------

    def test_malformed_grids_and_arguments_raise(self, sweep_db, gt_queries):
        with pytest.raises(ValueError, match="combo #0 must be a mapping"):
            run_sweep(sweep_db, gt_queries, combos=[["wide"]])
        with pytest.raises(ValueError, match="non-empty string 'name'"):
            run_sweep(sweep_db, gt_queries, combos=[{"params": None}])
        with pytest.raises(ValueError, match="RetrievalParams instance or None"):
            run_sweep(sweep_db, gt_queries, combos=[{"name": "bad", "params": 0.0}])
        with pytest.raises(ValueError, match="duplicate combo name"):
            run_sweep(
                sweep_db,
                gt_queries,
                combos=[{"name": "wide", "params": None}, {"name": "wide", "params": None}],
            )
        with pytest.raises(ValueError, match="unknown metric"):
            run_sweep(sweep_db, gt_queries, combos=_combos(), metric="ndcg")
        with pytest.raises(ValueError, match="names no combo"):
            run_sweep(sweep_db, gt_queries, combos=_combos(), baseline="ghost")


# ---------------------------------------------------------------------------
# evaluate_full_set (the post-selection reporting path)
# ---------------------------------------------------------------------------


class TestEvaluateFullSet:
    def test_measures_every_query_with_no_split(self, sweep_db, gt_queries):
        report = evaluate_full_set(sweep_db, gt_queries)
        assert report["purpose"] == "full-set"
        assert report["n_queries"] == 3
        assert set(report["per_query"]) == {"l1-alpha", "l1-node", "l1-bulk"}
        # Default config over the full set: recall (1+1+0)/3, MRR (1+.5+0)/3.
        assert report["recall_at_10"] == 0.6667
        assert report["mrr"] == 0.5

    def test_threads_params_like_the_seam(self, sweep_db, gt_queries):
        report = evaluate_full_set(
            sweep_db, gt_queries, params=RetrievalParams(dense_threshold=0.0)
        )
        # The widened threshold finds alphaBulk at rank 3: recall 3/3,
        # MRR (1 + 0.5 + 1/3) / 3.
        assert report["recall_at_10"] == 1.0
        assert report["mrr"] == 0.6111

    def test_reports_per_query_durations(self, sweep_db, gt_queries):
        report = evaluate_full_set(sweep_db, gt_queries, timer=_fake_timer_factory())
        assert report["durations_ms"] == pytest.approx(
            {"l1-alpha": 7.0, "l1-node": 7.0, "l1-bulk": 7.0}
        )

    def test_loud_failures(self, sweep_db, gt_queries):
        with pytest.raises(ValueError, match="GradedQuery"):
            evaluate_full_set(sweep_db, ["l1-alpha"])
        with pytest.raises(ValueError, match="no queries"):
            evaluate_full_set(sweep_db, [])


# ---------------------------------------------------------------------------
# Seam additions: params threading + durations_ms (additive contract)
# ---------------------------------------------------------------------------


class TestEvaluateOnSeamAdditions:
    def test_params_object_reaches_every_retrieval_call(self, sweep_db, gt_queries, monkeypatch):
        seen = []

        def spy(conn, bundle_root, graded, k=10, params=None):
            seen.append((graded.query_id, params))
            return 1.0, 0.5

        monkeypatch.setattr(eval_mod, "evaluate_graded_query", spy)
        params = RetrievalParams(dense_threshold=0.0)
        evaluate_on(
            sweep_db,
            gt_queries,
            ids=["l1-alpha"],
            purpose="selection",
            held_out_ids=["l1-bulk"],
            params=params,
        )
        assert seen == [("l1-alpha", params)]

    def test_report_carries_per_query_durations(self, sweep_db, gt_queries, monkeypatch):
        monkeypatch.setattr(
            eval_mod, "evaluate_graded_query", lambda *a, **kw: (1.0, 0.5)
        )
        report = evaluate_on(
            sweep_db,
            gt_queries,
            ids=["l1-alpha"],
            purpose="selection",
            held_out_ids=["l1-bulk"],
            timer=_fake_timer_factory(),
        )
        assert report["durations_ms"] == pytest.approx({"l1-alpha": 7.0})
        # The guarded report shape is otherwise untouched.
        assert report["per_query"] == {"l1-alpha": {"recall_at_10": 1.0, "mrr": 0.5}}
        assert report["n_queries"] == 1

    def test_defaults_preserve_the_t002_report_shape(self, sweep_db, gt_queries, monkeypatch):
        monkeypatch.setattr(
            eval_mod, "evaluate_graded_query", lambda *a, **kw: (1.0, 0.5)
        )
        report = evaluate_on(
            sweep_db,
            gt_queries,
            ids=["l1-alpha"],
            purpose="selection",
            held_out_ids=["l1-bulk"],
        )
        assert report["purpose"] == "selection"
        assert report["recall_at_10"] == 1.0
        assert report["mrr"] == 0.5
        assert isinstance(report["durations_ms"]["l1-alpha"], float)


class TestSweepCli:
    """T005: `cairn eval --sweep` is a thin consumer of run_sweep."""

    def test_sweep_requires_ground_truth_dir(self, tmp_path, monkeypatch):
        from click.testing import CliRunner

        from cairn.cli.system import eval_cmd

        runner = CliRunner()
        result = runner.invoke(
            eval_cmd,
            ["--sweep", "[]", "--queries", str(tmp_path / "nope.yaml")],
            catch_exceptions=True,
        )
        assert result.exit_code != 0
        assert "ground-truth directory" in result.output

    def test_sweep_inline_combos_emit_canonical_doc(
        self, tmp_path, monkeypatch, sweep_db, gt_dir
    ):
        import json as _json

        from click.testing import CliRunner

        from cairn.cli.system import eval_cmd

        monkeypatch.setenv("CAIRN_DB", str(sweep_db))
        runner = CliRunner()
        spec = _json.dumps([{"name": "loose", "params": {"dense_threshold": 0.0}}])
        result = runner.invoke(
            eval_cmd,
            ["--sweep", spec, "--queries", str(gt_dir)],
            catch_exceptions=True,
        )
        assert result.exit_code == 0, result.output
        doc = _json.loads(result.stdout)
        assert doc["schema"] == "cairn-quality-sweep/1"
        names = [r["combo"] for r in doc["rows"]]
        assert names[0] == "all-levers-off" and "loose" in names

    def test_sweep_out_writes_file(self, tmp_path, monkeypatch, sweep_db, gt_dir):
        from click.testing import CliRunner

        from cairn.cli.system import eval_cmd

        monkeypatch.setenv("CAIRN_DB", str(sweep_db))
        out = tmp_path / "sweep.json"
        runner = CliRunner()
        result = runner.invoke(
            eval_cmd,
            ["--sweep", "[]", "--queries", str(gt_dir), "--out", str(out)],
            catch_exceptions=True,
        )
        assert result.exit_code == 0, result.output
        assert out.exists() and "row(s)" in result.stdout
