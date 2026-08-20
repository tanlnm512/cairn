# Tasks: ui-dashboard-live-updates

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
Status reflects code state per [survey.md](survey.md), not intent.

## Burndown
| Phase | Total | Done |
|-------|-------|------|
| 1     | 4     | 0    |
| 2     | 3     | 0    |
| 3     | 3     | 0    |
| **Σ** | 10    | 0    |

## Phase 1: Polling spine on history (FR-001, FR-003, FR-006)
<!-- Checkpoint: new store rows render into an open history page without
     interaction; filters + scroll survive the swap. -->
- [ ] T001 Mark the history body region in `src/cairn/dashboard/templates/history.html` and add the shared poll-control chrome to `src/cairn/dashboard/templates/base.html` (FR-001)
- [ ] T002 Implement the poll loop module in `src/cairn/dashboard/static/app.js`: re-arming timer, same-origin fetch, DOMParser fragment swap of the marked region, guard-skip when no region present (FR-001)
- [ ] T003 Preserve filter inputs and scroll position across swaps in the poll module; make the interval configurable from the page (FR-003)
- [ ] T004 Add the server-side fragment re-fetch test and the idempotency assertion (same rows re-rendered, no duplicates) to `tests/test_dashboard_app.py` (FR-001, FR-006)

## Phase 2: Pause + connection state (FR-004, FR-005)
<!-- Checkpoint: pause visibly halts updates; a stopped server shows a
     disconnected state that self-heals. -->
- [ ] T005 Add pause/resume control with visible state indication to the shared chrome and wire it into the loop's state machine (FR-004)
- [ ] T006 Add the disconnected banner driven by fetch rejection with recovery on the next successful poll, styled in `src/cairn/dashboard/static/app.css` (FR-005)
- [ ] T007 Add loop-module state tests: paused-issues-no-fetch, rejected-then-resolved transitions (FR-004, FR-005)

## Phase 3: Chains + tokens live; soak (FR-002)
<!-- Checkpoint: chains and tokens refresh on the same interval; the soak
     reports zero duplicates and zero missed batches. -->
- [ ] T008 Mark the body regions in `src/cairn/dashboard/templates/chains.html` and `src/cairn/dashboard/templates/tokens.html` so the existing loop refreshes them (FR-002)
- [ ] T009 Add fragment growth tests for chains and tokens (call count +1, totals shift) to `tests/test_dashboard_app.py` (FR-002)
- [ ] T010 Build the soak harness (tick-driven auto soak + one-hour manual procedure) asserting rendered id-set equality with the stored slice (FR-006)

## Conventions
- `- [ ]` todo · `(in-progress)` claimed · `- [x]` done + proof note:
      done DATE — the test/command that proves it
- Dropped: `- [ ] ~~T011~~ dropped DATE (D-###)` — never delete the line;
  dropped tasks stay visible with the decision that killed them
- `[P]` = parallelizable (default — no shared files, no upstream task);
  chained tasks note `(after T###)`; serial runs need a reason, parallel
  runs need none
- Every task cites its FR-###; tasks with no FR are scope creep — fix the
  spec first
