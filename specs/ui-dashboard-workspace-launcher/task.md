# Tasks: ui-dashboard-workspace-launcher

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
Status reflects code state per [survey.md](survey.md), not intent.

## Burndown
| Phase | Total | Done |
|-------|-------|------|
| 1     | 4     | 4    |
| 2     | 2     | 2    |
| 3     | 3     | 3    |
| **Σ** | 9     | 9    |

## Phase 1: Workspaces overview (FR-001, FR-002)
<!-- Checkpoint: /workspaces lists every local store with size,
     last-indexed, and call count; divergent states render. -->
- [x] T001 Build the enumeration module (registry union store-dirs, explicit populated/empty/missing/unreadable states) as `src/cairn/dashboard/workspaces.py` (FR-002)
      done 2026-08-20 — acceptance script: four-state union fixture OK; parameterized inputs (hermetic); never writes anywhere (tree-hash proven)
- [x] T002 Add the bounded per-store probe (stat-based size/freshness, one read-only open for call count) to the module (FR-001)
      done 2026-08-20 — probe contract verified incl. corrupt-.kg → unreadable (sqlite3.DatabaseError at query, broad-guarded) and the max_opens budget knob
- [x] T003 Add the `/workspaces` route in `src/cairn/dashboard/app.py`, the `workspaces.html` template, and the nav entry in `base.html` (FR-001)
      done 2026-08-20 — mixed-state fixture renders all four states 200; empty-registry + capped branches exercised; Jinja none-casing bug self-caught
- [x] T004 Add overview tests: stats columns render per store; the four-state fixture renders without errors (FR-001, FR-002)
      done 2026-08-20 — 4 tests incl. probe-cap degradation line; 57 passed at land time

## Phase 2: Restart-free switching (FR-003)
<!-- Checkpoint: selecting a workspace serves every view from its store;
     the launch store stays the default. -->
- [x] T005 Add the per-request store resolution seam (store param as registry key, default = launch store, unknown key renders the missing state) to the handlers in `src/cairn/dashboard/app.py` (FR-003)
      done 2026-08-20 — resolve_selection in every handler (db + knowledge root scoped together); nav carries the selection; unknown/non-populated → friendly missing page; 9/9 smoke
- [x] T006 Add switching tests: two seeded stores, selection tracked across projects/history/health, three-way switch sequence (FR-003)
      done 2026-08-20 — Part A closed T005's gaps: store rides every inter-view link (url macro) and both graph fetch builders (data-store hook); Part B 5 tests incl. three-way switch; 62 passed; D-005 records the tokens.html import

## Phase 3: Guard + budget (FR-004, FR-005)
<!-- Checkpoint: byte-identical guard holds across all interactions; 200+
     synthesized stores render within 2s. -->
- [x] T007 Extend `tests/test_dashboard_readonly.py` with the before/after tree-hash guard covering visited and merely-listed stores, sidecars included (FR-004)
      done 2026-08-20 — multi-store fixture, full interaction sweep, byte-identical + no-new-files + no-sidecars; WAL first-visit sidecar finding recorded as D-006 (fixture uses rollback-journal stores per suite convention)
- [x] T008 Add the 200+ synthesized-store fixture and the overview render-budget test (FR-005)
      done 2026-08-20 — 220 stores (0.158s synthesis via template-copy), first render 0.136s, strict <2s behind CAIRN_WORKSPACES_STRICT=1, structural bounds ungated; 3 passed
- [x] T009 Verify probe-cost degradation honesty: bounded opens surface a visible counts-unavailable state rather than hanging (FR-005)
      done 2026-08-20 — 4 data-layer tests: cap honesty in list order, max_opens=0, corrupt-first no-hang with budget accounting, empty/missing consume no budget; 60 passed

## Conventions
- `- [ ]` todo · `(in-progress)` claimed · `- [x]` done + proof note:
      done DATE — the test/command that proves it
- Dropped: `- [ ] ~~T010~~ dropped DATE (D-###)` — never delete the line;
  dropped tasks stay visible with the decision that killed them
- `[P]` = parallelizable (default — no shared files, no upstream task);
  chained tasks note `(after T###)`; serial runs need a reason, parallel
  runs need none
- Every task cites its FR-###; tasks with no FR are scope creep — fix the
  spec first
