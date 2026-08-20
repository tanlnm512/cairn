# Test Cases: ui-dashboard-graph-nav

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-20
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details.

## TC-001 — Exact name focuses its neighborhood
- **Story**: US1 · **Traces to**: FR-001, AC1, SC-1
- **Given** the graph view on a store with a uniquely-named symbol
- **When** the name is searched and confirmed
- **Then** the graph focuses on that symbol with its callers and callees,
  in one interaction
- **Pass condition**: auto — candidates endpoint returns one exact match
  and the focus URL serves the symbol scope; manual — search on the dev
  store completes in well under a second.

## TC-002 — Ambiguous name offers candidates, not an arbitrary pick
- **Story**: US1 · **Traces to**: FR-002, AC2
- **Given** a name defined in multiple files (seeded)
- **When** the name is searched
- **Then** the matching candidates are listed with distinguishing context
  and the graph focuses on the selected one only
- **Pass condition**: auto — seed same-name symbols in two files; assert
  the candidates response lists both with file/kind; selecting one serves
  that symbol's neighborhood.

## TC-003 — Expansion adds a node's neighbors
- **Story**: US2 · **Traces to**: FR-003, AC1, SC-2
- **Given** a rendered graph node with callers/callees not yet in view
- **When** the node's expand action is activated
- **Then** its callers and callees join the view connected to it, without
  re-submitting the form
- **Pass condition**: auto — neighbors endpoint returns the expected
  node/edge set for a seeded symbol; manual — expand a node in the browser
  and observe the merge.

## TC-004 — Counts stay accurate through search and expansion
- **Story**: US1, US2 · **Traces to**: FR-005
- **Given** the graph displaying node/edge counts
- **When** search-to-focus or repeated expansions change the visible set
- **Then** the displayed counts match the visible node/edge sets exactly,
  with the truncation notice remaining truthful at caps
- **Pass condition**: auto — merge-shape test asserts count fields equal
  the merged DataSets' sizes; cap-hit fixture asserts the shown-of-total
  rendering.

## TC-005 — Layout toggle re-renders in the chosen style, focus kept
- **Story**: US3 · **Traces to**: FR-004, AC1
- **Given** a rendered graph in force-directed layout
- **When** the layout is toggled to hierarchical and back
- **Then** the same node/edge set renders in each style and the current
  focus (camera/selection) is preserved
- **Pass condition**: manual — toggle both ways on the dev store and
  observe; auto — the URL param persistence and option-application unit
  assertions.

## TC-006 — New endpoints stay read-only
- **Story**: US1, US2, US3 · **Traces to**: FR-006
- **Given** the search and expansion endpoints
- **When** they are exercised across the readonly suite
- **Then** no store content changes (byte-identical guard)
- **Pass condition**: auto — `tests/test_dashboard_readonly.py` extension
  covering both endpoints stays green.

## Coverage matrix
| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001 | TC-001 | auto + manual |
| FR-002 | TC-002 | auto |
| FR-003 | TC-003 | auto + manual |
| FR-004 | TC-005 | auto + manual |
| FR-005 | TC-004 | auto |
| FR-006 | TC-006 | auto |
