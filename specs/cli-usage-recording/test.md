# Test Cases: cli-usage-recording

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-20
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details.

## TC-001 — Representative CLI batch lands completely
- **Story**: US1 · **Traces to**: FR-001, SC-1
- **Given** recording enabled and an empty usage store
- **When** a representative batch runs (a build, a query, a memory record,
  one failing command)
- **Then** every invocation appears in the history with command, timestamp,
  duration, and status (ok/error matching the exit)
- **Pass condition**: auto — CliRunner batch against a tmp store with a
  forced drain; assert one row per invocation with correct fields.

## TC-002 — Short-lived process still flushes
- **Story**: US1 · **Traces to**: FR-003, SC-2
- **Given** a CLI process that exits immediately after one command
- **When** it exits cleanly
- **Then** its row is in the store without waiting for any periodic flush
- **Pass condition**: auto — subprocess invocation of the real entry point;
  assert the row exists after process exit (exit-time drain proof).

## TC-003 — Source is visible and filterable
- **Story**: US1 · **Traces to**: FR-002, AC2
- **Given** both MCP and CLI usage recorded
- **When** history is viewed and filtered by source
- **Then** each row's source is displayed and the filter narrows to that
  source's rows
- **Pass condition**: auto — seeded mixed rows; assert display + filter
  param behavior on the history route.

## TC-004 — CLI usage appears in token aggregates
- **Story**: US2 · **Traces to**: FR-002, AC1
- **Given** recorded CLI rows with payload sizes
- **When** the tokens view is opened
- **Then** CLI commands appear in the aggregates labeled by source
- **Pass condition**: auto — seeded cli rows with sizes; assert their
  inclusion and labeling in the tokens aggregates.

## TC-005 — Argument summaries are redacted and truncated
- **Story**: US1, US3 · **Traces to**: FR-001
- **Given** a CLI invocation whose arguments embed a path-like secret
  pattern and a long argument
- **When** its row is recorded
- **Then** the stored summary is scrubbed and capped at the same limit as
  MCP tool-call summaries
- **Pass condition**: auto — invoke with canary args; assert the stored
  summary contains neither the canary nor more than the cap.

## TC-006 — Opt-out records nothing, changes nothing
- **Story**: US3 · **Traces to**: FR-004, AC1
- **Given** recording disabled via the documented switch
- **When** CLI commands run
- **Then** nothing is recorded and command behavior/output is otherwise
  identical
- **Pass condition**: auto — paired runs (on/off) with identical output
  diff and an empty-store assertion for the off case.

## TC-007 — Session identity groups shell sessions where derivable
- **Story**: US1 · **Traces to**: FR-006
- **Given** multiple invocations sharing a terminal-session identifier
  (and others without one)
- **When** their rows are recorded
- **Then** the shared ones group under one session id and the others fall
  back to per-invocation identities — none land in the default 'unknown'
  session
- **Pass condition**: auto — identity derivation unit tests over env
  fixtures (set/unset), plus a row assertion that session_id is never
  'unknown' for cli rows.

## TC-008 — MCP recording unchanged
- **Story**: US1 · **Traces to**: FR-005
- **Given** the landed CLI recording
- **When** the existing MCP metric suites run
- **Then** they pass unchanged (row shape, buffering semantics, flush
  behavior)
- **Pass condition**: auto — `uv run pytest tests/test_metrics.py
  tests/test_metrics_extensions.py tests/test_telemetry.py -q` green with
  zero modifications to those suites.

## Coverage matrix
| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001 | TC-001, TC-005 | auto |
| FR-002 | TC-003, TC-004 | auto |
| FR-003 | TC-002 | auto |
| FR-004 | TC-006 | auto |
| FR-005 | TC-008 | auto |
| FR-006 | TC-007 | auto |
