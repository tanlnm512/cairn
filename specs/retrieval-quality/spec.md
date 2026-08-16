# Spec: retrieval-quality

**Status**: draft
**Created**: 2026-08-16
**Branch**: `feat/retrieval-quality`
**Origin**: the DS-v1 quality baseline (PR #35) measured **L1 recall@10 = 0.4174, MRR = 0.2862** over the 58-query hand-verified ground truth — roughly 3 in 5 agent-shaped queries do not surface their primary target in the top 10. This spec exists to raise those numbers measurably, with ablation evidence, without giving back the latency wins (model warm-up, rerank confidence gate, pooled connections) or the verification doctrine (resolution labels, no name-collision inflation).

## What

A tuned retrieval pipeline for `semantic_search` (and therefore `explore`) that measurably improves L1 recall@10 and MRR on the DS-v1 ground truth: query-side enrichment, corpus-side embedding context, fusion/threshold tuning, and reranker input formatting — each lever ablated alone and in combination by a sweep harness, the winning configuration shipped as defaults, and the committed reference tables regenerated to prove the gain.

## Why

The DS-v1 baseline turned retrieval quality from invisible to measurable — and the measurement says the hybrid pipeline underperforms on exactly the traffic that matters: natural-language queries ("where is the function that parses an unencoded URL string...") against short symbol records (name + docstring). The known mismatch classes: sentence queries vs telegraphic corpus text; BM25's implicit-AND over sentence tokens matching nothing; a cosine threshold tuned by folklore; reranker pairs built from whatever the chunk happens to contain. Every competitor that publishes quality numbers (graphify's LOCOMO recall tables) treats this as a first-class engineering surface; cairn now has the ground truth to do the same honestly.

## Business value

- Agents find their target symbol more often on the first query (SC-1: full-set L1 recall@10 improves from 0.4174 to **≥ 0.50** and MRR from 0.2862 to **≥ 0.33**, with the held-out split non-regressing and the ablation showing which lever bought what. Honesty clause: if the sweep cannot reach the margin without violating the protected baselines or the precision doctrine, the best evidenced configuration ships and the shortfall is documented in the ablation table — never gamed.)
- Improvements are attributable and regression-proof (SC-2: every lever lands with a sweep-table row; future regressions are caught against the regenerated DS tables).
- No silent trade-offs (SC-3: latency and agent-effort baselines re-measured; the shipped config may not regress impact p95, first-query warm time, or the agent-effort token totals beyond stated bounds).
- Anti-overfit discipline (SC-4: tuning decisions are validated on a held-out query split, not the full 58; the final number is reported on the full set with the split disclosed).

## User stories

### US1 — Tuned defaults, evidenced (P1)
As a cairn user, I want the shipped retrieval defaults to be the best-measured configuration, so that semantic queries just work better without knobs.

**Acceptance criteria**:
- AC1: Given the sweep harness, when the ablation runs against DS-v1, then it commits a machine-readable `benchmarks/quality/ablation.json` plus a rendered `benchmarks/quality/ablation.md` (lever combination → recall@10 / MRR / p95, both splits) including the shipped-defaults and all-levers-off rows. (FR-005)
- AC2: Given the winning configuration, when it ships as defaults, then the regenerated quality table in `docs/benchmarks.md` shows the improvement and the old numbers remain in the DS-v1 artifact for attribution. (FR-007)

### US2 — Held-out honesty (P1)
As a maintainer, I want tuning validated on queries the tuning never saw, so the improvement generalizes past the 58-query set.

**Acceptance criteria**:
- AC3: Given the ground truth split (tune half / validate half, seeded), when levers are chosen, then selection uses the tuning split and the spec reports both splits' final numbers. (FR-006)

### US3 — No latency give-back (P2)
As a served agent, I want quality gains that don't reintroduce the costs the performance phase removed.

**Acceptance criteria**:
- AC4: Given the shipped config, when perf and agent-effort suites re-run, then impact/first-query/semantic p95 and agent-effort tokens stay within the committed baselines' regression bounds. (FR-007)

