"""Tests for scripts/verify_ground_truth.py -- the FR-003/AC5 staleness gate.

Hermetic strategy (same shape as tests/test_verify_datasource.py): the real
82-query pair is the CI run's business; every unit test below mints a SMALL
scratch snapshot + ground-truth pair in tmp_path (T010's pair-building
pattern + T008's build-over-a-copy pattern), then drives the validator as a
library call, with one --json run through main() for the wire shape and one
end-to-end run against the committed pair (exit 0, 234/234) as the real-data
anchor.

Covered contract points (task T012 / TC-021 / TC-022):
* all-verified pass -> exit 0 with the per-kind/level summary;
* a tampered grade-2 expectation -> exit 1 NAMING the entry (query text +
  missing symbol); grade-1 misses are consciously listed too, grade-2 first;
* missing dataset dir / malformed dataset / missing snapshot / empty build /
  explicitly requested missing bundle -> exit 2 (infrastructure, not stale);
* a missing auto-discovered bundle is NOT an error (L5 falls back to the
  graph surface -- T011's authoring reality);
* an OKF bundle at <t2>/.knowledge joins the L5 surface and rescues
  concept-shaped expectations the graph cannot verify;
* D-010: a failing run never rewrites the dataset files;
* the .git scanner marker lands on the tmp COPY, never the source snapshot.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "verify_ground_truth.py"
REAL_DATASET = REPO_ROOT / "benchmarks" / "datasource" / "t2" / "ground_truth"
REAL_SNAPSHOT = REPO_ROOT / "benchmarks" / "datasource" / "t2" / "yarl"

# scripts/ is not a package; load the validator by file path so the object
# under test is the same module the subprocess executes (T003 test pattern).
_spec = importlib.util.spec_from_file_location("verify_ground_truth", SCRIPT)
vg = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("verify_ground_truth", vg)
_spec.loader.exec_module(vg)


# ---------------------------------------------------------------------------
# Scratch fixtures: a tiny snapshot + a matching D-004 pair
# ---------------------------------------------------------------------------

WIDGET_PY = '''"""A tiny widget package for the validator's unit fixtures."""


class Widget:
    """The product type."""

    def spin(self):
        return "spinning"


def make_widget(path):
    """Build a Widget after normalizing the path."""
    return Widget(), normalize_path(path)
'''

UTILS_PY = '''"""Path helpers."""


def normalize_path(path):
    """Normalize a filesystem path."""
    return "/".join(segment for segment in path.split("/") if segment)
