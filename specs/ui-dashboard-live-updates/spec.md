# Spec: ui-dashboard-live-updates

**Status**: done
**Created**: 2026-08-20
**Branch**: `docs/dashboard-v2-specs`

## What
The dashboard's tool-traffic views (history, chains, tokens) update
themselves automatically while the page is open, so a running agent session
can be watched live — calls appearing in history, chains growing mid-session,
rankings shifting — without touching refresh.

## Why
Every dashboard view today is a snapshot behind a manual refresh. The single
most valuable moment for a cairn owner is *while an agent is working*: seeing
the tool sequence unfold in real time is how inefficient agent patterns get
spotted at all. Post-hoc browsing covers the static case; live view covers
the interesting one.

## Business value
- Turns the dashboard from a report into an observation console during
  active MCP sessions.
- Success criteria:
  - **SC-1**: with the dashboard open and a tool call landing in the store,
    the call is visible in the history view within 2 refresh cycles with no
    user interaction.
  - **SC-2**: a page left open for an hour of active traffic stays
    responsive and correctly ordered (no duplicates, no missed batches).

## User stories
### US1 — Watch a session live (P1)
As a cairn owner, I want the history view to update itself while an agent
works, so that I can follow the session without refreshing.

**Acceptance criteria**:
- AC1: Given the history view open and new calls recorded, When the next
  refresh cycle elapses, Then the new calls appear at the top without
  interaction.
- AC2: Given active filters, When an auto-refresh occurs, Then the filters
  stay applied and the view does not reset.

### US2 — Live chains and rankings (P1)
As an agent-behavior researcher, I want chains and token rankings to update
live too, so that a session in progress is visible end-to-end.

**Acceptance criteria**:
- AC1: Given the chains view open, When a session makes a new call, Then its
  chain grows on the next refresh cycle.
- AC2: Given the tokens view open, When calls land, Then rankings/totals
  update on the next refresh cycle.

### US3 — Control over updating (P2)
As a viewer, I want to pause auto-refresh and see the connection state, so
that a stable view is possible while reading and an unreachable server is
obvious.

**Acceptance criteria**:
- AC1: Given auto-refresh on, When I pause it, Then updates stop and the
  paused state is visibly indicated until I resume.
- AC2: Given the server stopped, When a refresh fails, Then a clear
  disconnected state is shown and updates resume when the server returns.

## Requirements
- **FR-001**: While the history view is open, the system SHALL refresh its
  contents automatically on a configurable interval (default ≤ 5 seconds).
- **FR-002**: The chains and tokens views SHALL auto-refresh on the same
  interval.
- **FR-003**: Auto-refresh SHALL preserve the user's active filters and
  input state across refreshes.
- **FR-004**: Each traffic view SHALL provide a visible pause/resume control
  for auto-refresh, with the current state indicated.
- **FR-005**: When the dashboard server is unreachable, traffic views SHALL
  show a distinct disconnected state and SHALL recover automatically when it
  returns.
- **FR-006**: Refreshed content SHALL never duplicate rows already shown
  (idempotent refresh).

## Scope
**In**: polling-based auto-refresh for history/chains/tokens; pause control;
connection state; filter preservation.
**Out (deferred)**: push-based streaming (SSE/WebSocket) as a requirement —
polling is acceptable first; live-updating graph, projects, and health
views; per-view intervals; notifications/toasts on new calls.

## Assumptions & risks
- Assumption: a 5s poll against a read-only store is negligible load.
  Render cost is unmeasured in-repo (no render-timing test or benchmark
  exists), but views are server-rendered Jinja over read-only SQL at
  current row counts in the hundreds — the poll budget gets measured at
  test time, not assumed from today's numbers.
- Assumption: new-record detection keys on the recording's monotonic row
  identity (`tool_metrics.id` is INTEGER PRIMARY KEY AUTOINCREMENT); no
  wall-clock comparison needed.
- Risk: rapid DOM replacement can flicker or lose scroll position on long
  lists — mitigation: FR-003 plus scroll preservation; sequencing: land
  after (or with) ui-dashboard-traffic-scale, whose pagination this spec's
  filter-preservation AC must also hold across.
