# Tasks: cli-usage-recording

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
Status reflects code state per [survey.md](survey.md), not intent.

## Burndown
| Phase | Total | Done |
|-------|-------|------|
| 1     | 4     | 4    |
| 2     | 3     | 3    |
| 3     | 3     | 3    |
| **Σ** | 10    | 10   |

## Phase 1: CLI-side recording (FR-001, FR-003, FR-005)
<!-- Checkpoint: representative batch lands one row per invocation,
     flushed on clean exit; MCP suites unchanged-green. -->
- [x] T001 Add the row builder module `src/cairn/telemetry/cli_metrics.py`: argv summary with strip_private_data + 200-char cap, deque buffer, flusher registered with the shared sink (FR-001, FR-003)
      done 2026-08-20 — contract script OK (term:/tmux:/cli: sessions, truncated ≤200 summary); mirrors the metric_buffering doctrine incl. gates + redaction chokepoint; 82 metric/telemetry tests green
- [x] T002 Wrap dispatch with the timing/status capture Group in `src/cairn/cli/main.py`, calling the builder best-effort (FR-001)
      done 2026-08-20 — _RecordingGroup.invoke wraps dispatch; parse_args hook for bare no-args (D-004); exit-code semantics (D-005); subcommand read at record time (D-006); end-to-end rows verified incl. real-process atexit
- [x] T003 Add the CliRunner batch test (build/query/memory/failing command → one row each, correct status/duration) and the subprocess exit-time-drain test (FR-001, FR-003)
      done 2026-08-20 — tests/test_cli_metrics.py: 5-shape batch, exit-code matrix, redaction canary, real-subprocess atexit drain, paired on/off gates; 6→9 tests green
- [x] T004 Run the MCP regression guard: `tests/test_metrics.py`, `tests/test_metrics_extensions.py`, `tests/test_telemetry.py` green with zero suite modifications (FR-005)
      done 2026-08-20 — uv run pytest on the three suites: 82 passed; git diff --stat on the files = 0 lines (byte-identical); metric_buffering.py untouched by the whole plan

## Phase 2: Source labeling + views (FR-002)
<!-- Checkpoint: history shows and filters source; tokens include CLI
     rows labeled. -->
- [x] T005 Add the `source` column (default mcp) via the MIGRATIONS seam in `src/cairn/graph/schema.py` and stamp cli rows in the builder (FR-002)
      done 2026-08-20 — fresh-DB + old-DB migration proofs OK; MCP INSERT byte-identical; _TELEMETRY_TABLE_COLUMNS carry fix recorded as D-007
- [x] T006 Add the source display + filter to history in `src/cairn/dashboard/data.py` and `src/cairn/dashboard/templates/history.html`, following the tool/session param precedent (FR-002)
      done 2026-08-20 — source filter composes with since/cursors; Source column + form input + pagination carry; route smoke-verified
- [x] T007 Add the mixed-source filter test and the tokens-inclusion test (FR-002)
      done 2026-08-20 — 4 mixed-source tests (column, filter+composition, Older carry, tokens inclusion under cli:* names) + both fixture repairs; 214 passed across the six suites

## Phase 3: Opt-out + session identity (FR-004, FR-006)
<!-- Checkpoint: the switch disables CLI recording cleanly; rows never
     land in the 'unknown' session. -->
- [x] T008 Gate the wrapper on the documented master switch (and the CLI-scoped refinement if adopted) with the paired on/off test (FR-004)
      done 2026-08-20 — already satisfied by T001+T003: record_cli_invocation gates on is_telemetry_off/is_read_only (cli_metrics.py:183); test_telemetry_off_records_nothing_paired proves zero rows with identical output
- [x] T009 Implement session identity derivation (terminal/tmux env where present, per-invocation uuid fallback) in the builder (FR-006)
      done 2026-08-20 — already satisfied by T001: derive_session_id (TERM_SESSION_ID→term:, TMUX_PANE→tmux:, else cli:+uuid) landed with the pinned contract; precedence + fallback verified in T001's acceptance and T010's matrix
- [x] T010 Add identity derivation unit tests over env fixtures and the never-unknown row assertion; document the switch where CAIRN_TELEMETRY is documented (FR-004, FR-006)
      done 2026-08-20 — env-matrix + never-unknown + store-level grouping tests (9 green, tmux-host-proof scrub helper); CAIRN_TELEMETRY and CAIRN_READ_ONLY rows in docs/configuration.md now name usage rows incl. cli:* invocations

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
