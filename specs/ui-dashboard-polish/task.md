# Tasks: ui-dashboard-polish

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
Status reflects code state per [survey.md](survey.md), not intent.

## Burndown
| Phase | Total | Done |
|-------|-------|------|
| 1     | 5     | 0    |
| 2     | 3     | 0    |
| 3     | 3     | 0    |
| **Σ** | 11    | 0    |

## Phase 1: Recording-side — warm health, truncation magnitude, retention (FR-001, FR-003, FR-004, FR-007)
<!-- Checkpoint: first /health under 200ms; magnitude lands durably;
     over-cap stores age oldest rows with the policy visible in health. -->
- [ ] T001 Add the probe cache + startup prewarm thread for health in `src/cairn/dashboard/app.py` and `src/cairn/dashboard/data.py` (FR-001)
- [ ] T002 Record original-vs-delivered chars on the truncation branch of `src/cairn/mcp_server/metric_buffering.py`, with the additive tool_metrics columns in `src/cairn/graph/schema.py` (FR-003)
- [ ] T003 Extend `_prune` in `src/cairn/telemetry/sink.py` with the configurable tool_metrics cap (row count + optional age) inside the flush transaction (FR-004, FR-007)
- [ ] T004 Surface the retention policy and current size in `get_health` and `src/cairn/dashboard/templates/health.html` (FR-004)
- [ ] T005 Add the first-health timing test, truncation-magnitude assertion, prune-cap tests, and the dashboard-never-ages guard extension (FR-001, FR-003, FR-004, FR-007)

## Phase 2: View-side — tokenizer mode + truncation surfacing (FR-002, FR-003)
<!-- Checkpoint: exact mode used and labeled when available; heuristic
     fallback labeled; per-tool truncation counts render. -->
- [ ] T006 Add the tokenizer-mode helper (optional import, chars÷4 fallback, mode name) (FR-002)
- [ ] T007 Make the tokens estimates mode-aware and render the active-mode label plus per-tool truncation counts in `src/cairn/dashboard/data.py` and `src/cairn/dashboard/templates/tokens.html` (FR-002, FR-003)
- [ ] T008 Add mode-selection tests (import present/absent) and the truncation-surfacing view tests (FR-002, FR-003)

## Phase 3: Export + dark theme (FR-005, FR-006)
<!-- Checkpoint: filtered views export to CSV/JSON matching what is
     shown; dark theme persists per browser. -->
- [ ] T009 Add the CSV/JSON export routes over the existing data functions (current filter params, RFC-4180-correct csv module, attachment disposition) in `src/cairn/dashboard/app.py` plus export buttons in the templates (FR-005)
- [ ] T010 Add the dark palette as CSS-variable overrides, the prefers-color-scheme default, and the localStorage-persisted toggle in `src/cairn/dashboard/static/app.css` and `src/cairn/dashboard/templates/base.html` (FR-006)
- [ ] T011 Add the export-parity tests (filtered row sets, quoted fields) and the theme apply/persist unit test + manual procedure (FR-005, FR-006)

## Conventions
- `- [ ]` todo · `(in-progress)` claimed · `- [x]` done + proof note:
      done DATE — the test/command that proves it
- Dropped: `- [ ] ~~T012~~ dropped DATE (D-###)` — never delete the line;
  dropped tasks stay visible with the decision that killed them
- `[P]` = parallelizable (default — no shared files, no upstream task);
  chained tasks note `(after T###)`; serial runs need a reason, parallel
  runs need none
- Every task cites its FR-###; tasks with no FR are scope creep — fix the
  spec first
