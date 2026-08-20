# Tasks: ui-dashboard-live-updates

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
Status reflects code state per [survey.md](survey.md), not intent.

## Burndown
| Phase | Total | Done |
|-------|-------|------|
| 1     | 4     | 4    |
| 2     | 3     | 3    |
| 3     | 3     | 3    |
| **Σ** | 10    | 10   |

## Phase 1: Polling spine on history (FR-001, FR-003, FR-006)
<!-- Checkpoint: new store rows render into an open history page without
     interaction; filters + scroll survive the swap. -->
- [x] T001 Mark the history body region in `src/cairn/dashboard/templates/history.html` and add the shared poll-control chrome to `src/cairn/dashboard/templates/base.html` (FR-001)
      done 2026-08-20 — #refresh-region wraps the history panel contents; #live-controls chrome (data-state, hidden, #live-state, #live-pause) in base.html; suite green
- [x] T002 Implement the poll loop module in `src/cairn/dashboard/static/app.js`: re-arming timer, same-origin fetch, DOMParser fragment swap of the marked region, guard-skip when no region present (FR-001)
      done 2026-08-20 — node --check OK; re-arming setTimeout only (no setInterval), visibility gate, importNode swap, running/disconnected state words
- [x] T003 Preserve filter inputs and scroll position across swaps in the poll module; make the interval configurable from the page (FR-003)
      done 2026-08-20 — harvest/restore field state with count guard, scroll capture/restore, data-refresh-ms override; flagged the missing app.js include (fixed in T005)
- [x] T004 Add the server-side fragment re-fetch test and the idempotency assertion (same rows re-rendered, no duplicates) to `tests/test_dashboard_app.py` (FR-001, FR-006)
      done 2026-08-20 — 4 tests: region+chrome present, new-row visibility on re-fetch, byte-identical idempotent refetch, no region on /projects; 45 passed at land time

## Phase 2: Pause + connection state (FR-004, FR-005)
<!-- Checkpoint: pause visibly halts updates; a stopped server shows a
     disconnected state that self-heals. -->
- [x] T005 Add pause/resume control with visible state indication to the shared chrome and wire it into the loop's state machine (FR-004)
      done 2026-08-20 — pause clears the timer + wins over visibility; resume re-arms immediately; button label toggles; app.js loaded once on /history (closing T003's gap)
- [x] T006 Add the disconnected banner driven by fetch rejection with recovery on the next successful poll, styled in `src/cairn/dashboard/static/app.css` (FR-005)
      done 2026-08-20 — #live-banner written only while running (connection lost — retrying / cleared on success); data-state styling distinguishes disconnected/paused
- [x] T007 Add loop-module state tests: paused-issues-no-fetch, rejected-then-resolved transitions (FR-004, FR-005)
      done 2026-08-20 — DOM-contract + static control-flow pins + LIVE_TC005/TC006 manual procedures (no JS harness in repo — orchestrator-approved shape); constant prefix recorded as D-004; 49 passed at land time

## Phase 3: Chains + tokens live; soak (FR-002)
<!-- Checkpoint: chains and tokens refresh on the same interval; the soak
     reports zero duplicates and zero missed batches. -->
- [x] T008 Mark the body regions in `src/cairn/dashboard/templates/chains.html` and `src/cairn/dashboard/templates/tokens.html` so the existing loop refreshes them (FR-002)
      done 2026-08-20 — both templates mirror history.html (region wrapper + single app.js script block); stale CSS-only comment corrected
- [x] T009 Add fragment growth tests for chains and tokens (call count +1, totals shift) to `tests/test_dashboard_app.py` (FR-002)
      done 2026-08-20 — 4 tests: region presence on both views, same-session chain +1 (no dup identities), new-session chain lands top, tokens totals shift via CHARS_PER_TOKEN arithmetic; 53 passed at land time
- [x] T010 Build the soak harness (tick-driven auto soak + one-hour manual procedure) asserting rendered id-set equality with the stored slice (FR-006)
      done 2026-08-20 — tests/test_dashboard_live_soak.py: 30 tick cycles w/ 1-5 inserts each, id-set equality + no duplicates + page-size pin + missed-batch check; manual procedure documented; 2 passed in 0.58s

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
