"""End-of-plan integration: the joint US1+US2 demo over the heavy twins
(FR-011, FR-013, FR-014).

Pins the T021 contract at fixture scale: ~5x SCIP-vs-tree-sitter call-edge
volume, exact-share uplift index-on vs off on the stats surface, zero
false-exact conversions against the committed ground truth, and a
seconds-scale full build with a non-empty closure (TC-018/TC-020/TC-021).
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from cairn.graph.builder import build_graph
from cairn.graph.dataflow import build_transitive_closure
from cairn.graph.stats import get_stats
from cairn.parsers.scip_importer import scip_available

from tests.scip_eval_harness import REPORT_SCHEMA, _materialize, run_ab_eval

FIXTURES = Path(__file__).parent / "fixtures" / "scip-indexing"
HEAVY = FIXTURES / "heavy"
HEAVY_OFF = FIXTURES / "heavy-off"
HEAVY_GT = FIXTURES / "heavy-ground-truth"

EDGE_RATIO = 5.0
RATIO_WINDOW = 0.5
BUILD_BUDGET_S = 10.0

needs_scip = pytest.mark.skipif(not scip_available(), reason="[scip] extra not installed")


@pytest.fixture(autouse=True)
def _hash_embedder(hash_backend):
    """Pin the dep-free hash embedder so retrieval stays deterministic and offline."""


def _count(db: str, sql: str) -> int:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql).fetchone()[0]
    finally:
        conn.close()


def _stats(db: str) -> dict:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return get_stats(conn)
    finally:
        conn.close()


@needs_scip
def test_edge_ratio_uplift_and_zero_false_exact(tmp_path):
    """The heavy A/B: scip calls at ~5x tree-sitter volume, exact-share
    uplift on and off the stats surface, zero false-exact conversions."""
    report, record = run_ab_eval(HEAVY, HEAVY_OFF, HEAVY_GT, tmp_path)

    ts_calls = _count(str(tmp_path / "index-off.db"), "SELECT COUNT(*) FROM edges WHERE kind = 'calls'")
    assert ts_calls > 0
    scip_calls = _count(
        str(tmp_path / "index-on.db"),
        "SELECT COUNT(*) FROM edges WHERE kind = 'calls' AND source = 'scip'",
    )
    ratio = scip_calls / ts_calls
    assert EDGE_RATIO - RATIO_WINDOW <= ratio <= EDGE_RATIO + RATIO_WINDOW
    assert _count(str(tmp_path / "index-on.db"), "SELECT COUNT(*) FROM edges WHERE kind = 'calls'") > ts_calls

    assert report["schema"] == REPORT_SCHEMA
    assert report["corpus"]["tree_identical"] is True
    assert report["exact_share"]["on"] == 1.0
    assert report["exact_share"]["off"] < report["exact_share"]["on"]
    assert report["false_exact"] == 0
    assert report["retrieval"]["n_queries"] > 0
    assert report["retrieval"]["precision_on"] >= report["retrieval"]["precision_off"]
    assert report["disagreements"] == record["disagreements"] >= 0
    assert report["upgrades"] == record["upgrades"] >= 0

    stats_on = _stats(str(tmp_path / "index-on.db"))
    assert stats_on["edge_sources"]["scip"] == record["edges"] > 0
    assert stats_on["edge_sources"]["tree_sitter"] > 0
    assert stats_on["exact_share_by_language"]["python"] == 1.0

    stats_off = _stats(str(tmp_path / "index-off.db"))
    assert stats_off["edge_sources"].get("scip", 0) == 0
    assert stats_off["exact_share_by_language"]["python"] < 1.0


@needs_scip
def test_closure_builds_within_fixture_budget(tmp_path):
    """Full builds of both twins, closure included, stay seconds-scale with a
    non-empty closure and the index-on edge uplift present (TC-018)."""
    walls = {}
    for name, src in (("on", HEAVY), ("off", HEAVY_OFF)):
        ws = _materialize(src, tmp_path, f"budget-{name}-ws")
        db = str(tmp_path / f"budget-{name}.db")
        start = time.perf_counter()
        summary = build_graph(workspace=str(ws), db_path=db)
        conn = sqlite3.connect(db)
        build_transitive_closure(conn)
        conn.close()
        walls[name] = time.perf_counter() - start
        assert walls[name] <= BUILD_BUDGET_S
        assert _count(db, "SELECT COUNT(*) FROM transitive_edges") > 0
        if name == "on":
            assert summary["scip"]["edges"] > 0

    on_calls = _count(str(tmp_path / "budget-on.db"), "SELECT COUNT(*) FROM edges WHERE kind = 'calls'")
    off_calls = _count(str(tmp_path / "budget-off.db"), "SELECT COUNT(*) FROM edges WHERE kind = 'calls'")
    assert on_calls > off_calls > 0
