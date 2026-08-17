"""The committed ablation artifact validates against its self-declared schema.

The retrieval-quality record was UNIFIED on 2026-08-17 (owner request,
after the two campaign PRs — #37 then #38 — left v1/v2 sibling artifacts):
``benchmarks/quality/ablation.{json,md}`` is now the single record,
schema ``cairn-quality-ablation/2``. The second campaign's content is the
document body; the first campaign's ``cairn-quality-ablation/1`` record is
embedded VERBATIM under ``campaigns.retrieval-quality-v1`` (originals in
git history at merge commit 7d9049e and earlier; blob hashes recorded in
the artifact). The guards below pin, so drift fails loudly:

* parses, canonical bytes (sorted keys, trailing newline — the
  ``format_sweep_json`` discipline the artifact commits to);
* the embedded first-campaign record is BYTE-IDENTICAL to its recorded
  blob hashes (the TC-028 pin, moved from the removed sibling files onto
  the embedded copy and the appendix's verbatim block);
* the two measurement families are declared with the DS-v1 identity
  copied verbatim from the embedded first-campaign record;
* every row carries ``family`` + ``dataset`` labels from the declared
  families, satisfies the additive row-shape contract, and no v2 row
  presents a delta against a v1 row (TC-028/D-008/D-011); ds-v2
  aggregates never appear without per-corpus rows;
* the ``mv`` marker follows the combo's lever, not a row-constructor
  constant: a row whose combo IS the multivector lever carries
  ``mv=true`` in every family (the DS-v2 runner once hardcoded ``mv``
  false, mislabeling the refuted-transfer rows);
* the (closed) verdict: SC-1 targets exactly 0.50/0.33, evidence slots
  filled (folds >= 5, DS-v2 counts), per-leg actuals, the document-branch
  close with the best candidate's intervals and the next constraint;
* the embedded first-campaign record keeps its own invariants: one
  shipped_defaults row reproducing DS-v1's L1 block at 4 decimals
  (TC-017) and the honest SC-1 shortfall verdict with the p=0.118
  near-miss row it cites.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
QUALITY = REPO_ROOT / "benchmarks" / "quality"
ARTIFACT = QUALITY / "ablation.json"
RENDERING = QUALITY / "ablation.md"
DS_V1 = REPO_ROOT / "benchmarks" / "baselines" / "DS-v1" / "quality.json"

SCHEMA = "cairn-quality-ablation/2"
V1_SCHEMA = "cairn-quality-ablation/1"
FAMILIES = {"ds-v1-kfold", "ds-v2"}
FAMILY_VERSIONS = {"ds-v1-kfold": "DS-v1", "ds-v2": "DS-v2"}
# The additive row-shape contract (D-008 consequences): guards compare with
# >= so later tasks may add columns but never remove or retype these.
ROW_KEYS = {
    "family",
    "dataset",
    "combo",
    "recall_at_10",
    "mrr",
    "p95_ms",
    "db_mb",
    "mv",
}
MACRO = "macro-average"
# Blob hashes of the original standalone /1 files at unification time
# (TC-028 pin carried over; git history keeps the files at 7d9049e and
# earlier — CI's shallow checkout cannot, hence the embedded-copy pin).
V1_JSON_BLOB = "3649dd1c572652b1660d82f53d5d5bcdd1c8c76b"
V1_MD_BLOB = "7112bb0899aef22dfda8080596cc63bbbfb8314c"
V1_APPENDIX_BEGIN = "<!-- verbatim-begin (cairn-quality-ablation/1 ablation.md) -->"
V1_APPENDIX_END = "<!-- verbatim-end (cairn-quality-ablation/1 ablation.md) -->"


def _doc() -> dict:
    return json.loads(ARTIFACT.read_text())


def _v1() -> dict:
    return _doc()["campaigns"]["retrieval-quality-v1"]["record"]


def _git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\x00" % len(data) + data).hexdigest()


def test_parses_with_self_declared_schema_and_canonical_bytes():
    raw = ARTIFACT.read_text()
    doc = json.loads(raw)
    assert doc["schema"] == SCHEMA
    # Canonical serialization: sorted keys, 2-space indent, one trailing \n
    # (the format_sweep_json discipline inherited from the v1 record).
    assert raw == json.dumps(doc, indent=2, sort_keys=True) + "\n"
    # Family declaration: exactly the two FR-006 measurement families.
    assert set(doc["dataset"]["families"]) == FAMILIES


def test_embedded_first_campaign_is_byte_identical_to_its_recorded_blobs():
    """TC-028's pin, moved onto the embedded copy at unification."""
    doc = _doc()
    camp = doc["campaigns"]["retrieval-quality-v1"]
    assert camp["original_schema"] == V1_SCHEMA
    embedded = (json.dumps(camp["record"], indent=2, sort_keys=True) + "\n").encode()
    assert camp["original_blobs"]["ablation.json"] == V1_JSON_BLOB
    assert _git_blob_sha(embedded) == V1_JSON_BLOB
    # The appendix's verbatim block is the same bytes as the original md
    # (one template newline separates each marker from the content).
    md = RENDERING.read_text()
    assert md.count(V1_APPENDIX_BEGIN) == 1 and md.count(V1_APPENDIX_END) == 1
    block = md.split(V1_APPENDIX_BEGIN, 1)[1].split(V1_APPENDIX_END, 1)[0]
    assert block.startswith("\n") and block.endswith("\n")
    assert _git_blob_sha(block[1:].encode()) == V1_MD_BLOB


