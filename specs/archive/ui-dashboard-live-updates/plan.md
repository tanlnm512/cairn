# Plan: ui-dashboard-live-updates

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-20
Source of truth for code state: [survey.md](survey.md). Statuses below cite
survey items (Q#) or greps run in this planning session (file:line).

## Milestones
| Phase | Milestone | Delivers (demoable) | FRs | Depends on |
|-------|-----------|---------------------|-----|------------|
| 1 | Polling spine on history | With history open, calls recorded server-side appear at the top of the table on the next cycle with no interaction; filters and scroll survive each refresh | FR-001, FR-003, FR-006 | — |
| 2 | Pause + connection state | Every traffic view has a visible pause/resume control with state indication, and a stopped server shows a distinct disconnected state that self-heals on return | FR-004, FR-005 | Phase 1 |
| 3 | Chains + tokens live; soak | Chains and tokens refresh on the same interval; an hour of open-page traffic shows no duplicates, no missed batches, no responsiveness loss (SC-2) | FR-002 | Phase 1 |

Sequencing note (from spec): land after (or with) ui-dashboard-traffic-scale —
its pagination is what the filter-preservation AC must hold across.

## Dependencies

- **Phase 1 → Phases 2, 3** — the poll loop is the substrate both build on:
  pause is a gate inside the loop (Phase 2) and chains/tokens attach a second
  body region to the same cycle (Phase 3).
- **Phase 2 ∥ Phase 3** — disjoint surfaces: pause/connection chrome is shared
  layout + loop state, chains/tokens wiring is per-view selectors; both edit
  `src/cairn/dashboard/static/app.js`, so the task-breaker serializes only the
  JS spine task (see parallelization map).

## Parallelization map

**Area A — client spine** (Phase 1: FR-001, FR-003, FR-006)
Files: `src/cairn/dashboard/static/app.js` (poll loop, fragment swap,
scroll/filter preservation), `src/cairn/dashboard/templates/history.html`
(body region marker), `src/cairn/dashboard/templates/base.html` (shared
controls chrome). No server changes required: the fragment is the existing
route's HTML re-fetched with current query params (survey Q2, Q4).

**Area B — pause + connection** (Phase 2: FR-004, FR-005)
Files: `src/cairn/dashboard/static/app.js`, `src/cairn/dashboard/static/app.css`
(state indicators). Purely additive to Area A's loop.

**Area C — chains/tokens** (Phase 3: FR-002)
Files: `src/cairn/dashboard/templates/chains.html`, `tokens.html` (region
markers), tail of `src/cairn/dashboard/static/app.js` (per-view wiring).

- Independent: **B ∥ C** after A — different templates, additive JS sections.
- Strictly ordered: **A → B, A → C** — the loop owns the hooks both consume.
- Server layer is untouched throughout; `src/cairn/dashboard/app.py` changes
  only if a fragment marker cannot be expressed in the existing templates.

## Checkpoints

- **After Phase 1**: with two terminal sessions — one running `cairn serve`,
  one running instrumented queries — an open history page shows new rows
  appear without interaction within two cycles of the recorder's 30s flush
  (survey Q6 governs visibility, not the poll). Verify: the phase's TestClient
  test that re-fetches the route after inserting a row and asserts the row
  renders, plus manual browser check with the dev store.
- **After Phase 2**: pausing stops updates and shows the paused state; killing
  the server shows the disconnected state; restarting it clears it. Verify:
  manual browser check (connection-refused cannot be simulated in TestClient)
  plus the paused-state unit test on the loop module.
- **After Phase 3** (covers SC-1 end-to-end, SC-2 soak): chains grow and token
  totals shift on later cycles; the soak test (auto: simulated clock-driven
  ticks; manual: one hour of real traffic) reports zero duplicate row ids and
  zero dropped batches. Verify: `uv run pytest tests/test_dashboard_app.py -q`
  plus the soak script's summary output.

## Risks & mitigations
- Risk: full-region swap loses scroll position on long lists → mitigation:
  FR-003 task explicitly preserves scroll via pre/post capture around the
  swap; composes with traffic-scale pagination which bounds list length.
- Risk: interval stacking when the tab is hidden (research RQ1) → mitigation:
  re-arming timeout, visibility-gated skip while hidden.
- Risk: SC-1's "2 refresh cycles" is flush-bound (survey Q6) — a call can sit
  buffered up to 30s → mitigation: acceptance measured from store-landing
  (row exists in DB → visible within 2 cycles), documented in TC-001.
- Risk: test flakiness from timers → mitigation: no real sleeps in auto tests;
  drive the loop's tick function directly; real-interval behavior is the
  manual soak.

## Delivery
Branch `feat/ui-dashboard-live-updates` (or rides the dashboard-v2 train);
one PR, one commit per task, code + docs together. Post-merge: `cairn update`
+ `record_memory` per AGENTS.md.
