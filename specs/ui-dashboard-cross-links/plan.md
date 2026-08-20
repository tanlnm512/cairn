# Plan: ui-dashboard-cross-links

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-20
Source of truth for code state: [survey.md](survey.md). Statuses below cite
survey items (Q#) or greps run in this planning session (file:line).

## Milestones
| Phase | Milestone | Delivers (demoable) | FRs | Depends on |
|-------|-----------|---------------------|-----|------------|
| 1 | Session focus + row links | A tokens row click lands on /history pre-filtered to that tool; a history row's session click lands on /chains focused on that session; the existing projects→graph link is pinned by a regression test | FR-001, FR-002, FR-003 | — |
| 2 | No orphan views | /graph is reachable from the shared nav and the landing page (FR-006) | FR-006 | — |
| 3 | Node inspect + window carry | A graph node's inspect action opens its symbol-neighborhood subgraph via the same neighborhood data path ui-dashboard-graph-nav uses; link helpers carry the time-window param where one exists | FR-004, FR-005 | ui-dashboard-graph-nav's shared path (or lands concurrently with it) |

## Dependencies

- **Phase 1 ∥ Phase 2** — disjoint templates: Phase 1 edits
  `tokens.html`/`history.html` + the chains route; Phase 2 edits
  `base.html`/`index.html`.
- **Phase 3 after graph-nav's neighborhood seam exists** — the spec's
  assumption binds inspect and expand to one data path; implementing inspect
  first would force graph-nav to conform later, the duplication the
  assumption forbids. If both specs land in one train, Phase 3 tasks pair
  with graph-nav's expansion tasks.
- **FR-005's window parameter** comes from ui-dashboard-traffic-scale — the
  link helper reads the active window if present and omits it otherwise
  (spec's "where one exists"); no hard dependency, only composition.

## Parallelization map

**Area A — traffic-row links** (Phase 1: FR-001, FR-002, FR-003)
Files: `src/cairn/dashboard/templates/tokens.html`, `history.html`
(anchor on primary entity per spec risk note), `src/cairn/dashboard/app.py`
(chains handler gains a session param — survey Q4),
`src/cairn/dashboard/data.py` (`get_session_chains` gains an optional
session filter), `tests/test_dashboard_app.py`.

**Area B — nav completeness** (Phase 2: FR-006)
Files: `src/cairn/dashboard/templates/base.html`, `index.html` (survey Q6).

**Area C — node inspect + link helper** (Phase 3: FR-004, FR-005)
Files: `src/cairn/dashboard/static/app.js` (selectNode → navigate, survey
Q7), a shared link-helper in the templates' context or a Jinja macro file.

- Independent: **A ∥ B** — no shared files.
- Independent: **C ∥ A/B** except the helper's consumers — C's inspect
  navigation composes Area A's URL conventions; sequence the helper task
  after A's route changes settle.
- Cross-spec: **C pairs with ui-dashboard-graph-nav** (shared neighborhood
  path; see that spec's plan).

## Checkpoints

- **After Phase 1**: `/history?tool=X` and `/chains?session=Y` render
  filtered content; clicking a tokens row's tool name and a history row's
  session id navigates correctly; the projects→graph link test passes.
  Verify: `uv run pytest tests/test_dashboard_app.py -q` (new route tests
  + regression test) and manual clicks on the dev store.
- **After Phase 2**: /graph reachable from every page's nav and from the
  landing list. Verify: `grep -c "graph" src/cairn/dashboard/templates/base.html`
  and manual navigation.
- **After Phase 3**: clicking a node's inspect action navigates to the
  symbol-neighborhood graph; links built while a window filter is active
  carry it. Verify: manual browser flow + the link-helper unit test.

## Risks & mitigations
- Risk: link proliferation clutters dense tables → mitigation: anchor only
  the primary entity per row (spec's own mitigation), styled subtly.
- Risk: legacy 'unknown' sessions make session links target a giant chain
  set → mitigation: chains' new session filter must compose with
  traffic-scale FR-004's bounds; the link stays functional because the
  destination is filtered server-side, not client-rendered whole.
- Risk: graph-nav lands later than this spec → mitigation: Phase 3 is
  explicitly sequenced behind (or with) graph-nav; Phases 1-2 ship value
  independently.

## Delivery
Branch `feat/ui-dashboard-cross-links` (or rides the dashboard-v2 train);
one PR, one commit per task, code + docs together. Post-merge:
`cairn update` + `record_memory` per AGENTS.md.
