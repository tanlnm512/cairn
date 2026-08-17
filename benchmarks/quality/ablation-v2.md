# Retrieval-quality ablation v2 — k-fold + DS-v2 (T022 skeleton, FR-006)

Machine-readable source of record: [`ablation-v2.json`](ablation-v2.json)
(schema `cairn-quality-ablation/2`, serialized canonically via the same
`format_sweep_json` discipline as v1: `json.dumps(..., indent=2,
sort_keys=True)`). This file is its human rendering; where the two could
drift, the JSON wins.

**STATUS: PENDING.** This is the T022 skeleton — the measurement rows land
with T023 (ds-v2) and T024 (ds-v1-kfold). Nothing in this document is a
measurement; every quantitative figure below is copied verbatim from the
cited committed artifacts (the v1 record, the tech-spec decisions, the test
spec).

## Dataset and measurement families (D-008/D-009/D-011)

Two families, never mixed and never cross-diffed (FR-006 "Chosen
approach"):

1. **ds-v1-kfold** — the legacy set re-measured under k-fold. Same DS-v1
   identity as the v1 record (copied verbatim from `ablation.json`'s
   dataset block): `benchmark-datasource` / DS-v1, tree
   `65e3df39…f87bb`, 58 L1 queries / 160 L1 expectations (82 queries /
   234 expectations total). Per D-009: 5 seeded rotation folds
   (`kfold_partitions`, FR-001), rotation-mean + per-fold spread reported
   descriptively, accept gate = `paired_bootstrap` over pooled per-query
   paired differences (each query validate-side exactly once). The v1
   29/29 seeded split is superseded for this family by the fold rotation.
2. **ds-v2** — the new dataset under `benchmarks/datasource/ds2/` (FR-002,
   D-008: a new directory, immutable once landed, own budget rule). Per
   D-011 (BEIR-style): tune on DS-v1, validate zero-shot on DS-v2's
   corpora; report **per-corpus rows plus macro-average,
   never an aggregate alone, never cross-corpus row diffs**. Per D-010:
   sizing runs a Sakai topic-set-size power analysis (T005) with floors
   **150 L1 queries / 40 L5 queries**; target n = max(150 L1, n_required).
   Per-corpus labels at authoring time: `t2` (in-repo) and `attrs-26.1.0`
   — the second corpus T007 vendored (MIT, 674.9 KB / 67 files, within
   the per-corpus 3072 KB rule) under
   `benchmarks/datasource/ds2/second-corpus/`, named from its DECISION.md;
   T023 finalizes the label set when it mints the DS-v2 rows.

The v1 record (`ablation.json` + `ablation.md`,
`cairn-quality-ablation/1`) is immutable DS-v1-era evidence, pinned by
`tests/test_ablation_artifact.py` (6 tests) and kept byte-identical.
The v2 rows are a new measurement family, **never diffed against v1 rows**
(D-008/D-011) — no v2 row is presented as a delta against a legacy row.

## Row shape (additive columns only)

Every v2 row carries at least: `family` (ds-v1-kfold | ds-v2), `dataset`
(the family's dataset label), `combo`, `recall_at_10`, `mrr`, `p95_ms`,
`db_mb`, and the `mv` marker (true = measured against the `embeddings_mv`
multi-vector store, FR-005; false = the single-vector default). ds-v2 rows
additionally carry `corpus` (a per-corpus label or `macro-average`). The
guards assert `set(row) >=` these keys, so later tasks may add columns but
never remove or retype them.

## Rows — pending

`rows: []` — empty by design. T023 fills the ds-v2 per-corpus +
macro-average rows; T024 fills the ds-v1-kfold ladder rows. Measurement
protocol inherits the v1 D-009 discipline pinned in `ablation.json`
(`measurement.protocol`); the protected baselines are re-measured on
shipping (TC-027: the all-levers-off row equals the committed artifact at
4 decimals).

## SC-1 verdict — PENDING

| | recall@10 | MRR |
|---|---|---|
| SC-1 target (unchanged) | ≥ 0.50 | ≥ 0.33 |
| Actual | *pending* | *pending* |

The targets are the same bar as the first campaign — **0.50 / 0.33,
untouchable by this campaign (TC-026)**; no re-scoped target, no metric
swap. The verdict block in the JSON carries slots that T023/T024 fill:
`fold_count` (minimum 5 per D-009/TC-029) with `per_fold_spread`, and
`ds2_counts` (TC-029 requires ≥ 150 L1 / ≥ 40 L5). Per TC-029 the verdict
must cite the k-fold aggregate over the legacy set AND the DS-v2
measurement with its query counts — never the legacy single split — so a
reader can see the evidence power actually bought. The honesty clause is
unchanged from v1: if the sweep cannot reach the margin without violating
the protected baselines or the precision doctrine, the best evidenced
configuration ships and the shortfall is documented here — never gamed
(spec.md SC-1).

## Standing rules this document is pinned to

- The v1 record files are byte-identical to their committed hashes (pinned
  in `tests/test_ablation_v2_artifact.py`; TC-028).
- v2 rows carry their own dataset/family labels; no v2 row is a delta
  against a v1 row, and ds-v2 aggregates are never presented without their
  per-corpus rows (D-011).
- Match rules are never loosened; SC-1 stays 0.50/0.33 (TC-026/TC-027).
