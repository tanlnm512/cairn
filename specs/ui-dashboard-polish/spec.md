# Spec: ui-dashboard-polish

**Status**: done
**Created**: 2026-08-20
**Branch**: `docs/dashboard-v2-specs`

## What
A bundle of small operational refinements to the dashboard: a warm health
view (no multi-second cold hit), honest token accounting (exact tokenizer
when available, with the heuristic labeled as such), truncation visibility
(how often tool results are being cut down, per tool), usage retention
(recorded history ages out per a configurable policy instead of growing
forever), data export (CSV/JSON of the filtered views), and a dark theme.

## Why
These are individually small but each removes a daily irritant: the first
health hit pays a multi-second probe warm-up (the health handler's
reranker probe imports sentence-transformers inside the first request);
token estimates are a flat chars÷4 approximation with no indication of
confidence; truncation is recorded only as a coarse bucketed event that
no view surfaces — the exact magnitude thrown away, and the events
table's row cap prunes even the occurrences; the usage store grows
without bound (events and build_runs are pruned to fixed caps by the
recording pipeline, tool_metrics never); analysis beyond the screen
requires re-deriving the SQL; and the audience for this tool lives in
dark terminals.

## Business value
- The dashboard's numbers become trustworthy (token mode labeled,
  truncation cost visible) and its data becomes portable (export).
- The store stays bounded (retention), so the dashboard's own telemetry
  can't become the problem it monitors.
- Success criteria:
  - **SC-1**: the health view's first load after server start responds in
    under 200ms server-side.
  - **SC-2**: with retention at its default and the store over the limit,
    oldest records age out and the health panel shows the policy in force.

## User stories
### US1 — Health without the wait (P1)
As an operator, I want the health view fast on first load, so that a quick
check doesn't pay a warm-up penalty.

**Acceptance criteria**:
- AC1: Given a freshly started dashboard server, When I load health first,
  Then it renders without a multi-second delay.

### US2 — Trust the numbers (P1)
As a cairn maintainer, I want token estimates labeled by method and
truncation stats per tool, so that I know what the numbers mean and what
they're hiding.

**Acceptance criteria**:
- AC1: Given the tokens view, When a tokenizer mode is available and
  enabled, Then counts use it and the active mode is displayed.
- AC2: Given the tokens view, When results have been truncated, Then per-
  tool truncation counts are shown alongside usage.

### US3 — Bounded history (P1)
As the owner, I want recorded usage to age out per a policy, so that the
store doesn't grow forever.

**Acceptance criteria**:
- AC1: Given retention configured and the store over the limit, When aging
  runs, Then the oldest records are removed and the health panel shows the
  policy and current size.

### US4 — Take the data with me (P2)
As an analyst, I want CSV/JSON export of the filtered history/tokens
views, so that deeper analysis doesn't mean writing SQL.

**Acceptance criteria**:
- AC1: Given any filtered history or tokens view, When I export, Then the
  output matches exactly what's shown (filters included) in the chosen
  format.

### US5 — Dark theme (P2)
As a terminal-dwelling user, I want a dark theme that persists, so that
the dashboard doesn't flash white at 2am.

**Acceptance criteria**:
- AC1: Given the theme control, When I pick dark, Then every view renders
  dark and the choice persists across visits.

## Requirements
- **FR-001**: The dashboard SHALL serve the health view without a
  cold-start warm-up penalty (first health render after server start under
  200ms server-side).
- **FR-002**: Token estimation SHALL support an exact tokenizer mode when
  one is locally available, fall back to the documented heuristic
  otherwise, and display the active mode wherever estimates are shown.
- **FR-003**: Truncation observability SHALL extend the existing
  `truncate_result` recording (already emitted at the truncation
  chokepoint, with a coarse chars bucket) rather than duplicate it:
  record the truncation magnitude per call, durably (not subject to the
  events table's row cap), and the tokens view SHALL surface per-tool
  truncation counts.
- **FR-004**: Recorded usage SHALL age out under a configurable retention
  policy (time- and/or row-bounded), applied without manual intervention,
  and the health panel SHALL show the policy and current store size.
  This extends the recording pipeline's existing prune — which already
  caps events and build_runs at fixed row counts inside the flush
  transaction — to tool_metrics with a configurable bound.
- **FR-005**: The history and tokens views SHALL export the current
  filtered contents as CSV and JSON.
- **FR-006**: The dashboard SHALL offer light and dark themes with the
  choice persisted per browser.
- **FR-007**: Retention and export SHALL NOT violate the read-only
  discipline of the dashboard process (aging is performed by the recording
  side, never by the dashboard).

## Scope
**In**: health prewarming; tokenizer mode + labeling; truncation recording
and display; retention policy + health surfacing; CSV/JSON export; light/
dark themes.
**Out (deferred)**: per-encoding tokenizer selection per model; export of
chains/graph data; theme customization beyond light/dark; scheduled
reports.

## Assumptions & risks
- Assumption: an exact tokenizer (if used) must remain an optional
  dependency — the heuristic stays the zero-dependency default, so the
  installed footprint doesn't grow for everyone.
- Assumption: the chars÷4 constant is shared with the bench suite so
  dashboard and bench numbers stay comparable; once an exact-tokenizer
  mode exists, the active-mode labeling (FR-002) is what keeps
  bench-derived and exact numbers interpretable side by side.
- Assumption: retention runs inside the recording pipeline (which already
  owns writes and already prunes two tables), not the dashboard — hence
  FR-007's split of duties.
- Risk: exact per-call truncation magnitude only exists for recordings
  made after this change (past truncations live only as bucketed events
  competing for the events retention cap) — views must render unknown
  cleanly.
- Risk: aggressive default retention could surprise users who want full
  history — default should be generous, with the policy visible (FR-004).
