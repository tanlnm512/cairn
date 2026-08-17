# Retrieval-quality ablation record — unified (both campaigns)

Machine-readable source of record: [`ablation.json`](ablation.json)
(schema `cairn-quality-ablation/2`, serialized canonically:
`json.dumps(..., indent=2, sort_keys=True)`). This file is its human
rendering; where the two could drift, the JSON wins. The two campaign
records were unified into this one document on 2026-08-17 (owner
request): the second campaign's content is the body below, the first
campaign's `cairn-quality-ablation/1` record is preserved verbatim in
the appendix (and byte-identically under `campaigns` in the JSON, blob
hashes recorded there).

**STATUS: CLOSED (T024 — document branch, no ship).** The evidence is
complete: T014 (FR-003 calibration), T020 (FR-004 PRF), T021 (FR-005
multi-vector), and T023 (the confirmation ladder + DS-v2 zero-shot)
landed their rows, measured 2026-08-17 on the D-009 protocol. Exactly
one branch was taken: **no configuration ships; defaults are unchanged**
(the incumbent all-levers-off remains the shipped configuration, and
because nothing ships, no protected baseline is re-measured — the
committed figures of record stand). Every quantitative figure in this
document is measured or copied verbatim from the cited committed
artifacts.

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
`cairn-quality-ablation/1`) is immutable DS-v1-era evidence, kept byte-identical
inside `campaigns.retrieval-quality-v1` (pinned by the guard tests).
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

## Rows — T014 + T020 + T021 + T023 landed; verdict closed (T024)

The ds-v1-kfold family's first rows are T014's FR-003 cutoff calibration:
the all-levers-off **integrity row** plus the `enrich+enrich_idf` grid, 5
seeded rotation folds over the 58 L1 queries (D-009; per-query outcomes
pooled exactly once each; the tune/validate columns are the seed-24301
29/29 halves reconstructed from the same per-query maps — they reproduce
the committed Figure 1/2 anchors 0.5828/0.4444 and 0.2521/0.1279 to 4
decimals). T023 added the confirmation-ladder rows and the DS-v2 family
below; T024 closed the verdict (document branch — no ship). All rows
carry `family`/`dataset`/`combo`/`recall_at_10`/`mrr`/`p95_ms`/`db_mb`/`mv`.

| combo (ds-v1-kfold) | tune r@10 / MRR | validate r@10 / MRR | pooled r@10 / MRR | p95 source |
|---|---|---|---|---|
| all-levers-off (integrity) | 0.5828 / 0.4444 | 0.2521 / 0.1279 | **0.4174 / 0.2862** | quiet re-measure |
| enrich+enrich_idf@df_max=0.75 | 0.5828 / 0.4115 | 0.2417 / 0.1092 | 0.4123 / 0.2603 | sweep (contention caveat) |
| enrich+enrich_idf@df_max=0.80 | 0.5828 / 0.4115 | 0.2417 / 0.1092 | 0.4123 / 0.2603 | sweep (contention caveat) |
| enrich+enrich_idf@df_max=0.85 | 0.5828 / 0.4115 | 0.2417 / 0.1092 | 0.4123 / 0.2603 | sweep (contention caveat) |
| enrich+enrich_idf@df_max=**0.90 (shipped)** | 0.5828 / 0.4115 | 0.2417 / 0.1092 | 0.4123 / 0.2603 | quiet re-measure |
| prf@docs=3,terms=10,lambda=0.5 | 0.5484 / 0.2642 | 0.1972 / 0.0862 | 0.3728 / 0.1752 | sweep (serial, quiet machine) |
| prf@docs=10,terms=10,lambda=0.5 | 0.5713 / 0.2368 | 0.1535 / 0.0768 | 0.3624 / 0.1568 | sweep (serial, quiet machine) |
| **multivector** | 0.6262 / 0.4664 | 0.4915 / 0.2126 | **0.5588 / 0.3395** | sweep (serial, quiet machine) |
| enrich+rerank-off | 0.6337 / 0.4981 | 0.3989 / 0.1282 | 0.5163 / 0.3131 | sweep (serial, quiet machine) |
| enrich_idf+rerank-off | 0.6337 / 0.4981 | 0.3989 / 0.1282 | 0.5163 / 0.3131 | sweep (serial, quiet machine) |

