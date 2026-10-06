# Plan: scip-indexing-v2

**Spec**: [spec.md](spec.md) | **Created**: 2026-09-20
Citations are verbatim from [survey.md](survey.md) (file:line) or from queries run
in this session (noted as such). No status words live here — task.md owns status.

## Milestones
| Phase | Milestone | Delivers (demoable) | FRs | Depends on |
|-------|-----------|---------------------|-----|------------|
| 1 | Closure redesign at 5× edges | Redesigned transitive-closure build that completes within the recorded budget at ~5× tree-sitter edge volume on the scaling corpus, byte-identical multi-hop results on non-SCIP graphs (AC4, AC5), budget enforced as a scaling-suite gate | FR-011, FR-012 | — |
| 2 | SCIP ingestion core (edges-only overlay) | `[scip]` extra + vendored stub + regen script; importer that loads a configured `.scip` index as an edges-only overlay — position join onto the single tree-sitter symbol population (AC1, AC2), per-file authority, per-document repo attribution, anomalous-join guard, protobuf-absent degrade with `[scip]` hint | FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-017 | — (parallel with Phase 1) |
| 3 | Auto-generation registry | Registry of seven indexers (swift, java, kotlin, typescript, python, go, rust); configured-but-absent index generated exactly once under a bounded timeout when the binary is on PATH; every failure mode degrades observably to tree-sitter (AC6, AC7) | FR-007, FR-008, FR-009 | Phase 2 |
| 4 | Incremental honesty & measurement | `cairn update` flips covered-file edges to tree-sitter provenance observably (AC8); `cairn stats` shows per-language exact-share uplift index-on vs off (AC3, first half); ground-truth run shows zero false-exact conversions with disagreements counted (AC3, second half) | FR-010, FR-013, FR-014 | Phase 2 (after Phase 3 in practice — shared stats/CLI surfaces) |
| 5 | CLI surface & docs | `cairn import-scip` against a built DB under the same overlay rules (AC9); `cairn config` echoes the `scip` key; `docs/scip.md` end-to-end toolchain guide | FR-015, FR-016 | Phases 2–4 |

Every FR-001..FR-017 appears in exactly one row above.

## Dependencies
```text
M1 closure redesign ──────────────────────────────┐ (independent; no SCIP code)
M2 ingestion core ──→ M3 auto-gen registry ──→ M4 incremental + measurement ──→ M5 CLI + docs
        └──────────────────────────────────────────┘ (M4 consumes M2's edges
                                                      provenance column directly)
```
- M1 ∥ M2: disjoint file sets (see map) — the only clean concurrency.
- M2 → M3: the generation hook lands in the same builder.py region as the overlay
  hook and consumes the importer M2 produces.
- M2 → M4: M4 flips and measures the `edges` provenance column M2 introduces
  (schema.py has no edges-source column today — survey FR-002 gap).
- M3 → M4: no data dependency; serialized because both write the stats.py /
  cli/core.py observation surfaces.
- M2+M3+M4 → M5: docs/scip.md documents the registry and fallback behavior;
  `import-scip` wraps M2's importer; the config echo exercises the key.
- M1 must land before the end-of-plan integration checkpoint (below), which is
  where the budget gate first runs against real 5× SCIP edge volume.

## Parallelization map
- **Independent: M1 (closure machinery) ∥ M2 (SCIP ingestion)** — disjoint files:
  - M1 touches: `src/cairn/graph/dataflow.py` (`build_transitive_closure:218`,
    `maintain_transitive_closure:314`), `src/cairn/graph/incremental.py`
    (`_capture_derived_prestate:519`, `_maintain_derived_indexes:685`,
    `_rebuild_derived_indexes:437`), `src/cairn/bench/perf_suite.py`
    (`run_perf_suite:64`, op `build.derived.closure` at 177),
    `.github/workflows/ci.yml`, `tests/test_dataflow_transitive_closure.py`,
    `tests/test_incremental_derived.py` (all survey.md Supporting evidence).
  - M2 touches: new `src/cairn/parsers/scip_importer.py`, new vendored
    `_scip_pb2.py` stub + `scripts/regen_scip_pb2.sh` + `pyproject.toml` `[scip]`
    extra (all absent — survey FR-017), `src/cairn/graph/schema.py` (edges
    provenance migration beside `EDGE_RESOLUTION_MIGRATION` at schema.py:459),
    `src/cairn/graph/config.py` (no scip key today — survey FR-015),
    `src/cairn/graph/builder.py` (post-resolve overlay hook; history's anchor
    ran "AFTER _resolve_all ... BEFORE backup_to" — survey Supporting evidence),
    plus the two committed fixture-index shapes (spec Scope).
  - No shared file; the overlay hook is build-stage, the closure is
    derived-index stage — different stages of the same pipeline, ordered at
    runtime, not contended at edit time.
