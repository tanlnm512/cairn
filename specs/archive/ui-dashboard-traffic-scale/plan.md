# Plan: ui-dashboard-traffic-scale

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-20
Source of truth for code state: [survey.md](survey.md). Statuses below cite
survey items (Q#) or greps run in this planning session (file:line).

## Milestones
| Phase | Milestone | Delivers (demoable) | FRs | Depends on |
|-------|-----------|---------------------|-----|------------|
| 1 | Indexed + paginated history | Opening history on a large store renders a bounded first page quickly with working next/newer navigation; ordering is stable and rows never repeat across pages | FR-001, FR-006 | — |
| 2 | Time windows everywhere | The 24h/7d/30d/all control narrows history, chains, and token aggregates to the selected window's data | FR-002, FR-003 | — |
| 3 | Bounded chains + render budget | A giant session (including the all-'unknown' legacy shape) renders a bounded portion with an explicit expand; first render on a 10k+ call store completes under 2 seconds | FR-004, FR-005 | Phase 2 |

## Dependencies

- **Phase 1 ∥ Phase 2** — different clauses of the same query but disjoint
  code paths to start: Phase 1 = cursor param + ORDER BY tie-break in
  `list_history`; Phase 2 = window param threading through three views.
  They merge in the same WHERE clause; the task-breaker serializes only the
  composing task (T-final tests).
- **Phase 3 after Phase 2** — chain bounding composes with the window
  filter (bound applies within the windowed slice); doing it second avoids
  reworking the bound's cutoff twice.
- **Index first** — the `(invoked_at, id)` index (Phase 1, first task) is
  what keeps Phase 2's windows off full scans; FR-005's budget depends on it.

## Parallelization map

**Area A — history pagination** (Phase 1: FR-001, FR-006)
Files: `src/cairn/graph/schema.py` (index via the idempotent-script seam,
survey Q5), `src/cairn/dashboard/data.py` `list_history` (survey Q1),
`src/cairn/dashboard/app.py` history handler, `history.html` (page
controls), `tests/test_dashboard_data.py`.

**Area B — window filters** (Phase 2: FR-002, FR-003)
Files: `src/cairn/dashboard/app.py` (window param on history/tokens/chains),
`src/cairn/dashboard/data.py` (`get_tool_tokens` survey Q2, chains window),
shared window-control partial in `src/cairn/dashboard/templates/`.

**Area C — bounded chains + budget** (Phase 3: FR-004, FR-005)
Files: `src/cairn/dashboard/data.py` `get_session_chains` (survey Q3),
`chains.html` (expand affordance), a synthesized-store render-budget test.

- Independent: **A ∥ B** up to their composing tests — A owns cursor+order,
  B owns the window predicate; both edit `data.py` functions but disjoint
  functions (`list_history` vs `get_tool_tokens`/chains).
- Strictly ordered: **A's index task → B's budget-sensitive assertions** —
  windows are index-backed only after it lands.
- Strictly ordered: **B → C** — the bound is applied within the windowed
  slice.

## Checkpoints

- **After Phase 1**: `/history` on a seeded 10k-row store returns a bounded
  page; paging forward then back yields identical row sets (keyset
  stability); no LIMIT-free query remains in the history path. Verify:
  `uv run pytest tests/test_dashboard_data.py -q` (new pagination tests) and
  `grep -n "LIMIT" src/cairn/dashboard/data.py` (history query bounded).
- **After Phase 2**: changing the window visibly narrows all three views;
  token aggregates recompute within the window. Verify: window tests in
  `tests/test_dashboard_data.py`; manual check on the dev store.
- **After Phase 3** (covers SC-1, SC-2): the 10k-store render-budget test
  asserts first-render under 2s for history/tokens/chains; the all-'unknown'
  store shape renders bounded with a visible expand. Verify: the budget test
  with timing assertion and the legacy-shape fixture.

## Risks & mitigations
- Risk: window filters scan the whole table (spec-confirmed, survey Q4) →
  mitigation: `(invoked_at, id)` index lands in Phase 1 before any window
  query exists; FR-005's budget test fails loudly if it regresses.
- Risk: OFFSET-style paging duplicates/skips rows as live traffic lands →
  mitigation: keyset pagination chosen (research RQ1); US1-AC2's no-repeat
  is a structural property, not a test-only hope.
- Risk: chains bound hides the legacy 'unknown' session's tail the way
  users actually browse it → mitigation: bound shows newest-first chains
  and per-chain head/tail with explicit expand; the legacy fixture asserts
  the bound holds specifically (spec's "and first").
- Risk: pagination + windows + live-updates' swaps interact → mitigation:
  live-updates re-fetches the current URL — cursor and window params ride
  along; its FR-003 task tests preservation across paginated state.

## Delivery
Branch `feat/ui-dashboard-traffic-scale` (first of the dashboard-v2 train —
cross-links' FR-005 and live-updates compose on top of it); one PR, one
commit per task. Post-merge: `cairn update` + `record_memory` per AGENTS.md;
`cairn doctor` if the index migration shows lock contention.
