# Plan: cli-usage-recording

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-20
Source of truth for code state: [survey.md](survey.md). Statuses below cite
survey items (Q#) or greps run in this planning session (file:line).

## Milestones
| Phase | Milestone | Delivers (demoable) | FRs | Depends on |
|-------|-----------|---------------------|-----|------------|
| 1 | CLI-side recording | Running `cairn build` / `cairn memory record` / a failing command appends one buffered row each (command, timestamp, duration, status, redacted arg summary) that lands in the store on clean exit — no MCP behavior change | FR-001, FR-003, FR-005 | — |
| 2 | Source labeling + views | Recorded rows carry CLI vs MCP source; history shows and filters on it; token aggregates include CLI rows labeled | FR-002 | Phase 1 |
| 3 | Opt-out + session identity | A documented switch disables CLI recording with behavior otherwise identical; CLI rows carry a derived shell-session identity, never the 'unknown' default | FR-004, FR-006 | Phase 1 |

## Dependencies

- **Phase 1 → Phases 2, 3** — the recording wrapper Phase 1 lands is where
  source and session values are stamped; Phases 2-3 build on its row shape.
- **Phase 2 ∥ Phase 3** — Phase 2 is dashboard/schema work, Phase 3 is
  CLI-side gating + identity; disjoint files.
- **FR-005 (MCP regression guard) runs inside Phase 1's task set** — the
  existing metric suites are the guard; any wrapper change to shared
  modules must keep them green before Phase 2 starts.

## Parallelization map

**Area A — recording wrapper** (Phase 1: FR-001, FR-003, FR-005)
Files: `src/cairn/cli/main.py` (Group-level invoke wrapper — survey Q6),
a new `src/cairn/telemetry/cli_metrics.py` (row builder + registered
flusher, mirroring metric_buffering's registration — survey Q2),
`src/cairn/graph/schema.py` only if Phase 2's column lands early (it
doesn't — separate area), `tests/test_metrics_extensions.py` (suite
conventions — survey supporting evidence).

**Area B — source column + views** (Phase 2: FR-002)
Files: `src/cairn/graph/schema.py` (additive ALTER TABLE — survey Q7),
`src/cairn/mcp_server/metric_buffering.py` (stamp default source),
`src/cairn/dashboard/data.py` + history template (display + filter).

**Area C — gating + identity** (Phase 3: FR-004, FR-006)
Files: `src/cairn/telemetry/cli_metrics.py` (opt-out check, identity
derivation), docs surface where CAIRN_TELEMETRY is documented.

- Independent: **B ∥ C** — different files; B's column default keeps C's
  rows valid regardless of ordering.
- Strictly ordered: **A → B, A → C** — both consume A's row builder.
- Cross-spec: the `source` filter param joins history's existing
  tool/session params — cross-links' row links must forward it (that
  spec's FR-005 carries active filters).

## Checkpoints

- **After Phase 1** (covers SC-2's mechanics): a representative batch
  (build, a query, memory record, one failing command) leaves exactly one
  row per invocation with correct status/duration, flushed on clean exit;
  MCP suites untouched-green. Verify: the phase's CliRunner test invoking
  commands against a tmp store + `uv run pytest tests/test_metrics.py
  tests/test_metrics_extensions.py tests/test_telemetry.py -q`.
- **After Phase 2**: history shows the source column and filters on it;
  token totals include CLI rows under their label. Verify:
  `uv run pytest tests/test_dashboard_app.py -q` (new source tests) and a
  manual dashboard check.
- **After Phase 3** (covers SC-1 end-to-end): with the switch off, a CLI
  batch records nothing and behaves identically; with it on, rows group by
  shell session where derivable. Verify: the gating test pair + the
  identity derivation unit tests.

## Risks & mitigations
- Risk: short-lived processes exit before flush → mitigation: atexit drain
  is already the sink's design (survey Q2); Phase 1's test proves the
  exit-path with a real subprocess, not just a direct flush call.
- Risk: CLI args embed paths/code → mitigation: strip_private_data + the
  200-char cap applied verbatim at the row builder (survey Q3; research RQ4).
- Risk: 'unknown' session aggregation swallows CLI rows into the legacy
  giant chain → mitigation: FR-006 stamps terminal/tmux-derived identity,
  else per-invocation uuid — the 'unknown' default is never used for CLI
  rows (spec's explicit risk).
- Risk: recording a failing command doubles noise for `--help`-style
  exits → mitigation: define the record set (top-level invocations only,
  usage errors recorded as their status — spec's one-record-per-invocation
  granularity), keep `cairn` bare (no subcommand) recorded too.

## Delivery
Branch `feat/cli-usage-recording` (or rides the dashboard-v2 train); one
PR, one commit per task. Post-merge: `cairn update` + `record_memory` per
AGENTS.md; `cairn doctor` (recording path = performance path).
