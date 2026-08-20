# Spec: ui-dashboard-graph-nav

**Status**: draft
**Created**: 2026-08-20
**Branch**: `docs/dashboard-v2-specs`

## What
The graph view gains three navigation affordances: search-to-focus (type a
symbol name, the graph focuses on it and its neighborhood), click-to-expand
(activate a node to pull in its callers/callees), and a layout toggle
(force-directed vs hierarchical). Navigation stops being form-fiddling.

## Why
The graph view today navigates only through its scope/focus/depth form:
finding one symbol means scrolling a rendered graph or knowing the form's
vocabulary in advance. For a project of cairn's own size (~2,700 indexed
symbols) that's friction at exactly the moment of curiosity. Search-focus
and incremental expansion match how people actually explore: start at a
name, pull threads.

## Business value
- Symbol-level questions ("what surrounds X?") get answered in seconds
  from the browser, replacing CLI round-trips.
- Success criteria:
  - **SC-1**: typing a known symbol name and confirming focuses the graph
    on it with its neighborhood in one interaction, under 1 second.
  - **SC-2**: a node expansion visibly adds that node's neighbors without
    re-submitting the form, and the visible node/edge counts stay
    accurate.

## User stories
### US1 — Find a symbol (P1)
As a developer, I want to type a symbol name and see its neighborhood, so
that I don't scroll or guess scope parameters.

**Acceptance criteria**:
- AC1: Given the graph view, When I search an exact symbol name and
  confirm, Then the graph re-focuses on that symbol with its callers and
  callees.
- AC2: Given a name with multiple matches, When I search it, Then I can
  disambiguate (candidates shown) rather than getting an arbitrary pick.

### US2 — Pull the thread (P1)
As a developer, I want to expand a visible node's neighbors, so that I can
grow the interesting region of the graph incrementally.

**Acceptance criteria**:
- AC1: Given a rendered node, When I activate expand, Then its callers and
  callees join the view connected to it.

### US3 — Change the mental model (P2)
As a developer, I want a layout toggle between force-directed and
hierarchical, so that I can view structure the way I think about it.

**Acceptance criteria**:
- AC1: Given the graph view, When I toggle layout, Then the same node/edge
  set re-renders in the chosen style.

## Requirements
- **FR-001**: The graph view SHALL provide a symbol search that focuses the
  graph on an exact or disambiguated match together with its neighborhood.
- **FR-002**: Ambiguous symbol names SHALL present their candidate matches
  for selection, not an arbitrary one.
- **FR-003**: Each rendered node SHALL offer an expand action that adds
  that symbol's callers and callees to the current view.
- **FR-004**: The view SHALL offer at least force-directed and hierarchical
  (top-down) layouts, switchable without losing the current focus.
- **FR-005**: Search and expansion SHALL keep the displayed node/edge
  counts accurate (no silent overdraw past scope truncation).
- **FR-006**: All interactions SHALL remain read-only (standing guard).

## Scope
**In**: search-with-disambiguation; node expansion; layout toggle; count
accuracy; read-only guard extension.
**Out (deferred)**: editing/hiding/pinning nodes; saving or sharing view
state; additional layout algorithms; graph diffing between commits.

## Assumptions & risks
- Assumption: search-to-focus is pure composition — the symbol scope
  already serves a symbol plus its 1-hop callers and callees (capped at
  30 each). Multi-hop expansion is NOT already there: the symbol scope
  accepts a depth parameter but never uses it (fixed 1-hop), and only the
  impact scope walks depth — FR-003's expansion reuses that walk or adds
  one; this spec names the new traversal instead of assuming it exists.
- Risk: repeated expansions can grow the view past comfortable browser
  limits — mitigation: FR-005 count accuracy plus a visible bound; the
  underlying scope truncation still applies.
- Risk: name ambiguity is the norm in large projects — today the symbol
  scope resolves it by silently taking the first match (LIMIT 1), which
  is exactly the arbitrary pick FR-002 forbids; disambiguation becomes a
  first-class interaction, not an error.
