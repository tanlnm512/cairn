# Test Cases: ui-dashboard-polish

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-20
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details.

## TC-001 — First health render is warm
- **Story**: US1 · **Traces to**: FR-001, SC-1, AC1
- **Given** a freshly started dashboard server
- **When** health is loaded first
- **Then** it renders without a multi-second delay (under 200ms server-side)
- **Pass condition**: auto — timing test against a fresh app instance
  asserts the first /health server-side duration under the budget.

## TC-002 — Exact tokenizer used and labeled when available
- **Story**: US2 · **Traces to**: FR-002, AC1
- **Given** an exact tokenizer locally available (mocked import present)
- **When** the tokens view renders
- **Then** counts use it and the active mode is displayed
- **Pass condition**: auto — mode-selection unit test + view assertion
  that the mode label renders with the exact mode's name.

## TC-003 — Heuristic fallback labeled
- **Story**: US2 · **Traces to**: FR-002
- **Given** no exact tokenizer available (import absent)
- **When** the tokens view renders
- **Then** counts use the documented heuristic and the label says so
- **Pass condition**: auto — the same tests with the import absent assert
  the heuristic path and its label.

## TC-004 — Truncation magnitude recorded durably
- **Story**: US2 · **Traces to**: FR-003
- **Given** a tool call whose result exceeds the cap
- **When** the call is recorded
- **Then** the per-call record carries the truncation magnitude and it
  survives events-table pruning
- **Pass condition**: auto — truncating invocation asserts the magnitude
  columns on the tool_metrics row; prune test shows magnitude remains
  after events roll over.

## TC-005 — Per-tool truncation counts surface
- **Story**: US2 · **Traces to**: FR-003, AC2
- **Given** recorded calls including truncated ones
- **When** the tokens view renders
- **Then** per-tool truncation counts appear alongside usage; rows without
  evidence render unknown cleanly
- **Pass condition**: auto — seeded mixed rows assert counts and the
  unknown-clean rendering.

## TC-006 — Retention ages oldest rows and is visible
- **Story**: US3 · **Traces to**: FR-004, SC-2, AC1
- **Given** retention configured (default) and a store over the limit
- **When** aging runs (flush cycle)
- **Then** the oldest records are removed and the health panel shows the
  policy in force and the current size
- **Pass condition**: auto — over-cap store + forced flush asserts the
  row count at cap and the health payload's policy fields.

## TC-007 — Export matches the filtered view
- **Story**: US4 · **Traces to**: FR-005, AC1
- **Given** a filtered history or tokens view
- **When** it is exported as CSV and as JSON
- **Then** the outputs contain exactly the rows shown, filters included,
  in well-formed format
- **Pass condition**: auto — export parity test byte-compares exported
  rows against the view's data function output under the same params
  (including quoted-field correctness per RFC 4180).

## TC-008 — Dark theme applies everywhere and persists
- **Story**: US5 · **Traces to**: FR-006, AC1
- **Given** the theme control
- **When** dark is selected
- **Then** every view renders dark and the choice persists across visits
- **Pass condition**: manual — select dark, visit every view, reload,
  reopen; auto — the apply/persist script's unit test.

## TC-009 — Dashboard process never ages data
- **Story**: US3 · **Traces to**: FR-007
- **Given** a store over the retention limit and the dashboard running
- **When** views (including health) are exercised
- **Then** no rows are removed by the dashboard and its store access stays
  read-only
- **Pass condition**: auto — readonly guard extension: row counts and
  file hashes unchanged across dashboard interactions.

## Coverage matrix
| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001 | TC-001 | auto |
| FR-002 | TC-002, TC-003 | auto |
| FR-003 | TC-004, TC-005 | auto |
| FR-004 | TC-006 | auto |
| FR-005 | TC-007 | auto |
| FR-006 | TC-008 | auto + manual |
| FR-007 | TC-009 | auto |
