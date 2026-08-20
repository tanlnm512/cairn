# Test Cases: ui-dashboard-traffic-scale

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-20
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details.

## TC-001 — History opens bounded on a large store
- **Story**: US1 · **Traces to**: FR-001, AC1
- **Given** a store with thousands of recorded calls
- **When** history is opened
- **Then** a bounded first page renders quickly with pagination controls
  and an indication of how much more there is
- **Pass condition**: auto — seeded multi-thousand-row store: response
  contains at most the page-size rows and the pagination controls; manual —
  dev store opens responsively.

## TC-002 — Pages are stable and repeat-free
- **Story**: US1 · **Traces to**: FR-001, FR-006, AC2
- **Given** paginated history on a store receiving new rows between page
  fetches
- **When** paging forward and back
- **Then** ordering stays consistent and no row appears on two pages
- **Pass condition**: auto — seed rows, insert a newer row mid-paging,
  walk pages, assert the union of page rows has no duplicates and the
  newer row appears only ahead of the cursor.

## TC-003 — Window narrows all three views
- **Story**: US2 · **Traces to**: FR-002, AC1
- **Given** recorded calls spanning several days
- **When** the last-24h window is selected
- **Then** history, chains, and tokens show only that window's data
- **Pass condition**: auto — seed rows inside/outside the window; assert
  each view excludes the outside rows and includes the inside ones.

## TC-004 — Aggregates recompute within the window
- **Story**: US2 · **Traces to**: FR-003, AC2
- **Given** a tool with heavy old traffic and light recent traffic
- **When** the window changes from all to last-24h
- **Then** the tool's totals and ranking reflect only the window's calls
- **Pass condition**: auto — seeded split traffic; assert per-tool totals
  equal the window's sums and ranking order changes accordingly.

## TC-005 — Giant session renders bounded with expand
- **Story**: US3 · **Traces to**: FR-004, AC1
- **Given** a session with hundreds of calls (the all-'unknown' legacy
  shape as the first fixture)
- **When** chains is viewed
- **Then** a bounded portion renders with an explicit way to see more and
  honest shown-of-total counts
- **Pass condition**: auto — legacy-shaped fixture; assert rendered call
  count is at the bound, the expand interaction returns the tail, and the
  shown/total counts match the store.

## TC-006 — 10k-call render budget
- **Story**: US1, US2, US3 · **Traces to**: FR-005, SC-1
- **Given** a synthesized store with 10,000+ recorded calls
- **When** each traffic view is rendered for the first time
- **Then** the first render completes within 2 seconds
- **Pass condition**: auto — timing-marked test over the synthesized store
  asserting per-route wall time (strict gate local/slow-marked; CI asserts
  the structural bounds per tech-spec pitfall note).

## TC-007 — Window and pagination compose
- **Story**: US1, US2 · **Traces to**: FR-006
- **Given** a windowed, paginated history
- **When** paging within the window
- **Then** every page stays inside the window and the row set stays
  consistent with the unpaginated windowed query
- **Pass condition**: auto — walk all pages under a window; assert the
  union equals the window's full row set exactly once each.

## Coverage matrix
| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001 | TC-001, TC-002 | auto + manual |
| FR-002 | TC-003 | auto |
| FR-003 | TC-004 | auto |
| FR-004 | TC-005 | auto |
| FR-005 | TC-006 | auto |
| FR-006 | TC-002, TC-007 | auto |
