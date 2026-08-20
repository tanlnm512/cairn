# Tasks: ui-dashboard-polish

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
Status reflects code state per [survey.md](survey.md), not intent.

## Burndown
| Phase | Total | Done |
|-------|-------|------|
| 1     | 5     | 5    |
| 2     | 3     | 3    |
| 3     | 3     | 3    |
| **Σ** | 11    | 11   |

## Phase 1: Recording-side — warm health, truncation magnitude, retention (FR-001, FR-003, FR-004, FR-007)
<!-- Checkpoint: first /health under 200ms; magnitude lands durably;
     over-cap stores age oldest rows with the policy visible in health. -->
- [x] T001 Add the probe cache + startup prewarm thread for health in `src/cairn/dashboard/app.py` and `src/cairn/dashboard/data.py` (FR-001)
      done 2026-08-20 — `tests/test_dashboard_app.py::test_first_health_render_on_fresh_app_is_under_budget` PASSED (prewarm-join; D-005)
- [x] T002 Record original-vs-delivered chars on the truncation branch of `src/cairn/mcp_server/metric_buffering.py`, with the additive tool_metrics columns in `src/cairn/graph/schema.py` (FR-003)
      done 2026-08-20 — `uv run pytest tests/test_metrics_extensions.py tests/test_metrics.py -q` → 51 passed; `test_truncating_invocation_records_magnitude_columns` PASSED
- [x] T003 Extend `_prune` in `src/cairn/telemetry/sink.py` with the configurable tool_metrics cap (row count + optional age) inside the flush transaction (FR-004, FR-007)
      done 2026-08-20 — `uv run pytest tests/test_telemetry.py tests/test_cli_metrics.py -q` → 40 passed; D-004 (time-ordered key)
- [x] T004 Surface the retention policy and current size in `get_health` and `src/cairn/dashboard/templates/health.html` (FR-004)
      done 2026-08-20 — rendered-/health sanity (policy + over-cap badge under pinned env) + `uv run pytest tests/test_dashboard_app.py tests/test_dashboard_data.py -q` green
- [x] T005 Add the first-health timing test, truncation-magnitude assertion, prune-cap tests, and the dashboard-never-ages guard extension (FR-001, FR-003, FR-004, FR-007)
      done 2026-08-20 — TC proof run: 21 tests PASSED incl. TC-001/004/006/009; D-005 test adapted (asserts served cache); full suite 2123 passed

## Phase 2: View-side — tokenizer mode + truncation surfacing (FR-002, FR-003)
<!-- Checkpoint: exact mode used and labeled when available; heuristic
     fallback labeled; per-tool truncation counts render. -->
- [x] T006 Add the tokenizer-mode helper (optional import, chars÷4 fallback, mode name) (FR-002)
      done 2026-08-20 — sanity: mode `exact (BAAI/bge-m3)` live, both fallback paths → `heuristic (chars/4)`; `tests/test_dashboard_data.py` green untouched
- [x] T007 Make the tokens estimates mode-aware and render the active-mode label plus per-tool truncation counts in `src/cairn/dashboard/data.py` and `src/cairn/dashboard/templates/tokens.html` (FR-002, FR-003)
      done 2026-08-20 — TC-002/TC-003/TC-005 tests PASSED (mode label, calibration honesty, unknown≠zero); D-006 (per-window calibration)
- [x] T008 Add mode-selection tests (import present/absent) and the truncation-surfacing view tests (FR-002, FR-003)
      done 2026-08-20 — `uv run pytest tests/test_dashboard_data.py -q` → 65 passed (59 existing + 6 new, order-stable)

## Phase 3: Export + dark theme (FR-005, FR-006)
<!-- Checkpoint: filtered views export to CSV/JSON matching what is
     shown; dark theme persists per browser. -->
- [x] T009 Add the CSV/JSON export routes over the existing data functions (current filter params, RFC-4180-correct csv module, attachment disposition) in `src/cairn/dashboard/app.py` plus export buttons in the templates (FR-005)
      done 2026-08-20 — `uv run pytest tests/test_dashboard_export.py -q` → 7 passed (row parity, RFC-4180 round-trip, unpaginated, store-switch)
- [x] T010 Add the dark palette as CSS-variable overrides, the prefers-color-scheme default, and the localStorage-persisted toggle in `src/cairn/dashboard/static/app.css` and `src/cairn/dashboard/templates/base.html` (FR-006)
      done 2026-08-20 — `uv run pytest tests/test_dashboard_theme.py -q` → 4 passed (toggle, pre-paint script, persist contract, all-six-variable dark override)
- [x] T011 Add the export-parity tests (filtered row sets, quoted fields) and the theme apply/persist unit test + manual procedure (FR-005, FR-006)
      done 2026-08-20 — `uv run pytest tests/test_dashboard_export.py tests/test_dashboard_theme.py -q` → 11 passed; TC-008 manual procedure documented in test module docstring

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