'''

QUERIES = [
    {
        "query_id": "l1-def",
        "level": "L1",
        "kind": "definition",
        "text": "Where is the Widget class defined?",
        "rationale": "widget.py declares the product type",
    },
    {
        "query_id": "l1-callers",
        "level": "L1",
        "kind": "callers",
        "text": "Who consumes normalize_path?",
        "rationale": "make_widget calls normalize_path",
    },
    {
        "query_id": "l5-lore",
        "level": "L5",
        "kind": "knowledge",
        "text": "widget lore guide",
        "rationale": "only verifiable through the knowledge surface",
    },
]

# The l5-lore row is concept-shaped ("guide" is no substring of any graph
# symbol) -- without a bundle it is stale; with the lore/ concept it verifies.
EXPECTATIONS = [
    ("l1-def", "widget.py#Widget", 2),
    ("l1-def", "widget.py#make_widget", 1),
    ("l1-callers", "utils.py#normalize_path", 2),
    ("l1-callers", "widget.py#make_widget", 1),
    ("l5-lore", "lore/widget-guide.md#guide", 2),
]


def _write_snapshot(t2_root: Path) -> Path:
    """A two-module snapshot under ``t2_root`` shaped like t2/ (yarl -> synth)."""
    snap = t2_root / "synth"
    snap.mkdir(parents=True)
    (snap / "widget.py").write_text(WIDGET_PY, encoding="utf-8")
    (snap / "utils.py").write_text(UTILS_PY, encoding="utf-8")
    return snap


def _write_dataset(gt_dir: Path, expectations=EXPECTATIONS, queries=QUERIES) -> Path:
    """Materialize a D-004 file pair (T010's test-building pattern)."""
    gt_dir.mkdir(parents=True, exist_ok=True)
    (gt_dir / "queries.jsonl").write_text(
        "".join(json.dumps(q) + "\n" for q in queries), encoding="utf-8"
    )
    lines = ["query_id\tsymbol_id\tgrade"]
    lines += [f"{qid}\t{symbol}\t{grade}" for qid, symbol, grade in expectations]
    (gt_dir / "expectations.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return gt_dir


def _write_bundle(t2_root: Path) -> Path:
    """An OKF knowledge bundle at <t2>/.knowledge holding the lore concept."""
    concepts = t2_root / ".knowledge" / "lore"
    concepts.mkdir(parents=True)
    (concepts / "widget-guide.md").write_text(
        "---\ntype: wiki\ntitle: widget lore guide\n"
        "description: the widget lore guide\n---\n"
        "This widget lore guide covers everything.\n",
        encoding="utf-8",
    )
    return t2_root / ".knowledge"


@pytest.fixture()
def synth(tmp_path):
    """A scratch t2-shaped tree: snapshot + ground-truth pair, no bundle."""
    t2 = tmp_path / "t2"
    return _write_dataset(t2 / "ground_truth"), _write_snapshot(t2), t2


# ---------------------------------------------------------------------------
# Infrastructure failures (exit 2) -- nothing was verified
# ---------------------------------------------------------------------------


class TestInfrastructure:
    def test_missing_dataset_dir_is_exit_2(self, synth):
        _dataset, snapshot, _t2 = synth
        report = vg.verify_ground_truth(dataset=_dataset / "nope", snapshot=snapshot)
        assert report.exit_code() == vg.EXIT_INFRA
        assert not report.ok
        assert any("dataset directory missing" in e for e in report.errors)
        assert report.stale == [] and report.summary == {}

    def test_malformed_dataset_is_exit_2(self, synth, tmp_path):
        _dataset, snapshot, _t2 = synth
        empty = tmp_path / "empty_gt"
        empty.mkdir()
        report = vg.verify_ground_truth(dataset=empty, snapshot=snapshot)
        assert report.exit_code() == vg.EXIT_INFRA
        assert any("dataset malformed" in e for e in report.errors)

    def test_missing_snapshot_dir_is_exit_2(self, synth, tmp_path):
        dataset, _snapshot, _t2 = synth
        report = vg.verify_ground_truth(dataset=dataset, snapshot=tmp_path / "void")
        assert report.exit_code() == vg.EXIT_INFRA
        assert any("snapshot directory missing" in e for e in report.errors)

    def test_empty_snapshot_build_is_exit_2(self, synth, tmp_path):
        dataset, _snapshot, _t2 = synth
        bare = tmp_path / "bare"
        bare.mkdir()
        report = vg.verify_ground_truth(dataset=dataset, snapshot=bare)
        # Either guard fires ("recognized no repos" / "empty symbol inventory")
        # -- the contract is: a build with nothing in it is infrastructure.
        assert report.exit_code() == vg.EXIT_INFRA
        assert report.errors and not report.stale

    def test_requested_but_missing_bundle_is_exit_2_not_stale(self, synth, tmp_path):
        dataset, snapshot, _t2 = synth
        report = vg.verify_ground_truth(
            dataset=dataset, snapshot=snapshot, bundle=tmp_path / "no_bundle"
        )
        assert report.exit_code() == vg.EXIT_INFRA
        assert any("bundle root missing" in e for e in report.errors)
        # The eval.py behavior this guards: a missing bundle root would
        # 0.0/0.0 every L5 query -- infrastructure, never a stale verdict.
        assert report.stale == []


# ---------------------------------------------------------------------------
# Happy path (exit 0) and stale path (exit 1)
# ---------------------------------------------------------------------------


class TestVerifyPaths:
    def test_all_verified_green_with_bundle(self, synth):
        dataset, snapshot, t2 = synth
        _write_bundle(t2)  # rescue the concept-shaped l5-lore expectation
        report = vg.verify_ground_truth(dataset=dataset, snapshot=snapshot)
        assert report.exit_code() == vg.EXIT_OK and report.ok
        assert report.errors == [] and report.stale == []
        assert report.bundle_status == "auto"
        t = report.totals
        assert (t["queries"], t["expectations"], t["verified"], t["stale"]) == (3, 5, 5, 0)
        assert report.summary["L1"]["definition"] == {
            "queries": 1, "expectations": 2, "verified": 2, "stale": 0
        }
        assert report.summary["L5"]["knowledge"]["verified"] == 1

    def test_no_bundle_leaves_concept_shaped_l5_row_stale(self, synth):
        # T011's committed reality has no bundle; code-symbol L5 rows verify
        # against the graph, but a concept-shaped row cannot -- it is stale.
        dataset, snapshot, _t2 = synth
        report = vg.verify_ground_truth(dataset=dataset, snapshot=snapshot)
        assert report.exit_code() == vg.EXIT_STALE
        assert [s.symbol_id for s in report.stale] == ["lore/widget-guide.md#guide"]
        assert report.bundle_status == "none"

    def test_stale_grade2_is_named_with_query_text_and_symbol(self, synth):
        dataset, snapshot, _t2 = synth
        tampered = [("l1-def", "widget.py#Widget_v2", 2)] + [
            row for row in EXPECTATIONS if row[0] != "l1-def" or row[2] != 2
        ]
        _write_dataset(dataset, expectations=tampered)
        report = vg.verify_ground_truth(dataset=dataset, snapshot=snapshot)
        assert report.exit_code() == vg.EXIT_STALE
        assert len(report.stale) == 2  # tampered grade-2 + concept-shaped row
        first = report.stale[0]
        assert first.grade == 2  # grade-2 primary targets list first (D-004)
        assert first.query_id == "l1-def"
        assert first.query_text == "Where is the Widget class defined?"
        assert first.symbol_id == "widget.py#Widget_v2"

    def test_stale_grade1_is_consciously_listed_too(self, synth, tmp_path):
        # AC5: EVERY expectation verifies or names the stale entry -- grade-1
        # rows are must-return context, and their misses are listed, not
        # silently swallowed.
        dataset, snapshot, _t2 = synth
        queries = [q for q in QUERIES if q["query_id"] != "l5-lore"]
        expectations = [
            ("l1-def", "widget.py#Widget", 2),
            ("l1-def", "widget.py#make_widget", 1),
            ("l1-callers", "utils.py#normalize_path", 2),
            ("l1-callers", "widget.py#absent_helper", 1),
        ]
        _write_dataset(dataset, expectations=expectations, queries=queries)
        report = vg.verify_ground_truth(dataset=dataset, snapshot=snapshot)
        assert report.exit_code() == vg.EXIT_STALE
        assert [(s.grade, s.symbol_id) for s in report.stale] == [
            (1, "widget.py#absent_helper")
        ]
        assert report.totals["stale_grade1"] == 1
        assert report.totals["stale_grade2"] == 0


# ---------------------------------------------------------------------------
# CLI surface: --json shape, human output, D-010, marker isolation
# ---------------------------------------------------------------------------


class TestCliAndContract:
    def test_json_shape_ok_and_exit_codes(self, synth, capsys):
        dataset, snapshot, t2 = synth
        _write_bundle(t2)
        code = vg.main(
            ["--dataset", str(dataset), "--snapshot", str(snapshot), "--json"]
        )
        payload = json.loads(capsys.readouterr().out)
        assert code == vg.EXIT_OK == payload["exit_code"]
        assert payload["ok"] is True
        for key in (
            "dataset", "snapshot", "bundle", "bundle_status", "ok", "exit_code",
            "errors", "build", "summary", "totals", "stale",
        ):
            assert key in payload
        assert payload["build"]["parse_errors"] == 0
        assert payload["build"]["repos"] == 1

    def test_json_stale_run_carries_named_entries(self, synth, capsys):
        dataset, snapshot, _t2 = synth
        code = vg.main(["--dataset", str(dataset), "--snapshot", str(snapshot), "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert code == vg.EXIT_STALE == payload["exit_code"]
        entry = payload["stale"][0]
        for key in ("query_id", "query_text", "level", "kind", "symbol_id", "grade"):
            assert key in entry

    def test_human_output_names_query_and_missing_symbol(self, synth, capsys):
        dataset, snapshot, _t2 = synth
        queries = [q for q in QUERIES if q["query_id"] == "l1-def"]
        expectations = [("l1-def", "widget.py#Widget_v2", 2)]
        _write_dataset(dataset, expectations=expectations, queries=queries)
        code = vg.main(["--dataset", str(dataset), "--snapshot", str(snapshot)])
        err = capsys.readouterr().err
        assert code == vg.EXIT_STALE
        assert "Where is the Widget class defined?" in err
        assert "widget.py#Widget_v2" in err
        assert "DS-v2" in err  # D-010: the fix ships as a new version

    def test_failing_run_never_rewrites_the_dataset(self, synth):
        # D-010: stale sets ship as DS-v2; the validator is read-only on the
        # pair. Byte-identical files after a stale verdict proves it.
        dataset, snapshot, _t2 = synth
        before = {
            p.name: p.read_bytes() for p in dataset.iterdir() if p.is_file()
        }
        report = vg.verify_ground_truth(dataset=dataset, snapshot=snapshot)
        assert report.exit_code() == vg.EXIT_STALE
        after = {p.name: p.read_bytes() for p in dataset.iterdir() if p.is_file()}
        assert after == before

    def test_marker_lands_on_the_copy_never_the_snapshot(self, synth, tmp_path):
        dataset, snapshot, _t2 = synth
        workroot = tmp_path / "wr"
        report = vg.verify_ground_truth(
            dataset=dataset, snapshot=snapshot, workroot=workroot
        )
        assert report.build is not None  # the build ran
        # The committed-style source tree stays marker-free...
        assert not (snapshot / ".git").exists()
        # ...while the throwaway copy carries the scanner marker + graph DB.
        assert (workroot / "workspace" / snapshot.name / ".git").is_dir()
        assert (workroot / "graph.db").exists()

    def test_default_workroot_is_cleaned_up(self, synth, monkeypatch):
        import tempfile

        created = []
        real_mkdtemp = tempfile.mkdtemp

        def spy(*args, **kwargs):
            path = real_mkdtemp(*args, **kwargs)
            created.append(Path(path))
            return path

        monkeypatch.setattr(tempfile, "mkdtemp", spy)
        dataset, snapshot, _t2 = synth
        report = vg.verify_ground_truth(dataset=dataset, snapshot=snapshot)
        assert report.exit_code() in (vg.EXIT_OK, vg.EXIT_STALE)
        # The throwaway workspace (snapshot copy + graph DB) is gone.
        assert created and not created[0].exists()


# ---------------------------------------------------------------------------
# Real committed pair (TC-021 anchor; ~0.4s: tiny yarl snapshot builds fast)
# ---------------------------------------------------------------------------


class TestRealCommittedPair:
    def test_committed_pair_verifies_on_a_fresh_build(self):
        report = vg.verify_ground_truth(
            dataset=REAL_DATASET, snapshot=REAL_SNAPSHOT
        )
        assert report.errors == []
        assert report.exit_code() == vg.EXIT_OK
        t = report.totals
        assert t["queries"] == 82
        assert t["expectations"] == 234
        assert t["verified"] == 234
        assert t["stale"] == 0
        assert report.build["parse_errors"] == 0
        # The committed tree carries no bundle: L5 verifies against the graph
        # surface (T011 authored L5 expectations as code-symbol ids).
        assert report.bundle_status == "none"
        # No .git marker leaked into the committed snapshot.
        assert not (REAL_SNAPSHOT / ".git").exists()
