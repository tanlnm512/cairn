# Tasks: ui-dashboard-workspace-launcher

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
Status reflects code state per [survey.md](survey.md), not intent.

## Burndown
| Phase | Total | Done |
|-------|-------|------|
| 1     | 4     | 0    |
| 2     | 2     | 0    |
| 3     | 3     | 0    |
| **Σ** | 9     | 0    |

## Phase 1: Workspaces overview (FR-001, FR-002)
<!-- Checkpoint: /workspaces lists every local store with size,
     last-indexed, and call count; divergent states render. -->
- [ ] T001 Build the enumeration module (registry union store-dirs, explicit populated/empty/missing/unreadable states) as `src/cairn/dashboard/workspaces.py` (FR-002)
- [ ] T002 Add the bounded per-store probe (stat-based size/freshness, one read-only open for call count) to the module (FR-001)
- [ ] T003 Add the `/workspaces` route in `src/cairn/dashboard/app.py`, the `workspaces.html` template, and the nav entry in `base.html` (FR-001)
- [ ] T004 Add overview tests: stats columns render per store; the four-state fixture renders without errors (FR-001, FR-002)

## Phase 2: Restart-free switching (FR-003)
<!-- Checkpoint: selecting a workspace serves every view from its store;
     the launch store stays the default. -->
- [ ] T005 Add the per-request store resolution seam (store param as registry key, default = launch store, unknown key renders the missing state) to the handlers in `src/cairn/dashboard/app.py` (FR-003)
- [ ] T006 Add switching tests: two seeded stores, selection tracked across projects/history/health, three-way switch sequence (FR-003)

## Phase 3: Guard + budget (FR-004, FR-005)
<!-- Checkpoint: byte-identical guard holds across all interactions; 200+
     synthesized stores render within 2s. -->
- [ ] T007 Extend `tests/test_dashboard_readonly.py` with the before/after tree-hash guard covering visited and merely-listed stores, sidecars included (FR-004)
- [ ] T008 Add the 200+ synthesized-store fixture and the overview render-budget test (FR-005)
- [ ] T009 Verify probe-cost degradation honesty: bounded opens surface a visible counts-unavailable state rather than hanging (FR-005)

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
