# Tasks: cli-usage-recording

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
Status reflects code state per [survey.md](survey.md), not intent.

## Burndown
| Phase | Total | Done |
|-------|-------|------|
| 1     | 4     | 0    |
| 2     | 3     | 0    |
| 3     | 3     | 0    |
| **Σ** | 10    | 0    |

## Phase 1: CLI-side recording (FR-001, FR-003, FR-005)
<!-- Checkpoint: representative batch lands one row per invocation,
     flushed on clean exit; MCP suites unchanged-green. -->
- [ ] T001 Add the row builder module `src/cairn/telemetry/cli_metrics.py`: argv summary with strip_private_data + 200-char cap, deque buffer, flusher registered with the shared sink (FR-001, FR-003)
- [ ] T002 Wrap dispatch with the timing/status capture Group in `src/cairn/cli/main.py`, calling the builder best-effort (FR-001)
- [ ] T003 Add the CliRunner batch test (build/query/memory/failing command → one row each, correct status/duration) and the subprocess exit-time-drain test (FR-001, FR-003)
- [ ] T004 Run the MCP regression guard: `tests/test_metrics.py`, `tests/test_metrics_extensions.py`, `tests/test_telemetry.py` green with zero suite modifications (FR-005)

## Phase 2: Source labeling + views (FR-002)
<!-- Checkpoint: history shows and filters source; tokens include CLI
     rows labeled. -->
- [ ] T005 Add the `source` column (default mcp) via the MIGRATIONS seam in `src/cairn/graph/schema.py` and stamp cli rows in the builder (FR-002)
- [ ] T006 Add the source display + filter to history in `src/cairn/dashboard/data.py` and `src/cairn/dashboard/templates/history.html`, following the tool/session param precedent (FR-002)
- [ ] T007 Add the mixed-source filter test and the tokens-inclusion test (FR-002)

## Phase 3: Opt-out + session identity (FR-004, FR-006)
<!-- Checkpoint: the switch disables CLI recording cleanly; rows never
     land in the 'unknown' session. -->
- [ ] T008 Gate the wrapper on the documented master switch (and the CLI-scoped refinement if adopted) with the paired on/off test (FR-004)
- [ ] T009 Implement session identity derivation (terminal/tmux env where present, per-invocation uuid fallback) in the builder (FR-006)
- [ ] T010 Add identity derivation unit tests over env fixtures and the never-unknown row assertion; document the switch where CAIRN_TELEMETRY is documented (FR-004, FR-006)

## Conventions
- `- [ ]` todo · `(in-progress)` claimed · `- [x]` done + proof note:
      done DATE — the test/command that proves it
- Dropped: `- [ ] ~~T011~~ dropped DATE (D-###)` — never delete the line;
  dropped tasks stay visible with the decision that killed them
- `[P]` = parallelizable (default — no shared files, no upstream task);
  chained tasks note `(after T###)`; serial runs need a reason, parallel
  runs need none
- Every task cites its FR-###; tasks with no FR are scope creep — fix the
  spec first
