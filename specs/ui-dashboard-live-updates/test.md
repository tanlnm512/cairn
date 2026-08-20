# Test Cases: ui-dashboard-live-updates

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-20
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details.

## TC-001 — New call appears without interaction
- **Story**: US1 · **Traces to**: FR-001, AC1
- **Given** the history view open in a browser and a tool call that has landed
  in the store (flushed)
- **When** the next refresh cycle elapses with no user interaction
- **Then** the new call is visible at the top of the history table
- **Pass condition**: auto — TestClient seeds a store, fetches /history, inserts
  a newer row, re-fetches, asserts the new row id is served in the table;
  manual — live `cairn serve` traffic visible on an open page within two
  cycles of the recorder's 30s flush.

## TC-002 — Filters survive auto-refresh
- **Story**: US1 · **Traces to**: FR-003, AC2
- **Given** the history view with an active tool filter and partially scrolled
- **When** an auto-refresh occurs
- **Then** the filter is still applied to the refreshed rows and the scroll
  position is not reset to the top
- **Pass condition**: manual — apply filter, scroll, wait one cycle, observe
  filtered rows and preserved position; auto — fragment test asserts the
  filter input's value round-trips into the swapped region.

## TC-003 — Chains view grows mid-session
- **Story**: US2 · **Traces to**: FR-002, AC1
- **Given** the chains view open while its session records a new call
- **When** the next refresh cycle elapses
- **Then** the chain's call list includes the new call
- **Pass condition**: auto — chains fragment re-fetch after row insert asserts
  the call count grew by exactly one.

## TC-004 — Token rankings update live
- **Story**: US2 · **Traces to**: FR-002, AC2
- **Given** the tokens view open while calls land
- **When** the next refresh cycle elapses
- **Then** the displayed totals change to include the new calls
- **Pass condition**: auto — tokens fragment re-fetch after insert asserts the
  changed aggregate.

## TC-005 — Pause stops updates and is indicated
- **Story**: US3 · **Traces to**: FR-004, AC1
- **Given** auto-refresh running
- **When** the viewer pauses it
- **Then** no further refreshes occur and the paused state stays visibly
  indicated until resumed
- **Pass condition**: auto — loop-module state test (paused → tick → no fetch
  issued); manual — banner/pill visible on the page.

## TC-006 — Disconnected state on unreachable server, self-healing
- **Story**: US3 · **Traces to**: FR-005, AC2
- **Given** a traffic view open against a running dashboard server
- **When** the server process is stopped, then later restarted
- **Then** a distinct disconnected state appears on the next failed cycle and
  clears with content resuming on the first successful one
- **Pass condition**: manual — stop/start the server and observe the banner;
  auto — loop-module test feeds a rejected fetch then a resolved one.

## TC-007 — No duplicate rows across sustained refresh
- **Story**: US1, US2 · **Traces to**: FR-006, SC-2
- **Given** a page left open across many refresh cycles with traffic landing
  throughout
- **When** the soak period completes
- **Then** no row id appears twice in any rendered region and no landed batch
  is missing from the final render
- **Pass condition**: auto — soak harness drives N ticks against a growing
  store and asserts id-set equality between rendered ids and stored ids in
  the queried slice; manual — one hour of real traffic, same check.

## Coverage matrix
| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001 | TC-001 | auto + manual |
| FR-002 | TC-003, TC-004 | auto |
| FR-003 | TC-002 | auto + manual |
| FR-004 | TC-005 | auto + manual |
| FR-005 | TC-006 | auto + manual |
| FR-006 | TC-007 | auto + manual |
