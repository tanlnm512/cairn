# Plan: pr-review-loop

**Spec**: [spec.md](spec.md) | **Created**: 2026-09-17
**Baseline**: main @ d18768c | **Team context**: solo, PR-per-milestone (payload default)

## Milestones
<!-- Each milestone = a phase in task.md. -->
| Phase | Milestone | Delivers (demoable) | FRs | Depends on |
|-------|-----------|---------------------|-----|------------|
| 1     | Review context pack | `cairn review --base main` on a real diff emits a text+markdown pack with the precise radius plus memories/compass/wiki matched to affected symbols (US1 AC1); empty radius exits 0 with a no-dependents statement (US1 AC2) | FR-001, FR-005 | — |
| 2     | Auto-capture hook | A resolved/accepted review comment tied to changed symbols records a draft-tier mistake/pattern memory keyed to those symbols with the PR-thread link (US3 AC1); file-level fallback when no symbol encloses | FR-003 | Phase 1 |
| 3     | Pre-submit guard | `cairn review --pre-submit` surfaces one warning per mistake/pattern memory keyed to a diff symbol, with its guidance (US2 AC1); exit 0 by default, non-zero only when gated | FR-002 | Phase 1 |
| 4     | GitHub Action | Opening a PR runs the workflow, which invokes `cairn review --pre-submit` and posts findings as a PR comment | FR-004 | Phase 3 |

FR coverage: FR-001+FR-005 → Phase 1 · FR-003 → Phase 2 · FR-002 → Phase 3 ·
FR-004 → Phase 4 — each FR in exactly one milestone. Risk-first ordering: the
spec's named risk (comment→symbol attribution) lands in Phase 2, the earliest
slot its dependency allows.

## Dependencies
```
P1 pack ──┬──> P2 capture   (wave 2, parallel with P3)
          └──> P3 guard ────> P4 action   (wave 3, strictly after P3)
```
- **P2 after P1**: consumes P1's diff→affected-symbols seeding (built on
  blast's `_changed_files`/`_seed_symbols`, survey S1). Re-deriving it in the
  capture path would duplicate graph logic — an FR-005 violation.
- **P3 after P1**: same shared seeding, plus the pack emitter reused for
  warning formatting.
- **P4 after P3**: the Action's entire payload is invoking
  `cairn review --pre-submit` — its flag, exit-code contract, and findings
  output are P3 deliverables. Nothing to run or post before P3 exists.

Coupling evidence (session, cairn graph CLI + grep):
- `cairn callers compute_blast` → only `src/cairn/cli/blast.py:41` and the
  blast module itself — leaf surface; the review command is a purely additive
  second caller.
- `cairn callers capture_memory` / `search_memory` → tests,
  `src/cairn/cli/memory.py`, `src/cairn/mcp_server/tools_memory.py`,
  `src/cairn/compass/router.py`, `src/cairn/eval.py` — additive consumer
  surfaces only; no store-internal changes.
- `grep -n "def search_knowledge" src/cairn/knowledge/search.py` → line 38:
  core reader beneath the MCP wrapper (survey S4), callable without an MCP hop.
- `src/cairn/cli/main.py`: per-module click groups (`@<subgroup>.command()`;
  package `__init__` imports each module) → a new command module registers
  additively.

Standing constraint (FR-005, proven first in P1, re-checked at every later
checkpoint): additive callers only — no logic changes inside the blast engine,
memory store, or knowledge readers.

## Parallelization map
<!-- Which work areas are independent (different files/subsystems, no shared
     state) and can be developed concurrently, and which are strictly
     sequential. The task-breaker turns this into [P] markers per task. -->
| Area | Phase / FR | File set (disjointness evidence) |
|------|------------|----------------------------------|
| A — pack | P1 (FR-001, FR-005) | NEW `src/cairn/cli/review.py` + NEW pack-builder module; one additive import in the cli package init. Reads blast/knowledge/memory APIs. |
| B — capture | P2 (FR-003) | NEW capture module; install surface in EXISTING `src/cairn/hooks/` (claude_hooks.py, cursor_hooks.py, git_hooks.py — survey supporting evidence). Reads schema spans (S2); writes via `capture_memory` (S3). |
| C — guard | P3 (FR-002) | EXTENDS area A's `src/cairn/cli/review.py` with the pre-submit mode + gate config. |
| D — action | P4 (FR-004) | NEW `.github/workflows/<review>.yml` (+ any action packaging). Touches `.github/` only (session `ls`: only ci.yml, release.yml exist today). |