### T021 (FR-005) — multi-vector: the strongest row, both SC-1 targets reached

`RetrievalParams(multivector=True)` over the incumbent base — name and
docstring vectors beside the chunk vector, max-score dedup per symbol —
on the same 5-fold rotation, against the fr005-mv scratch DB (1066 base
+ 1240 mv rows from one `cairn embed --multivector` pass). **Pooled
0.5588 / 0.3395 reaches both SC-1 targets (0.50 / 0.33) — the first
configuration in either campaign to do so — and clears the 95% pooled
bootstrap guard decisively**: Δ +0.1414, p = 0.0035 (t-test cross-check
p = 0.0040), 95% CI [+0.0527, +0.2373], per-fold Δ range
[+0.0969, +0.1832] with all five folds positive. Storage growth is
**1.8103×** (13,058,048 vs 7,215,056 bytes on the same corpus, no-vec0
doctrine) — inside the ≤3× bound. Pooled p95 is 703.0 ms, *below* the
incumbent's 1026.1 ms (rerank stays armed under the marker on this
combo). Integrity (TC-022): the implicit all-levers-off row reproduces
the committed T014 baseline with 58/58 per-query identity, drift
0.000000 — the mv rows sit unread in the DB during that row, proving the
flag-off byte-equivalence T018 pinned. Raw sweep under
`benchmarks/quality/fr005-mv/`.

### T023 (FR-006) — the confirmation ladder on the upgraded evidence base

**The DS-v1 leg (k-fold pooled aggregate, n=58).** The v1 campaign's
headline near-miss — enrich+rerank-off at Δ+0.1123, p=0.118 on the
29-query single split — **clears the 95% pooled bootstrap guard at
n=58**: Δ +0.0988, p = 0.0491, CI [+0.0037, +0.2006], per-fold Δ
[+0.0680, +0.1239] all positive (t-test cross-check p = 0.0530 —
recorded; the guard is the bootstrap per D-009). Candidate (b)
(enrich_idf, at the SHIPPED 0.90 cutoff) is byte-identical per-query to
(a) — 0/58 differ; the cutoff's drop-set is empty on DS-v1 (highest
term_df fraction `test` 0.8583 < 0.90). Together with T021's
multivector row (Δ +0.1414, p = 0.0035), **all three D-016 candidates
clear on the DS-v1 leg**, and multivector reaches both SC-1 targets on
that leg (0.5588 / 0.3395). The evidence-power bet paid: the same
levers, unchanged, went from p = 0.118 to significance purely by
doubling n through the rotation.

**The DS-v2 leg (zero-shot validation, D-011) — and it refutes
transfer.** Tune on DS-v1, validate zero-shot over both DS-v2 corpora
(per-corpus rows + macro-average, never diffed against DS-v1 rows; the
44 L5 queries evaluated and recorded as the structural zero above).
On the 154 L1 queries:

| combo (ds-v2) | attrs r@10 / MRR (n=106) | yarl r@10 / MRR (n=48) | macro r@10 / MRR |
|---|---|---|---|
| all-levers-off (incumbent) | 0.4835 / 0.3358 | 0.4722 / 0.4179 | **0.4778 / 0.3769** |
| multivector | 0.4403 / 0.2431 | 0.4861 / 0.3257 | 0.4632 / 0.2844 |
| enrich+rerank-off | 0.4961 / 0.2936 | 0.4601 / 0.4171 | 0.4781 / 0.3554 |
| enrich_idf+rerank-off | 0.4961 / 0.2936 | 0.4601 / 0.4171 | 0.4781 / 0.3554 |

