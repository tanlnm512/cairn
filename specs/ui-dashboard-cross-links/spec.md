# Spec: ui-dashboard-cross-links

**Status**: draft
**Created**: 2026-08-20
**Branch**: `docs/dashboard-v2-specs`

## What
Every entity on the dashboard links to its natural drill-down: a tool in
the token ranking opens its filtered history; a call's session opens its
chain; a project row opens its graph; a graph node opens a focused
subgraph. The views stop being dead ends and become one navigable surface.

## Why
The views answer questions in isolation, but real questions cross views:
"this tool is expensive — show me the calls", "this call — show me the rest
of its session", "this project — show me its structure". Today each of
those means re-typing filters in another view. Cross-links make the
dashboard a tool rather than a stack of reports.

## Business value
- Time-to-answer for cross-view questions drops from "re-type the filter"
  to one click.
- Success criteria:
  - **SC-1**: every entity shown on a view that can anchor another view is
    reachable there in ≤ 1 click.

## User stories
### US1 — From cost to evidence (P1)
As a cairn maintainer viewing token rankings, I want to click a tool and
land on its history, so that I can see *which calls* made it expensive.

**Acceptance criteria**:
- AC1: Given the tokens view, When I activate a tool's row link, Then the
  history view opens filtered to that tool.

### US2 — From call to session (P1)
As an agent-behavior researcher viewing history, I want to jump from a call
to its whole chain, so that I see the surrounding sequence.

**Acceptance criteria**:
- AC1: Given a history row, When I activate its session link, Then the
  chains view opens focused on that session's chain(s).

### US3 — From project to structure (P1)
As a developer on the projects view, I want a project row to open its
graph, so that structure inspection is one click from the overview.

**Acceptance criteria**:
- AC1: Given a project row, When I activate its graph link, Then the graph
  view opens scoped to that project.

### US4 — From node to neighborhood (P2)
As a developer exploring a graph, I want to open a node's focused
subgraph (the symbol and its callers/callees), so that I can inspect one
symbol's surroundings without form-fiddling.

**Acceptance criteria**:
- AC1: Given a rendered graph node, When I activate its inspect action,
  Then a symbol-neighborhood subgraph for it opens.

## Requirements
- **FR-001**: Each row in the tokens view SHALL link to the history view
  pre-filtered to that tool.
- **FR-002**: Each row in the history view SHALL link to the chains view
  focused on that row's session.
- **FR-003**: Each row in the projects view SHALL link to the graph view
  scoped to that project.
- **FR-004**: Each rendered graph node SHALL expose an inspect action that
  opens a symbol-neighborhood subgraph for it.
- **FR-005**: Cross-links SHALL carry the active time-window filter (where
  one exists) so the destination view shows the same slice.
- **FR-006**: The landing view SHALL provide navigation to every other
  view (no orphan views).

## Scope
**In**: link wiring across existing views; context preservation; the
symbol-neighborhood preset via existing graph scopes.
**Out (deferred)**: a dedicated symbol-detail page (caller lists, metrics,
source excerpts) beyond the focused subgraph; breadcrumb trails beyond the
browser's native back.

## Assumptions & risks
- Assumption: all destinations are expressible as today's URL parameters
  (tool/session filters, graph scope/focus/repo) — no new query surfaces.
- Risk: link proliferation cluttering dense tables — mitigation: link each
  row's primary entity (tool name, session id, project name), not every
  cell.
