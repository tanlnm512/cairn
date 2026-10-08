# Tasks: comment-sweep

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
**Lifecycle**: v2
Status reflects code state per [survey.md](survey.md), not intent.
**Delivered**: commit @ 4fdc478

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| 1 | 2 | 2 |
| 2 | 2 | 2 |
| 3 | 9 | 9 |
| 4 | 2 | 2 |
| **Σ** | 15 | 15 |

## Phase 1: Quiet gate spine (FR-004)
<!-- Checkpoint: make comment-style / make audit-status stderr carry no runpy RuntimeWarning; targeted suites green; ledger scaffold exists. -->
- [x] T001 (implemented) [P] Land the D-012 import fix in `report.py`: delete the module-level collector imports at lines 17-18 and import `collect_audit_status` locally inside `_audit_quality_gate` (`report.py:211`) and `DEFAULT_BASELINE`/`check_comment_baseline` locally inside `_comment_quality_gate` (`report.py:226`); leave `src/cairn/cli/system/__init__.py:14` eager so `cairn report` stays registered (FR-004)
  - done 2026-10-08 — D-012 runpy fix landed; 51 tests green; zero RuntimeWarning on both gate entrypoints
  - Touches:
    - `src/cairn/cli/system/report.py`
  - Proof: before — `UV_CACHE_DIR=/tmp/cairn-uv-survey make comment-style 2>&1 | grep RuntimeWarning` shows the warning; after — empty output for both `make comment-style` and `make audit-status`, and `CAIRN_LIB=/tmp/__no_such_lib__ UV_CACHE_DIR=/tmp/cairn-uv-survey uv run --extra test pytest tests/test_check_comment_lengths.py tests/test_report.py -q` green. This commit is the sweep's single `make verify-no-code-change` exception (tech-spec D-012/D-008).
- [x] T002 (implemented) [P] Scaffold the delivery record `docs/audits/comment-sweep.md`: title, per-phase remaining-count table (seeded 1213), and an empty migration ledger with columns `file :: symbol | landing (memory type + title / docs target)` per tech-spec D-002 (FR-002, NFR-005)
  - done 2026-10-08 — ledger scaffolded; 12 area rows seeded, counts sum 1213
  - Touches:
    - `docs/audits/comment-sweep.md`
  - Proof: file exists with both tables; `.venv/bin/cairn memory record --help` still shows the `{decision|pattern|mistake|workaround} TITLE` contract (survey FR-002 verify).

