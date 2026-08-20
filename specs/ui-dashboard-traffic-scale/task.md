# Tasks: ui-dashboard-traffic-scale

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
Status reflects code state per [survey.md](survey.md), not intent.

## Burndown
| Phase | Total | Done |
|-------|-------|------|
| 1     | 4     | 4    |
| 2     | 3     | 3    |
| 3     | 4     | 4    |
| **Σ** | 11    | 11   |

## Phase 1: Indexed + paginated history (FR-001, FR-006)
<!-- Checkpoint: bounded first page on a large store; stable repeat-free
     paging; no LIMIT-free history query remains. -->
- [x] T001 Add the composite index on tool_metrics(invoked_at, id) via the idempotent executescript in `src/cairn/graph/schema.py` (FR-001)
      done 2026-08-20 — grep shows the index line beside the existing pair; fresh-DB PRAGMA index_list/index_info confirms idx_tool_metrics_invoked (invoked_at, id); cross-process reconnect re-adds it after drop
- [x] T002 Convert `list_history` in `src/cairn/dashboard/data.py` to keyset paging: ORDER BY invoked_at DESC, id DESC with a before-cursor and bounded page size, returning the next cursor (FR-001)
      done 2026-08-20 — page-walk checks: 3-page forward walk no repeats, backward walk retraces, tie-break, unparseable-cursor tolerance; tests/test_dashboard_data.py keyset tests green
- [x] T003 Accept the cursor param in the history handler of `src/cairn/dashboard/app.py` and render prev/next controls carrying tool/session/window params in `src/cairn/dashboard/templates/history.html` (FR-001, FR-006)
      done 2026-08-20 — uv run pytest tests/test_dashboard_app.py -q: 23 passed incl. the 4 previously-broken history-route tests UNCHANGED; Newer/Older links carry tool/session/window
- [x] T004 Add pagination tests to `tests/test_dashboard_data.py`: bounded page, cursor stability under mid-paging inserts, no row repeats (FR-001, FR-006)
      done 2026-08-20 — uv run pytest tests/test_dashboard_data.py -q green: 5 updated + 7 new pagination tests (bounded page, mid-walk insert stability, full-walk coverage, backward retrace, tie-break, cursor tolerance, filter composition)

## Phase 2: Time windows everywhere (FR-002, FR-003)
<!-- Checkpoint: the window control narrows history, chains, and token
     aggregates to the selected slice. -->
- [x] T005 Add the shared window-control partial (24h/7d/30d/all) in `src/cairn/dashboard/templates/` and thread the window param through the history, tokens, and chains handlers in `src/cairn/dashboard/app.py` (FR-002)
      done 2026-08-20 — uv run pytest tests/test_dashboard_app.py -q green; partial renders on all three routes; window=bogus falls back to all; links preserve filters/cursors
- [x] T006 Add the since predicate to `list_history`, `get_tool_tokens`, and the chains query in `src/cairn/dashboard/data.py`, composed in the shared WHERE builder (FR-002, FR-003)
      done 2026-08-20 — 37 existing data tests pass unchanged with since=None; scratch verification covered windowed pages, ranking flips, ghost-session vanishing, empty window
- [x] T007 Add window tests: exclusion of outside rows in all three views and window-scoped recomputation of per-tool aggregates (FR-002, FR-003)
      done 2026-08-20 — uv run pytest tests/test_dashboard_data.py tests/test_dashboard_app.py -q: 68 passed (5 data + 3 route window tests added)

## Phase 3: Bounded chains + render budget (FR-004, FR-005)
<!-- Checkpoint: the giant-session fixture renders bounded with honest
     counts and expand; the 10k-store budget test passes. -->
- [x] T008 Bound `get_session_chains` in `src/cairn/dashboard/data.py`: newest-chains-first cap, per-chain head cap, shown/total metadata, chain-expand tail fetch (FR-004)
      done 2026-08-20 — acceptance script: giant session renders 25 of 200 with truncated_calls; expand="unknown" returns all 200; chain-list cap keeps newest
- [x] T009 Render the bound affordances and shown-of-total counts in `src/cairn/dashboard/templates/chains.html` (FR-004)
      done 2026-08-20 — uv run pytest tests/test_dashboard_app.py -q: 26 passed; smoke-verified "showing 20 of 22 chains", "showing 25 of 30 calls", expand href carries window
- [x] T010 Add the legacy all-unknown-session fixture and bound tests to `tests/test_dashboard_data.py` (FR-004)
      done 2026-08-20 — uv run pytest tests/test_dashboard_data.py -q: 46 passed (5 chain tests updated to wrapper + 4 bound tests incl. legacy unknown-session cap)
- [x] T011 Add the synthesized 10k-row store and the first-render budget test (strict 2s locally, structural bounds in CI) for the three traffic routes (FR-005)
      done 2026-08-20 — CAIRN_SCALE_STRICT=1 uv run pytest tests/test_dashboard_scale.py: 5 passed (10.5k-row store, per-route strict <2s, structural bounds ungated)

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
