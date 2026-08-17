# Retrieval-quality ablation v2 — k-fold + DS-v2 (T022 skeleton, FR-006)

Machine-readable source of record: [`ablation-v2.json`](ablation-v2.json)
(schema `cairn-quality-ablation/2`, serialized canonically via the same
`format_sweep_json` discipline as v1: `json.dumps(..., indent=2,
sort_keys=True)`). This file is its human rendering; where the two could
drift, the JSON wins.

**STATUS: PENDING.** The FR-006 verdict is still pending (T023 fills the
ds-v2 rows, T024 the ds-v1-kfold ladder, and the ladder mints the v2
shipped-defaults row). The document is no longer empty, though: **T014
(FR-003) landed the first measurement rows** — the cutoff-calibration
family below, measured 2026-08-17 on the D-009 protocol. Every other
quantitative figure outside the FR-003 section is copied verbatim from the
cited committed artifacts.

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

## Rows — T014 (FR-003) calibration rows landed; T023/T024 pending

The ds-v1-kfold family's first rows are T014's FR-003 cutoff calibration:
the all-levers-off **integrity row** plus the `enrich+enrich_idf` grid, 5
seeded rotation folds over the 58 L1 queries (D-009; per-query outcomes
pooled exactly once each; the tune/validate columns are the seed-24301
29/29 halves reconstructed from the same per-query maps — they reproduce
the committed Figure 1/2 anchors 0.5828/0.4444 and 0.2521/0.1279 to 4
decimals). T023 fills the ds-v2 rows; T024 the ladder rows. All rows carry
`family`/`dataset`/`combo`/`recall_at_10`/`mrr`/`p95_ms`/`db_mb`/`mv`.

| combo (ds-v1-kfold) | tune r@10 / MRR | validate r@10 / MRR | pooled r@10 / MRR | p95 source |
|---|---|---|---|---|
| all-levers-off (integrity) | 0.5828 / 0.4444 | 0.2521 / 0.1279 | **0.4174 / 0.2862** | quiet re-measure |
| enrich+enrich_idf@df_max=0.75 | 0.5828 / 0.4115 | 0.2417 / 0.1092 | 0.4123 / 0.2603 | sweep (contention caveat) |
| enrich+enrich_idf@df_max=0.80 | 0.5828 / 0.4115 | 0.2417 / 0.1092 | 0.4123 / 0.2603 | sweep (contention caveat) |
| enrich+enrich_idf@df_max=0.85 | 0.5828 / 0.4115 | 0.2417 / 0.1092 | 0.4123 / 0.2603 | sweep (contention caveat) |
| enrich+enrich_idf@df_max=**0.90 (shipped)** | 0.5828 / 0.4115 | 0.2417 / 0.1092 | 0.4123 / 0.2603 | quiet re-measure |

**Integrity gate (the hard gate, first run of the session).** The
all-levers-off k-fold rotation reproduces the committed DS-v1 session
baseline **exactly** — pooled 0.4174/0.2862, drift 0.0000 against the
documented ±0.002 recall / ±0.006 MRR band — and the tune/validate
reconstructions match the committed anchors to 4 decimals. The quiet
re-measure pass reproduced the figures again (per-query equality with the
sweeps: all 58/58, both configs).

**Shipped cutoff: 0.90 — unchanged in code** (`ENRICH_DF_MAX_FRACTION =
0.90`, D-004). The grid gives no reason to move it: all four cutoffs
produce byte-identical per-query outcomes (pooled 0.4123/0.2603; bootstrap
vs all-levers-off Δ −0.0052, p = 0.82, CI straddles zero), so no cutoff
beats 0.90 under paired bootstrap and ties resolve to the default (AC3).
The grid was truncated to {0.75, 0.80, 0.85, 0.90} by D-014 (wall-clock
descope); AC3's wording permits calibration within 0.75–0.95, and 0.95
measures the same drop-set as 0.90 on this corpus (nothing above 0.8583).

**Why the band is inert on DS-v1 (the calibration's actual finding).** The
corpus's `term_df` distribution puts the highest token at `test` 0.8583
(915/1066); `url` — the token behind the recorded enrichment regression —
sits at **0.2711** (289/1066). A max_df cutoff at any value in [0.75,
0.95] therefore drops nothing that any of the 58 queries' enrichments
append, and the four grid rows are byte-identical to DF-blind enrichment.
The regression L1-D03 records is not a >90%-ubiquity effect on this corpus.

**AC4 (TC-013), measured honestly — not met by the cutoff lever in-band.**
The previously-passing tune set (23 queries with recall > 0 under the
integrity run, anchored to the first campaign by the band check) shows
exactly one regression to zero under every in-band cutoff: **L1-I03**
(`What breaks if split_url's parsing rules change?`). L1-D03 itself (which
lands in the seed-24301 **validate** half) stays at recall 0.0 — its
`url` identifier is not droppable in-band. The diagnostic
(`fr003-calibration/d03-diagnostic.json`) proves the mechanism and the
root causes: below the band (cutoff 0.25 < 0.2711) `URL` drops from both
legs and L1-D03 returns to its incumbent state (recall 1.0 at rank 6 —
the committed "1.0 → 0.0" fall was recall, and the incumbent rank is 6,
not 1); L1-I03 does **not** recover even at 0.25 because its rare `split`
identifier (DF 0.0047) keeps the dilution — an identifier-append effect
the DF lever cannot repair by construction (it suppresses ubiquity, not
specificity). The unit-level repair proofs stand (T012/T013 boundary
tests); the corpus-level disposition of AC4 is the orchestrator's call on
this evidence.

**p95 discipline.** The k-fold sweeps overlapped a full test suite on the
same machine (orchestrator-confirmed), so in-sweep durations are
contention-inflated; the integrity and shipped rows carry quiet-machine
re-measured p95 (`p95-remeasure.json`), the non-shipped grid rows carry
in-sweep p95 under that stated caveat. recall/MRR are deterministic under
the protocol pins and stand as measured.

Raw sweeps, the analysis record, the quiet p95 pass, and the diagnostic
live under `benchmarks/quality/fr003-calibration/` (see its README).

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