## Requirements

- **FR-001**: The system shall enrich natural-language queries before retrieval — identifier-like and code-ish tokens extracted and emphasized for both the dense (query reformulation before embedding) and sparse (BM25 term weighting) paths — behind a measured default, with the enrichment deterministic and hermetic (no LLM in the query path; the verification doctrine holds).
- **FR-002**: The system shall enrich the embedded corpus context — the chunk embeddings carry qualified name, file path, and docstring/signature context per a measured recipe — re-embeddable via the existing embed pipeline with a new content-hash (embedding regeneration is an index operation, not a dataset change; DS-v1 ground truth is untouched).
- **FR-003**: The system shall expose and tune the fusion/threshold parameters (RRF weights, dense and sparse threshold defaults, candidate pool sizes) as measured defaults rather than folklore, with each parameter's swept range and chosen value recorded.
- **FR-004**: The system shall construct reranker inputs from the measured-best pair format (query vs enriched candidate text), with the rerank stage's marginal recall/MRR value reported at the shipped config and the confidence gate's interplay re-calibrated if the pair format changes its score distribution.
- **FR-005**: The system shall provide an ablation/sweep harness (`cairn eval --sweep` or a scripts/ equivalent) that evaluates lever combinations against DS-v1 on the tune split, emits a machine-readable results table, and regenerates the reference quality table from the shipped config's full-set measurement.
- **FR-006**: The system shall enforce held-out validation: lever selection runs on a seeded tune split; final numbers are reported for both splits; the harness fails if selection reads the validation split.
- **FR-007**: The shipped configuration shall re-measure everything the perf phase established — perf suite, agent-effort suite, and first-query warm time (for which a real measurement harness and committed artifact shall be minted, replacing the manual 322ms note) — and regenerate the committed reference tables; any regression beyond the compare thresholds must be either fixed or documented as a conscious trade with the quality gain quantified.

## Scope

**In**: query/corpus/fusion/rerank levers on the existing hybrid pipeline; the sweep harness; held-out discipline; defaults shipping; table regeneration; re-calibration of the rerank gate if needed.

**Out (deferred)**: L5 knowledge-retrieval quality (surface absent — needs an OKF bundle for the t2 snapshot; separate decision); new embedding models beyond the installed bge-m3 (infrastructure decision, not tuning); LLM-in-the-loop query rewriting (violates the no-LLM query path doctrine); ground-truth expansion (DS-v2 authoring is a separate data decision); ANN index internals.

## Assumptions & risks

- Assumption: the local bge-m3 + bge-reranker-base stack remains the measurement backend (DS-v1's provenance cites it; switching models invalidates comparability).
- Risk: overfitting 58 queries — mitigation: FR-006 held-out split + reporting both splits.
- Risk: enriched chunks blow the embedding max sequence / storage — mitigation: measured recipe with size bounds; db_mb tracked in the sweep.
- Risk: quality-latency tension (e.g., always-rerank) — mitigation: FR-007 re-measurement with the compare thresholds.
- Risk: rerank gate calibration drift when pair formats change score distributions — mitigation: FR-004 requires re-calibration measurement.

## Research questions (for Stage 1)

- RQ1: Query-document mismatch techniques for embedding retrieval over short code-symbol records (query expansion, pseudo-relevance feedback, identifier extraction from natural language) — what works without an LLM in the loop?
- RQ2: Hybrid fusion tuning practice — weighted RRF vs plain RRF, when BM25 contributes vs hurts on sentence queries, threshold-setting methodology for dense scores.
- RQ3: What context belongs in a code-symbol embedding (name / qualified name / path / signature / docstring / body prefix) — published ablations (CodeSearchNet-era and modern) on chunk composition for symbol-level retrieval.
- RQ4: Cross-encoder input formatting for code search — pair construction, truncation, and context length effects on reranking quality.
- RQ5: Small-ground-truth evaluation methodology — train/validation splits for IR tuning on tiny query sets, avoiding selection overfitting (BEIR-era practice, k-fold where feasible).
