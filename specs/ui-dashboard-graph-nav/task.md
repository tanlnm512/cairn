# Tasks: ui-dashboard-graph-nav

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
Status reflects code state per [survey.md](survey.md), not intent.

## Burndown
| Phase | Total | Done |
|-------|-------|------|
| 1     | 4     | 0    |
| 2     | 4     | 0    |
| 3     | 3     | 0    |
| **Σ** | 11    | 0    |

## Phase 1: Search-to-focus with disambiguation (FR-001, FR-002)
<!-- Checkpoint: exact name focuses its neighborhood in one interaction;
     ambiguous names present candidates; the arbitrary pick is
     unreachable from the dashboard. -->
- [ ] T001 Add the candidates query (same-name symbols with file/kind, capped, truncated flag) to `src/cairn/dashboard/data.py` (FR-002)
- [ ] T002 Add the candidates endpoint in `src/cairn/dashboard/app.py` and the search box in `src/cairn/dashboard/templates/graph.html` (FR-001)
- [ ] T003 Wire confirm-to-focus and the inline candidate list in `src/cairn/dashboard/static/app.js` (FR-001, FR-002)
- [ ] T004 Add candidates tests (exact/ambiguous/capped) to `tests/test_dashboard_app.py` and `tests/test_dashboard_data.py` (FR-001, FR-002)

## Phase 2: Node expansion (FR-003, FR-005)
<!-- Checkpoint: expansion adds a node's neighbors without form
     resubmit; shown counts match the merged view. -->
- [ ] T005 Add the generalized neighbors function (name set, depth-ready signature) to `src/cairn/viz/query.py`, reusing the symbol scope's callers/callees SQL (FR-003)
- [ ] T006 Add the neighbors endpoint in `src/cairn/dashboard/app.py` returning the node/edge JSON (FR-003)
- [ ] T007 Implement expand activation, DataSet merge (no duplicate ids), and count refresh in `src/cairn/dashboard/static/app.js` (FR-003, FR-005)
- [ ] T008 Add expansion tests: merge shape, no duplicates, count honesty at caps (FR-003, FR-005)

## Phase 3: Layout toggle + guard (FR-004, FR-006)
<!-- Checkpoint: layouts toggle without losing focus; new endpoints sit
     inside the read-only guard. -->
- [ ] T009 Add the layout control and the live-network option toggle with camera preservation in `src/cairn/dashboard/static/app.js` and `src/cairn/dashboard/templates/graph.html` (FR-004)
- [ ] T010 Extend `tests/test_dashboard_readonly.py` to cover the candidates and neighbors endpoints (FR-006)
- [ ] T011 Add the layout persistence (URL param) and option-application assertions, plus the manual toggle procedure (FR-004)

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
