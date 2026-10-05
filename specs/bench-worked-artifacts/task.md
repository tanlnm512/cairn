# Tasks: bench-worked-artifacts

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
**Lifecycle**: v2
Status reflects code state per [survey.md](survey.md), not intent.
**Delivered**: pending — delivery evidence; the orchestrator writes `commit @ <sha>` here

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| 1 | 3 | 3 |
| 2 | 3 | 3 |
| **Σ** | 6 | 6 |

## Phase 1: Worked bundle writer (FR-001, FR-002, NFR-002, NFR-004)
<!-- Checkpoint: tests green; --worked writes <suite>.json + manifest.json + README.md; unwritable target fails cleanly after results print (plan.md After Phase 1). -->

- [x] T001 (implemented) Write failing infra-marked tests pinning the worked-bundle contract (FR-001, FR-002, NFR-002, NFR-004)
  - done 2026-10-05 — done 2026-10-05 — 15 failing-first contract tests (writer surface, manifest schema, atomic, scrub, fail-clean)
  - Determinism-only asserts (survey S7 gap: "structure + idempotence asserts only, no wall-clock asserts"): bundle holds `<suite>.json` (payload identical to `--save`'s, S2 seam `src/cairn/cli/bench.py:663`), `manifest.json` (inputs + stamp + full command per tech-spec D-003), `README.md`; canonical layout `benchmarks/worked/<suite>-<datasetversion>/` when DIR is the benchmarks root vs exact DIR otherwise (D-006); atomic overwrite on re-run (D-005); serialized manifest contains no absolute home path (D-004); unwritable `--worked` target exits non-zero with bench results still on stdout (D-008). Infra mark per `pyproject.toml:207-208` (S7). Constitution C-04: lazy `cairn.cli` import, `tmp_path` only, never patch global `subprocess.Popen`.
  - These tests are the contract every later task in this spec consumes: they pin `src/cairn/bench/worked.py`'s public surface (function names, `write -> list[Path]` return, `OSError` on unwritable targets) and the `manifest.json` schema `cairn.worked.manifest.v1` (tech-spec D-003).
  - Verify before implementing: `grep -n "infra" pyproject.toml | head -2` (S7); `cairn bench --help` shows no `--worked` yet (S1).
  - Touches:
    - `tests/test_bench_worked.py`
- [x] T002 (implemented) Implement the worked-bundle writer module (after T001) (FR-001, FR-002, NFR-002)
  - done 2026-10-05 — done 2026-10-05 — writer module worked.py (manifest v1, click-Context command, os.replace atomic, run-dir resolver)
  - Consumes T001's failing tests in `tests/test_bench_worked.py` — they define `src/cairn/bench/worked.py`'s public surface and the manifest schema; make them pass without weakening asserts.
  - New module per tech-spec D-001/D-002/D-003/D-004/D-005/D-006: manifest builder (inputs incl. `seed` = `DEFAULT_SEED` from `src/cairn/bench/corpus.py` when the corpus was generated, else null; stamp block copied from the payload's `dataset`/`cairn_version`/`machine_profile` keys; command rebuilt from `click.get_current_context()`), privacy scrub (repo-relative or `$HOME`-templated), sibling-temp-file + `os.replace` atomic writer for all three files, run-dir resolver (benchmarks root = parent of `default_baselines_root()`, version segment `dataset.version` → swe-bench `revision_sha[:12]` → `unversioned`).
  - Verify before implementing: `grep -n "def _stamp_and_emit" src/cairn/cli/bench.py` (S3); `grep -n "Path(save).write_text" src/cairn/cli/bench.py` (S2).
  - Pitfalls: never mutate the passed payload (compare still consumes it); writer stays suite-agnostic (swe-bench reaches the seam with its own payload, tech-spec D-002).
  - Touches:
    - `src/cairn/bench/worked.py`
- [x] T003 (implemented) Wire `--worked DIR` into the bench command at the save seam (after T002) (FR-001, NFR-004)
  - done 2026-10-05 — done 2026-10-05 — --worked wired at the save seam; OSError exit 1 after stdout results
  - Consumes T002's `src/cairn/bench/worked.py` entry point — the function T001's tests pin (payload-in, written-paths-out, `OSError` on unwritable target).
  - Add the `--worked` option to the option list (`src/cairn/cli/bench.py:425-435`, S1 — no worked today) and one call block after the `--save` block (`:661-664`, S2), before compare; on `OSError`: `display.error` + `sys.exit(1)` — results already on stdout (D-008). Success prints a one-line summary naming the run dir.
  - Verify before implementing: `cairn bench --help` (S1). Verify after: Phase 1 checkpoint in plan.md (`uv run pytest tests/test_bench_worked.py -q` green; manual `--worked <tmp>` run).
  - Pitfalls: keep the block inside the existing `try` whose `finally` (`:716-724`) restores `CAIRN_DB` and cleans temp dirs; exit-code discipline — 1 usage/failure, 2 regression.
  - Touches:
    - `src/cairn/cli/bench.py`

## Phase 2: Inventory + first bundle (FR-003, FR-004)
<!-- Checkpoint: two consecutive --worked benchmarks runs leave the README row count unchanged; git diff shows appended rows only (plan.md After Phase 2). -->

- [x] T004 (implemented) Write failing idempotence + untouched-rows tests for the inventory append (after T003) (FR-003, FR-004)
  - done 2026-10-05 — done 2026-10-05 — idempotence + untouched-rows + outside-repo tests (failing first)
  - Consumes the finished CLI from T003 (`--worked` flag) via `CliRunner` + `tmp_path` to produce real bundles; appends to the shared `tests/test_bench_worked.py` (same file as T001 — serial).
  - Asserts: one row per JSON artifact keyed repo-relative in the `| Artifact | Named by |` table (`benchmarks/README.md`, S4 — rows key repo-relative, never basename, `:8-9`); a second run adds no duplicate rows (FR-003); pre-existing rows and companion docs are byte-identical after appends (FR-004); a bundle outside the repo appends nothing (tech-spec D-007).
  - Touches:
    - `tests/test_bench_worked.py`
- [x] T005 (implemented) Implement the inventory appender and hook it into the worked writer (after T004) (FR-003, FR-004)
  - done 2026-10-05 — done 2026-10-05 — append_inventory_rows hooked; repo-relative keys; sorted insert
  - Consumes T004's failing tests in the shared `tests/test_bench_worked.py`; extends the shared `src/cairn/bench/worked.py` (same file as T002) and its CLI call site in `src/cairn/cli/bench.py` (same file as T003) — serial on both counts.
  - Appender per tech-spec D-007: read `benchmarks/README.md`, append one `| Artifact | Named by |` row per new JSON artifact (`<suite>.json`, `manifest.json`) naming the run `README.md`, skip paths already present; write via the T002 atomic writer; skip entirely when the bundle dir is outside the repo. The writer's return carries the run dir + written paths the appender needs.
  - Verify after: Phase 2 checkpoint's idempotence half (`grep -c 'benchmarks/worked/' benchmarks/README.md` stable across re-runs, on a scratch checkout).
  - Touches:
    - `src/cairn/bench/worked.py`
    - `src/cairn/cli/bench.py`
- [x] T006 (implemented) Mint the first committed worked bundle and land its inventory rows (after T005) (FR-003)
  - done 2026-10-05 — done 2026-10-05 — first bundle minted at benchmarks/worked/perf-DS-v1 + 2 README rows; re-mint no-dup verified
  - Consumes T005's completed command: `uv run cairn bench --suite perf --worked benchmarks` run on the reference machine (committed-baselines precedent, survey S5/S6; plan.md assumption), then commit `benchmarks/worked/<suite>-<datasetversion>/` plus the rows the command appended to `benchmarks/README.md`.
  - Confirm FR-004 on the real tree: `git diff benchmarks/README.md` shows appended rows only; rows follow S4's repo-relative convention.
  - Verify after: `sed -n '1,12p' benchmarks/README.md` (S4) shows the convention intact; `ls benchmarks benchmarks/baselines` (S5) now joined by `worked/`.
  - Touches:
    - `benchmarks/worked/`
    - `benchmarks/README.md`

## Conventions
- `- [ ]` todo · `(in-progress)` claimed · `(implemented)` landed —
      implementation evidence durable before the one all-at-once tick ·
      `- [x]` done + proof note: `done <date> — <test/command that proves it>`
- Dropped: `- [ ] ~~T004~~ dropped <date> (D-###)` — never delete the line;
  dropped tasks stay visible with the decision that killed them
- `[P]` = parallelizable (default — no shared files, no upstream task);
  chained tasks note `(after T###)` and name the exact interface they
  consume from their upstream — symbols, signatures, file formats; serial
  runs need a reason, parallel runs need none. Per plan.md's
  parallelization map, every task here is chained: C-02 failing-test-first
  (T001→T002, T004→T005), upstream symbol/file consumption (T002→T003,
  T003→T004, T005→T006), and shared-file overlaps (T001/T004 share
  `tests/test_bench_worked.py`; T002/T005 share `src/cairn/bench/worked.py`;
  T003/T005 share `src/cairn/cli/bench.py`)
- Fix rounds append `(fix <n>/5)` to the entry — the cap survives resume
  only if the count lives here, in the status holder. From round 2 on, an
  implementer's scratch note (what was tried, why it failed) may live at
  `notes/T###.md` — the one file an implementer may write under specs/,
  never read by check.py, never counted as status
- Every task cites FR-### or an applicable NFR-###; a task with neither is
  scope creep — fix the spec first
- Every code task carries a `Touches:` block (repo-relative files,
  directories, or globs); `[P]` tasks whose touches overlap are chained, not
  spawned together
