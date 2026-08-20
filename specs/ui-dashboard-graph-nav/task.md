# Tasks: ui-dashboard-graph-nav

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
Status reflects code state per [survey.md](survey.md), not intent.

## Burndown
| Phase | Total | Done |
|-------|-------|------|
| 1     | 4     | 4    |
| 2     | 4     | 4    |
| 3     | 3     | 3    |
| **Σ** | 11    | 11   |

## Phase 1: Search-to-focus with disambiguation (FR-001, FR-002)
<!-- Checkpoint: exact name focuses its neighborhood in one interaction;
     ambiguous names present candidates; the arbitrary pick is
     unreachable from the dashboard. -->
- [x] T001 Add the candidates query (same-name symbols with file/kind, capped, truncated flag) to `src/cairn/dashboard/data.py` (FR-002)
      done 2026-08-20 — acceptance script: 2 ambiguous matches in file order, miss/blank empty; truncation verified at >10 via limit+1 over-fetch
- [x] T002 Add the candidates endpoint in `src/cairn/dashboard/app.py` and the search box in `src/cairn/dashboard/templates/graph.html` (FR-001)
      done 2026-08-20 — uv run pytest tests/test_dashboard_app.py -q green; route + markup hooks grep-verified
- [x] T003 Wire confirm-to-focus and the inline candidate list in `src/cairn/dashboard/static/app.js` (FR-001, FR-002)
      done 2026-08-20 — node --check OK; single-match auto-navigate, inline list textContent-built, truncated/zero/failure states, no timers
- [x] T004 Add candidates tests (exact/ambiguous/capped) to `tests/test_dashboard_app.py` and `tests/test_dashboard_data.py` (FR-001, FR-002)
      done 2026-08-20 — uv run pytest both files -q: 78 passed incl. 6 new candidates tests

## Phase 2: Node expansion (FR-003, FR-005)
<!-- Checkpoint: expansion adds a node's neighbors without form
     resubmit; shown counts match the merged view. -->
- [x] T005 Add the generalized neighbors function (name set, depth-ready signature) to `src/cairn/viz/query.py`, reusing the symbol scope's callers/callees SQL (FR-003)
      done 2026-08-20 — functional suite: merge shape, per-direction cap 30 w/ LIMIT 31 sentinel, external callees, dual-definition names, dedupe/order; get_symbol_graph untouched
- [x] T006 Add the neighbors endpoint in `src/cairn/dashboard/app.py` returning the node/edge JSON (FR-003)
      done 2026-08-20 — repeatable name param strip/dedupe verified; empty → 200 empty JSON; depth gate mirrors graph handler
- [x] T007 Implement expand activation, DataSet merge (no duplicate ids), and count refresh in `src/cairn/dashboard/static/app.js` (FR-003, FR-005)
      done 2026-08-20 — node --check OK; doubleClick activation (vis emits "doubleClick"), id-keyed nodeView merge, edge-triple dedupe, #graph-counts live refresh, in-flight guard
- [x] T008 Add expansion tests: merge shape, no duplicates, count honesty at caps (FR-003, FR-005)
      done 2026-08-20 — uv run pytest both files -q: 85 passed incl. 7 new neighbors tests (cap honesty: 30 returned, truncated True, counts equal lists)

## Phase 3: Layout toggle + guard (FR-004, FR-006)
<!-- Checkpoint: layouts toggle without losing focus; new endpoints sit
     inside the read-only guard. -->
- [x] T009 Add the layout control and the live-network option toggle with camera preservation in `src/cairn/dashboard/static/app.js` and `src/cairn/dashboard/templates/graph.html` (FR-004)
      done 2026-08-20 — node --check OK; live setOptions toggle with getViewPosition/getScale + moveTo restore via once("afterDrawing"); history.replaceState persistence; bogus falls back to force
- [x] T010 Extend `tests/test_dashboard_readonly.py` to cover the candidates and neighbors endpoints (FR-006)
      done 2026-08-20 — uv run pytest tests/test_dashboard_readonly.py -q green: byte-identical digest + no sidecars across both endpoints' happy/edge paths, non-vacuous content assertions
- [x] T011 Add the layout persistence (URL param) and option-application assertions, plus the manual toggle procedure (FR-004)
      done 2026-08-20 — uv run pytest tests/test_dashboard_app.py -q: 33 passed incl. 3 layout tests + TC-005 manual procedure documented in-module

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
