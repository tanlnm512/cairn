# Plan: retrieval-quality-v2

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-17 | **Survey**: [survey.md](survey.md) (status arbiter)

This spec lands **docs-only this cycle**. The phases below are execution-ready for a
future implementation wave; they are not executed now. Scheduling facts that drove the
sequence: FR-001 + FR-002 are the evidence-power unlocks everything else measures
against; DS-v2 authoring is the long pole (starts day 1, finishes last before the
ladder); FR-005 has the deepest blast radius (80 impacted symbols via `embed_all`);
the ladder (FR-006) is the sole all-dependencies milestone.

## Milestones

| Phase | Milestone | Delivers (demoable) | FRs | Depends on |
|-------|-----------|---------------------|-----|------------|
| 1 | Evidence core: k-fold harness | A ≥5-fold, seeded, rotation-aggregated sweep over the **existing** DS-v1 ground truth: per-fold spread + aggregate verdict in the emitted sweep doc, held-out guard provably firing per fold | FR-001 | — |
| 2 | DS-v2 ground truth | A new immutable DS-v2 dataset dir: ≥150 L1 (all four kinds) / ≥40 L5, hand-verified against fresh graph builds with zero aspirational entries; second-corpus vendored-or-deferred decision recorded; sibling-dir budget rule in the verifier | FR-002 | — (authoring starts day 1, parallel; **completion hard-gates Phase 5**) |
| 3 | Enrichment repaired | Corpus-aware IDF term weighting with the DF signal **injected** into the pure `enrich()`; the reproduced L1-D03 failure (ubiquitous `'URL'` token) is gone; no DS-v1 tune-split query regresses to zero | FR-003 | Phase 1 (soft — measurement power) |
| 4 | New lever families | PRF (FR-004) and multi-vector (FR-005) wired as flag-off levers, each with ablation rows on both splits: recall/MRR/p95, plus db-size for multi-vector; PRF p95 fits the rerank budget it may replace; all-levers-off integrity row still reproduces the session baseline | FR-004, FR-005 | Phase 3 (soft — semantic.py hygiene) |
| 5 | Confirmation ladder + extended record | Ladder re-run on the upgraded evidence base (k-fold aggregate + DS-v2) as a **new measurement family** (DS-v1 doc stays pinned); cleared combinations ship as defaults with all protected baselines re-measured, or the verdict states shortfall + binding constraint | FR-006 | Phases 1, 2, 3, 4 |

Phase numbers are checkpoint order, not work-start order: Phase 2's authoring begins
immediately alongside Phase 1 (it shares no files with any code phase); its checkpoint
completes when the dataset is verified, which is the long pole gating Phase 5.

## Dependencies

```
FR-001 k-fold ──────────────────────────────────────────┐
FR-002 DS-v2 (starts day 1 ── long pole) ───────────────┤
FR-003 IDF enrich ──┬─ lever rows ──────────────────────┼──► FR-006 ladder +
FR-004 PRF ─────────┤                                    │     record extension
FR-005 multi-vector ┘                                    │
        └── FR-004 ∥ FR-005 ∥ FR-003 measure on FR-001's k-fold
```

- **FR-001 → everything that selects.** The fold loop replaces `run_sweep`'s single
  `split_queries` call (`src/cairn/eval.py:1066`) and the single-split reporting; the
  guard extends as-is (`evaluate_on(held_out_ids=...)` takes a flat id iterable — no
  signature change, survey FR-001). Soft-gates Phases 3-4 (their ablation rows gain
  power from k-fold), hard-gates Phase 5 (the aggregate verdict is a ladder input).
- **FR-002 → FR-006 only.** No lever consumes DS-v2 until final selection; authoring
  is fully parallel with all code phases. Exposed via `cairn eval --sweep --queries
  <gt-dir>` (the sweep CLI already takes a ground-truth directory), so DS-v2 needs no
  harness change to be loadable — only Phase 1's fold aggregation to be ladder-ready.
- **FR-003 →(soft) FR-004/FR-005.** Not a data dependency — a shared-file ordering:
  all three touch `src/cairn/graph/semantic.py` at different stages (enrichment
  boundary ~L567-581; post-fusion block ~L726-754 for PRF; candidate-dict loops for
  multi-vector's brute leg). FR-003's boundary edit is the smallest; landing it first
  keeps the hottest file mergeable. Decision-gated: the DF-injection mechanism
  (parameter vs table) is tech-spec's D-### to make.
- **FR-004 ∥ FR-005.** Genuinely disjoint seams: PRF = semantic.py post-fusion +
  `RetrievalParams` additive field (additive-field doctrine already documented);
  multi-vector = embeddings PK/staleness/CLI + ANN + both candidate paths. Overlap is
  confined to semantic.py's brute-leg candidate loops (FR-005's max-score seam).
- **FR-006 → all.** The only convergence point. Its ablation-record extension is
  constrained: `tests/test_ablation_artifact.py` (6 tests) pins the DS-v1 doc's
  dataset block — v2 rows are a new family (new doc vs schema bump is tech-spec's
  decision); the ladder machinery itself already exists.

## Parallelization map

Parallel is the DEFAULT; each parallel claim below names its evidence. The
task-breaker turns these into `[P]` / `(after T###)` markers.

- **[P] DS-v2 authoring (FR-002) ∥ every code phase** — writes only a new dataset
  directory under `benchmarks/datasource/` plus verifier rules; the code phases write
  `src/cairn/` and `tests/`. Zero shared files (survey FR-002: the surface is
  `queries.jsonl` + `expectations.tsv` + manifest). Long pole ⇒ start day 1.
