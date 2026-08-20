# Tasks: ui-dashboard-traffic-scale

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
Status reflects code state per [survey.md](survey.md), not intent.

## Burndown
| Phase | Total | Done |
|-------|-------|------|
| 1     | 4     | 0    |
| 2     | 3     | 0    |
| 3     | 4     | 0    |
| **Σ** | 11    | 0    |

## Phase 1: Indexed + paginated history (FR-001, FR-006)
<!-- Checkpoint: bounded first page on a large store; stable repeat-free
     paging; no LIMIT-free history query remains. -->
- [ ] T001 Add the composite index on tool_metrics(invoked_at, id) via the idempotent executescript in `src/cairn/graph/schema.py` (FR-001)
- [ ] T002 Convert `list_history` in `src/cairn/dashboard/data.py` to keyset paging: ORDER BY invoked_at DESC, id DESC with a before-cursor and bounded page size, returning the next cursor (FR-001)
- [ ] T003 Accept the cursor param in the history handler of `src/cairn/dashboard/app.py` and render prev/next controls carrying tool/session/window params in `src/cairn/dashboard/templates/history.html` (FR-001, FR-006)
- [ ] T004 Add pagination tests to `tests/test_dashboard_data.py`: bounded page, cursor stability under mid-paging inserts, no row repeats (FR-001, FR-006)

## Phase 2: Time windows everywhere (FR-002, FR-003)
<!-- Checkpoint: the window control narrows history, chains, and token
     aggregates to the selected slice. -->
- [ ] T005 Add the shared window-control partial (24h/7d/30d/all) in `src/cairn/dashboard/templates/` and thread the window param through the history, tokens, and chains handlers in `src/cairn/dashboard/app.py` (FR-002)
- [ ] T006 Add the since predicate to `list_history`, `get_tool_tokens`, and the chains query in `src/cairn/dashboard/data.py`, composed in the shared WHERE builder (FR-002, FR-003)
- [ ] T007 Add window tests: exclusion of outside rows in all three views and window-scoped recomputation of per-tool aggregates (FR-002, FR-003)

## Phase 3: Bounded chains + render budget (FR-004, FR-005)
<!-- Checkpoint: the giant-session fixture renders bounded with honest
     counts and expand; the 10k-store budget test passes. -->
- [ ] T008 Bound `get_session_chains` in `src/cairn/dashboard/data.py`: newest-chains-first cap, per-chain head cap, shown/total metadata, chain-expand tail fetch (FR-004)
- [ ] T009 Render the bound affordances and shown-of-total counts in `src/cairn/dashboard/templates/chains.html` (FR-004)
- [ ] T010 Add the legacy all-unknown-session fixture and bound tests to `tests/test_dashboard_data.py` (FR-004)
- [ ] T011 Add the synthesized 10k-row store and the first-render budget test (strict 2s locally, structural bounds in CI) for the three traffic routes (FR-005)

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
