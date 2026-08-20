# Spec: cli-usage-recording

**Status**: draft
**Created**: 2026-08-20
**Branch**: `docs/dashboard-v2-specs`

## What
cairn CLI invocations are recorded into the usage history just like MCP
tool calls — command, timestamp, duration, status, and argument summary —
clearly labeled by source, so the dashboard's history/tokens/chains views
cover ALL cairn usage: agent-driven tool traffic and the owner's own CLI
work.

## Why
Recording today wraps MCP tool calls only. Every `cairn build`, `cairn
bench`, `cairn memory record` the owner runs is invisible to the usage
views, which skews two ways: the "how is cairn used" picture is missing its
human half, and CLI-driven load (builds are the heaviest operations cairn
does) doesn't appear next to agent traffic. One recording pipeline, both
sources, labeled.

## Business value
- The usage history becomes the complete usage record — one place to see
  everything cairn did on a machine and what it cost.
- Success criteria:
  - **SC-1**: after running a representative CLI batch (build, query,
    memory record, one failing command), every invocation appears in the
    history with source = CLI, status, duration, and argument summary.
  - **SC-2**: CLI commands add < 5% overhead (same buffered, off-hot-path
    discipline as tool-call recording; a clean exit loses nothing).

## User stories
### US1 — See my own usage (P1)
As a cairn owner, I want my CLI commands in the history, so that the usage
views show everything cairn did, not just agent traffic.

**Acceptance criteria**:
- AC1: Given a set of CLI runs, When I open history, Then each appears with
  its command, timestamp, duration, and status.
- AC2: Given both MCP and CLI usage recorded, When I view history, Then
  each row's source (CLI vs MCP tool) is visible.

### US2 — Cost of maintenance work (P2)
As a cairn owner, I want CLI usage in the token/cost view, so that heavy
commands are visible next to tool traffic.

**Acceptance criteria**:
- AC1: Given recorded CLI rows with payload sizes, When I open tokens, Then
  CLI commands appear in the aggregates labeled by source.

### US3 — Opt-out (P2)
As a privacy-sensitive user, I want a documented switch to disable CLI
recording, so that usage stays unrecorded if I choose.

**Acceptance criteria**:
- AC1: Given recording disabled via configuration, When CLI commands run,
  Then nothing is recorded and behavior is otherwise identical.

## Requirements
- **FR-001**: The system SHALL record every CLI command invocation with
  command name, timestamp, duration, status (ok/error), and a redacted,
  truncated argument summary — the same fields and protections as MCP
  tool-call records.
- **FR-002**: Recorded usage SHALL carry a source (CLI vs MCP tool) that
  the history, tokens, and chains views display and that history can
  filter on.
- **FR-003**: CLI recording SHALL be buffered off the command's hot path
  and flushed on clean exit, with no silent drops (same discipline and
  guarantees as tool-call recording).
- **FR-004**: CLI recording SHALL be enabled by default with a documented
  opt-out (environment/config), and the active state SHALL be discoverable.
- **FR-005**: Existing MCP tool-call recording SHALL be unchanged in
  shape, behavior, and performance (standing regression guard).
- **FR-006**: CLI records SHALL use a session identity that groups a shell
  session's commands where derivable, falling back to per-invocation
  identity.

## Scope
**In**: CLI-side recording into the existing usage store; source labeling +
history filter; buffered flush-on-exit; opt-out; session identity.
**Out (deferred)**: recording subprocesses cairn spawns (parsers, watchers
— only the top-level command is recorded); recording non-cairn commands;
retention policy (ui-dashboard-polish owns it); per-flag argument
redaction beyond the existing redaction pipeline.

## Assumptions & risks
- Assumption: the existing recording pipeline is reused rather than
  duplicated — the shared telemetry sink (one 30s flush thread + atexit
  drain), the `strip_private_data` redaction chokepoint, and the
  200-char args-summary truncation all apply as-is (extend, not replace
  — the dashboard spec's own hard-won rule).
- Assumption: FR-004's opt-out extends the existing master switch —
  `CAIRN_TELEMETRY=off` already gates tool_metrics — rather than
  inventing a parallel one; "discoverable" means visible where that
  switch is documented today.
- Assumption: one top-level record per invocation (not per subcommand
  hop) is the right granularity for usage analysis.
- Risk: very short-lived CLI processes may exit before a periodic flush —
  confirmed real: the shared sink's only drains are the 30s tick and the
  atexit handler, so the flush-on-exit path must cover them (exit-time
  drain is the SC-2 proof).
- Risk: argument summaries of CLI commands can embed user paths/code —
  same redaction chokepoint as tool calls applies (FR-001 bakes it in).
- Risk: CLI records stamped with the default session would land in the
  giant legacy `unknown` session that ui-dashboard-traffic-scale bounds —
  FR-006 must set an explicit session identity (the MCP side stamps a
  per-boot CAIRN_SESSION; the CLI side derives shell-session identity or
  falls back to per-invocation, never to the default).
