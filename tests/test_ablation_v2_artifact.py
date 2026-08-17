"""The committed ablation-v2 artifact validates against its self-declared schema.

T022 (FR-006, D-008/D-011) commits ``benchmarks/quality/ablation-v2.json``
(``cairn-quality-ablation/2``) plus its rendering ``ablation-v2.md`` as a
SKELETON: the measurement rows land with T023 (ds-v2 per-corpus +
macro-average) and T024 (ds-v1-kfold ladder). The schema is self-declared —
documented in the artifact's own ``dataset.families``/``row_shape``/
``verdict`` blocks and in ablation-v2.md — and pinned here so drift fails
loudly:

* parses, canonical bytes (sorted keys, trailing newline — the same
  ``format_sweep_json`` discipline the v1 artifact commits to);
* the two measurement families are declared with the DS-v1 identity copied
  verbatim from the v1 record (never retyped by hand);
* every row — now and when T023/T024 fill them — carries ``family`` +
  ``dataset`` labels from the declared families, satisfies the additive
  row-shape contract, and no v2 row presents a delta against a v1 row
  (TC-028/D-008); ds-v2 aggregates never appear without per-corpus rows
  (D-011);
* the verdict block is visibly PENDING with SC-1 targets exactly 0.50/0.33
  (TC-026) and TC-029's slots (fold count >= 5, DS-v2 counts >= 150 L1 /
  >= 40 L5) declared as minimums;
* the v1 record files are byte-identical to the hashes captured at T022
  authoring time via ``git hash-object`` (TC-028's byte-identical pin).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
QUALITY = REPO_ROOT / "benchmarks" / "quality"
ARTIFACT = QUALITY / "ablation-v2.json"
RENDERING = QUALITY / "ablation-v2.md"
V1_JSON = QUALITY / "ablation.json"
V1_MD = QUALITY / "ablation.md"

SCHEMA = "cairn-quality-ablation/2"
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
# git hash-object values, captured at T022 authoring time (TC-028 pin).
V1_JSON_BLOB = "3649dd1c572652b1660d82f53d5d5bcdd1c8c76b"
V1_MD_BLOB = "7112bb0899aef22dfda8080596cc63bbbfb8314c"


def _doc() -> dict:
    return json.loads(ARTIFACT.read_text())


def _git_blob_sha(path: Path) -> str:
    data = path.read_bytes()
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


def test_families_declared_with_verbatim_ds_v1_identity():
    """The ds-v1-kfold family reuses the v1 record's dataset block verbatim."""
    doc = _doc()
    fam = doc["dataset"]["families"]["ds-v1-kfold"]
    assert (fam["name"], fam["version"]) == ("benchmark-datasource", "DS-v1")
    assert fam["corpora"] is None  # single-corpus family: no cross-corpus rows
    # Copied, not retyped: identity equals the v1 record's own dataset block.
    v1 = json.loads(V1_JSON.read_text())
    assert fam["tree_hash"] == v1["dataset"]["tree_hash"]
    assert fam["ground_truth"] == v1["dataset"]["ground_truth"]
    assert fam["ground_truth"]["l1_queries"] == 58
    assert fam["ground_truth"]["l1_expectations"] == 160
    # ds-v2 family: BEIR-style protocol + declared per-corpus labels.
    ds2 = doc["dataset"]["families"]["ds-v2"]
    assert ds2["corpora"] == ["t2", "attrs-26.1.0"]  # from T007's DECISION.md
    assert "never an aggregate alone" in ds2["protocol"]
    assert ds2["sizing"]["floor_l1_queries"] == 150
    assert ds2["sizing"]["floor_l5_queries"] == 40


def test_rows_carry_family_and_dataset_labels():
    """TC-028/D-008/D-011: v2 rows are a new family, never a v1 delta.

    Binds every future row: the assertion set is empty today (skeleton) but
    each row T023/T024 appends must satisfy the label + shape contract.
    """
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
    # Skeleton honesty: a PENDING verdict means no measurements yet.
    if doc["verdict"]["status"] == "pending":
        assert doc["rows"] == []
        assert doc["shipped_defaults"]["row"] is None


def test_verdict_pending_with_sc1_targets_and_tc029_slots():
    """TC-026 (targets 0.50/0.33) + TC-029 slots (folds >= 5, DS-v2 counts)."""
    doc = _doc()
    v = doc["verdict"]
    assert v["status"] == "pending"
    assert v["outcome"] == "pending"
    assert v["sc1_targets"] == {"recall_at_10": 0.50, "mrr": 0.33}
    assert "never gamed" in v["honesty_clause"]
    # Slots for T023/T024 to fill — declared with their TC-029 minimums.
    assert v["fold_count_minimum"] == 5
    for slot in ("fold_count", "per_fold_spread"):
        assert slot in v
    assert v["ds2_counts"]["minimum"] == {"l1_queries": 150, "l5_queries": 40}
    for slot in ("l1_queries", "l5_queries"):
        assert slot in v["ds2_counts"]
    # While pending, no actuals or margins exist to be misread.
    assert v["sc1_actual"] is None and v["margins"] is None


def test_v1_record_files_are_byte_identical():
    """TC-028: the legacy record stays byte-identical to its committed hash.

    Values captured at T022 authoring time via ``git hash-object``; the
    helper recomputes the same blob hash (sha1 over ``blob <len>\\0`` +
    bytes) so the pin needs no git binary at test time.
    """
    assert _git_blob_sha(V1_JSON) == V1_JSON_BLOB
    assert _git_blob_sha(V1_MD) == V1_MD_BLOB


def test_rendering_states_pending_targets_and_family_isolation():
    """The human rendering carries the PENDING verdict + isolation rules."""
    md = RENDERING.read_text()
    assert "STATUS: PENDING" in md
    assert "cairn-quality-ablation/2" in md
    assert "ablation-v2.json" in md  # source-of-record pointer
    # TC-026: the same bar as the first campaign, no goalpost moves.
    assert "| SC-1 target (unchanged) | ≥ 0.50 | ≥ 0.33 |" in md
    assert "0.50 / 0.33" in md
    # D-008/D-011: new family, never diffed against v1; never aggregate alone.
    assert "never diffed against v1 rows" in md
    assert "never an aggregate alone" in md
    for family in FAMILIES:
        assert family in md, family
    assert "attrs-26.1.0" in md  # second-corpus label from T007's DECISION.md
    # Row shape documented: every additive column is named in the rendering.
    for column in sorted(ROW_KEYS):
        assert column in md, column
