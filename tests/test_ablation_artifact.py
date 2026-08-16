"""The committed ablation artifact validates against its self-declared schema.

T024 (FR-005/FR-006/FR-007) commits ``benchmarks/quality/ablation.json``
(``cairn-quality-ablation/1``) plus its rendering ``ablation.md``. The
schema is self-declared — documented in the artifact's own
``measurement``/``shipped_defaults``/``verdict`` blocks and in ablation.md's
"Provenance and schema" — and pinned here so drift fails loudly:

* parses, canonical bytes (sorted keys, trailing newline — the
  ``format_sweep_json`` discipline the artifact commits to);
* exactly one row carries ``shipped_defaults: true`` (TC-015);
* every row's tune split is a non-null ``{recall_at_10, mrr, p95_ms}``
  triple (TC-015's three columns);
* every ``validate``/``full_set`` is either a measured object (recall@10 +
  MRR, optional bootstrap) or a ``{"reason": str}`` placeholder — never a
  bare null (the null-with-reason contract);
* the shipped row's full-set figures equal DS-v1's L1 block at 4 decimals
  (TC-017: numbers bought by retrieval, not looser matching);
* the verdict block states SC-1's targets/actuals/margins consistently with
  the honesty clause (spec.md SC-1) and names the shortfall outcome.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = REPO_ROOT / "benchmarks" / "quality" / "ablation.json"
RENDERING = REPO_ROOT / "benchmarks" / "quality" / "ablation.md"
DS_V1 = REPO_ROOT / "benchmarks" / "baselines" / "DS-v1" / "quality.json"

SCHEMA = "cairn-quality-ablation/1"


def _doc() -> dict:
    return json.loads(ARTIFACT.read_text())


def test_parses_with_self_declared_schema_and_canonical_bytes():
    raw = ARTIFACT.read_text()
    doc = json.loads(raw)
    assert doc["schema"] == SCHEMA
    # Canonical serialization: sorted keys, 2-space indent, one trailing \n.
    assert raw == json.dumps(doc, indent=2, sort_keys=True) + "\n"
    # Dataset identity: the DS-v1 ground truth the sweep ran against.
    ds = doc["dataset"]
    assert (ds["name"], ds["version"]) == ("benchmark-datasource", "DS-v1")
    assert ds["split"]["tune"] == 29 and ds["split"]["validate"] == 29
    assert ds["split"]["tune"] + ds["split"]["validate"] == ds["ground_truth"]["l1_queries"]


def test_exactly_one_shipipped_defaults_row_and_tune_triples_complete():
    doc = _doc()
    shipped = [r for r in doc["rows"] if r["shipped_defaults"]]
    assert len(shipped) == 1
    assert shipped[0]["combo"] == doc["shipped_defaults"]["row"]
    for row in doc["rows"]:
        tune = row["tune"]
        assert isinstance(tune["recall_at_10"], float)
        assert isinstance(tune["mrr"], float)
        assert isinstance(tune["p95_ms"], (int, float))
        assert 0.0 <= tune["recall_at_10"] <= 1.0
        assert 0.0 <= tune["mrr"] <= 1.0
        assert tune["p95_ms"] > 0
        # Provenance: every row names its sweep task.
        assert row["source"].startswith("T0"), row["combo"]


def test_split_objects_are_measured_or_null_with_reason():
    doc = _doc()
    for row in doc["rows"]:
        for split in ("validate", "full_set"):
            block = row[split]
            assert isinstance(block, dict), (row["combo"], split)
            if "reason" in block:
                assert isinstance(block["reason"], str) and block["reason"]
            else:
                assert set(block) >= {"recall_at_10", "mrr"}, (row["combo"], split)


def test_shipped_row_reproduces_ds_v1_baseline_at_4_decimals():
    """TC-017: the all-levers-off full-set row equals the immutable BEFORE."""
    doc = _doc()
    ds_v1 = json.loads(DS_V1.read_text())
    shipped = next(r for r in doc["rows"] if r["shipped_defaults"])
    assert shipped["full_set"]["recall_at_10"] == round(ds_v1["L1"]["recall_at_10"], 4)
    assert shipped["full_set"]["mrr"] == round(ds_v1["L1"]["mrr"], 4)
    assert shipped["full_set"]["recall_at_10"] == 0.4174
    assert shipped["full_set"]["mrr"] == 0.2862


def test_verdict_block_states_sc1_shortfall_honestly():
    doc = _doc()
    v = doc["verdict"]
    assert v["sc1_targets"] == {"recall_at_10": 0.50, "mrr": 0.33}
    assert v["sc1_actual"] == {"recall_at_10": 0.4174, "mrr": 0.2862}
    assert v["margins"] == {"recall_at_10": -0.0826, "mrr": -0.0438}
    assert v["outcome"] == "shortfall-documented"
    assert "never gamed" in v["honesty_clause"]
    assert v["evidence_pointers"] and v["what_would_clear_the_bar"]
    # The near-miss evidence the verdict cites must exist as measured rows.
    combos = {r["combo"] for r in doc["rows"]}
    assert "enrich-on + rerank-off (B)" in combos
    assert "C_TRIM + enrich-on + rerank-on" in combos
    near = next(r for r in doc["rows"] if r["combo"] == "enrich-on + rerank-off (B)")
    assert round(near["validate"]["bootstrap"]["p_value"], 3) == 0.118
    assert near["validate"]["bootstrap"]["significant"] is False


def test_rendering_carries_three_labeled_figures_and_verdict():
    """TC-020: tune/validate/full-set figures for recall AND MRR, labeled."""
    md = RENDERING.read_text()
    for label in ("Figure 1 — tune split", "Figure 2 — validate split",
                  "Figure 3 — full set"):
        assert label in md, label
    assert "0.4174 / 0.2862" in md  # shipped full-set row, both metrics
    assert "0.5828 / 0.4444" in md  # tune pair
    assert "0.2521 / 0.1279" in md  # validate pair
    assert "shortfall documented" in md
    for finding in ("FTS5 quoted-phrase defect", "Cross-encoder flattening",
                    "Structured-pair MRR cost"):
        assert finding in md
