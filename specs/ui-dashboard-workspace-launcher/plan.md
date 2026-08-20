# Plan: ui-dashboard-workspace-launcher

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-20
Source of truth for code state: [survey.md](survey.md). Statuses below cite
survey items (Q#) or greps run in this planning session (file:line).

## Milestones
| Phase | Milestone | Delivers (demoable) | FRs | Depends on |
|-------|-----------|---------------------|-----|------------|
| 1 | Workspaces overview | A `/workspaces` route lists every local store with workspace identity, size, last-indexed time, and call count; empty/missing/unreadable stores render their state without crashing | FR-001, FR-002 | — |
| 2 | Restart-free switching | Selecting a workspace serves every existing view against that store without a server restart; returning to the overview and picking another works | FR-003 | Phase 1 |
| 3 | Guard + budget | The read-only guard extends to listed/probed stores; the overview renders 200+ synthesized stores within 2 seconds | FR-004, FR-005 | Phase 1 |

## Dependencies

- **Phase 1 → Phases 2, 3** — the enumeration/probe module Phase 1 builds
  is what Phase 2's selection and Phase 3's budget test consume.
- **Phase 2 ∥ Phase 3** — switching is handler-seam work; guard+budget is
  test work; disjoint files except the shared probe module (read-only use).
- **CLI surface untouched** — `cairn dashboard` flags stay as-is (survey
  Q4); switching is purely in-server.

## Parallelization map

**Area A — enumeration + overview route** (Phase 1: FR-001, FR-002)
Files: new probe module under `src/cairn/dashboard/` (enumeration +
per-store stats), `src/cairn/dashboard/app.py` (route),
`src/cairn/dashboard/templates/workspaces.html`, `base.html` (nav entry).
Data sources: registry + CAIRN_HOME dirs (survey Q1, Q2), stat + cheap
reads (survey Q6).

**Area B — per-request store selection** (Phase 2: FR-003)
Files: `src/cairn/dashboard/app.py` handlers (they already open per-request
via `get_read_only_db(db_path)` — survey Q5), the overview template's
selection links.

**Area C — guard + budget** (Phase 3: FR-004, FR-005)
Files: `tests/test_dashboard_readonly.py` (extension), a synthesized-store
budget test.

- Independent: **B ∥ C** after A — B touches handlers/templates, C touches
  tests + fixtures only.
- Strictly ordered: **A → B** (B's selection needs A's enumeration ids)
  and **A → C** (C's fixtures drive A's probe module).

## Checkpoints

- **After Phase 1**: `/workspaces` on the dev machine lists the 2 real
  stores with size, last-indexed, and call count; a fixture with
  missing/empty/unreadable entries renders states, not errors. Verify:
  manual browser check + `uv run pytest tests/test_dashboard_app.py -q`
  (new overview tests).
- **After Phase 2**: clicking a workspace serves /projects, /history, and
  /health from that store; the URL is shareable; the launch store remains
  the default. Verify: manual two-store walkthrough on this machine (the
  registry's two real entries) + selection tests.
- **After Phase 3** (covers SC-1, SC-2): the byte-identical guard holds
  across overview + switching interactions on all fixture stores; 200
  synthesized stores render within 2s. Verify: the extended readonly suite
  and the budget test's timing output.

## Risks & mitigations
- Risk: probe cost scales past the budget on many stores → mitigation:
  stat-only size/freshness (research RQ2), counts via one bounded open
  each, cache-with-refresh only if the budget test forces it (spec's own
  escalation).
- Risk: probing creates sidecars on closed stores (WAL) → mitigation:
  size/mtime via os.stat only; SQL opens are mode=ro (survey supporting
  evidence); the guard test hashes the store tree before/after.
- Risk: stale registry entries (proven: 227 test-leaked) mislead the
  overview → mitigation: reconcile registry ∪ dirs with explicit states
  (FR-002); never write/re-register from the dashboard (FR-004).
- Risk: switching changes which store a URL refers to, breaking
  shareability → mitigation: selection is an explicit URL param, not
  hidden server state (D-001); default remains the launch store.

## Delivery
Branch `feat/ui-dashboard-workspace-launcher` (or rides the dashboard-v2
train); one PR, one commit per task. Post-merge: `cairn update` +
`record_memory` per AGENTS.md.