def test_families_declared_with_verbatim_ds_v1_identity():
    """The ds-v1-kfold family reuses the v1 record's dataset block verbatim."""
    doc = _doc()
    fam = doc["dataset"]["families"]["ds-v1-kfold"]
    assert (fam["name"], fam["version"]) == ("benchmark-datasource", "DS-v1")
    assert fam["corpora"] is None  # single-corpus family: no cross-corpus rows
    # Copied, not retyped: identity equals the embedded v1 record's own
    # dataset block.
    v1 = _v1()
    assert fam["tree_hash"] == v1["dataset"]["tree_hash"]
    assert fam["ground_truth"] == v1["dataset"]["ground_truth"]
    assert fam["ground_truth"]["l1_queries"] == 58
    assert fam["ground_truth"]["l1_expectations"] == 160
    # ds-v2 family: BEIR-style protocol + declared per-corpus labels.
    ds2 = doc["dataset"]["families"]["ds-v2"]
    assert ds2["corpora"] == ["yarl", "attrs-26.1.0"]  # manifest convention
    assert "never an aggregate alone" in ds2["protocol"]
    assert ds2["sizing"]["floor_l1_queries"] == 150
    assert ds2["sizing"]["floor_l5_queries"] == 40


def test_rows_carry_family_and_dataset_labels():
    """TC-028/D-008/D-011: v2 rows are a new family, never a v1 delta."""
    doc = _doc()
    declared = doc["dataset"]["families"]
    for row in doc["rows"]:
        assert set(row) >= ROW_KEYS, row.get("combo")
        assert row["family"] in FAMILIES, row["combo"]
        assert row["dataset"] == FAMILY_VERSIONS[row["family"]], row["combo"]
        # No v2 row is presented as a delta against a v1 row (D-008).
        assert not any("vs_v1" in k or "vs-v1" in k for k in row), row["combo"]
        if row["family"] == "ds-v2":
            assert row["corpus"] in set(declared["ds-v2"]["corpora"]) | {MACRO}
    # D-011: a ds-v2 macro-average never appears without per-corpus rows.
    ds2_rows = [r for r in doc["rows"] if r["family"] == "ds-v2"]
    if any(r["corpus"] == MACRO for r in ds2_rows):
        assert any(r["corpus"] != MACRO for r in ds2_rows)
    # The embedded /1 rows (the legacy single-split family) never leak into
    # the /2 rows array — different measurement protocols, one document.
    assert all(r["family"] in FAMILIES for r in doc["rows"])
    assert not any("full_set" in r for r in doc["rows"])
    # Honesty coupling, final state: the shipped_defaults row and the
    # verdict status move together (T024 closed on the document branch).
    sd = doc["shipped_defaults"]
    if sd["row"] is None:
        assert "no-ship" in sd["status"], sd["status"]
        assert doc["verdict"]["outcome"] == "documented-shortfall-no-ship"
    else:
        assert "no-ship" not in sd["status"], sd["status"]
        assert doc["verdict"]["outcome"] not in ("pending", "documented-shortfall-no-ship")


