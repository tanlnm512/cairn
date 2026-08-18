# Plan: retrieval-quality

**Spec**: [spec.md](spec.md) | **Survey**: [survey.md](survey.md) | **Created**: 2026-08-15
**Baseline**: `feat/retrieval-quality` @ e8eee0e · DS-v1 BEFORE = L1 recall@10 0.4174 / MRR 0.2862 (immutable artifact)

## Milestones

| Phase | Milestone | Delivers (demoable) | FRs | Depends on |
|-------|-----------|---------------------|-----|------------|
| 1 | Measurement substrate | Sweep harness (`cairn eval --sweep` or scripts/ equivalent): per-run lever overrides (no more process-env-only), lever-combination enumeration, machine-readable results table, seeded tune/validate split of the 58 L1 queries, guard that fails if selection reads validation. Proof of trust: the harness's baseline full-set row reproduces DS-v1's 0.4174 / 0.2862. | FR-005, FR-006 | — |
| 2 | Query-path levers | Deterministic hermetic query enrichment (identifier/code-ish token extraction, dense reformulation, BM25 term weighting — kills the quoted-FTS5-phrase defect that makes BM25 an empty list on every sentence query) + fusion/threshold/pool knobs wired as sweepable tunables (RRF weights/k, dense threshold, pool sizes, BM25 fetch). Tune-split sweeps run, enrichment × fusion defaults chosen and shipped. Enrichment lands at the `semantic_search` call boundary, NOT inside `embeddings.embed_query` (memory layer calls it too). | FR-001, FR-003 | Phase 1 |
| 3 | Corpus recipe | Chunk-variant ablation (existing A/B/C + any new variant the sweep motivates) measured under Phase 2's shipped defaults, db_mb + size bounds tracked per recipe, winning recipe shipped as default. Re-embed proven via the existing content-hash pipeline (full re-embed, minutes-scale on t2; rowid-stable upsert). | FR-002 | Phase 2 |
| 4 | Rerank stage + gate re-calibration | Pair formats (query vs stored chunk / vs enriched name+path+sig+docstring text) + explicit truncation policy (CrossEncoder `max_length` set, not inherited) measured; rerank stage's marginal recall/MRR value reported AT the shipped config; rerank-gate margin re-calibrated — trigger is Phase 2's enriched queries shifting `_exact_name_hit` corroboration and margin basis (pair format itself is gate-safe: gate reads pre-rerank fused scores). Confirmation sweep of leading combinations before final rerank/gate defaults ship. | FR-004 | Phase 3 |
| 5 | Prove & publish | Perf + agent-effort suites re-run vs DS-v1 baselines through `cairn bench --compare` (threshold 0.15, exit 2); first-query warm time gets a real measurement path + artifact (none exists today — 322 ms is a phase-doc line only); reference tables regenerated (fresh quality.json mint from shipped config full-set; docs/benchmarks.md via gen_benchmark_tables.py keeping the exact-key contract); final committed ablation table incl. shipped-defaults row (FR-005 harness's closing invocation); regressions fixed or documented as quantified trades. | FR-007 | Phase 4 |

**FR ledger**: FR-005+006 → P1 · FR-001+003 → P2 · FR-002 → P3 · FR-004 → P4 · FR-007 → P5. The FR-005-owned harness is *invoked* by P3/P4/P5 (rows) and P5 (final committed table); its acceptance criteria for the harness itself complete in P1.

## Dependencies

```
P1 harness+split ──→ P2 query levers ──→ P3 corpus recipe ──→ P4 rerank+gate ──→ P5 prove&publish
                        │                                        ↑
                        └── gate inputs shift (enriched query) ───┘
```

The spine is serial **because each lever composes with the previous stage's shipped output**, not by accident:

1. **P1 → everything**: every "measured default" clause is unmeasurable until overrides/splits exist (survey FR-005: eval.py has no subset/override; FR-006: no seeded Random anywhere in the eval path).
2. **P2 → P3 (measurement validity)**: recipe value depends on query treatment. Variant choice measured under raw queries could flip once queries are enriched — and enriched queries are what retrieve the chunks. Measuring variants under P2's shipped defaults avoids a redo.
3. **P3 → P4 (output consumption)**: reranker pairs are built FROM the stored chunk (`reranker.py:189 pairs = [(query, c.get("chunk") or "")]`). A recipe change invalidates every pair-format measurement, so the recipe must be settled first.
4. **P2 → P4 (gate re-calibration)**: the gate's `_exact_name_hit(query, candidates[0])` compares the query string (semantic.py:140-155); enrichment changes that string and the fused-margin basis. Calibration only means something in the enriched world. (P3 does NOT gate-shift: gate reads fused RRF scores before rerank — verified semantic.py:546-552 vs :567.)
5. **P4 → P5**: FR-007 re-measures the *shipped* config; there is nothing to re-measure until rerank/gate defaults are final.

## Parallelization map

Parallel is the default inside each milestone; the milestone spine yields for the output-consumption reasons above.

- **Independent (parallel)**:
  - P2: FR-001 enrichment module + `lexical.py` FTS fix ∥ FR-003 knob wiring in `semantic.py`/env plumbing — file-disjoint except `semantic.py`, where the hunks don't overlap: query-path edits cluster at the `embed_query`/`search_symbols` call sites (semantic.py:405, :477), fusion edits at :402, :429-450, :494. Parallel with a disjoint-hunk agreement; merge into one owner if conflicts appear.
  - P2: within FR-001, the sparse fix (`lexical.py`) ∥ the dense reformulation (new module) — disjoint files.
  - P3: new-variant implementation (`embeddings.py`) ∥ sweep-runner extension for per-variant re-embed + db_mb accounting. The per-variant *measurement runs* are serial (each variant = one re-embed round) but that is machine time, not agent time.
  - P4: reranker pair formats + truncation (`reranker.py`) ∥ gate re-calibration measurement (`semantic.py`) — file-disjoint AND gate-safe by construction (gate inputs are pre-rerank fused scores). Only the final margin validation at the combined shipped config must wait for both.
  - P5: perf re-run ∥ agent-effort re-run ∥ warm-time harness addition — three independent artifacts (perf.json / agent.json / new warm artifact).
- **Strictly ordered**:
  - Harness core (eval.py: splits, overrides, enumeration) → CLI surface (`cli/system.py` flags) — the CLI is a thin consumer of the core API.
  - P2 → P3 → P4 → P5 as a chain (see Dependencies).
  - Within P2, the `semantic.py` shared file between FR-001 call-site edits and FR-003 wiring — coordinate hunks (above).
  - P5: table regeneration (`gen_benchmark_tables.py` + docs/benchmarks.md + committed sweep table) consumes all re-measurement artifacts → runs last.

**Cross-area blast radius (cairn-verified, constrains the map)**:
- `embed_query` is called by the memory layer (`src/cairn/memory/promotion.py:311`, `:581`) — enrichment baked into it would silently change memory retrieval. Keep enrichment at the `semantic_search` boundary.
- `rrf_fuse` is shared with `search_memory` (`promotion.py:270`, also k=60) — wire weights/k at the semantic.py call site; do NOT change `fusion.py` defaults, or memory fusion shifts under FR-003.
- `chunk_for_symbol` impact = 85 items, including semantic/rerank-gating tests whose fixtures embed symbols — budget fixture churn in P3.
- `semantic_search` feeds `explore.py:210` and the MCP tools — every shipped default is user-visible at each milestone's ship; quality wins propagate for free, but so do regressions.

## Checkpoints

Exit condition per phase; verify before starting the next. Reuse survey.md's verify commands where they exist.

- **After Phase 1**: harness emits a machine-readable table whose baseline full-set row reproduces DS-v1's L1 recall@10 0.4174 / MRR 0.2862 (same DB, exact modulo runner noise); re-running produces an identical seeded split; a unit test proves selection touching the validation split fails loudly. Verify: run the sweep entrypoint with the baseline config; re-run and diff the split; run the guard test.
- **After Phase 2**: results table holds enrichment on/off × fusion-param rows on the tune split; swept ranges and chosen values recorded; both splits' numbers reported. Verify: `uv run python -c "from cairn.graph.lexical import _pattern_to_fts; print(repr(_pattern_to_fts('where is the function that parses an unencoded URL string')))"` no longer emits a single quoted phrase; `grep -n "rrf_fuse(" src/cairn/graph/semantic.py` shows weights/k sourced from tunables; full-set row moved vs baseline.
- **After Phase 3**: variant table (recall@10 / MRR / db_mb / p95) on the tune split under P2 defaults; chosen recipe + size bounds recorded; re-embed round-trip proven (hash change detected → full re-embed → vec0 rowids stable). Verify: sweep rows on disk; `grep -n "CAIRN_CHUNK_VARIANT" src/cairn/graph/embeddings.py` default reflects the winner; embedding suite green after fixture churn.
- **After Phase 4**: pair-format ablation + rerank marginal-value rows at the shipped config; gate margin re-calibrated (or explicit no-change justification) with the calibration note updated; confirmation sweep of leading combinations shows the shipped defaults are the joint winner on the tune split, held-out agrees. Verify: results-table rows; `grep -n "_DEFAULT_RERANK_MIN_MARGIN\|max_length" src/cairn/graph/semantic.py src/cairn/graph/reranker.py`; full-set re-run recorded.
- **After Phase 5**: `cairn bench --compare` exits 0 against DS-v1 perf.json + agent.json (threshold 0.15), or every regression is a documented trade with the quality gain quantified; regenerated tables show AFTER vs the untouched DS-v1 BEFORE; a warm-time artifact exists with a measured first-query number (the 322 ms doc figure treated as advisory context, not a gate, since no committed baseline ever carried it — this decision is recorded in the artifact's notes). Verify: compare run exit code; `uv run python scripts/gen_benchmark_tables.py` succeeds (exact-key contract intact); docs/benchmarks.md sentinel block updated; agent tokens within bounds vs 6848.

## Risks & mitigations

- Risk: harness cannot reproduce the DS-v1 baseline → every later number is suspect → mitigation: P1's checkpoint is exactly this reproduction, before any lever lands.
- Risk: overfitting 58 queries → mitigation: FR-006 split + both-splits reporting + conservative parameter grids (research RQ5: small tuning sets pick fusion params that win on tune and lose in expectation).
- Risk: enriched chunks blow max-sequence/storage → mitigation: db_mb + size bounds tracked per variant in the sweep (spec risk, FR-002).
- Risk: quality-latency tension (e.g., always-rerank, bigger chunks) → mitigation: p95 tracked per sweep row; P5 compare gates at 0.15 with exit 2.
- Risk: rerank score distribution shifts with pair text (bge-reranker scores are unbounded raw logits) → mitigation: P4 re-calibration is mandatory whenever pair text changes; sigmoid normalization considered per research RQ4.
- Risk: fixture churn from recipe change (impact 85) → mitigation: budgeted in P3; chunk-shape tests updated with the recipe, not against it.
- Risk: standing-config drift (later levers measured under stale defaults) → mitigation: each phase measures under the previously shipped defaults and records that config in its results-table rows; deviations called out.

## Delivery

Branch `feat/retrieval-quality`; one PR per milestone (conventional commit + PR audit checklist per `docs/contribution-workflow.md`); code + its results-table rows land together. CI is advisory for *timing* numbers, but the DS tables/docs regeneration is a real gate: `gen_benchmark_tables.py` exits 1 on malformed keys, and `cairn bench --compare` exits 2 on regression — neither may be bypassed. DS-v1 artifacts stay immutable (the BEFORE snapshot); new measurements mint fresh artifacts — the measurement DB is a build artifact, regenerable at will.