## Phase 2: Pilot sweep protocol (NFR-004)
<!-- Checkpoint: telemetry + memory trimmed with migrations recorded before deletion; make comment-style reports remaining <=1130, new [], stale []; verify-no-code-change clean. -->
- [x] T003 (implemented) (after T002 — consumes the ledger's migration-table format from `docs/audits/comment-sweep.md`) Sweep `src/cairn/telemetry/` (4 files, 54 baseline fingerprints): migrate durable rationale via `.venv/bin/cairn memory record <type> "<title>"` with `Why:`/`How to apply:` body lines and ledger each row BEFORE trimming; collapse docstrings to 1-line contracts; delete history/obvious narration; `rg -n "<distinctive removed phrase>" tests/` per trim with same-step pin updates; then shrink and commit together (FR-001, FR-002, NFR-004)
  - done 2026-10-08 — telemetry swept 1213→1159; 11 migrations; AST-identical; suite 4059 green
  - Touches:
    - `src/cairn/telemetry/`
    - `docs/audits/comment-sweep.md`
    - `docs/audits/comment-style-baseline.json`
  - Proof: `UV_CACHE_DIR=/tmp/cairn-uv-survey make comment-style ARGS=--json` → `remaining` strictly below 1213 with `new: []`, `stale: []`; `make verify-no-code-change REF=HEAD~1` exit 0; `CAIRN_LIB=/tmp/__no_such_lib__ UV_CACHE_DIR=/tmp/cairn-uv-survey uv run --extra test pytest tests/test_check_comment_lengths.py tests/test_report.py -q` green.
- [x] T004 (implemented) (after T003 — serializes on the shared `docs/audits/comment-style-baseline.json` and the append-only ledger) Sweep `src/cairn/memory/` (6 files, 51 fingerprints) with the identical migrate→trim→shrink→gate protocol from T003, updating any prose-pinned test in the same commit (FR-001, FR-002, NFR-004)
  - done 2026-10-08 — memory swept 1159→1108; 14 migrations; suite green
  - Touches:
    - `src/cairn/memory/`
    - `docs/audits/comment-sweep.md`
    - `docs/audits/comment-style-baseline.json`
  - Proof: `make comment-style ARGS=--json` → `remaining ≤1130`, `new: []`, `stale: []`; `make verify-no-code-change REF=HEAD~1` exit 0; targeted suites green.

## Phase 3: Bulk sweep (FR-001, FR-002, FR-003)
<!-- Checkpoint: graph, interface, and long-tail areas trimmed in per-area suite-gated commits; remaining <=550, new [], stale []; every commit passed pre-commit and verify-no-code-change; full suite green in the phase PR's CI. -->
- [x] T005 (implemented) (after T004 — shared baseline + ledger) Sweep `src/cairn/graph/embeddings.py` + `src/cairn/graph/schema.py` (82 fingerprints) with the T003 protocol (FR-001, FR-002, FR-003)
  - done 2026-10-08 — embeddings+schema swept 1108→1026; 31 migrations; SQL DDL untouched
  - Touches:
    - `src/cairn/graph/embeddings.py`
    - `src/cairn/graph/schema.py`
    - `docs/audits/comment-sweep.md`
    - `docs/audits/comment-style-baseline.json`
  - Proof: `make comment-style ARGS=--json` remaining strictly lower, `new: []`, `stale: []`; `make verify-no-code-change REF=HEAD~1` exit 0.
- [x] T006 (implemented) (after T005 — shared baseline + ledger) Sweep `src/cairn/graph/builder.py`, `incremental.py`, `scanner.py`, `dataflow.py` (94 fingerprints) with the T003 protocol (FR-001, FR-002, FR-003)
  - done 2026-10-08 — graph core swept 1026→932; 43 migrations; suite green
  - Touches:
    - `src/cairn/graph/builder.py`
    - `src/cairn/graph/incremental.py`
    - `src/cairn/graph/scanner.py`
    - `src/cairn/graph/dataflow.py`
    - `docs/audits/comment-sweep.md`
    - `docs/audits/comment-style-baseline.json`
  - Proof: same gate shape as T005; AST proof exit 0.
- [x] T007 (implemented) (after T006 — shared baseline + ledger) Sweep the remaining `src/cairn/graph/` files (~158 fingerprints across the area's other 24 files, from the baseline inventory) with the T003 protocol (FR-001, FR-002, FR-003)
  - done 2026-10-08 — graph remainder swept 932→774; 158 fingerprints, 80 migrations; infra leg 249 green
  - Touches:
    - `src/cairn/graph/`
    - `docs/audits/comment-sweep.md`
    - `docs/audits/comment-style-baseline.json`
  - Proof: area exhausted — no `src/cairn/graph/` path in `jq -r '.violations[].paths[]' docs/audits/comment-style-baseline.json` beyond kept-constraint entries; gate shape as T005.
- [x] T008 (implemented) (after T007 — shared baseline + ledger) Sweep `src/cairn/mcp_server/` (11 files, 96 fingerprints): trim tool docstrings to one-line contracts, NEVER empty — `tests/test_invariants.py:114` requires non-empty MCP tool docstrings (tech-spec D-007) (FR-001, FR-002, FR-003)
  - done 2026-10-08 — mcp_server swept 774→678; 96 fingerprints, 24 migrations; non-empty docstring invariant held (5 invariants tests)
  - Touches:
    - `src/cairn/mcp_server/`
    - `docs/audits/comment-sweep.md`
    - `docs/audits/comment-style-baseline.json`
  - Proof: gate shape as T005; `CAIRN_LIB=/tmp/__no_such_lib__ UV_CACHE_DIR=/tmp/cairn-uv-survey uv run --extra test pytest tests/test_invariants.py -q` green.
- [x] T009 (implemented) (after T008 — shared baseline + ledger) Sweep `src/cairn/cli/` (24 files, 126 fingerprints): preserve the pinned phrase `read-only unless --fix` in `src/cairn/cli/system/doctor.py:1252` (`tests/test_doctor.py:1491` asserts it in `doctor --help`) or update that pin in the same commit; never trim decorator `help=` strings (FR-001, FR-002, FR-003)
  - done 2026-10-08 — cli swept 678→552 — FR-001 floor ≤606 beaten here; 18 migrations; read-only-unless---fix pin preserved
  - Touches:
    - `src/cairn/cli/`
    - `docs/audits/comment-sweep.md`
    - `docs/audits/comment-style-baseline.json`
  - Proof: gate shape as T005; `uv run --extra test pytest tests/test_doctor.py tests/test_cli_smoke.py -q` green (doctor help pin + smoke help pins).
- [x] T010 (implemented) (after T009 — shared baseline + ledger) Sweep `src/cairn/dashboard/` (15 files, 83 fingerprints) with the T003 protocol (FR-001, FR-002, FR-003)
  - done 2026-10-08 — dashboard swept 552→469; 28 migrations; suite 4311 green
  - Touches:
    - `src/cairn/dashboard/`
    - `docs/audits/comment-sweep.md`
    - `docs/audits/comment-style-baseline.json`
  - Proof: gate shape as T005; dashboard test files for the touched modules green.
- [x] T011 (implemented) (after T010 — shared baseline + ledger) Sweep `src/cairn/parsers/` (18 files, 107 fingerprints) with the T003 protocol; grammar-edge notes that state real constraints stay as single-line notes (FR-001, FR-002, FR-003)
  - done 2026-10-08 — parsers swept 469→362; 10 migrations; D-010 regen deviation ruled to closeout
  - Touches:
    - `src/cairn/parsers/`
    - `docs/audits/comment-sweep.md`
    - `docs/audits/comment-style-baseline.json`
  - Proof: gate shape as T005; parser test files for touched modules green.
- [x] T012 (implemented) (after T011 — shared baseline + ledger) Sweep `src/cairn/knowledge/` (17 files, 70 fingerprints) with the T003 protocol (FR-001, FR-002, FR-003)
  - done 2026-10-08 — knowledge swept 362→292; 20 migrations; suite green
  - Touches:
    - `src/cairn/knowledge/`
    - `docs/audits/comment-sweep.md`
    - `docs/audits/comment-style-baseline.json`
  - Proof: gate shape as T005; knowledge test files for touched modules green.
- [x] T013 (implemented) (after T012 — shared baseline + ledger) Sweep `src/cairn/agent_install/` (13 files, 66 fingerprints) with the T003 protocol (FR-001, FR-002, FR-003)
  - done 2026-10-08 — agent_install swept 292→226; 38 migrations; final area at zero
  - Touches:
    - `src/cairn/agent_install/`
    - `docs/audits/comment-sweep.md`
    - `docs/audits/comment-style-baseline.json`
  - Proof: gate shape as T005; install/uninstall fidelity tests green.

## Phase 4: Closeout & ratchet handoff (FR-005)
<!-- Checkpoint: final shrink-only re-derivation lands <=606 (target <=550); ledger complete with every migration; CHANGELOG entry; full CI green on the phase PR. -->
- [x] T014 (implemented) (after T013 — consumes the final post-trim tree state) Run the definitive baseline re-derivation `UV_CACHE_DIR=/tmp/cairn-uv-survey make comment-style-shrink ARGS=--json` and confirm `jq '.violations | length' docs/audits/comment-style-baseline.json` ≤606 (target ≤550); if above target, trim long-tail areas (bench, compass, wiki, llm, eval, paths, skillgen, pack, hooks, review, okf, refs, viz, utils — 205 fingerprints combined) ONLY as needed for margin, each with the T003 protocol (FR-001, FR-005)
  - done 2026-10-08 — definitive re-derivation byte-stable at 226 (≤550 ≤606); regen script stabilized (D-010); scip tests 35 green
  - Touches:
    - `docs/audits/comment-style-baseline.json`
    - `docs/audits/comment-sweep.md`
    - `src/cairn/**`
  - Proof: `UV_CACHE_DIR=/tmp/cairn-uv-survey make comment-style ARGS=--json` → `{"ok": true, ..., "remaining": ≤606}` (survey FR-005 verify); `make verify-no-code-change REF=HEAD~1` exit 0 for any trim commits added here.
- [x] T015 (implemented) (after T014 — consumes the final count and complete migration ledger) Finalize the delivery record (per-phase counts, migration totals, final remaining) and add the CHANGELOG entry per tech-spec D-009 (FR-002, NFR-005)
  - done 2026-10-08 — delivery record finalized 1213→226 (81.3%), 321 rows→317 records, CHANGELOG landed; proofs 8/8 + 6 manual, regression 4059/5skip, mutation 40/40, reviewer 1 BLOCK+3 WARNs all fixed (audit-status green 144/144)
  - Touches:
    - `docs/audits/comment-sweep.md`
    - `CHANGELOG.md`
  - Proof: every migration row names a memory type + title or docs target; ledger totals match the commits; `UV_CACHE_DIR=/tmp/cairn-uv-survey make comment-style` prints the final remaining count.

## Conventions
- `- [ ]` todo · `(in-progress)` claimed · `(implemented)` landed —
      implementation evidence durable before the one all-at-once tick ·
      `- [x]` done + proof note: `done <date> — <test/command that proves it>`
- Dropped: `- [ ] ~~T016~~ dropped <date> (D-###)` — never delete the line;
  dropped tasks stay visible with the decision that killed them
- `[P]` = parallelizable (default — no shared files, no upstream task);
  chained tasks note `(after T###)` and name the exact interface they
  consume from their upstream — symbols, signatures, file formats; serial
  runs need a reason, parallel runs need none
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