def test_mv_marker_follows_the_multivector_lever_not_a_constant():
    """The mv lever marker is a function of the combo, never a hardcode.

    The DS-v2 zero-shot runner once hardcoded ``"mv": False`` in its row
    constructors, so the committed artifact labeled the multivector
    combo's DS-v2 rows (attrs / yarl / macro-average) single-vector —
    wrong lever metadata for the campaign's headline zero-shot
    refutation (DS-v1 SC-1 0.5588/0.3395, refuted at macro
    0.4632/0.2844). Invariant, every family: a row whose combo is the
    multivector lever was measured against the ``embeddings_mv`` store
    and must carry ``mv=true``; weaker, DS-v2 rows of every other combo
    measured flag-off shapes and must carry ``mv=false``.
    """
    doc = _doc()
    for row in doc["rows"]:
        if row["combo"] == "multivector":
            assert row["mv"] is True, (row["family"], row.get("corpus"))
    for row in doc["rows"]:
        if row["family"] == "ds-v2" and row["combo"] != "multivector":
            assert row["mv"] is False, (row["combo"], row.get("corpus"))
    # The intermediate payload that merge_t023.py folds into the artifact
    # carries the same invariant — a merge re-run must not reintroduce it.
    ds2_rows = json.loads(QUALITY.joinpath(
        "ladder-v2", "rows-ds2.json").read_text())["rows"]
    for row in ds2_rows:
        expected = row["combo"] == "multivector"
        assert row["mv"] is expected, (row["combo"], row.get("corpus"))


def test_verdict_evidence_filled_targets_unchanged():
    """TC-026 (targets 0.50/0.33) + TC-029 evidence slots, closed (T024)."""
    doc = _doc()
    v = doc["verdict"]
    assert v["status"] == "done"
    assert v["outcome"] == "documented-shortfall-no-ship"
    assert v["sc1_targets"] == {"recall_at_10": 0.50, "mrr": 0.33}
    assert "never gamed" in v["honesty_clause"]
    # TC-029 slots, FILLED: fold count >= 5 with a spread, DS-v2 counts
    # above their floors.
    assert v["fold_count_minimum"] == 5
    assert v["fold_count"] >= v["fold_count_minimum"]
    spread = v["per_fold_spread"]
    assert isinstance(spread, dict) and "delta_min" in spread and "delta_max" in spread
    assert v["ds2_counts"]["minimum"] == {"l1_queries": 150, "l5_queries": 40}
    assert v["ds2_counts"]["l1_queries"] >= 150
    assert v["ds2_counts"]["l5_queries"] >= 40
    # The actuals are per-leg (D-011: never a single-leg figure alone):
    # DS-v1 k-fold best AND DS-v2 macro best, with the full-evidence verdict.
    assert v["sc1_actual"]["ds_v1_kfold_best"]["both_targets_reached"] is True
    assert v["sc1_actual"]["ds_v2_macro_best"]["both_targets_reached"] is False
    assert v["sc1_actual"]["reached_on_full_evidence_base"] is False
    assert v["margins"]["ds_v2_macro_best_vs_targets"]["recall_at_10"] < 0
    # The zero-shot refutation is part of the record, not a footnote:
    assert v["sc1_actual"]["ds_v1_kfold_best"]["zero_shot_validated"] is False
    # The document branch's required content (MEASURE.md Step 5): the best
    # candidate's interval + p on BOTH legs, and a named next constraint.
    best = v["best_candidate_record"]
    assert best["candidate"] == "multivector"
    assert best["ds_v1_kfold"]["cleared"] is True and best["ds_v1_kfold"]["p_value"] < 0.05
    assert best["ds_v2_zero_shot"]["cleared"] is False
    assert "generalization" in v["next_binding_constraint"]["constraint"]


