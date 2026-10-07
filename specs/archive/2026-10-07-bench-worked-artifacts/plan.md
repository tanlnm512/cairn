# Plan: bench-worked-artifacts

**Spec**: [spec.md](spec.md) | **Created**: 2026-10-05

## Milestones
<!-- Each milestone = a phase in task.md. -->
| Phase | Milestone | Delivers (demoable) | Requirements | Depends on |
|-------|-----------|---------------------|--------------|------------|
| 1 | Worked bundle writer | `cairn bench --worked DIR` leaves a 3-file bundle per run — raw result JSON (identical payload to `--save`), inputs manifest (suite, dataset version, seed/repeats/runs, embed backend, machine stamp, full command line), README companion — overwritten atomically, privacy-scrubbed; an unwritable target fails cleanly after the results are on stdout | FR-001, FR-002, NFR-002, NFR-004 | — |
| 2 | Inventory + first bundle | `benchmarks/README.md` indexes the first committed worked bundle with one row per JSON artifact keyed repo-relative; re-runs append no duplicate rows; pre-existing rows and companion docs are untouched | FR-003, FR-004 | Phase 1 |

## Dependencies
Phase 2 consumes Phase 1's bundle writer: the inventory appender needs the
written-artifact paths and run directory the writer returns, and the
first-bundle mint needs the finished command. Within Phase 1 the order is
test-first (constitution C-02): the failing tests define the writer module's
API, and the module defines what the CLI seam calls. NFR-001/003/005/006 are
marked not applicable in spec.md and carry no milestone work.

## Parallelization map
<!-- The task-breaker turns this into [P] markers per task. -->
- Independent: none across implementation files — this spec is one vertical
  slice (one new module, one CLI seam, one test file, one inventory file);
  every candidate pair shares a file or consumes an upstream output.
- Strictly ordered (Phase 1): tests (`tests/test_bench_worked.py`) →
  writer module (`src/cairn/bench/worked.py`) → CLI seam
  (`src/cairn/cli/bench.py`) — C-02 failing-test-first, then each link
  consumes the previous link's symbols (the `worked.py` functions called
  from `bench()`).
- Strictly ordered (Phase 2): inventory tests + appender share
  `tests/test_bench_worked.py` / `src/cairn/bench/worked.py` /
  `src/cairn/cli/bench.py` with Phase 1 and consume the writer's returned
  artifact list; the first-bundle mint (`benchmarks/worked/`,
  `benchmarks/README.md`) consumes the completed command. Assumption (no
  survey evidence either way): minting runs on the reference machine per the
  committed-baselines precedent (survey S5, S6).

## Checkpoints
<!-- Exit condition per phase; verify before starting the next. -->
- **After Phase 1**: `uv run pytest tests/test_bench_worked.py -q` green
  (infra-marked, determinism-only per survey S7); `uv run cairn bench
  --suite perf --n-files 5 --worked "$TMPDIR/worked-check"` exits 0 and the directory
  holds `<suite>.json`, `manifest.json`, `README.md`; pointing `--worked` at
  an unwritable path exits non-zero with the bench results still on stdout
  (NFR-004).
- **After Phase 2**: two consecutive `--worked benchmarks` runs leave
  `grep -c 'benchmarks/worked/' benchmarks/README.md` unchanged (FR-003
  idempotence); `git diff benchmarks/README.md` shows appended rows only,
  existing rows byte-identical (FR-004).

## Risks & mitigations
- Risk: `benchmarks/README.md` appends clobber concurrent edits → mitigation:
  append-only, idempotent rows keyed on the repo-relative artifact path
  (spec risk note; worked runs are explicit and rare).
- Risk: manifest drift when CLI flags change → mitigation: the manifest
  records the full effective command it was produced with (self-describing),
  and a test asserts the recorded command re-parses (spec risk note).
- Risk: dataset version is null on a degraded stamp → mitigation: the run-dir
  version segment degrades to a literal placeholder instead of crashing
  (same doctrine as `build_artifact_stamp`'s missing-manifest degrade at the
  survey S3 seam).

## Delivery
Solo; branch `feat/bench-worked-artifacts` (spec.md). One PR per phase —
PR 1 `feat(bench): worked bundle writer for cairn bench` (US1), PR 2
`feat(bench): worked inventory rows + first worked bundle` (US2) — each via
the C-01 shipping workflow (branch → pre-commit → conventional commit → PR
with audit checklist → CI). Code and its tests land together per phase,
never per task.
