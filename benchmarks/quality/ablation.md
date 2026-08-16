# Retrieval-quality ablation — DS-v1 (T024, FR-005/006/007)

Machine-readable source of record: [`ablation.json`](ablation.json)
(schema `cairn-quality-ablation/1`, serialized canonically via
`eval.format_sweep_json` discipline: `json.dumps(..., indent=2,
sort_keys=True)`). This file is its human rendering; where the two could
drift, the JSON wins.

**Dataset**: DS-v1 (`benchmark-datasource`, tree `65e3df39…f87bb`), 58 L1
ground-truth queries / 160 expectations, seeded 50/50 split (seed 24301):
**tune 29 / validate 29** (D-006). **Protocol (D-009)**: torch threads
pinned to 1, local bge-m3, brute-force cosine (no vec0), rerank via the
`CAIRN_RERANK=1` marker with flat pairs and gate margin 0.45, reference-local
machine. Quality figures carry a ±0.002 recall / ±0.006 MRR session band
(rerank-active near-tie flips); the T024 gap-fill session reproduced the
integrity figure 0.4174/0.2862 exactly.

## SC-1 verdict: shortfall documented

| | recall@10 | MRR |
|---|---|---|
| SC-1 target | ≥ 0.50 | ≥ 0.33 |
| Shipped (full set, DS-v1.1 mint) | **0.4174** | **0.2862** |
| Margin | **−0.0826** | **−0.0438** |

**Statement.** SC-1 was not reached. The shipped configuration is unchanged
(variant B / enrich OFF / rerank auto+gate under marker / flat pairs / gate
0.45 / k=60 / w(1,1) / topN none / thr 0.3). Five confirmation-ladder
candidates beat the incumbent on the tune split — up to C_TRIM × enrich ×
rerank-on at 0.6989 tune recall (+0.1161) — and **all five failed the
held-out paired-bootstrap guard** on the 29-query validate split (best:
B × enrich × rerank-off, Δ +0.1123, p = 0.118). The binding constraint is
the ground truth, not the levers: at n = 29 every bootstrap CI straddles
zero (widths ≈ ±0.13–0.15), so no true improvement of the observed size can
clear 95% on DS-v1. Per the honesty clause (spec.md SC-1) the best
**evidenced** configuration ships — the incumbent — the shortfall is
documented here, the match rules were never loosened, and nothing shipped
unverified.

**What would clear the bar**: a DS-v2 with substantially more L1 queries
(at ~2–3× the validate count, a +0.11 true delta clears 95%); its first
candidate is the **enrich + rerank-off** direction — the only direction
every near-miss points to (T011/T015/T017/T019) — with enrichment re-tested
after any corpus-recipe change (T012's order dependency: the sparse leg only
contributes under enrich-on, which is where fusion knobs stop being inert).

## Figure 1 — tune split (n = 29): every lever combination

recall@10 / MRR / p95 ms; **▸ = shipped defaults**; all rows through the
guarded selection seam (`evaluate_on(purpose="selection")`).

| combo | recall@10 | MRR | p95 ms | source |
|---|---|---|---|---|
| **all-levers-off ▸ shipped defaults** | **0.5828** | **0.4444** | 1142.0 | T019 |
| enrich-on (all else incumbent) | 0.5713 | 0.4115 | 1267.1 | T011 + T024 gap-fill |
| fusion: rrf_k=1 | 0.5828 | 0.4444 | 979.7 | T012 |
| fusion: rrf_weights=[2,1] | 0.5828 | 0.4444 | 979.0 | T012 |
| fusion: sparse_top_n=10 | 0.5828 | 0.4444 | 979.7 | T012 |
| fusion: dense_threshold=0.45 | 0.5656 | 0.4272 | 979.6 | T012 |
| fusion: dense_threshold=0.60 | 0.2644 | 0.2636 | 411.6 | T012 |
| rerank-off (shipped config) | 0.5811 | 0.4351 | 28.9 | T012/T017/T019 |
| recipe-A | 0.5828 | 0.4201 | 1151.2 | T015 |
| recipe-C | 0.6759 | 0.3973 | 1130.3 | T015 |
| recipe-B_NO_SCOPE | 0.6530 | 0.4722 | 549.8 | T015 |
| recipe-B_NO_SIG | 0.5828 | 0.4444 | 1100.7 | T015 |
| recipe-B_IDENTITIES | 0.6530 | 0.4722 | 514.7 | T015 |
| recipe-C_TRIM | 0.6759 | 0.3973 | 1052.9 | T015 |
| pair=structured, rerank-on | 0.5897 | 0.3399 | 1302.5 | T017 |
| pair=structured, enrich-on, rerank-on | 0.5952 | 0.4106 | 1634.8 | T017 |
| pair=structured, C_TRIM, rerank-on | 0.6472 | 0.4156 | 1300.3 | T017 |
| enrich-on + rerank-off (B) | 0.6337 | 0.4866 | 56.9 | T019 |
| C_TRIM + rerank-on | 0.6759 | 0.3973 | 1148.9 | T019 |
| C_TRIM + rerank-off | 0.6095 | 0.4806 | 39.5 | T019 |
| C_TRIM + enrich-on + rerank-on | **0.6989** | 0.4455 | 1338.2 | T019 |
| C_TRIM + enrich-on + rerank-off | 0.6469 | **0.4926** | 41.1 | T019 |