def test_embedded_first_campaign_keeps_its_own_invariants():
    """The /1 record's guards, re-anchored onto the embedded copy."""
    v1 = _v1()
    # Exactly one shipped_defaults row, and it reproduces DS-v1's L1 block
    # at 4 decimals (TC-017: numbers bought by retrieval, not looser
    # matching) against the immutable minted baseline.
    shipped = [r for r in v1["rows"] if r["shipped_defaults"]]
    assert len(shipped) == 1
    assert shipped[0]["combo"] == v1["shipped_defaults"]["row"]
    ds_v1 = json.loads(DS_V1.read_text())
    assert shipped[0]["full_set"]["recall_at_10"] == round(ds_v1["L1"]["recall_at_10"], 4)
    assert shipped[0]["full_set"]["mrr"] == round(ds_v1["L1"]["mrr"], 4)
    assert shipped[0]["full_set"]["recall_at_10"] == 0.4174
    assert shipped[0]["full_set"]["mrr"] == 0.2862
    # The honest SC-1 shortfall verdict with the near-miss it cites.
    v = v1["verdict"]
    assert v["sc1_targets"] == {"recall_at_10": 0.50, "mrr": 0.33}
    assert v["sc1_actual"] == {"recall_at_10": 0.4174, "mrr": 0.2862}
    assert v["margins"] == {"recall_at_10": -0.0826, "mrr": -0.0438}
    assert v["outcome"] == "shortfall-documented"
    assert "never gamed" in v["honesty_clause"]
    near = next(
        r for r in v1["rows"] if r["combo"] == "enrich-on + rerank-off (B)"
    )
    assert round(near["validate"]["bootstrap"]["p_value"], 3) == 0.118
    assert near["validate"]["bootstrap"]["significant"] is False


def test_rendering_carries_closed_verdict_and_family_isolation():
    """The human rendering carries the CLOSED verdict + isolation rules."""
    md = RENDERING.read_text()
    assert "STATUS: CLOSED (T024" in md
    assert "no ship" in md
    assert "cairn-quality-ablation/2" in md
    assert "ablation.json" in md  # source-of-record pointer
    # TC-026: the same bar as the first campaign, no goalpost moves.
    assert "| SC-1 target (unchanged) | ≥ 0.50 | ≥ 0.33 |" in md
    assert "0.50 / 0.33" in md
    # D-008/D-011: new family, never diffed against v1; never aggregate alone.
    assert "never diffed against v1 rows" in md
    assert "never an aggregate alone" in md
    for family in FAMILIES:
        assert family in md, family
    assert "attrs-26.1.0" in md  # second-corpus label (manifest convention)
    # Row shape documented: every additive column is named in the rendering.
    for column in sorted(ROW_KEYS):
        assert column in md, column
    # The appendix keeps the first campaign's labeled figures and verdict.
    for label in ("Figure 1 — tune split", "Figure 2 — validate split",
                  "Figure 3 — full set"):
        assert label in md, label
    assert "shortfall documented" in md
    for finding in ("FTS5 quoted-phrase defect", "Cross-encoder flattening",
                    "Structured-pair MRR cost"):
        assert finding in md, finding