- **[P] PRF (FR-004) ∥ multi-vector (FR-005)** — disjoint subsystems: FR-004's entire
  footprint is the semantic.py post-fusion block (`candidates = fused_candidates`,
  `src/cairn/graph/semantic.py:754`) + a `RetrievalParams` field; FR-005's is the
  embeddings schema (`PK (symbol_id, model)` today silently overwrites a second
  vector), staleness hash, embed CLI, vec0, and the two candidate paths. Coordinate
  only on the brute-leg candidate loops both touch in semantic.py.
- **[P] FR-003 ∥ FR-002** — no shared files (query_enrich.py + semantic.py boundary
  vs dataset dirs); measure FR-003's rows on DS-v1 k-fold while DS-v2 is authored.
- **Soft-ordered: FR-003 → (FR-004 ∥ FR-005)** — same file, different stages; the
  small boundary edit (DF injection) lands first so PRF's second-embed exception and
  multi-vector's scan changes branch from a stable base. Express as `(after T###)`
  on the boundary edit only, not a full serial phase.
- **Strictly serial spine**: FR-001 (k-fold, small, first — unblocks powered
  measurement on existing ground truth) → lever phases (3, 4) → FR-006 ladder last,
  hard-gated by FR-002 completion and FR-001's aggregate machinery.
- **Within FR-005**, the schema migration is the risk item (80 impacted symbols,
  `cairn impact embed_all`) — schedule it at the front of its phase with the flag
  off, so default paths stay untouched while the scan/max-score work proceeds.

## Checkpoints

- **After Phase 1 (FR-001)**: a seeded ≥5-fold sweep over DS-v1 completes and emits
  per-fold spread + rotation-aggregated verdict; a negative test proves selection-stage
  reads of any fold's validate ids raise `HeldOutError`; fold count is configurable.
  Verify: `uv run cairn eval --sweep <spec> --queries benchmarks/datasource/t2/ground_truth --out /tmp/kfold-sweep.json`
  shows fold-level rows; targeted pytest for the fold loop + guard is green.
- **After Phase 2 (FR-002)**: verifier passes with the new sibling-dir budget rule
  (`uv run python benchmarks/datasource/verify_datasource.py --budget`); loader counts
  confirm ≥150 L1 across all four kinds and ≥40 L5; every expectation empirically
  verified against a fresh graph build (T011 bar, zero aspirational); tree_hash
  pinned; second-corpus decision (vendored with size/license per the datasource spec,
  or deferred) recorded with reasons.
- **After Phase 3 (FR-003)**: `enrich()` remains pure (no env/graph reads) — the DF
  signal demonstrably arrives as an injected parameter/table; on the L1-D03 repro the
  `'URL'` identifier is down-weighted/dropped deterministically; threshold documented;
  ablation rows on both splits; no previously-passing DS-v1 tune-split query regresses
  to zero (AC4). Verify: repro unit test + k-fold ablation rows.
- **After Phase 4 (FR-004 + FR-005)**: both levers flag-off by default; default
  config's all-levers-off row still reproduces the session baseline (integrity
  doctrine); PRF's p95 recorded and under the rerank budget it replaces (~1113ms p95
  gap, ablation.json 1142.0 vs 28.9 — cite p95, not the unretained ~780ms p50 figure);
  multi-vector row carries db-size (≤3× growth) + p95; PRF's second-embed exception is
  documented at the boundary (one-call doctrine amended, not silently broken).
  Verify: sweep with each flag flipped emits its rows; full pytest green.
- **After Phase 5 (FR-006)**: ladder re-run on k-fold aggregate + DS-v2; v2 rows live
  in their new family and are never diffed against DS-v1 rows; the six existing guard
  tests still pass (`uv run pytest tests/test_ablation_artifact.py` → 6 passed) plus
  new v2-family guard tests; SC-1 targets unchanged at 0.50/0.33 and match rules
  untouched; if anything ships: shipped_defaults row + perf/agent-effort/warm-time
  baselines re-measured; if nothing ships: verdict states the shortfall and the next
  binding constraint.

## Risks & mitigations

- Risk: DS-v2 authoring at 3–5× scale is the long pole → starts day 1 on a parallel
  track; staged batches each land verifiable; agents draft + verify empirically per
  the T011 method (spec risk register).
- Risk: k-fold multiplies sweep machine time by fold count → fold count configurable;
  selection grids conservative (Benham discipline inherited).
- Risk: multi-vector multiplies embedding storage up to 3× and breaks one-vector
  assumptions across both candidate paths → db-size tracked in the ablation row;
  flag-off default; schema migration front-loaded within Phase 4 (deepest blast
  radius — 80 impacted symbols).
- Risk: second corpus makes all numbers incomparable to DS-v1 → DS-v2 rows are a new
  measurement family, never diffed against DS-v1 rows; DS-v1 artifacts immutable
  (D-010 discipline).
- Risk: three FRs edit `src/cairn/graph/semantic.py` (boundary, post-fusion, brute
  loops) → soft order FR-003-first for the boundary, PRF/MV on parallel branches with
  the candidate loops called out as the coordination point.
- Risk: PRF violates the one-embed-call doctrine → the second embed is a principled,
  flag-gated, documented exception at the post-fusion seam that *replaces* the rerank
  budget rather than stacking on it.
- Risk: guard tests pin the ablation record to DS-v1 → extension is additive (new
  family/doc); the new-doc-vs-schema-bump choice is a tech-spec decision recorded
  before Phase 5 starts.

## Delivery

Docs-only this cycle: the spec set (spec/plan/tech-spec/test) lands as one PR on
`docs/retrieval-quality-v2-spec`. For the future implementation wave: one branch per
parallel area (k-fold+spine, DS-v2, lever families), cut from main, landing through
the standard contribution workflow (pre-commit, conventional commits, PR checklist);
one commit per task, code + docs together; Phase 5 lands last as a single
ship-or-document PR on top of all merged phases.
