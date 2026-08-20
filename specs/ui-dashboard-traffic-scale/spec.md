# Spec: ui-dashboard-traffic-scale

**Status**: draft
**Created**: 2026-08-20
**Branch**: `docs/dashboard-v2-specs`

## What
The dashboard handles months of recorded tool traffic gracefully: history
paginates instead of rendering everything, the traffic views filter by time
window (last 24h / 7d / 30d / all), token aggregates and chains respect the
selected window, and views stay fast at ten-thousand-plus recorded calls.

## Why
The recording introduced with the dashboard has no volume ceiling in the
views: history currently renders every row (a workspace store already holds
2,000+ calls and grows with every session), aggregates are all-time only —
so one bad afternoon last week still dominates today's ranking — and a
single giant session can swallow the chains view. Without scale handling
the dashboard degrades exactly as it becomes valuable.

## Business value
- The dashboard stays useful from day one of recording to years in.
- Success criteria:
  - **SC-1**: on a store with ≥ 10,000 recorded calls, every traffic view's
    first render completes in under 2 seconds.
  - **SC-2**: a time-window change visibly and correctly narrows history,
    chains, and token aggregates.

## User stories
### US1 — Browse a long history (P1)
As a cairn maintainer, I want history paginated, so that opening it with
thousands of calls is fast and navigable.

**Acceptance criteria**:
- AC1: Given a store with thousands of calls, When I open history, Then a
  bounded first page renders quickly with pagination controls.
- AC2: Given pagination, When I move between pages, Then the ordering stays
  consistent and no rows repeat across pages.

### US2 — Slice by time (P1)
As a cairn maintainer, I want a time-window filter on the traffic views, so
that I can reason about recent behavior rather than all-time totals.

**Acceptance criteria**:
- AC1: Given the window control, When I select last-24h, Then history,
  chains, and token views show only that window's data.
- AC2: Given the tokens view, When the window changes, Then aggregates and
  ranking are computed within the window.

### US3 — Survive giant sessions (P2)
As a viewer, I want chains bounded per view, so that one enormous session
cannot dominate rendering.

**Acceptance criteria**:
- AC1: Given a session with hundreds of calls, When I view chains, Then a
  bounded portion renders with an explicit way to see more.

## Requirements
- **FR-001**: The history view SHALL paginate results with a bounded default
  page size and working navigation (no full-history renders).
- **FR-002**: The history, tokens, and chains views SHALL offer a time-window
  filter (at least last-24h / 7d / 30d / all).
- **FR-003**: Token aggregates SHALL be computed within the selected time
  window.
- **FR-004**: The chains view SHALL bound the number of chains and calls
  rendered at once, with an explicit expand mechanism.
- **FR-005**: On a store with ≥ 10,000 recorded calls, each traffic view's
  first render SHALL complete within 2 seconds.
- **FR-006**: Pagination and window filters SHALL compose (a windowed,
  paginated history stays consistent).

## Scope
**In**: pagination; time-window filters across traffic views; bounded chain
rendering; render-budget proof at 10k+ calls.
**Out (deferred)**: arbitrary date-range pickers (preset windows first);
retention/rotation of old records (ui-dashboard-polish owns it); server-side
sorting controls beyond the default newest-first.

## Assumptions & risks
- Assumption: preset windows cover the analysis need; custom ranges are a
  later refinement.
- Risk: window filters on unindexed timestamps could scan the whole table —
  mitigation: render-budget requirement (FR-005) forces the index or plan
  that keeps it.
- Risk: chains of legacy sessions recorded before per-session ids exist
  (single "unknown" session) can be huge — FR-004's bound must hold for
  that shape specifically.
