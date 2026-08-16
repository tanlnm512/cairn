"""Dedicated tests for the eval harness (FR-003 / T010).

The survey found zero dedicated eval tests; this is the first suite. Covers:

* the graded ground-truth loader (D-004 schema: queries.jsonl +
  expectations.tsv) and its validation errors;
* the two-tier identity-first matcher (strict ``file#symbol`` identity,
  then the legacy substring rule);
* grade-aware recall@10 / MRR arithmetic on hand-built result sets;
* ``run_evaluation`` / ``eval_cmd --queries`` directory dispatch;
* the untouched yaml fixture (D-008: 30 L1 + 10 L5, legacy report shape);
* the seeded 50/50 tune/validate split (FR-006, TC-018);
* the held-out guard on ``evaluate_on`` selection runs (FR-006, TC-019);
* the paired-bootstrap accept guard (D-006: bootstrap/t, not Wilcoxon).

Loader/matcher fixtures live in tmp_path. The split tests
(TestSplitQueries) additionally read the committed real pair under
benchmarks/datasource/t2/ground_truth/ to pin the 58-L1 29/29 contract
against the actual dataset.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

import cairn.eval as eval_mod
from cairn.cli import main
from cairn.eval import (
    DEFAULT_SPLIT_SEED,
    Expectation,
    HeldOutError,
    evaluate_on,
    load_eval_queries,
    load_ground_truth,
    match_rank,
    paired_bootstrap,
    parse_symbol_id,
    run_evaluation,
    score_graded_query,
    split_queries,
)
from cairn.graph.schema import get_db

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_GROUND_TRUTH = REPO_ROOT / "benchmarks" / "datasource" / "t2" / "ground_truth"
N_REAL_L1 = 58

GOOD_QUERIES = [
    {
        "query_id": "l1-url",
        "level": "L1",
        "kind": "definition",
        "text": "construct a URL object",
        "rationale": "core constructor of the dataset corpus",
    },
    {
        "query_id": "l5-quote",
        "level": "L5",
        "kind": "knowledge",
        "text": "how percent-quoting works",
        "rationale": "docstring-documented behavior",
    },
]
GOOD_EXPECTATIONS = [
    ("l1-url", "src/yarl/_url.py#URL", 2),
    ("l1-url", "src/yarl/_url.py#_new", 1),
    ("l5-quote", "docs/quoting.md#quoting-guide", 2),
]


def _write_ground_truth(directory, queries, expectation_rows):
    """Materialize a D-004 file pair inside ``directory`` (tmp_path-backed)."""
    directory.mkdir(parents=True, exist_ok=True)
    queries_path = directory / "queries.jsonl"
    queries_path.write_text(
        "".join(json.dumps(q) + "\n" for q in queries), encoding="utf-8"
    )
    lines = ["query_id\tsymbol_id\tgrade"]
    lines += [f"{qid}\t{symbol}\t{grade}" for qid, symbol, grade in expectation_rows]
    (directory / "expectations.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return directory


@pytest.fixture()
def good_gt_dir(tmp_path):
    return _write_ground_truth(tmp_path / "ground_truth", GOOD_QUERIES, GOOD_EXPECTATIONS)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

class TestLoadGroundTruth:
    def test_parses_and_joins_the_pair(self, good_gt_dir):
        queries = load_ground_truth(good_gt_dir)
        by_id = {q.query_id: q for q in queries}
        assert set(by_id) == {"l1-url", "l5-quote"}

        l1 = by_id["l1-url"]
        assert (l1.level, l1.kind, l1.text) == ("L1", "definition", "construct a URL object")
        assert l1.rationale == "core constructor of the dataset corpus"
        assert [(e.symbol_id, e.grade) for e in l1.expectations] == [
            ("src/yarl/_url.py#URL", 2),
            ("src/yarl/_url.py#_new", 1),
        ]

    def test_unknown_query_id_in_tsv_is_an_error(self, tmp_path):
        gt = _write_ground_truth(
            tmp_path / "gt",
            GOOD_QUERIES[:1],
            [("l1-url", "src/yarl/_url.py#URL", 2), ("ghost", "src/x.py#Y", 1)],
        )
        with pytest.raises(ValueError, match="unknown query_id 'ghost'"):
            load_ground_truth(gt)

    def test_query_with_zero_expectations_is_an_error(self, tmp_path):
        gt = _write_ground_truth(
            tmp_path / "gt",
            GOOD_QUERIES,
            [("l1-url", "src/yarl/_url.py#URL", 2)],  # l5-quote left empty
        )
        with pytest.raises(ValueError, match="'l5-quote' has zero expectation"):
            load_ground_truth(gt)

    @pytest.mark.parametrize("bad_grade", ["3", "0", "-1", "primary"])
    def test_grade_outside_1_2_is_an_error(self, tmp_path, bad_grade):
        lines = ["query_id\tsymbol_id\tgrade", f"l1-url\tsrc/yarl/_url.py#URL\t{bad_grade}"]
        gt = tmp_path / "gt"
        gt.mkdir()
        (gt / "queries.jsonl").write_text(json.dumps(GOOD_QUERIES[0]) + "\n")
        (gt / "expectations.tsv").write_text("\n".join(lines) + "\n")
        with pytest.raises(ValueError, match="grade"):
            load_ground_truth(gt)

    @pytest.mark.parametrize(
        "filename", ["queries.jsonl", "expectations.tsv"]
    )
    def test_missing_file_is_an_error(self, good_gt_dir, filename):
        (good_gt_dir / filename).unlink()
        with pytest.raises(ValueError, match="ground-truth file missing"):
            load_ground_truth(good_gt_dir)

    def test_malformed_symbol_id_is_an_error(self, tmp_path):
        gt = _write_ground_truth(
            tmp_path / "gt", GOOD_QUERIES[:1], [("l1-url", "no-hash-here", 2)]
        )
        with pytest.raises(ValueError, match=r"malformed symbol_id 'no-hash-here'"):
            load_ground_truth(gt)

    def test_unknown_level_is_an_error(self, tmp_path):
        bad = [dict(GOOD_QUERIES[0], level="L3")]
        gt = _write_ground_truth(tmp_path / "gt", bad, [("l1-url", "a.py#A", 2)])
        with pytest.raises(ValueError, match="level 'L3'"):
            load_ground_truth(gt)

    def test_bad_tsv_header_is_an_error(self, good_gt_dir):
        (good_gt_dir / "expectations.tsv").write_text("qid\tsym\tg\nl1-url\ta.py#A\t2\n")
        with pytest.raises(ValueError, match="unexpected header"):
            load_ground_truth(good_gt_dir)

    def test_duplicate_query_id_is_an_error(self, tmp_path):
        gt = tmp_path / "gt"
        gt.mkdir()
        (gt / "queries.jsonl").write_text(
            json.dumps(GOOD_QUERIES[0]) + "\n" + json.dumps(GOOD_QUERIES[0]) + "\n"
        )
        (gt / "expectations.tsv").write_text(
            "query_id\tsymbol_id\tgrade\nl1-url\ta.py#A\t2\n"
        )
        with pytest.raises(ValueError, match="duplicate query_id 'l1-url'"):
            load_ground_truth(gt)


class TestParseSymbolId:
    def test_splits_on_last_hash(self):
        assert parse_symbol_id("src/yarl/_url.py#URL") == ("src/yarl/_url.py", "URL")
        assert parse_symbol_id("we#ird/name.py#Foo") == ("we#ird/name.py", "Foo")

    @pytest.mark.parametrize("bad", ["", "nothash", "trail.py#", "#Foo"])
    def test_malformed_ids_raise(self, bad):
        with pytest.raises(ValueError, match="malformed symbol_id"):
            parse_symbol_id(bad)


# ---------------------------------------------------------------------------
# Two-tier matcher
# ---------------------------------------------------------------------------

class TestMatchRank:
    RESULTS = [
        {"name": "_URL__init__", "file_path": "/repo/src/yarl/_url.py"},
        {"name": "URL", "file_path": "/repo/src/yarl/_url.py"},
        {"name": "Quoter", "file_path": "/repo/src/yarl/_quoting.py"},
    ]

    def test_identity_hit_exact_name_and_file_suffix(self):
        # Exact symbol name at rank 2 + file path ends with the component.
        assert match_rank("src/yarl/_url.py#URL", self.RESULTS) == 2

    def test_identity_does_not_match_wrong_file(self):
        # "Quoter" exists by name at rank 3 but under a different file path:
        # the identity tier must fail and the substring tier must find it.
        assert match_rank("src/other/place.py#Quoter", self.RESULTS) == 3

    def test_substring_fallback_hit(self):
        # No result is named exactly "_new", but "URL__new__helper" contains it.
        results = [{"name": "URL__new__helper", "file_path": "/x/src/yarl/_url.py"}]
        assert match_rank("src/yarl/_url.py#_new", results) == 1

    def test_case_insensitive_substring_matches(self):
        results = [{"name": "SOMETHING_URL_BUILDER", "file_path": "/x/other.py"}]
        assert match_rank("a.py#url_builder", results) == 1

    def test_strict_miss_returns_zero(self):
        assert match_rank("src/gone.py#DoesNotExist", self.RESULTS) == 0

    def test_results_without_file_path_take_the_substring_tier(self):
        # L5-shaped results (concept ids, no file path) can never satisfy the
        # identity tier and always fall through to the substring rule.
        concepts = [{"name": "wiki/quoting-guide", "file_path": ""}]
        assert match_rank("docs/quoting.md#quoting-guide", concepts) == 1
        assert match_rank("docs/quoting.md#absent", concepts) == 0

    def test_sqlite_row_results_are_supported(self):
        class FakeRow(dict):
            """dict subclass behaves like sqlite3.Row for [key] access."""

        row = FakeRow(name="URL", file_path="/repo/src/yarl/_url.py")
        assert match_rank("src/yarl/_url.py#URL", [row]) == 1


# ---------------------------------------------------------------------------
# Graded scoring
# ---------------------------------------------------------------------------

def _results(*names):
    return [{"name": n, "file_path": "/repo/src/yarl/_url.py"} for n in names]


class TestScoreGradedQuery:
    def test_recall_counts_matched_expectations(self):
        # 2 of 3 expectations appear in the top-10 -> recall 2/3.
        results = _results("URL", "_new", "unrelated", "other")
        exps = [
            Expectation("src/yarl/_url.py#URL", 2),
            Expectation("src/yarl/_url.py#_new", 1),
            Expectation("src/yarl/_url.py#absent", 1),
        ]
        recall, rr = score_graded_query(results, exps, k=10)
        assert recall == pytest.approx(2 / 3)
        assert rr == 1.0  # grade-2 primary at rank 1

    def test_mrr_ranks_grade2_first(self):
        # The grade-2 primary sits at rank 4 while a grade-1 context symbol
        # ranks 1: MRR must use the primary's rank (1/4), not 1/1.
        results = _results("_new", "filler_a", "filler_b", "URL")
        exps = [
            Expectation("src/yarl/_url.py#URL", 2),
            Expectation("src/yarl/_url.py#_new", 1),
        ]
        recall, rr = score_graded_query(results, exps, k=10)
        assert recall == 1.0
        assert rr == pytest.approx(1 / 4)

    def test_mrr_falls_to_grade1_when_no_grade2_exists(self):
        results = _results("filler", "URL")
        exps = [Expectation("src/yarl/_url.py#URL", 1)]
        recall, rr = score_graded_query(results, exps, k=10)
        assert (recall, rr) == (1.0, pytest.approx(1 / 2))

    def test_mrr_is_zero_when_primary_missed(self):
        # A grade-2 expectation exists but nothing matches it; the grade-1
        # hit at rank 1 must NOT rescue the reciprocal rank (documented rule:
        # the primary target outranks must-return context).
        results = _results("_new")
        exps = [
            Expectation("src/yarl/_url.py#absent", 2),
            Expectation("src/yarl/_url.py#_new", 1),
        ]
        recall, rr = score_graded_query(results, exps, k=10)
        assert recall == 0.5
        assert rr == 0.0

    def test_matches_beyond_k_do_not_count(self):
        # "URL" sits at position 11: with k=10 it is out of the window.
        results = _results(*([f"filler_{i}" for i in range(10)] + ["URL"]))
        exps = [Expectation("src/yarl/_url.py#URL", 2)]
        recall, rr = score_graded_query(results, exps, k=10)
        assert (recall, rr) == (0.0, 0.0)

    def test_empty_expectations_score_zero(self):
        recall, rr = score_graded_query(_results("URL"), [], k=10)
        assert (recall, rr) == (0.0, 0.0)


# ---------------------------------------------------------------------------
# run_evaluation dispatch (graded directory vs yaml file vs default)
# ---------------------------------------------------------------------------

class TestRunEvaluationDispatch:
    def test_directory_takes_the_graded_path(self, good_gt_dir, tmp_path, monkeypatch):
        conn = get_db(str(tmp_path / "eval.db"))

        def fake_l1(conn_, query, k, params=None):
            # Rank 2 for the primary target, rank 1 for context: RR = 1/2.
            # (params: the D-008 RetrievalParams run_evaluation threads
            # through since T003 -- the double accepts and ignores it.)
            return [
                {"name": "_new", "file_path": "/repo/src/yarl/_url.py"},
                {"name": "URL", "file_path": "/repo/src/yarl/_url.py"},
            ]

        def fake_l5(bundle_root, query, k):
            return [{"name": "wiki/quoting-guide", "file_path": ""}]

        monkeypatch.setattr(eval_mod, "_retrieve_l1", fake_l1)
        monkeypatch.setattr(eval_mod, "_retrieve_l5", fake_l5)
        try:
            report = run_evaluation(
                conn, bundle_root=None, queries_path=good_gt_dir, corpus_filter="all"
            )
        finally:
            conn.close()

        # Legacy keys preserved...
        assert set(report) == {"L1", "L5"}
        for bucket in report.values():
            for key in ("count", "recall_at_10", "mrr"):
                assert key in bucket
        # ...plus the additive graded keys.
        assert report["L1"]["n_queries"] == 1
        assert report["L1"]["n_expectations"] == 2
        assert report["L1"]["recall_at_10"] == 1.0
        assert report["L1"]["mrr"] == 0.5  # grade-2 primary at rank 2
        assert report["L5"]["n_expectations"] == 1
        assert report["L5"]["recall_at_10"] == 1.0
        assert report["L5"]["mrr"] == 1.0

    def test_graded_corpus_filter_skips_other_level(self, good_gt_dir, tmp_path, monkeypatch):
        conn = get_db(str(tmp_path / "eval.db"))
        monkeypatch.setattr(eval_mod, "_retrieve_l1", lambda *a, **kw: [])
        monkeypatch.setattr(eval_mod, "_retrieve_l5", lambda *a, **kw: [])
        try:
            report = run_evaluation(
                conn, bundle_root=None, queries_path=good_gt_dir, corpus_filter="L5"
            )
        finally:
            conn.close()
        assert report["L5"]["count"] == 1
        assert report["L1"]["count"] == 0

    def test_yaml_file_path_stays_on_the_legacy_shape(self, tmp_path):
        conn = get_db(str(tmp_path / "eval.db"))
        try:
            report = run_evaluation(
                conn,
                bundle_root=None,
                queries_path=eval_mod.DEFAULT_QUERIES_PATH,
                corpus_filter="all",
            )
        finally:
            conn.close()
        # Byte-identical legacy report: exactly the three legacy keys.
        assert set(report) == {"L1", "L5"}
        assert set(report["L1"]) == {"count", "recall_at_10", "mrr"}
        assert set(report["L5"]) == {"count", "recall_at_10", "mrr"}
        assert report["L1"]["count"] == 30
        assert report["L5"]["count"] == 10

    def test_empty_directory_raises_validation_error(self, tmp_path):
        # An existing directory without the file pair is a malformed graded
        # dataset (a *missing* path keeps the legacy yaml warning behavior).
        empty = tmp_path / "empty_gt"
        empty.mkdir()
        conn = get_db(str(tmp_path / "eval.db"))
        try:
            with pytest.raises(ValueError, match="ground-truth file missing"):
                run_evaluation(
                    conn, bundle_root=None, queries_path=empty, corpus_filter="all"
                )
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# yaml fixture untouched (D-008)
# ---------------------------------------------------------------------------

class TestYamlFixtureUntouched:
    def test_bundled_set_is_still_30_l1_10_l5(self):
        queries = load_eval_queries()
        assert len(queries) == 40
        assert sum(1 for q in queries if q.get("corpus") == "L1") == 30
        assert sum(1 for q in queries if q.get("corpus") == "L5") == 10

    def test_yaml_shape_unchanged(self):
        queries = load_eval_queries()
        for q in queries:
            assert set(q) == {"query", "corpus", "expect"}


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------

class TestEvalCli:
    def test_directory_queries_render_both_levels(self, good_gt_dir, tmp_path):
        runner = CliRunner()
        db_path = str(tmp_path / "cli.db")
        knowledge_dir = str(tmp_path / ".knowledge")
        Path(knowledge_dir).mkdir(parents=True, exist_ok=True)
        get_db(db_path).close()  # initialize schema

        result = runner.invoke(
            main,
            [
                "eval",
                "--db", db_path,
                "--knowledge", knowledge_dir,
                "--queries", str(good_gt_dir),
                "--json",
            ],
        )
        assert result.exit_code == 0
        assert "L1" in result.output
        assert "L5" in result.output

        payload = json.loads(result.stdout)
        assert set(payload) == {"L1", "L5"}
        # Both queries ran (retrieval over an empty index scores 0, but the
        # buckets count them) and the additive keys ride along.
        assert payload["L1"]["count"] == 1
        assert payload["L1"]["n_queries"] == 1
        assert payload["L1"]["n_expectations"] == 2
        assert payload["L5"]["count"] == 1

    def test_table_render_keeps_l1(self, good_gt_dir, tmp_path):
        runner = CliRunner()
        db_path = str(tmp_path / "cli.db")
        knowledge_dir = str(tmp_path / ".knowledge")
        Path(knowledge_dir).mkdir(parents=True, exist_ok=True)
        get_db(db_path).close()

        result = runner.invoke(
            main,
            [
                "eval",
                "--db", db_path,
                "--knowledge", knowledge_dir,
                "--queries", str(good_gt_dir),
            ],
        )
        assert result.exit_code == 0
        assert "L1" in result.output
        assert "recall@10" in result.output

    def test_invalid_dataset_is_a_clean_cli_error(self, tmp_path):
        runner = CliRunner()
        db_path = str(tmp_path / "cli.db")
        knowledge_dir = str(tmp_path / ".knowledge")
        Path(knowledge_dir).mkdir(parents=True, exist_ok=True)
        get_db(db_path).close()

        bad_dir = tmp_path / "bad_gt"
        bad_dir.mkdir()
        (bad_dir / "queries.jsonl").write_text(json.dumps(GOOD_QUERIES[0]) + "\n")
        # expectations.tsv absent -> loader ValueError -> ClickException.

        result = runner.invoke(
            main,
            [
                "eval",
                "--db", db_path,
                "--knowledge", knowledge_dir,
                "--queries", str(bad_dir),
                "--json",
            ],
        )
        assert result.exit_code != 0
        assert "invalid eval dataset" in result.output

    def test_yaml_path_still_accepted(self, tmp_path):
        runner = CliRunner()
        db_path = str(tmp_path / "cli.db")
        knowledge_dir = str(tmp_path / ".knowledge")
        Path(knowledge_dir).mkdir(parents=True, exist_ok=True)
        get_db(db_path).close()

        result = runner.invoke(
            main,
            [
                "eval",
                "--db", db_path,
                "--knowledge", knowledge_dir,
                "--queries", str(eval_mod.DEFAULT_QUERIES_PATH),
                "--json",
            ],
        )
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["L1"]["count"] == 30
        assert payload["L5"]["count"] == 10


# ---------------------------------------------------------------------------
# Seeded tune/validate split (FR-006, D-006, TC-018)
# ---------------------------------------------------------------------------

#: Same shape as the real measurement set: 58 synthetic ids. split_queries
#: never mutates its input (it shuffles its own sorted copy), so sharing one
#: list across tests is safe.
SYNTH_IDS_58 = [f"q-{i:02d}" for i in range(N_REAL_L1)]


class TestSplitQueries:
    def test_same_seed_reproduces_identical_halves(self):
        first = split_queries(SYNTH_IDS_58)
        second = split_queries(SYNTH_IDS_58)
        assert first == second

    def test_input_order_duplicates_and_set_input_do_not_matter(self):
        ids = sorted(f"sym-{i}" for i in range(10))
        baseline = split_queries(ids)
        assert split_queries(list(reversed(ids))) == baseline
        assert split_queries(set(ids)) == baseline
        assert split_queries(ids + ids) == baseline  # duplicates deduped

    def test_different_seed_yields_a_different_partition(self):
        tune_a, validate_a = split_queries(SYNTH_IDS_58, seed=1)
        tune_b, validate_b = split_queries(SYNTH_IDS_58, seed=2)
        assert set(tune_a) != set(tune_b)
        assert set(validate_a) != set(validate_b)

    def test_default_seed_is_a_fixed_constant(self):
        # The harness contract is "same dataset, same split, forever" -- the
        # default must not depend on time, env, or call site.
        assert split_queries(SYNTH_IDS_58) == split_queries(SYNTH_IDS_58, seed=DEFAULT_SPLIT_SEED)
        assert isinstance(DEFAULT_SPLIT_SEED, int)

    def test_real_l1_set_splits_29_29_disjoint_complete(self):
        l1 = [q for q in load_ground_truth(REAL_GROUND_TRUTH) if q.level == "L1"]
        assert len(l1) == N_REAL_L1  # the dataset this contract is written for

        tune, validate = split_queries(l1)
        assert len(tune) == 29
        assert len(validate) == 29
        assert not set(tune) & set(validate)
        assert set(tune) | set(validate) == {q.query_id for q in l1}

    def test_level_agnostic_partitions_exactly_what_it_is_given(self):
        # No level filtering inside the split -- handing it the full mixed
        # dataset yields an 82-id partition, not an L1-only or L5-only one.
        everything = load_ground_truth(REAL_GROUND_TRUTH)
        assert {q.level for q in everything} == {"L1", "L5"}

        tune, validate = split_queries(everything)
        assert len(tune) + len(validate) == len(everything)
        assert not set(tune) & set(validate)
        assert set(tune) | set(validate) == {q.query_id for q in everything}

    def test_odd_count_gives_tune_the_extra(self):
        ids = ["a", "b", "c", "d", "e"]
        tune, validate = split_queries(ids)
        assert len(tune) == 3
        assert len(validate) == 2
        assert not set(tune) & set(validate)
        assert set(tune) | set(validate) == set(ids)

        single_tune, single_validate = split_queries(["only"])
        assert single_tune == ["only"]
        assert single_validate == []

    def test_partition_properties_hold_across_seeds_on_an_odd_set(self):
        ids = [f"q-{i:02d}" for i in range(17)]  # odd size stresses the ceil rule
        for seed in range(8):
            tune, validate = split_queries(ids, seed=seed)
            assert not set(tune) & set(validate)
            assert set(tune) | set(validate) == set(ids)
            assert (len(tune), len(validate)) == (9, 8)

    def test_ratio_controls_the_cut(self):
        ids = [f"n{i}" for i in range(8)]
        tune, validate = split_queries(ids, ratio=0.25)
        assert (len(tune), len(validate)) == (2, 6)

    def test_ratio_out_of_bounds_fails_loudly(self):
        with pytest.raises(ValueError, match="ratio"):
            split_queries(["a"], ratio=1.5)
        with pytest.raises(ValueError, match="ratio"):
            split_queries(["a"], ratio=-0.1)

    def test_stable_under_pythonhashseed_variation(self):
        # The sort-before-shuffle rule buys process-level determinism: two
        # fresh interpreters with different hash seeds must agree, and both
        # must agree with this process.
        code = (
            "from cairn.eval import split_queries\n"
            "ids = [f'q-{i:02d}' for i in range(58)]\n"
            "tune, validate = split_queries(ids)\n"
            "print(','.join(tune), '|', ','.join(validate))\n"
        )
        outputs = set()
        for hash_seed in ("0", "31337"):
            proc = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                check=True,
                env={**os.environ, "PYTHONHASHSEED": hash_seed},
            )
            outputs.add(proc.stdout.strip())
        assert len(outputs) == 1

        tune, validate = split_queries(SYNTH_IDS_58)
        assert outputs.pop() == ",".join(tune) + " | " + ",".join(validate)


# ---------------------------------------------------------------------------
# Held-out guard on evaluate_on (FR-006, TC-019, D-006)
# ---------------------------------------------------------------------------

#: Hand-built split over the good_gt_dir fixture: l1-url is "tune",
#: l5-quote is the held-out validate half.
TUNE_IDS = ["l1-url"]
HELD_OUT_IDS = ["l5-quote"]


@pytest.fixture()
def gt_conn(tmp_path):
    """An open scratch connection (retrieval itself is monkeypatched)."""
    conn = get_db(str(tmp_path / "guard.db"))
    yield conn
    conn.close()


class TestEvaluateOnHeldOutGuard:
    def test_selection_touching_validate_ids_raises_naming_ids_and_mode(
        self, good_gt_dir, gt_conn
    ):
        queries = load_ground_truth(good_gt_dir)
        with pytest.raises(HeldOutError) as excinfo:
            evaluate_on(
                gt_conn,
                queries,
                ids=TUNE_IDS + HELD_OUT_IDS,  # the tampered sweep: tune + validate
                purpose="selection",
                held_out_ids=HELD_OUT_IDS,
            )
        message = str(excinfo.value)
        # The error names the held-out violation: which ids, which mode.
        assert "purpose='selection'" in message
        assert "'l5-quote'" in message
        assert "l1-url" not in message  # only the violating ids are named
        assert "FR-006" in message  # traceable to the requirement

    def test_guard_fires_before_any_query_is_scored(self, good_gt_dir, gt_conn, monkeypatch):
        # "No results table is emitted" is structural: the raise precedes
        # every retrieval/evaluation call, so nothing can be scored first.
        def _must_not_run(*args, **kwargs):
            raise AssertionError("evaluation ran before the held-out guard")

        monkeypatch.setattr(eval_mod, "evaluate_graded_query", _must_not_run)
        queries = load_ground_truth(good_gt_dir)
        with pytest.raises(HeldOutError):
            evaluate_on(
                gt_conn,
                queries,
                ids=HELD_OUT_IDS,
                purpose="selection",
                held_out_ids=HELD_OUT_IDS,
            )

    def test_held_out_error_cannot_be_swallowed_as_a_dataset_error(self):
        # eval_cmd catches ValueError as "invalid eval dataset"; the guard
        # must survive that handler and reach the top as a loud failure.
        assert issubclass(HeldOutError, RuntimeError)
        assert not issubclass(HeldOutError, ValueError)

    def test_selection_on_tune_ids_evaluates_normally(
        self, good_gt_dir, gt_conn, monkeypatch
    ):
        monkeypatch.setattr(
            eval_mod, "evaluate_graded_query", lambda *a, **kw: (1.0, 0.5)
        )
        queries = load_ground_truth(good_gt_dir)
        report = evaluate_on(
            gt_conn,
            queries,
            ids=TUNE_IDS,
            purpose="selection",
            held_out_ids=HELD_OUT_IDS,
        )
        assert report["purpose"] == "selection"
        assert report["n_queries"] == 1
        assert report["recall_at_10"] == 1.0
        assert report["mrr"] == 0.5
        assert report["per_query"]["l1-url"] == {"recall_at_10": 1.0, "mrr": 0.5}
        assert "bootstrap" not in report  # selection never sees the guard output

    def test_selection_requires_a_declared_held_out_set(self, good_gt_dir, gt_conn):
        queries = load_ground_truth(good_gt_dir)
        with pytest.raises(ValueError, match="held_out_ids"):
            evaluate_on(gt_conn, queries, ids=TUNE_IDS, purpose="selection")

    def test_validate_purpose_attaches_the_bootstrap_verdict(
        self, good_gt_dir, gt_conn, monkeypatch
    ):
        monkeypatch.setattr(
            eval_mod, "evaluate_graded_query", lambda *a, **kw: (1.0, 0.5)
        )
        queries = load_ground_truth(good_gt_dir)
        # Incumbent: perfect on l1-url, failed on l5-quote. Candidate scores
        # 1.0 recall everywhere -> paired delta on recall@10 = 0.5.
        report = evaluate_on(
            gt_conn,
            queries,
            ids=TUNE_IDS + HELD_OUT_IDS,
            purpose="validate",
            baseline_metrics={"l1-url": 1.0, "l5-quote": 0.0},
        )
        assert report["purpose"] == "validate"
        assert report["metric"] == "recall_at_10"
        assert report["n_queries"] == 2
        assert report["baseline_mean"] == 0.5
        boot = report["bootstrap"]
        assert boot["delta"] == pytest.approx(0.5)
        for key in ("delta", "ci_low", "ci_high", "p_value", "significant"):
            assert key in boot

    def test_validate_can_target_mrr(self, good_gt_dir, gt_conn, monkeypatch):
        monkeypatch.setattr(
            eval_mod, "evaluate_graded_query", lambda *a, **kw: (1.0, 0.5)
        )
        queries = load_ground_truth(good_gt_dir)
        report = evaluate_on(
            gt_conn,
            queries,
            ids=TUNE_IDS + HELD_OUT_IDS,
            purpose="validate",
            baseline_metrics={"l1-url": 0.5, "l5-quote": 0.5},
            metric="mrr",
        )
        assert report["metric"] == "mrr"
        assert report["bootstrap"]["delta"] == pytest.approx(0.0)

    def test_validate_requires_baseline_metrics(self, good_gt_dir, gt_conn, monkeypatch):
        monkeypatch.setattr(
            eval_mod, "evaluate_graded_query", lambda *a, **kw: (1.0, 1.0)
        )
        queries = load_ground_truth(good_gt_dir)
        with pytest.raises(ValueError, match="baseline_metrics"):
            evaluate_on(
                gt_conn, queries, ids=HELD_OUT_IDS, purpose="validate"
            )

    def test_validate_requires_query_for_query_pairing(
        self, good_gt_dir, gt_conn, monkeypatch
    ):
        monkeypatch.setattr(
            eval_mod, "evaluate_graded_query", lambda *a, **kw: (1.0, 1.0)
        )
        queries = load_ground_truth(good_gt_dir)
        with pytest.raises(ValueError, match="'l5-quote'"):
            evaluate_on(
                gt_conn,
                queries,
                ids=TUNE_IDS + HELD_OUT_IDS,
                purpose="validate",
                baseline_metrics={"l1-url": 1.0},  # l5-quote missing
            )

    def test_validate_rejects_non_numeric_baseline(
        self, good_gt_dir, gt_conn, monkeypatch
    ):
        monkeypatch.setattr(
            eval_mod, "evaluate_graded_query", lambda *a, **kw: (1.0, 1.0)
        )
        queries = load_ground_truth(good_gt_dir)
        with pytest.raises(ValueError, match="non-numeric"):
            evaluate_on(
                gt_conn,
                queries,
                ids=TUNE_IDS + HELD_OUT_IDS,
                purpose="validate",
                baseline_metrics={"l1-url": 1.0, "l5-quote": "good"},
            )

    def test_malformed_calls_fail_loudly(self, good_gt_dir, gt_conn, monkeypatch):
        monkeypatch.setattr(
            eval_mod, "evaluate_graded_query", lambda *a, **kw: (0.0, 0.0)
        )
        queries = load_ground_truth(good_gt_dir)
        with pytest.raises(ValueError, match="purpose"):
            evaluate_on(gt_conn, queries, ids=TUNE_IDS, purpose="yolo")
        with pytest.raises(ValueError, match="metric"):
            evaluate_on(
                gt_conn, queries, ids=TUNE_IDS, metric="ndcg", held_out_ids=[]
            )
        with pytest.raises(ValueError, match="unknown query id"):
            evaluate_on(
                gt_conn, queries, ids=["ghost"], purpose="selection", held_out_ids=[]
            )
        with pytest.raises(ValueError, match="empty id set"):
            evaluate_on(gt_conn, queries, ids=[], purpose="selection", held_out_ids=[])
        with pytest.raises(ValueError, match="GradedQuery"):
            evaluate_on(gt_conn, TUNE_IDS, ids=TUNE_IDS, purpose="selection", held_out_ids=[])

    def test_end_to_end_against_the_real_58_l1_split(self, gt_conn, monkeypatch):
        # The T004 consumption shape against the committed dataset: split,
        # tamper, observe the named violation; a legal tune-only run passes.
        queries = [
            q for q in load_ground_truth(REAL_GROUND_TRUTH) if q.level == "L1"
        ]
        assert len(queries) == N_REAL_L1
        tune, validate = split_queries(queries)

        monkeypatch.setattr(
            eval_mod, "evaluate_graded_query", lambda *a, **kw: (1.0, 1.0)
        )
        with pytest.raises(HeldOutError) as excinfo:
            evaluate_on(
                gt_conn, queries, ids=tune + validate[:3], purpose="selection",
                held_out_ids=validate,
            )
        for qid in validate[:3]:
            assert repr(qid) in str(excinfo.value) or f"'{qid}'" in str(excinfo.value)

        report = evaluate_on(
            gt_conn, queries, ids=tune, purpose="selection", held_out_ids=validate
        )
        assert report["n_queries"] == 29


# ---------------------------------------------------------------------------
# Paired bootstrap accept guard (D-006: bootstrap/t, not Wilcoxon)
# ---------------------------------------------------------------------------

#: A clearly-better candidate: recall@10 per query, candidate vs baseline.
A_CLEAR = [1.0, 0.9, 1.0, 0.8, 1.0]
B_CLEAR = [0.2, 0.1, 0.3, 0.0, 0.2]


class TestPairedBootstrap:
    def test_result_carries_the_full_verdict(self):
        result = paired_bootstrap(A_CLEAR, B_CLEAR)
        assert set(result) == {
            "delta",
            "ci_low",
            "ci_high",
            "p_value",
            "significant",
            "t_statistic",
            "p_value_t",
            "n_queries",
            "n_resamples",
            "confidence",
        }

    def test_known_delta_with_ci_bracketing_it(self):
        result = paired_bootstrap(A_CLEAR, B_CLEAR)
        assert result["delta"] == pytest.approx(0.78)
        # The CI brackets the observed delta and excludes zero (clear case)...
        assert result["ci_low"] <= 0.78 <= result["ci_high"]
        assert result["ci_low"] > 0.0
        # ...and the delta passes the accept gate.
        assert result["p_value"] < 0.05
        assert result["significant"] is True

    def test_zero_delta_is_never_significant(self):
        values = [0.5, 0.2, 1.0, 0.0, 0.7]
        result = paired_bootstrap(values, values)
        assert result["delta"] == 0.0
        assert (result["ci_low"], result["ci_high"]) == (0.0, 0.0)
        assert result["p_value"] == 1.0
        assert result["significant"] is False
        # Degenerate zero-variance t is defined, not a crash.
        assert result["t_statistic"] == 0.0
        assert result["p_value_t"] == 1.0

    def test_small_5_query_delta_stays_within_noise(self):
        # The honest-small case (the acceptance example): delta 0.4 on five
        # queries does NOT clear the bar — the guard refuses weak evidence.
        a = [1.0, 1.0, 1.0, 0.0, 1.0]
        b = [0.0, 1.0, 0.0, 0.0, 1.0]
        result = paired_bootstrap(a, b)
        assert result["delta"] == pytest.approx(0.4)
        assert result["significant"] is False
        assert result["p_value"] > 0.05

    def test_same_seed_reproduces_the_verdict_exactly(self):
        first = paired_bootstrap(A_CLEAR, B_CLEAR, n_resamples=2000, seed=7)
        second = paired_bootstrap(A_CLEAR, B_CLEAR, n_resamples=2000, seed=7)
        assert first == second

    def test_different_seed_gives_an_independent_resample(self):
        # On a noisy sample the extreme-count (hence p) moves with the seed;
        # the clear-case arrays are deliberately excluded — their null
        # distribution can never reach |delta|, so p sits at the add-one
        # floor for every seed.
        a = [1.0, 1.0, 1.0, 0.0, 1.0]
        b = [0.0, 1.0, 0.0, 0.0, 1.0]
        first = paired_bootstrap(a, b, n_resamples=2000, seed=1)
        second = paired_bootstrap(a, b, n_resamples=2000, seed=2)
        assert first["p_value"] != second["p_value"]

    def test_paired_t_agrees_on_a_clear_case(self):
        # Smucker: bootstrap and t are interchangeable at this n — on a
        # clear case both reject, with p-values of the same magnitude.
        result = paired_bootstrap(A_CLEAR, B_CLEAR)
        assert result["p_value"] < 0.05
        assert result["p_value_t"] < 0.05
        assert result["significant"] is True

    def test_p_value_t_matches_the_t_distribution(self):
        # diffs [1..5] against zeros: mean 3, sample sd sqrt(2.5),
        # t = 3 / (sqrt(2.5)/sqrt(5)) = 4.242640687119285 with df = 4.
        # The two-sided t tail there is 0.013235599563682107 (reference
        # value from the t-distribution, implementation cross-validated
        # against t-table critical values to < 5e-5).
        result = paired_bootstrap([1.0, 2.0, 3.0, 4.0, 5.0], [0.0] * 5)
        assert result["t_statistic"] == pytest.approx(4.242640687119285)
        assert result["p_value_t"] == pytest.approx(0.013235599563682107, abs=1e-9)

    @pytest.mark.parametrize(
        "a, b, pattern",
        [
            ([1.0, 2.0], [1.0], "equal-length"),
            ([], [], "at least one"),
        ],
    )
    def test_malformed_inputs_raise(self, a, b, pattern):
        with pytest.raises(ValueError, match=pattern):
            paired_bootstrap(a, b)

    def test_bad_n_resamples_and_confidence_raise(self):
        with pytest.raises(ValueError, match="n_resamples"):
            paired_bootstrap(A_CLEAR, B_CLEAR, n_resamples=0)
        with pytest.raises(ValueError, match="confidence"):
            paired_bootstrap(A_CLEAR, B_CLEAR, confidence=1.5)
