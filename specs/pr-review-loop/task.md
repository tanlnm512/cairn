# Tasks: pr-review-loop

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
Status reflects code state per [survey.md](survey.md), not intent.
**Before-audit**: pending — the orchestrator writes `passed @ <sha>` here

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| 1     | 3     | 0    |
| 2     | 3     | 0    |
| 3     | 2     | 0    |
| 4     | 2     | 0    |
| **Σ** | 10    | 0    |

## Phase 1: Review context pack (FR-001, FR-005)
<!-- Checkpoint: on a fixture diff the pack carries the precise radius plus memories/compass/wiki; empty radius exits 0 with a no-dependents statement; blast/store/readers are called, not copied. All todo per survey gaps S1 (radius-only output) and S4 (readers not composed into a review-facing pack). -->
- [ ] T001 [P] Build the fixture builder `tests/fixtures/review-loop/prepare.sh` — the shared two-module workspace (`ledger` consumed by `reporting` and `api`; `banner` consuming nothing) with the `pack` and `banner` scenarios per the test.md fixture contract — plus red test skeletons for TC-001..TC-004 against the `cairn review --base main` surface (C-02 failing-test-first)
    Check first whether an existing git-fixture helper under `tests/` is reusable or a new builder is needed; plan flags this `unknown — verify` (FR-001)
- [ ] T002 Implement the review pack skeleton: new `src/cairn/review/` engine plus new `src/cairn/cli/review.py` registering `cairn review` with the D-005 mutually exclusive mode flags and one additive import in the cli package init; `--base <ref>` calls `compute_blast` (`src/cairn/graph/blast.py:471`), reads the `seeds`/`changed_files` fields of its result dict, and renders the precise radius in text and markdown via `--format text|markdown` mirroring `render_blast` semantics; empty radius exits 0 with a no-dependents statement
    Consumes T001's `pack`/`banner` scenarios; turns TC-002, TC-003, TC-004 green (TC-001 stays red until T003). Test modules import the engine, never `cairn.cli`/`cairn.mcp_server` eagerly (C-04). Verify first: `grep -n "def compute_blast\|_seed_symbols" src/cairn/graph/blast.py` (FR-001, FR-005) (after T001)
- [ ] T003 Enrich the pack with memory, compass, and wiki sections: per-seed matches via `search_memory` (`src/cairn/memory/promotion.py:242`), module guides via `get_compass` and wiki pages via `search_knowledge(type_filter="Wiki")` (`src/cairn/mcp_server/tools_compass.py:34`/`:50`), imported lazily inside the enrichment function (D-002) and rendered in both formats; a reader failure degrades that pack section, not a crash
    Shares `src/cairn/review/` and `src/cairn/cli/review.py` with T002; turns TC-001 green. Read-only callers — zero edits to `graph/blast.py`, `memory/promotion.py`, `mcp_server/tools_compass.py` (D-001) (FR-001, FR-005) (after T002)

## Phase 2: Auto-capture hook (FR-003)
<!-- Checkpoint: a resolved comment records a draft-tier mistake/pattern memory keyed to the affected symbols with the PR link; a comment with no enclosing symbol records file-level. All todo per survey gaps S2 (no comment-to-symbol mapping) and S3 (nothing captures resolved review comments). -->
- [ ] T004 [P] Implement the comment-to-symbol span mapper in `src/cairn/review/`: map a comment's file+line to the enclosing symbol(s) via the stored `line_start`/`line_end` spans (`src/cairn/graph/schema.py:42-43`), degrading to file-level keying when no span encloses the line (spec risk mitigation); unit tests over a fixture index prove both paths
    Verify first: `grep -n "line_start\|line_end" src/cairn/graph/schema.py` (FR-003)