- **Strictly ordered: M2 → M3** — M3's `try_generate_index` trigger rides the
  same `builder.py` config hook region M2 lands (history's hook at 0a956fa^
  builder.py hist ~415-460, survey FR-007), and its product must import through
  M2's importer. Produces/consumes: importer API + `scip` config key.
- **Strictly ordered: M2 → M4** — M4 reads and flips the edges provenance
  column M2's migration adds; `repository.py:31` currently writes symbol-level
  `'tree_sitter'` only (survey FR-010).
- **Strictly ordered: M3 → M4** — shared files only: `src/cairn/graph/stats.py`
  (skip records at stats.py:61-68 vs provenance breakdown) and
  `src/cairn/cli/core.py` (`stats:456` display at 470-488). Serialization is
  contention avoidance, not a data dependency — called out so the task-breaker
  doesn't mark these `[P]` against each other.
- **Strictly ordered: M2/M3/M4 → M5** — `import-scip` consumes M2's importer
  under the same overlay rules (AC9); `docs/scip.md` (FR-016) must describe the
  registry install matrix (M3) and the fallback/observation surfaces (M2/M4),
  so it cannot be written truthfully before they exist.
- Within M3, the seven registry entries degrade independently by design
  (FR-009), but they land in one new module — parallelism there is test-writing
  vs registry rows, at task-breaker discretion.

## Checkpoints
- **After Phase 1 (closure)**: existing parity gates green and the enforcing
  scaling gate in place with its budget recorded as a tech-spec D-###.
  - `uv run --no-sync pytest tests/test_dataflow_transitive_closure.py -q`
    (survey verify: 2 passed today)
  - `uv run --no-sync pytest tests/test_incremental_derived.py -k "matches_full_rebuild or drops_rows" -q`
    (survey verify: 2 passed, 60 deselected today)
  - The scaling-suite gate run on the scaling corpus with the budget number
    from tech-spec (command lands with the gate; today's bench is advisory —
    survey FR-012).
- **After Phase 2 (ingestion core)**: on a fixture workspace with a committed
  opaque-USR index, every covered file's call/reference edges are
  index-sourced and exact with no duplicate symbol population (AC1/AC2); with
  the protobuf runtime unavailable the same build succeeds via tree-sitter
  with an observable fallback record.
  - `uv run --no-sync pytest tests/test_scip_import.py -q` (module added this
    phase, over the two committed fixture shapes)
  - `rg -n "^scip" pyproject.toml` → the extra (survey verify exits 1 today)
- **After Phase 3 (registry)**: configured-but-absent + fake binary on PATH →
  generated exactly once and imported (AC6); binary missing / nonzero exit /
  timeout → build succeeds via tree-sitter with an observable record and the
  install hint under `-v` (AC7).
  - `uv run --no-sync pytest tests/test_scip_indexers.py -q` (module added
    this phase; binaries faked — CI never runs real indexers, spec Assumptions)
- **After Phase 4 (incremental + measurement)**: editing a covered file then
  `cairn update` leaves that file's edges tree-sitter-sourced with the
  provenance flip observable (AC8), and the next full build restores index
  edges; stats shows per-language exact-share uplift index-on vs off on the
  same tree; the ground-truth harness reports zero false-exact conversions
  with disagreements counted (AC3).
  - `uv run --no-sync pytest tests/test_incremental_scip_provenance.py -q`
    (module added this phase)
  - Ground-truth evaluation via the harness at `src/cairn/eval.py:load_ground_truth:124`
    (survey FR-014); exact invocation per test.md's cases.