* **multivector's +14pp DS-v1 gain reverses zero-shot**: on the unseen
  attrs corpus Δ −0.0432 (p = 0.15, CI [−0.1017, +0.0157]); macro MRR
  −0.0925 vs the incumbent. The DS-v1 win was corpus-specific.
* **enrich+rerank-off's +9.9pp recall collapses to +0.0003 macro**
  (MRR −0.0215); both per-corpus bootstraps are non-significant
  (attrs Δ+0.0126 p=0.70; yarl Δ−0.0122 p=0.76). On DS-v2's *new* yarl
  queries the delta is −0.0122 — the DS-v1 gain was as much a
  query-population effect as a corpus effect. Its one robust transfer
  is latency: macro p95 48.3 ms vs the incumbent's 951.0 ms (rerank-off).
* (a) ≡ (b) byte-identical again — the 0.90 cutoff's drop-set is empty
  on the DS-v2 corpus mix as well.
* The **incumbent carries the best DS-v2 macro of every measured
  configuration** — no candidate improves on it zero-shot.

Raw sweeps, the zero-shot runner, and the one-pass merge script live
under `benchmarks/quality/ladder-v2/`.

### T020 (FR-004) — PRF: an honest negative, inside the budget it replaces

The D-002 grid (fb_terms 10, λ 0.5, fb_docs {3, 10}; Anserini RM3
anchors) measured on the same 5-fold rotation through the unchanged
seam. **Both grid points hurt and neither approaches significance**:
docs=3 Δ −0.0447 (p = 0.30, CI [−0.1315, +0.0402]), docs=10 Δ −0.0550
(p = 0.19, CI [−0.1375, +0.0250]); MRR falls harder than recall
(0.2862 → 0.1752 / 0.1568), and docs=10 is not significantly worse than
docs=3 head-to-head (Δ −0.0103, p = 0.66). The latency half of AC5
**holds**: p95 99.4 / 81.1 ms sits far inside the rerank budget PRF
replaces (committed session figures: rerank-on 1142.0 ms vs rerank-off
28.9 ms p95 — never the unretained ~780 ms p50), but the quality half is
negative, so PRF stays flag-off and out of the ladder's ship set.
Integrity for both runs: the implicit all-levers-off row reproduces the
committed DS-v1 baseline exactly (pooled 0.4174/0.2862; tune/validate
anchors 0.5828/0.4444 and 0.2521/0.1279 to 4 decimals), and the two
runs' implicit rows are byte-identical cross-run (determinism under the
D-009 pins). Raw sweeps and the merge payload live under
`benchmarks/quality/fr004-prf/`.

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

## SC-1 verdict — CLOSED (document branch, no ship)

| | recall@10 | MRR |
|---|---|---|
| SC-1 target (unchanged) | ≥ 0.50 | ≥ 0.33 |
| DS-v1 k-fold leg, best (multivector) | **0.5588** ✓ | **0.3395** ✓ |
| DS-v2 zero-shot macro, best (incumbent) | 0.4778 ✗ | 0.3769 ✓ |
| **Full evidence base** | **not reached** | **not reached** |

The targets are the same bar as the first campaign — **0.50 / 0.33,
untouchable by this campaign (TC-026)**; no re-scoped target, no metric
swap. The verdict's evidence: 5-fold seeded rotation over the 58 DS-v1
L1 queries (pooled per-query paired bootstrap at n=58 — all three
D-016 candidates clear; per-fold spread descriptive only) plus
zero-shot DS-v2 validation (154 L1 / 44 L5; per-corpus rows +
macro-average). **SC-1 is reached on the DS-v1 leg alone by
multivector (0.5588/0.3395) — the first configuration in either
campaign to do so — but that configuration is refuted zero-shot**
(DS-v2 macro 0.4632/0.2844; attrs Δ −0.0432; macro MRR −0.0925 vs the
incumbent), and no configuration reaches both targets on the DS-v2 leg
(best macro belongs to the recall-short incumbent row). On the full
evidence base, SC-1 is not reached.

