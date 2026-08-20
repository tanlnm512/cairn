# Plan: ui-dashboard-graph-nav

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-20
Source of truth for code state: [survey.md](survey.md). Statuses below cite
survey items (Q#) or greps run in this planning session (file:line).

## Milestones
| Phase | Milestone | Delivers (demoable) | FRs | Depends on |
|-------|-----------|---------------------|-----|------------|
| 1 | Search-to-focus with disambiguation | Typing a symbol name and confirming focuses the graph on it with its neighborhood in one interaction; ambiguous names present candidates for selection instead of an arbitrary pick | FR-001, FR-002 | — |
| 2 | Node expansion | Activating expand on a rendered node adds that symbol's callers and callees to the view without resubmitting the form; counts stay accurate | FR-003, FR-005 | Phase 1 |
| 3 | Layout toggle + guard | Force-directed and hierarchical layouts switchable without losing focus; the read-only guard extends to the new endpoints | FR-004, FR-006 | Phase 1 |

## Dependencies

- **Phase 1 → Phases 2, 3** — the search box lands the graph view's first
  interactive chrome; expansion's count display and the toggle's focus
  preservation build on that state.
- **Phase 2 owns the traversal seam** — wiring multi-hop (survey Q3) or
  capping at 1-hop-per-action is its first task, because cross-links'
  inspect action (that spec's D-003) consumes the same neighborhood path.
- **Phase 3 ∥ Phase 2** after Phase 1 — layout toggling is option-level
  work in `app.js`; disjoint from the expansion endpoint.

## Parallelization map

**Area A — search + candidates** (Phase 1: FR-001, FR-002)
Files: `src/cairn/dashboard/data.py` (candidates query — survey Q4, Q7),
`src/cairn/dashboard/app.py` (search/candidates endpoint),
`src/cairn/dashboard/templates/graph.html` (search box),
`src/cairn/dashboard/static/app.js` (confirm-to-focus wiring).

**Area B — expansion + traversal** (Phase 2: FR-003, FR-005)
Files: `src/cairn/viz/query.py` (neighborhood-for-nodes function; wire or
  formally cap the dead depth param — survey Q3, Q5),
`src/cairn/dashboard/app.py` (neighbors endpoint),
`src/cairn/dashboard/static/app.js` (merge + count updates — survey Q6
pattern).

**Area C — layout + guard** (Phase 3: FR-004, FR-006)
Files: `src/cairn/dashboard/static/app.js` (layout option toggle),
`src/cairn/dashboard/templates/graph.html` (toggle control),
`tests/test_dashboard_readonly.py` (new endpoints join the guard).

- Independent: **C ∥ B** — option-object work vs endpoint work.
- Strictly ordered: **A → B, A → C** — both reuse the search chrome's
  activation conventions and count display.
- Shared-file caution: all three areas touch `app.js` — the file keeps its
  one-IIFE-per-concern structure; tasks serialize on it only where they
  edit the same block (task-breaker marks those, not the whole areas).

## Checkpoints

- **After Phase 1**: searching an exact name focuses its neighborhood
  graph; searching an ambiguous name lists candidates with file/kind
  context; the arbitrary LIMIT-1 pick is no longer reachable from the
  dashboard. Verify: `uv run pytest tests/test_dashboard_data.py
  tests/test_dashboard_app.py -q` (candidates endpoint tests) + manual
  search on the dev store (~2,700 symbols, survey Q8).
- **After Phase 2**: expanding a node adds its neighbors connected to it;
  displayed node/edge counts match the merged view; the cap notice stays
  truthful. Verify: endpoint tests + manual expand chain on the dev store.
- **After Phase 3**: toggling layout re-renders the same node/edge set in
  the chosen style without losing focus; the new endpoints are inside the
  read-only guard. Verify: readonly suite extension green + manual toggle.

## Risks & mitigations
- Risk: repeated expansions blow past comfortable browser limits →
  mitigation: FR-005 count accuracy plus a visible expansion bound; the
  underlying scope caps (LIMIT 30) still apply per fetch (spec's own
  mitigation).
- Risk: touching `get_symbol_graph`'s signature breaks CLI viz consumers
  (survey supporting evidence) → mitigation: additive keyword-only
  changes or a new function; the shared-walk extraction updates both
  callers in one task.
- Risk: disambiguation candidate floods for very common names →
  mitigation: candidates endpoint caps results and groups by file; exact
  match short-circuits.
- Risk: layout toggle loses camera/focus → mitigation: restore camera
  state after option change or re-instantiate with saved positions
  (research RQ2's noted trap).

## Delivery
Branch `feat/ui-dashboard-graph-nav` (or rides the dashboard-v2 train);
pairs with ui-dashboard-cross-links Phase 3 (one neighborhood path). One
PR, one commit per task. Post-merge: `cairn update` + `record_memory`.