- Independent: B (capture) ∥ C (guard) — disjoint writable files (B: new
  capture module + hooks/; C: cli/review.py); both only READ P1's shared
  seeding; neither produces anything the other consumes. The serial burden is
  not met, so they run concurrently. Solo preference: B first (pulls the
  attribution risk earlier) — a preference, not a constraint.
- Strictly ordered: A → {B, C} — A produces the diff→symbols seeding and pack
  emitter that B and C consume; duplicating it would violate FR-005.
- Strictly ordered: C → D — C produces the CLI contract (`--pre-submit`,
  exit codes, findings output format) that D's workflow invokes and posts;
  scaffolding D earlier is untestable with an undefined demo.

Serial spine: A → {B ∥ C} → D.

## Checkpoints
<!-- Exit condition per phase; verify before starting the next. -->
- **After Phase 1**: both US1 ACs hold on a fixture diff — pack contains
  precise radius + relevant memories/compass/wiki; empty radius exits 0 with
  a no-dependents statement; blast/store/readers are called, not copied.
  Verify: reuse S1's `grep -n "def compute_blast\|_seed_symbols" src/cairn/graph/blast.py`;
  S4's `grep -n "def search_knowledge\|def get_compass" src/cairn/mcp_server/tools_compass.py`;
  session's `grep -n "def search_knowledge" src/cairn/knowledge/search.py`.
- **After Phase 2**: US3 AC1 holds on a fixture — resolved comment records a
  draft-tier mistake/pattern memory keyed to the affected symbols with the PR
  link; a comment with no enclosing symbol records file-level. Verify: reuse
  S2's `sed -n '36,50p' src/cairn/graph/schema.py` (spans) and S3's
  `grep -n "def capture_memory\|def search_memory\|def promote_memory" src/cairn/memory/promotion.py`
  and `grep -n "@memory.command" src/cairn/cli/memory.py`.
- **After Phase 3**: US2 AC1 plus the exit-code contract hold on fixture
  memories — warning per match with guidance; exit 0 ungated, non-zero only
  when gated. Verify: fixture run exit codes; matching rides S3's
  `search_memory`.
- **After Phase 4**: a PR event triggers the workflow, which posts the
  pre-submit findings as a PR comment on a fixture repo with a seeded mistake
  memory. Verify: review workflow present under `.github/workflows/`
  alongside ci.yml/release.yml.

## Risks & mitigations
- Risk: comment→symbol attribution imprecise for file-targeted comments (spec
  risk) → mitigation: de-risked at the start of Phase 2 — the file+line →
  enclosing-symbol resolver over stored spans (S2) is proven on fixtures
  before the GitHub-read path; the file-level fallback is inside Phase 2's
  checkpoint, not deferred.
- Risk: memory volume growth from auto-capture (spec risk) → mitigation:
  Phase 2 writes through the existing capture surface into the draft tier
  lifecycle (S3); no new tier logic in scope.
- Risk: capture hook's GitHub access is unproven — survey records no GitHub
  API client; session grep found only incidental matches (`github_pat_`
  secret regex at `src/cairn/memory/privacy.py:42`, CI runner slugs in bench
  modules, upstream URLs) — `unknown — verify` authentication approach
  (token vs gh CLI) early in Phase 2.
- Risk: fixture-repo test strategy (spec scope) has no documented builder in
  survey.md; session grep shows generic fixture usage in tests/ —
  `unknown — verify` whether an existing git-fixture helper is reusable or a
  new one is part of Phase 2's work.

## Delivery
Solo, one PR per milestone (P1–P4), each against `feat/pr-review-loop` from
baseline main @ d18768c. Per milestone the code + docs land together as a
single commit — never per task (ADR-001, tick-commit node). Post-merge per
milestone: `cairn update` + `record_memory` per the workspace shipping
procedure.
