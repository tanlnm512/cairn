# Ladder-prep figures (D-016) — enrich-direction candidates, DS-v1 5-fold

Protocol (D-009, inherited from T014's fr003-calibration runner): torch threads 1, local bge-m3, brute-force cosine, rerank under the CAIRN_RERANK=1 marker (flat pairs, gate 0.45); 5-fold seeded rotation (fold_seed 24301) over the 58 L1 queries through the unchanged evaluate_on seam; candidates carry rerank=False (which wins over the marker), everything else incumbent. Candidate (b) ran at the SHIPPED query_enrich.ENRICH_DF_MAX_FRACTION — never overridden.

Cells are recall@10/MRR. `*` in-sweep p95 — measured in the serial run on an otherwise-quiet reference machine (MEASURE.md; the D-015 parallel-wave plan was dropped before any run), so it is a quiet-machine figure rather than a contention caveat.

## Headline — does either pooled Δ clear 95% at n=58?

**YES.** (a) enrich+rerank-off: Δ +0.0988, p = 0.0491, CI [+0.0037, +0.2006] — clears; (b) enrich_idf+rerank-off: Δ +0.0988, p = 0.0491, CI [+0.0037, +0.2006] — clears. The v1 single-split near-miss was Δ+0.1123 at p=0.118 on n=29; D-009 pooling doubles n to 58 via the rotation.

### enrich+rerank-off

| | pooled (n=58) | tune (n=29) | validate (n=29) |
|---|---|---|---|
| candidate | 0.5163/0.3131 | 0.6337/0.4981 (p95 52.8 ms*) | 0.3989/0.1282 (p95 55.3 ms*) |
| incumbent (all-levers-off) | 0.4174/0.2862 | 0.5828/0.4444 (p95 1038.9 ms*) | 0.2521/0.1279 (p95 1049.7 ms*) |

- Pooled paired bootstrap vs incumbent (D-009 aggregate, n=58): **Δ = +0.0988**, p = 0.0491, 95% CI [+0.0037, +0.2006] (t-test cross-check p = 0.0530).
- 95% guard verdict: **CLEARS** (guard = significant AND Δ>0 AND CI excludes 0).
- Per-fold descriptive spread (never the significance basis): rotation-mean Δ +0.0989, per-fold Δ range [+0.0680, +0.1239].
- Baseline integrity vs committed DS-v1 full-set 0.4174/0.2862: 0.4174/0.2862, within band: True (recall ±0.002, MRR ±0.006).

### enrich_idf+rerank-off

| | pooled (n=58) | tune (n=29) | validate (n=29) |
|---|---|---|---|
| candidate | 0.5163/0.3131 | 0.6337/0.4981 (p95 56.7 ms*) | 0.3989/0.1282 (p95 45.8 ms*) |
| incumbent (all-levers-off) | 0.4174/0.2862 | 0.5828/0.4444 (p95 1029.4 ms*) | 0.2521/0.1279 (p95 1040.8 ms*) |

- Pooled paired bootstrap vs incumbent (D-009 aggregate, n=58): **Δ = +0.0988**, p = 0.0491, 95% CI [+0.0037, +0.2006] (t-test cross-check p = 0.0530).
- 95% guard verdict: **CLEARS** (guard = significant AND Δ>0 AND CI excludes 0).
- Per-fold descriptive spread (never the significance basis): rotation-mean Δ +0.0989, per-fold Δ range [+0.0680, +0.1239].
- Baseline integrity vs committed DS-v1 full-set 0.4174/0.2862: 0.4174/0.2862, within band: True (recall ±0.002, MRR ±0.006).

## (a) vs (b): the FR-003 repair's effect in this direction

Per-query outcomes BYTE-IDENTICAL between enrich+rerank-off and enrich_idf+rerank-off at the shipped cutoff (0/58 queries differ). Consistent with T014's calibration finding: DS-v1's highest term_df fraction is 'test' at 0.8583, so the 0.90 cutoff's drop-set is empty and enrich_idf is inert on this corpus.

## Artifacts

| Artifact | Role |
|---|---|
| `rows-ds2.json` | Machine-checkable DS-v2 rows for the `ablation-v2.json` merge (schema `cairn-ds2-rows/1`): a flat `rows` list with one row per lever combo × level × corpus cell (`combo`, `corpus`, `level`, `n_queries`, `recall_at_10`, `mrr`, `p95_ms`, `mv`), plus the `counts`, `db_mb`, and `l5_legs` blocks. |
| `sweep-ds2-zeroshot.json` | The DS-v2 zero-shot validation leg of the D-016 candidate ladder (schema `cairn-ds2-zeroshot/1`): `configs` holds per-combo aggregates (`l1_macro_average`, `l1_per_corpus`, `l5_leg`, `bootstrap_vs_incumbent_by_corpus`), `candidates` maps each candidate name to its lever combo, and `protocol`/`counts`/`db_mb` record the run context. |
