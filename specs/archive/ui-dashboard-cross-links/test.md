# Test Cases: ui-dashboard-cross-links

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-20
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details.

## TC-001 — Token row opens filtered history
- **Story**: US1 · **Traces to**: FR-001, AC1
- **Given** the tokens view listing per-tool aggregates
- **When** a tool's row link is activated
- **Then** the history view opens showing only that tool's calls
- **Pass condition**: auto — assert the rendered tokens row's anchor points
  at the history route with the tool param, and that fetching that URL
  returns only matching rows.

## TC-002 — History row opens its session's chains
- **Story**: US2 · **Traces to**: FR-002, AC1
- **Given** a history row belonging to a session
- **When** the row's session link is activated
- **Then** the chains view opens showing that session's chain(s) only
- **Pass condition**: auto — seed two sessions, fetch the chains route with
  the session param, assert only that session's calls appear.

## TC-003 — Projects→graph link still works
- **Story**: US3 · **Traces to**: FR-003, AC1
- **Given** the projects view with an indexed project
- **When** the row's graph link is activated
- **Then** the graph view opens scoped to that project
- **Pass condition**: auto — regression test asserting the anchor exists and
  the target route renders that repo's graph (guards the already-shipped
  link, per spec).

## TC-004 — Graph node inspect opens its neighborhood
- **Story**: US4 · **Traces to**: FR-004, AC1
- **Given** a rendered graph node
- **When** its inspect action is activated
- **Then** a symbol-neighborhood subgraph for it opens (the symbol with its
  callers and callees)
- **Pass condition**: manual — activate inspect on a node in the browser and
  observe the focused graph; auto — the navigation target URL construction
  is asserted at the template/JS boundary via the unit-testable helper.

## TC-005 — Links carry the active time window
- **Story**: US1, US2 · **Traces to**: FR-005
- **Given** a traffic view with a time-window filter active (once
  ui-dashboard-traffic-scale provides windows)
- **When** a cross-link is followed
- **Then** the destination shows the same window's slice
- **Pass condition**: auto — link-builder unit test asserts the window param
  is appended when present and absent when not (the "where one exists" rule).

## TC-006 — No orphan views
- **Story**: US3 · **Traces to**: FR-006
- **Given** any dashboard page, including the landing page
- **When** the shared navigation is inspected
- **Then** every view, including the graph view, is reachable in one click
- **Pass condition**: auto — assert the nav template renders a link to the
  graph route; manual — landing page lists all views.

## Coverage matrix
| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001 | TC-001 | auto |
| FR-002 | TC-002 | auto |
| FR-003 | TC-003 | auto |
| FR-004 | TC-004 | auto + manual |
| FR-005 | TC-005 | auto |
| FR-006 | TC-006 | auto + manual |
