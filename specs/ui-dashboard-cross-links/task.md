# Tasks: ui-dashboard-cross-links

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
Status reflects code state per [survey.md](survey.md), not intent.

## Burndown
| Phase | Total | Done |
|-------|-------|------|
| 1     | 4     | 0    |
| 2     | 1     | 0    |
| 3     | 4     | 0    |
| **Σ** | 9     | 0    |

## Phase 1: Session focus + row links (FR-001, FR-002, FR-003)
<!-- Checkpoint: tokens→history and history→chains navigate correctly;
     the projects→graph link is pinned by a test. -->
- [ ] T001 Add the optional session filter to `get_session_chains` in `src/cairn/dashboard/data.py` and read the param in the chains handler of `src/cairn/dashboard/app.py` (FR-002)
- [ ] T002 [P] Link the tokens row's tool name to the filtered history route in `src/cairn/dashboard/templates/tokens.html` (FR-001)
- [ ] T003 Link the history row's session id to the focused chains route in `src/cairn/dashboard/templates/history.html` (FR-002)
- [ ] T004 Add route tests (filtered chains by session), anchor-present assertions for tokens/history rows, and the projects→graph regression test to `tests/test_dashboard_app.py` (FR-001, FR-002, FR-003)

## Phase 2: No orphan views (FR-006)
<!-- Checkpoint: /graph reachable from every page's nav and the landing list. -->
- [ ] T005 [P] Add the graph entry to the shared nav in `src/cairn/dashboard/templates/base.html` and the landing list in `src/cairn/dashboard/templates/index.html` (FR-006)

## Phase 3: Node inspect + window carry (FR-004, FR-005)
<!-- Checkpoint: node inspect navigates to the symbol-neighborhood
     subgraph; links carry the active window where one exists. -->
- [ ] T006 Add the link-builder macro that appends the active time-window param when present in `src/cairn/dashboard/templates/` and use it for the row anchors (FR-005)
- [ ] T007 Wire the node inspect action in `src/cairn/dashboard/static/app.js` to navigate to the symbol-neighborhood route, using the activation gesture standardized by ui-dashboard-graph-nav (FR-004)
- [ ] T008 Add the link-builder unit test (window appended when present, omitted when not) (FR-005)
- [ ] T009 Add the inspect-navigation manual test procedure and the URL-construction assertion (FR-004)

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