- **After Phase 5 (CLI + docs)**: `cairn import-scip` lands edges under the
  same overlay rules on a built DB (AC9); config echo shows the key; guide
  exists.
  - `test -e docs/scip.md && echo PRESENT` (survey verify prints ABSENT today)
  - `uv run --no-sync cairn config` → echoes the `scip` key (surface at
    `src/cairn/cli/core.py:config:156`, survey FR-015)
  - `uv run --no-sync cairn import-scip --help` → exit 0
- **End-of-plan integration (after Phase 5)**: full build of the scaling
  corpus with an index on: closure within the recorded budget at real ~5×
  edge volume (the FR-011 condition, first time measured with SCIP edges),
  exact-share uplift visible, zero false-exact. This is the joint US1+US2
  demo; verify commands are Phase 1's gate re-run plus Phase 4's stats
  comparison.

## Risks & mitigations
- Risk: the closure redesign breaks multi-hop consumers — `build_transitive_closure`
  has 22 precise callers today (get_callers, this session): `src/cairn/cli/core.py:369`
  and `:373`, `src/cairn/graph/incremental.py:463`, `src/cairn/bench/perf_suite.py:184`,
  `src/cairn/bench/agent_suite.py:701`, `src/cairn/bench/swe_bench_suite.py:213`,
  plus test files including four `tests/test_skillgen_*.py` → mitigation: M1
  lands first, gated by the existing byte-identical parity tests
  (`tests/test_incremental_derived.py:747`, survey) before any SCIP edge exists.
- Risk: position-join misses on macro-generated / build-conditioned code drop
  edges (spec Assumptions & risks) → mitigation: the anomalous-join guard with
  drop counters (FR-005) and both fixture shapes — opaque-USR and
  readable-descriptor — committed as regression drivers (spec Scope).
- Risk: external indexer binaries drift (research.md RQ2: scip-kotlin's plugin
  repo archived at v0.6.0; no canonical scip-swift repo exists today) →
  mitigation: CI uses committed fixture indexes only (spec Assumptions);
  per-entry independent degradation (FR-009); install hints (FR-008) and
  docs/scip.md (FR-016) carry the truth about coverage.
- Risk: protobuf runtime/gencode mismatch breaks imports at runtime
  (research.md RQ4: import-time `VersionError` is the standard failure) →
  mitigation: floor pin in the `[scip]` extra decided as the FR-017 tech-spec
  decision; the FR-006 degrade path is tested with the runtime absent.
- Risk: shared-file contention across M2–M5 (`builder.py`, `stats.py`,
  `cli/core.py`, `incremental.py`) → mitigation: the serial spine above; the
  task-breaker sequences tasks within phases along the same edges.

## Assumptions (explicit — survey lacks evidence, so the plan does not claim it)
- The closure budget number (FR-012) and the anomalous-join threshold (FR-005)
  do not exist anywhere yet (survey FR-005/FR-012 gaps; tech-spec.md is the
  unfilled template at plan time). Checkpoints reference them symbolically;
  both are pinned as tech-spec D-###s before their phases start.
- `skipped_files.reason` accepts new indexer-failure reasons without a schema
  change (survey FR-008: CHECK-free TEXT) — tech-spec confirms or migrates.
- Solo delivery (no team context recorded in the spawn payload); the only
  concurrency this plan promises is M1 ∥ M2, and only if a second implementer
  exists — file-disjoint per the map.

## Delivery
Branch `feat/scip-indexing-v2` (spec.md). Solo cadence per the spawn payload's
team-context default: one PR per milestone — M1 through M5, five PRs, each
carrying that milestone's code + tests + its docs slice together (docs/scip.md
lands whole in M5 when its content exists). Never per-task commits. Every PR
follows the workspace shipping procedure (AGENTS.md): pre-commit, conventional
title, audit checklist, CI green before merge.