Notes. The T012 fusion rows are representatives of an 18-combo / 3-pass
grid: with rerank on, fused order is byte-identical 29/29 across
k{1,10,60} × weights{(2,1),(1,2),(3,1),(1,3)} × topN{10,20}, and with
rerank off all 12 norerank combos read 0.5811/0.4351 — every fusion lever
inert (see findings). T011's enrich row pairs task.md-recorded recalls
(tune 0.5713 / validate 0.2371 / full 0.4042) with MRR/p95 re-derived by
the T024 gap-fill session (its `/tmp` artifact was not retained); the same
session read the near-tie recalls one band-slot differently (tune 0.5828,
full 0.4123) and reproduced the +30% p95 observation (982 → 1267 ms) —
the no-ship verdict (p = 0.7198) was bootstrap-based within T011's own
session and stands.

## Figure 2 — validate split (n = 29) with bootstrap verdicts

Paired bootstrap vs the incumbent's validate recall 0.2521 (10,000
resamples, 95% CI); the accept guard every lever had to clear (T002/D-006).

| candidate | validate recall@10 | validate MRR | Δ recall | p | 95% CI | verdict |
|---|---|---|---|---|---|---|
| incumbent (shipped) | 0.2521 | 0.1279* | — | — | — | reference |
| enrich-on | 0.2371 | 0.1092 | −0.0150 | 0.7198 | — | NO-SHIP (T011) |
| recipe-C_TRIM (= C_TRIM + rerank-on) | 0.2943 | 0.1084 | +0.0422 | 0.579 | [−0.112, +0.192] | NO-SHIP |
| enrich-on + rerank-off (B) | **0.3644** | 0.1240 | **+0.1123** | **0.118** | [−0.024, +0.251] | NO-SHIP (closest) |
| C_TRIM + rerank-off | 0.1957 | 0.1069 | −0.0563 | 0.376 | [−0.184, +0.057] | NO-SHIP |
| C_TRIM + enrich-on + rerank-on | 0.2897 | 0.1089 | +0.0376 | 0.624 | [−0.115, +0.189] | NO-SHIP |
| C_TRIM + enrich-on + rerank-off | 0.3431 | **0.1563** | +0.0911 | 0.210 | [−0.048, +0.236] | NO-SHIP |

\* incumbent validate MRR re-derived by the T024 gap-fill session (the
recorded validate runs kept recall only, for the pairing). The T011
enrich-on validate row is task.md-sourced the same way as Figure 1.

## Figure 3 — full set (n = 58), split disclosed (TC-020)

| config | tune r@10 / MRR | validate r@10 / MRR | **full-set r@10 / MRR** |
|---|---|---|---|
| **all-levers-off ▸ shipped defaults** | 0.5828 / 0.4444 | 0.2521 / 0.1279 | **0.4174 / 0.2862** |
| enrich-on (evidence only) | 0.5713 / 0.4115 | 0.2371 / 0.1092 | 0.4042 / 0.2603 |
| recipe-C_TRIM (evidence only) | 0.6759 / 0.3973 | 0.2943 / 0.1084 | 0.4851 / 0.2529 |

The generalization gap is the story: tune→validate drops 0.27–0.41 recall
across every config measured on both splits (the validate half is harder —
16 of its 29 queries score 0.0 recall at the shipped config, 8 of them
F-tier), which is exactly why the tune leaders' gains could not be
confirmed. The shipped row's full-set figures
equal DS-v1's artifact to 4 decimals (TC-017): measured session-side by
T019 (before == after the ladder), independently by T015's integrity pass,
and again by the T024 gap-fill session — the tuned numbers were bought by
retrieval evidence, not by looser matching, name-collision inflation, or a
drifted judge.

## Structural findings (why the levers moved so little)

1. **FTS5 quoted-phrase defect** — sentence queries reached `search_symbols`
   as ONE quoted MATCH phrase, so the BM25 leg was empty 29/29 with enrich
   off; RRF fused [empty, dense] and every fusion knob measured noise
   (T007/T008/T012).
2. **Cross-encoder flattening** — with rerank on, fused order is
   byte-identical 29/29 across the whole fusion grid; under enrich-on,
   always-fused (0.6231/0.5172) beats always-rerank (0.5713/0.4101) — the
   reranker masks upstream lever gains (T012/T018).
3. **Rerank masking of enrichment** — at ~40× p95 the stage is strictly
   worse on BOTH metrics with enrich on (0.5952/0.4106 vs 0.6337/0.4808);
   its marginal value at the shipped flat config is only +0.0017 recall /
   +0.0093 MRR (T017/T019).
4. **Structured-pair MRR cost** — T016's importance-ordered structured pair
   cost −0.1045 MRR vs flat on identical pools (0.3399 vs 0.4444); the
   structured default was reverted to flat (T016/T017).
5. **Dropout axis inert** — parameters/return_type are empty for 1066/1066
   symbols, so B_NO_SCOPE ≡ B_IDENTITIES and C ≡ C_TRIM chunk identically
   (T013–T015 field audit).

## Provenance and schema

Row sources: T011 (task.md DONE notes + T024 gap-fill), T012 (`sweep_ofat`
/ `sweep_norerank`), T015 (`results_stage1/2`), T017 (`results.json`,
pre-revert structured pairs), T019 (`results_stage1/2/3_marginal`), T024
(`results_gapfill.json` — re-derived the two numbers no surviving artifact
carried, under the D-009 protocol). The self-declared
`cairn-quality-ablation/1` contract — pinned by
`tests/test_ablation_artifact.py` — requires: exactly one
`shipped_defaults: true` row; every row a non-null tune
`{recall_at_10, mrr, p95_ms}` triple (TC-015); `validate`/`full_set` either
measured objects or absent-with-reason; the shipped row's full-set figures
equal to DS-v1's L1 block at 4 decimals (TC-017); and the verdict block to
carry targets, actuals, margins, and the honesty clause. Sweep artifacts
stay beside `quality.json`'s role, never inside it (D-007); the reference
quality table remains the one minted by T023 (DS-v1.1, `render(DS-v1) ==
render(DS-v1.1)`).