- [ ] T005 Implement the `--capture-event <file>` mode (the third D-005 mode): adapt the GitHub resolved-comment event JSON at the CLI boundary into comment text/file/line, classify deterministically per D-003 (a `cairn:pattern` thread label records type `pattern`, anything else records `mistake`), and record a draft-tier memory through `capture_memory` (`src/cairn/memory/promotion.py:21`) keyed to the enclosing symbols with the PR-thread link in the body; an open (unresolved) comment records nothing
    Consumes T004's mapper: takes (file, line), returns the enclosing symbol keys or the file-level fallback. Extend T001's `tests/fixtures/review-loop/prepare.sh` with the `capture` scenario and the `events/*.json` payloads per the fixture contract; turns TC-009, TC-010, TC-011 green. Invoke the surface as `cairn review --capture-event <file>` per D-005 — test.md's TC-009..TC-011 conditions currently spell `cairn review capture-hook --event`, so run the D-005 form until test.md re-syncs. Do not extend the `memory_failure_signatures` path (`src/cairn/graph/schema.py:138`); redaction at the `capture_memory` chokepoint applies unchanged (FR-003) (after T004)
- [ ] T006 [P] Spike the GitHub read path: read a resolved review-comment event for a scratch repository once via the REST API and once via the gh CLI, pick the authentication approach (token vs gh CLI — plan flags this `unknown — verify`), and record the decision via `record_memory` naming the payload shape T005's event adapter must accept (FR-003)

## Phase 3: Pre-submit guard (FR-002)
<!-- Checkpoint: one warning per mistake/pattern match keyed to a diff symbol, with its guidance; exit 0 ungated, non-zero only when gated. All todo per survey gap S1 (no pre-submit mode). -->
- [ ] T007 Implement the `--pre-submit` mode in `src/cairn/cli/review.py` plus the engine: reuse T002's diff-to-seed path (`compute_blast` result `seeds`), query `search_memory` (`src/cairn/memory/promotion.py:242`) for type `mistake` and `pattern` keyed to the seed symbols, and print one warning per match with its recorded guidance, formatted through T003's emitter; exit 0 with zero or more warnings (D-004)
    Shares `src/cairn/cli/review.py` and the engine with T002/T003. Call `search_memory`, never modify it (279-node recursive impact). Extend `tests/fixtures/review-loop/prepare.sh` with the `guard`, `quiet`, and `types` scenarios; turns TC-005, TC-006, TC-007 green. Verify first: `grep -n "@memory.command" src/cairn/cli/memory.py` (FR-002, FR-005) (after T003)
- [ ] T008 Implement the `--gate` flag for `--pre-submit` per D-004: exit non-zero only when `--gate` is set and at least one mistake/pattern match exists; without the flag the run stays exit 0 however many warnings print
    Shares `src/cairn/cli/review.py` with T007. Extend `tests/fixtures/review-loop/prepare.sh` with the `gate` scenario (the `guard` workspace plus a seeded keyed match) and prove the contract: `cairn review --pre-submit --gate` exits non-zero, plain `cairn review --pre-submit` exits 0 — test.md TC-008's current condition omits the flag, so run the D-004 form until test.md re-syncs. Turns TC-008 green (FR-002) (after T007)

## Phase 4: GitHub Action (FR-004)
<!-- Checkpoint: a PR event runs the workflow, which invokes the pre-submit check and posts findings as a PR comment on a fixture repo with a seeded mistake memory. All todo per survey supporting evidence: no review workflow exists under .github/workflows. -->
- [ ] T009 Author `.github/workflows/review.yml`: on pull_request events install the repo's cairn CLI in CI (pin the install method — tech-spec pitfall), run `cairn review --pre-submit` without `--gate` (D-004), and post the printed findings as a PR comment; a findings-free run posts no comment (the TC-013 shape)
    Consumes T007/T008's CLI contract — the `--pre-submit` flag, the exit codes, and the findings output format. Verify first: `ls .github/workflows` — only ci.yml and release.yml exist today (survey supporting evidence) (FR-004) (after T008)
- [ ] T010 Validate the workflow end to end on a scratch repository with a seeded mistake memory: open a PR touching a keyed symbol and observe one findings comment carrying the recorded guidance (TC-012); open a clean PR and observe a successful check with no findings comment (TC-013); record both observations as the task proof (FR-004) (after T009)

## Conventions
- `- [ ]` todo · `(in-progress)` claimed · `- [x]` done + proof note:
      `done <date> — <test/command that proves it>`
- Dropped: `- [ ] ~~T004~~ dropped <date> (D-###)` — never delete the line;
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
- Every task cites its FR-###; tasks with no FR are scope creep — fix the
  spec first
