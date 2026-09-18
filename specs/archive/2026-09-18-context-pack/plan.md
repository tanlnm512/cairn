# Plan: context-pack

**Spec**: [spec.md](spec.md) | **Survey**: [survey.md](survey.md) (S1..S5) · **Team**: solo, PR-per-milestone

Graph checks run while planning (`cairn callers search_symbols|get_compass|search_memory|impact_from_closure`): all four are leaf functions consumed only by MCP/CLI/eval layers — a pack module composes them as a new consumer with no edits to their sources.

## Milestones
<!-- Each milestone = a phase in task.md. -->
| Phase | Milestone | Delivers (demoable) | FRs | Depends on |
|-------|-----------|---------------------|-----|------------|
| 1 | Pack skeleton | `cairn pack --task --budget` emits one markdown block (seed via semantic with lexical fallback → expand via precise call edges → emit), reported token count, deterministic and offline | FR-001, FR-005 | — |
| 2 | Rank + budget fit | Centrality-ordered output from the precomputed transitive tables; impossible budget drops lowest-centrality first and reports dropped counts | FR-002, FR-004 | Phase 1 |
| 3 | Enrichment content | Per-symbol source trim (signature + key body), depth-2 precise blast-radius summary, compass excerpt, top-3 memories — all within budget | FR-003 | Phase 1 |
| 4 | Bench fit-rate arm | Bench run reports pack-arm fit rate (target ≥0.85) and token fraction vs grep baseline | (scope item; no FR) | Phases 2+3 for final numbers; harness prep needs only Phase 1 |

## Dependencies
- M1 → M2: the fitter consumes M1's expanded item set; M1 fixes the per-item cost hook covering all FR-003 content kinds so the fitter stays content-kind-agnostic.
- M1 → M3: enrichment attaches to M1's item and emitter shapes.
- M1 → M4: the bench arm invokes the pack pipeline entry point.
- M2 ∥ M3: disjoint files; shared contract is M1's item model. Their combination is verified at the wave-C integration check.
- {M2, M3} → M4-final: fit-rate numbers are only meaningful on the assembled pipeline.
- Reuse anchors (survey): cost via `estimate_tokens`/`active_tokenizer_mode` (`src/cairn/dashboard/tokenizer.py`, S3) — no new counter; closure via `build_transitive_closure`/`impact_from_closure` (S1); seed via semantic + lexical fallback (S4); enrichment via `get_compass`/`search_memory` (S5). No `cairn pack` exists today (re-verified: no pack module under `src/cairn/cli/`, grep 0 hits).

## Parallelization map
<!-- Which work areas are independent (different files/subsystems, no shared
     state) and can be developed concurrently, and which are strictly
     sequential. The task-breaker turns this into [P] markers per task. -->
- Independent (wave B): M2 rank+fit files inside the new pipeline module ∥ M3 enrichment module + trimmer + emitter section ∥ M4-prep `src/cairn/bench/agent_suite.py` — disjoint file sets; enrichment readers are imported, not edited (caller evidence above); bench is an existing file untouched by pack work.
- Strictly ordered: M1 → everything — M1 produces the pipeline module, CLI surface, and item model all others consume. Wave B → wave C — the fit loop must consume enriched item costs (M2×M3) before final measurement. Parallel is the default; these two serialities are the only exceptions.

## Checkpoints
<!-- Exit condition per phase; verify before starting the next. -->
- **After Phase 1**: `cairn pack --task "<real task>" --budget 2000` emits a single markdown block with reported count ≤ budget; second run is byte-identical (`diff`); manual relevance eyeball on one bench task (early read on seed quality).
- **After Phase 2**: `cairn pack --budget 50` (impossible) degrades gracefully with dropped counts; output order follows centrality — spot-check top symbol via the impact CLI (`cairn knowledge impact`, `src/cairn/cli/knowledge.py:519`; exact invocation unknown — verify).
- **After Phase 3**: pack on a module with compass + memory coverage shows all four FR-003 content kinds within budget (section markers unknown — verify at task-break). Byte-identical rerun re-checked.
- **After Phase 4**: bench run reports fit rate ≥0.85 on in-budget packs (runner registered at `src/cairn/cli/bench.py:190`; exact invocation unknown — verify).

## Risks & mitigations
- Risk: semantic seed quality mid-band without embeddings → mitigation: thin slice lands first (Phase 1 eyeball checkpoint); lexical fallback + graph expansion per spec; fit-rate arm measures honestly (Phase 4).
- Risk: token accounting drift across models → mitigation: reuse `estimate_tokens` and report `active_tokenizer_mode`; budget conservative.
- Risk: determinism regression (FR-005) → mitigation: byte-identical rerun check at every checkpoint.
- Risk: bench corpus cannot host the fit-rate arm (spec assumption) → mitigation: first exercised at Phase 4 prep; if it fails, the harness extends task objects (per-arm accounting already exists).

## Plan assumptions (no survey evidence — verify at task-break)
- A1: new pack code lands as new modules under `src/cairn/`; exact paths decided by the task-breaker.
- A2: closure tables + `impact_from_closure` suffice for centrality without a `src/cairn/graph/dataflow.py` schema change; if an indexed view must be added there, M2 touches that file — wave-B disjointness with M3 still holds (different files).
- A3: at least one bench module has compass + memory coverage so the Phase 3 checkpoint is observable; readers exist (S5) but content coverage was not surveyed.
- A4: `cairn knowledge impact` is the CLI form of impact analysis; exact invocation unknown — verify.

## Delivery
Solo, one PR per milestone on `feat/context-pack` (spec.md branch); each PR carries that milestone's code + docs together, never per task. Conventional commit/PR titles; `main` is never pushed directly.