**T024's disposition — exactly one branch: the document branch.** Three
candidates cleared the DS-v1 pooled bootstrap guard — (a)/(b)
enrich+rerank-off Δ+0.0988, p=0.0491, CI [+0.0037, +0.2006];
(c) multivector Δ+0.1414, p=0.0035, CI [+0.0527, +0.2373] — but the
zero-shot DS-v2 leg refutes transfer for all of them: multivector
REVERSES on the unseen corpus (attrs Δ −0.0432, CI [−0.1017, +0.0157],
p = 0.15; macro MRR −0.0925) and enrich+rerank-off's +9.9pp recall
collapses to +0.0003 macro with −0.0215 MRR (per-corpus bootstraps
n.s.; on DS-v2's *new* yarl queries Δ −0.0122 — a query-population
effect, not only a corpus effect). Under the honesty clause the best
evidenced configuration on the FULL evidence base is the incumbent — it
carries the best DS-v2 macro of every measured configuration
(0.4778/0.3769) and reproduces the committed DS-v1 baseline exactly —
so the incumbent ships (defaults unchanged) and the shortfall is
documented, never gamed. Because nothing ships, no protected baseline
is re-measured; the committed figures stand.

**The next binding constraint — lever generalization, not evidence
power.** The v1 campaign's constraint (evidence power) was fixed: n=58
pooling converted the p=0.118 near-miss into three guard-clearing
candidates. The v2 constraint is that every candidate's DS-v1 gain
fails to transfer zero-shot, so no DS-v1-tuned lever can ship as a
global default on this evidence. Armed experiments, from the record:

1. **T014's diagnostic (already in the record): the enrichment cutoff's
   real DS-v1 threshold is ~0.27** (`url` prevalence 0.2711), not 0.90 —
   a 0.25–0.30 cutoff band is the concrete untested knob for the enrich
   direction (`d03-diagnostic.json`: L1-D03 recovers at cutoff 0.25,
   rank 6).
2. **Multivector's corpus-dependence**: the name/docstring max-score
   union over-fits yarl's docstring style — a name-kind-only ablation,
   or a DS-v2-era calibration (tune on DS-v2, validate on DS-v1), would
   isolate which vector kind carries the non-transferring gain.
3. **The rerank MRR finding is corpus-dependent** (v1: rerank costs
   7–9pp MRR on DS-v1; DS-v2 zero-shot: the incumbent's rerank-active
   MRR is +2.2pp over rerank-off) — any rerank-off default proposal
   needs both corpora's evidence.

The 44 L5 queries are evaluated and recorded as a structural zero (L5
retrieval is OKF bundle search; the benchmark corpora carry no
`.knowledge/` bundle — the committed DS-v1 baseline's "surface absent"
discipline), never blended into the L1 rows.

## Standing rules this document is pinned to

- The first campaign's record is embedded byte-identically (its original
  blob hashes are recorded under `campaigns.retrieval-quality-v1.original_blobs`
  and pinned in `tests/test_ablation_artifact.py`; TC-028's pin moved from
  the removed sibling files to the embedded copy at unification).
- v2 rows carry their own dataset/family labels; no v2 row is a delta
  against a v1 row, and ds-v2 aggregates are never presented without their
  per-corpus rows (D-011).
- Match rules are never loosened; SC-1 stays 0.50/0.33 (TC-026/TC-027).


---

# Appendix — the first campaign's record (preserved verbatim)

> Everything below this marker is the 2026-08-16 `cairn-quality-ablation/1`
> rendering (`ablation.md` as it stood at PR #37), carried over unchanged
> (original blob `7112bb0899aef22dfda8080596cc63bbbfb8314c`). Its rows are
> the legacy 29/29 single-split measurement family; they are historical
> evidence, never diffed against the k-fold or DS-v2 rows above.

<!-- verbatim-begin (cairn-quality-ablation/1 ablation.md) -->
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
<!-- verbatim-end (cairn-quality-ablation/1 ablation.md) -->
