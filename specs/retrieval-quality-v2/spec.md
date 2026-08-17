# Spec: retrieval-quality-v2

**Status**: done (closed 2026-08-17 on the document branch — no ship; all 24 tasks complete; verdict in `benchmarks/quality/ablation-v2.{json,md}`)
**Created**: 2026-08-17
**Branch**: `docs/retrieval-quality-v2-spec` (this spec lands docs-only; implementation branches cut later)
**Origin**: the retrieval-quality ablation verdict (PR #37, `benchmarks/quality/ablation.md`): SC-1 targets (L1 recall@10 ≥ 0.50, MRR ≥ 0.33) were not reached — five bootstrap-guarded candidates failed significance on the 58-query ground truth (best Δ+0.1123 at p=0.118, validate CIs ±0.13–0.15 at n=29) — and the verdict identified the binding constraint as **evidence power**, not lever quality. This spec is the follow-up: raise the evidence power, repair the measured enrichment failure mode, spend the freed rerank budget on the best-sourced untried lever, and add one genuinely new dense-side lever family.

## What

A second retrieval-quality campaign that (a) grows the evaluation evidence base so real effects can clear significance — k-fold cross-validation over the existing ground truth plus a DS-v2 ground truth 3–5× larger and cross-corpus; (b) repairs query enrichment's measured failure mode with corpus-aware (IDF-style) term weighting; (c) adds RM3-style pseudo-relevance feedback inside the latency budget freed by rerank-off configurations; (d) introduces multi-vector-per-symbol embeddings (name-only and docstring-only vectors alongside the chunk vector, max-score at query time); and (e) re-runs the confirmation ladder on the upgraded evidence — shipping whatever clears the guard, with the same honesty clause as its predecessor.

## Why

The first campaign's infrastructure is complete and trusted (the harness, the guard, the ablation record); what it proved is that the levers move recall +4 to +11 points but the 58-query ground truth cannot certify any of it at 95%. Meanwhile three specific, evidence-backed opportunities are open: the enrichment regression had a named cause (corpus-ubiquitous tokens like "URL" diluting both legs — query L1-D03 fell 1.0 → 0.0 on exactly this); rerank-off configurations free ~1.1s of p95 per query at the shipped config (ablation.json: 1142.0ms vs 28.9ms; T017: rerank buys ±1pp recall for −7 to −9pp MRR at ~40× latency), which re-opens PRF — research RQ1's best-sourced lever, previously rejected on latency alone; and the dense side has only ever embedded one chunk per symbol, leaving the sentence-vs-telegraphic mismatch attacked from a single angle.

## Business value

- Real quality gains become shippable (SC-1: at n≈100+ held-out queries the existing +0.11 near-miss would clear significance on today's levers unchanged).
- Conclusions generalize (cross-corpus ground truth — nothing is tuned on yarl alone).
- The bar stays honest (SC-1 targets unchanged at 0.50/0.33; match rules never loosened; nothing ships without the bootstrap guard).

## User stories

### US1 — Evidence power (P1)
As a maintainer, I want k-fold validation and a 3–5× larger ground truth, so that true +5–10pp effects clear statistical significance instead of dying in a ±0.14 CI.

**Acceptance criteria**:
- AC1: Given the harness, when selection runs k-fold (≥5 folds) over the existing ground truth, then every fold's held-out discipline is enforced (no fold's validate ids read during its selection) and the aggregate verdict is reported with per-fold spread. (FR-001)
- AC2: Given DS-v2, when queries are counted, then L1 ≥ 150 and every expectation is empirically verified against a fresh graph build (the T011 bar: zero aspirational entries), with the verification run committed as a provenance artifact beside the dataset (verification summary: per-kind/level counts, pass rate 100%, the build facts it verified against). (FR-002)

### US2 — Enrichment repaired (P1)
As an agent issuing natural-language queries, I want enrichment that emphasizes only discriminative tokens, so that corpus-ubiquitous terms stop diluting my query.

**Acceptance criteria**:
- AC3: Given a term matching more than a documented corpus-fraction cutoff (default **0.90**, the scikit-learn `max_df` convention; the sweep may calibrate within 0.75–0.95 but the shipped value must be recorded), when enrichment builds the sparse/dense query, then that term is down-weighted or dropped, deterministically. (FR-003)
- AC4: Given the DS-v1 regression queries (e.g. L1-D03), when the repaired enrichment runs, then no previously-passing query regresses to zero on the tune split. (FR-003)

### US3 — The untried levers (P2)
As a maintainer, I want PRF and multi-vector retrieval measured, so that the two highest-prior untried ideas have ablation rows like every other lever.

**Acceptance criteria**:
- AC5: Given a fused first pass, when PRF expands the query, then it costs less than the rerank budget it may replace (p95 measured) and its ablation row exists on both splits. (FR-004)
- AC6: Given multi-vector per symbol (name-only, docstring-only, chunk), when a query scores candidates by max-over-vectors, then the ablation row exists with recall/MRR/db-size/p95 on both splits. (FR-005)

### US4 — Ship or document, honestly (P1)
As a cairn user, I want the confirmation ladder re-run on upgraded evidence, so that whatever genuinely clears the guard ships and whatever doesn't is documented with the reason.

**Acceptance criteria**:
- AC7: Given the ladder outcome, when a combination clears the bootstrap guard on the upgraded evidence base, then it ships as defaults with all protected baselines re-measured (perf, agent-effort, warm-time); when nothing clears, then the ablation record states the shortfall and the next constraint. (FR-006)

## Requirements

- **FR-001**: The system shall extend the evaluation harness with k-fold cross-validation (≥5 folds, seeded) enforcing the existing held-out discipline per fold — selection-stage reads of any fold's validate ids still fail loudly. Aggregation is **pooled per-query paired errors across folds** for significance (per Bengio & Grandvalet 2004, per-fold spread is reported as descriptive only, never the significance basis) plus a rotation-mean point estimate.
- **FR-002**: The system shall provide a DS-v2 ground truth: ≥150 L1 queries (all four kinds represented) and ≥40 L5, hand-verified against fresh graph builds with zero aspirational entries, versioned as a new immutable dataset directory; the vendoring candidate(s) for a second corpus shall be evaluated against the datasource constraints (per-corpus ≤ 3 MB, datasource total ≤ 5 MB, permissive license, full provenance + NOTICE attribution) and either included or explicitly deferred with reasons.
- **FR-003**: The system shall weight enrichment terms by corpus-aware IDF (terms matching above a documented fraction of symbols are down-weighted or dropped from both legs' queries), computed from the graph deterministically, hermetically, and cheaply (no scan per query above a documented bound).
- **FR-004**: The system shall implement RM3-style pseudo-relevance feedback over the fused first pass as a wired, flag-off-by-default lever (deterministic, no LLM), measured on both splits with its p95 recorded against the rerank budget it may replace.
- **FR-005**: The system shall support multi-vector-per-symbol embeddings (at least: name-only and docstring-only vectors alongside the chunk vector) with max-score candidate selection at query time, as a flag-off lever measured on both splits including db-size and p95 costs.
- **FR-006**: The system shall re-run the confirmation ladder on the upgraded evidence base (k-fold aggregate + DS-v2), shipping cleared combinations as defaults with every protected baseline re-measured, or documenting the shortfall and the binding constraint in the extended ablation record; SC-1 targets remain 0.50/0.33 and match rules are never loosened.

## Scope

**In**: k-fold harness; DS-v2 authoring (and second-corpus evaluation); IDF-aware enrichment; PRF lever; multi-vector lever; the confirmation ladder; ablation-record extension; protected-baseline re-measurement.

**Out (deferred)**: reranker model swaps (bge-reranker-base stays; a DS-v2-era revisit is recorded as a decision, not executed); ANN/vec0 internals; LLM-in-the-loop anything; lowering SC-1 or loosening the matcher; new benchmark datasources beyond the second-corpus evaluation named in FR-002.

## Assumptions & risks

- Assumption: DS-v1 artifacts stay immutable; DS-v2 is a new directory (D-010 discipline inherited).
- Risk: DS-v2 authoring at 3–5× scale is the long pole — mitigation: staged authoring (batches land verifiable), agents draft + verify empirically per the T011 method.
- Risk: multi-vector multiplies embedding storage (up to 3×) — mitigation: db-size tracked in the ablation row; opt-in flag.
- Risk: k-fold multiplies sweep machine time by the fold count — mitigation: fold count configurable; selection grids conservative (Benham discipline inherited).
- Risk: second corpus changes ALL numbers (not comparable to DS-v1 rows) — mitigation: DS-v2 rows are a new measurement family in the ablation record, never diffed against DS-v1 rows directly.

## Research questions (for Stage 1)

- RQ1: RM3/Bo1 PRF parameter practice for short-query hybrid retrieval (feedback-doc count, original-query weight) and PRF-over-fused-first-pass precedents.
- RQ2: Multi-vector / late-interaction retrieval at symbol granularity — ColBERT-style max-sim, multi-vector doc representations, and any symbol/function-level precedents.
- RQ3: Query-term IDF weighting for corpus-specific stopwording in code search; adaptive thresholds (document-frequency cutoffs) in IR practice.
- RQ4: Ground-truth sizing for IR significance testing — how query counts relate to detectable effect size; k-fold vs held-out practice in small-eval regimes.
- RQ5: Cross-corpus IR evaluation design (transfer/robustness splits) — how BEIR-style suites avoid per-corpus overfitting.
