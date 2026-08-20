# Test Cases: ui-dashboard-workspace-launcher

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-20
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details.

## TC-001 — Overview lists every local store with stats
- **Story**: US1 · **Traces to**: FR-001, AC1
- **Given** multiple local stores (real or fixture)
- **When** the overview is opened
- **Then** each is listed with workspace identity, store size, last-indexed
  time, and recorded tool-call count
- **Pass condition**: auto — fixture with populated stores asserts every
  column renders per store; manual — this machine's two real stores show
  correct size/count vs `sqlite3` ground truth.

## TC-002 — Divergent store states render, nothing crashes
- **Story**: US1 · **Traces to**: FR-002, AC2
- **Given** stores in mixed states: populated, empty (no DB), registered
  but missing on disk, unreadable
- **When** the overview renders
- **Then** each store is presented with its state and the page completes
- **Pass condition**: auto — fixture covering all four states asserts
  state labels present and a 200 response.

## TC-003 — Switching serves the selected workspace
- **Story**: US2 · **Traces to**: FR-003, AC1
- **Given** the overview listing at least two stores with distinct content
- **When** a workspace is selected
- **Then** the dashboard's views (projects, history, health) serve that
  workspace's data
- **Pass condition**: auto — two seeded stores with distinguishable
  projects/calls; select each and assert the views reflect the right one;
  manual — two-store walkthrough on this machine.

## TC-004 — Return and pick another
- **Story**: US2 · **Traces to**: FR-003, AC2
- **Given** a selected workspace's dashboard
- **When** returning to the overview and selecting a different workspace
- **Then** the views switch to the new selection without a server restart
- **Pass condition**: auto — three-way switch sequence asserts the served
  content tracks the selection each time.

## TC-005 — Launcher never writes to any store
- **Story**: US3 · **Traces to**: FR-004, AC1
- **Given** a set of stores (visited and merely listed)
- **When** overview browsing and workspace switching complete
- **Then** every store's content is byte-identical to before (including
  sidecar files)
- **Pass condition**: auto — tree hash (files + `-wal`/`-shm` if present)
  before/after all interactions, across all fixture stores.

## TC-006 — 200-store render budget
- **Story**: US1 · **Traces to**: FR-005, SC-1
- **Given** a machine (fixture) with 200+ stores
- **When** the overview renders completely
- **Then** it completes within 2 seconds
- **Pass condition**: auto — synthesized 200+ store fixture; strict timing
  locally, structural probe-budget assertion in CI.

## Coverage matrix
| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001 | TC-001 | auto + manual |
| FR-002 | TC-002 | auto |
| FR-003 | TC-003, TC-004 | auto + manual |
| FR-004 | TC-005 | auto |
| FR-005 | TC-006 | auto |
