"""Zero-false-exact A/B harness over the covered fixture twins (FR-014).

Pins the eval report shape (TC-021): corpus/tree identity, exact_share
on/off, per-language uplift, disagreements/upgrades read from the import
record, retrieval precision on >= off, and false_exact == 0.
"""
import json
from pathlib import Path

import pytest

from cairn.parsers.scip_importer import scip_available

from tests.scip_eval_harness import REPORT_SCHEMA, run_ab_eval

FIXTURES = Path(__file__).parent / "fixtures" / "scip-indexing"
GT_DIR = FIXTURES / "ground-truth"

needs_scip = pytest.mark.skipif(not scip_available(), reason="[scip] extra not installed")

PINNED_KEYS = {
    "schema",
    "corpus",
    "exact_share",
    "exact_share_by_language",
    "disagreements",
    "upgrades",
    "retrieval",
    "false_exact",
}


@pytest.fixture(autouse=True)
def _hash_embedder(hash_backend):
    """Pin the dep-free hash embedder so retrieval stays deterministic and offline."""


@needs_scip
def test_report_shape_and_zero_false_exact(tmp_path):
    """The covered-twin A/B carries every pinned key, shows exact-share
    uplift index-on vs off, and converts zero ground-truth matches into
    misses."""
    out_path = tmp_path / "report.json"
    report, import_record = run_ab_eval(
        FIXTURES / "covered",
        FIXTURES / "covered-off",
        GT_DIR,
        tmp_path,
        out_path=out_path,
    )

    assert PINNED_KEYS <= set(report)
    assert report["schema"] == REPORT_SCHEMA
    assert report["corpus"]["tree_identical"] is True
    assert report["corpus"]["ground_truth"] == str(GT_DIR)

    assert report["false_exact"] == 0
    assert report["exact_share"]["on"] > report["exact_share"]["off"]
    assert report["retrieval"]["n_queries"] > 0
    assert report["retrieval"]["precision_on"] >= report["retrieval"]["precision_off"]

    (lang_shares,) = report["exact_share_by_language"].values()
    assert {"off", "on", "uplift"} <= set(lang_shares)
    assert lang_shares["on"] > lang_shares["off"]

    assert report["disagreements"] == import_record["disagreements"] >= 0
    assert report["upgrades"] == import_record["upgrades"] >= 0

    assert json.loads(out_path.read_text(encoding="utf-8")) == report


@needs_scip
def test_mismatched_twins_are_rejected(tmp_path):
    """An A/B over different trees raises instead of measuring noise."""
    with pytest.raises(ValueError, match="same tree"):
        run_ab_eval(
            FIXTURES / "covered",
            FIXTURES / "opaque-off",
            GT_DIR,
            tmp_path,
        )


@needs_scip
def test_ground_truth_loads_in_eval_format():
    """The committed ground-truth pair loads through eval's graded loader."""
    from cairn.eval import load_ground_truth

    graded = load_ground_truth(GT_DIR)
    assert {g.query_id for g in graded} == {
        "cov-provider",
        "cov-consumer",
        "cov-standalone",
        "cov-caller",
    }
    assert all(g.level == "L1" for g in graded)
    assert all(e.grade in (1, 2) for g in graded for e in g.expectations)
