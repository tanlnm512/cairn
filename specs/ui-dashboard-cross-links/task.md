# Tasks: ui-dashboard-cross-links

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
Status reflects code state per [survey.md](survey.md), not intent.

## Burndown
| Phase | Total | Done |
|-------|-------|------|
| 1     | 4     | 4    |
| 2     | 1     | 1    |
| 3     | 4     | 4    |
| **Σ** | 9     | 9    |

## Phase 1: Session focus + row links (FR-001, FR-002, FR-003)
<!-- Checkpoint: tokens→history and history→chains navigate correctly;
     the projects→graph link is pinned by a test. -->
- [x] T001 Add the optional session filter to `get_session_chains` in `src/cairn/dashboard/data.py` and read the param in the chains handler of `src/cairn/dashboard/app.py` (FR-002)
      done 2026-08-20 — session_id kwarg composes with window; no-match → empty wrapper; route tests green (90 passed at land time)
- [x] T002 Link the tokens row's tool name to the filtered history route in `src/cairn/dashboard/templates/tokens.html` (FR-001)
      done 2026-08-20 — anchor carries window when not all; suite green
- [x] T003 Link the history row's session id to the focused chains route in `src/cairn/dashboard/templates/history.html` (FR-002)
      done 2026-08-20 — session anchors carry window; legacy 'unknown' stays a functional link
- [x] T004 Add route tests (filtered chains by session), anchor-present assertions for tokens/history rows, and the projects→graph regression test to `tests/test_dashboard_app.py` (FR-001, FR-002, FR-003)
      done 2026-08-20 — 4 tests: tool/session anchors (incl. window carry + legacy unknown), projects→graph exact-anchor regression, /graph nav on both navs; 38 passed at land time

## Phase 2: No orphan views (FR-006)
<!-- Checkpoint: /graph reachable from every page's nav and the landing list. -->
- [x] T005 Add the graph entry to the shared nav in `src/cairn/dashboard/templates/base.html` and the landing list in `src/cairn/dashboard/templates/index.html` (FR-006)
      done 2026-08-20 — one line each, matched markup; nav+landing tests cover it

## Phase 3: Node inspect + window carry (FR-004, FR-005)
<!-- Checkpoint: node inspect navigates to the symbol-neighborhood
     subgraph; links carry the active window where one exists. -->
- [x] T006 Add the link-builder macro that appends the active time-window param when present in `src/cairn/dashboard/templates/` and use it for the row anchors (FR-005)
      done 2026-08-20 — view_link macro in _links.html; byte-identity proven across 8 rendered states; 38 exact-string pins unchanged
- [x] T007 Wire the node inspect action in `src/cairn/dashboard/static/app.js` to navigate to the symbol-neighborhood route, using the activation gesture standardized by ui-dashboard-graph-nav (FR-004)
      done 2026-08-20 — D-004 gesture split: selectNode/deselectNode + inspect anchor to scope=symbol&focus; "selectNode"/"deselectNode" casing verified in the vendored bundle; node --check OK
- [x] T008 Add the link-builder unit test (window appended when present, omitted when not) (FR-005)
      done 2026-08-20 — macro rendered via standalone Jinja env; 4 edge cases exact-matched; 39 passed at land time
- [x] T009 Add the inspect-navigation manual test procedure and the URL-construction assertion (FR-004)
      done 2026-08-20 — placeholder-span pin + target-URL neighborhood verification + TC004_MANUAL_PROCEDURE; 41 passed at land time

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
