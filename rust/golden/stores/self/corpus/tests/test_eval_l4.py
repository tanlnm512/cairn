"""Tests for the L4 tribal-memory evaluation level.

Covers the L4 ground-truth pair (queries.jsonl + expectations.tsv with
``memory/tribal#<slug>`` symbol ids), the ``_retrieve_l4`` normalization
over ``search_memory(tier="tribal")``, recall@k / MRR reporting in the
same shape as L1/L5, and ``cairn eval --corpus L4`` wiring.
"""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

import cairn.eval as eval_mod
from cairn.graph.schema import get_db
from cairn.okf.bundle import OKFBundle
from cairn.okf.concept import OKFConcept

GT_DIR = Path(__file__).resolve().parent / "eval" / "memory" / "ground_truth"

NUMPY_ID = "memory/tribal/never-evict-numpy-from-sys-modules-mid-process-e52eeb"
RESTART_ID = "memory/tribal/daemon-config-merge-needs-explicit-restart-3fa21c"

# The two memories the dataset's grade-2 expectations point at. L4-M03's
# expected memory is deliberately never seeded so recall is a genuine
# fraction rather than a constant 1.0.
MEMORIES = [
    (
        NUMPY_ID,
        "Never evict numpy from sys.modules mid-process",
        "Evicting numpy from sys.modules mid-process crashes native extensions.",
        "mistake",
    ),
    (
        RESTART_ID,
        "Daemon config merge needs an explicit restart",
        "Restarting the daemon is required after hand-editing the merged config.",
        "decision",
    ),
]


def _seed_memory(bundle, concept_id, title, body, memory_type):
    concept = OKFConcept(
        type="Tribal-decision",
        title=title,
        description=title,
        tags=["decision"],
        body=body,
        extensions={
            "memory_tier": "tribal",
            "memory_is_latest": True,
            "memory_type": memory_type,
        },
        concept_id=concept_id,
    )
    bundle.write_concept(concept)


@pytest.fixture()
def bundle_root(tmp_path):
    root = tmp_path / "knowledge"
    bundle = OKFBundle(str(root))
    for concept_id, title, body, memory_type in MEMORIES:
        _seed_memory(bundle, concept_id, title, body, memory_type)
    return root


@pytest.fixture()
def conn(tmp_path):
    c = get_db(str(tmp_path / "eval.db"))
    yield c
    c.close()


def test_ground_truth_pair_loads_with_l4_levels():
    queries = {q.query_id: q for q in eval_mod.load_ground_truth(GT_DIR)}
    assert set(queries) == {"L4-M01", "L4-M02", "L4-M03"}
    assert all(q.level == "L4" for q in queries.values())
    assert queries["L4-M01"].expectations[0].symbol_id.startswith("memory/tribal#")


def test_l4_reports_numeric_recall_and_mrr(conn, bundle_root, monkeypatch):
    def no_l5(*args, **kwargs):
        raise AssertionError("L4 queries must not route through _retrieve_l5")

    monkeypatch.setattr(eval_mod, "_retrieve_l5", no_l5)
    report = eval_mod.run_evaluation(
        conn,
        bundle_root=str(bundle_root),
        queries_path=GT_DIR,
        corpus_filter="L4",
    )

    bucket = report["L4"]
    assert bucket["count"] == 3
    assert bucket["n_queries"] == 3
    assert bucket["n_expectations"] == 3
    assert isinstance(bucket["recall_at_10"], float)
    assert isinstance(bucket["mrr"], float)
    # M01/M02 hit at rank 1; M03's memory is absent -> 2/3 for both metrics,
    # rendered at the report's 4-decimal rounding.
    assert bucket["recall_at_10"] == round(2 / 3, 4)
    assert bucket["mrr"] == round(2 / 3, 4)
    # An eval sweep must not write memory_refs (session_id=None).
    assert conn.execute("SELECT COUNT(*) FROM memory_refs").fetchone()[0] == 0


def test_retrieve_l4_normalizes_tribal_hits(conn, bundle_root):
    results = eval_mod._retrieve_l4(
        conn,
        str(bundle_root),
        "evicting numpy from sys.modules mid-process crashes native extensions",
        10,
    )
    # Disk-read concepts carry path-shaped ids; the normalized name must
    # identify the seeded concept and carry no file path (substring tier).
    assert len(results) == 1
    assert results[0]["name"].endswith(NUMPY_ID)
    assert results[0]["file_path"] == ""


def test_evaluate_l4_query_scores_substring_rank(conn, bundle_root):
    recall, rr = eval_mod.evaluate_l4_query(
        conn, str(bundle_root), "evicting numpy from sys.modules", [NUMPY_ID], k=10
    )
    assert (recall, rr) == (1.0, 1.0)

    recall, rr = eval_mod.evaluate_l4_query(
        conn,
        str(bundle_root),
        "evicting numpy from sys.modules",
        ["memory/tribal#absent"],
        k=10,
    )
    assert (recall, rr) == (0.0, 0.0)


def test_eval_cli_accepts_l4_corpus(bundle_root, tmp_path):
    from cairn.cli import main

    db_path = str(tmp_path / "cli.db")
    get_db(db_path).close()  # initialize schema

    result = CliRunner().invoke(
        main,
        [
            "eval",
            "--db",
            db_path,
            "--knowledge",
            str(bundle_root),
            "--queries",
            str(GT_DIR),
            "--corpus",
            "L4",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["L4"]["count"] == 3
    assert payload["L4"]["recall_at_10"] == round(2 / 3, 4)
    assert payload["L4"]["mrr"] == round(2 / 3, 4)
