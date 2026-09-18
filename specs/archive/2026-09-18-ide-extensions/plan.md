# Plan: ide-extensions

**Spec**: [spec.md](spec.md) | **Created**: 2026-09-17

## Milestones
<!-- Each milestone = a phase in task.md. -->
| Phase | Milestone | Delivers (demoable) | FRs | Depends on |
|-------|-----------|---------------------|-----|------------|
| 1 | Zero-config spine | The vsix-installed VS Code extension starts or attaches the local cairn server on an indexed workspace with zero manual MCP/CLI config (green status indicator); with the server stopped or the workspace unindexed it degrades to a non-blocking status indicator, editor unaffected. Includes the editor-facing JSON API on the existing dashboard HTTP stack. | FR-003, FR-004 | — |
| 2 | Inline blast radius | Hovering a known symbol renders caller/callee counts and a depth-limited precise blast radius; code-lens renders the counts; degraded states render the status indicator only (US1 AC1 + AC2). | FR-001 | Phase 1 |
| 3 | Compass & memory panel | Opening a file whose module has compass/memory content renders the compass excerpt and relevant memories in a panel (US2 AC1). | FR-002 | Phase 1 |
| 4 | Packaging & install | VS Code package ready for the marketplace plus the always-available manual vsix install path, exercised on a clean machine. | FR-005 | Phases 2, 3 |
| 5 | JetBrains parity | The second-priority extension (per FR-005's ruling) reaches FR-001–FR-004 parity on JetBrains. Deferred in spec scope ("scoped, not forgotten") — sequenced last, not dropped. | FR-006 | Phase 4 |

## Dependencies
- Phase 1 → Phases 2 and 3: the editor surfaces build inside the Phase-1 extension shell and consume its activation path, HTTP client, server lifecycle, and status/degradation infrastructure; both consume the Phase-1 JSON API contract.
- Phases 2 and 3 are mutually independent — order between them is free.
- Phase 4 packages the feature-complete extension; Phase 5 reuses the stabilized API contract and the UX reference.

## Parallelization map
<!-- Which work areas are independent (different files/subsystems, no shared
     state) and can be developed concurrently, and which are strictly
     sequential. The task-breaker turns this into [P] markers per task. -->
- Independent: server-side editor API ∥ extension shell — the API is Python
  under `src/cairn/dashboard/routes/` plus one `register(routes, context)`
  line in `src/cairn/dashboard/app.py` (the uniform pattern `memory`,
  `knowledge`, `wiki`, `settings` already follow); the shell lives in a
  separate extension codebase — no IDE code exists in this repo (survey
  supporting evidence: proposal §6.2 places extensions in separate
  repositories). Disjoint files and languages; the only shared artifact is
  the endpoint contract, frozen before both proceed. `cairn impact serve`
  returns no impacted symbols — the server-lifecycle area is not coupled to
  the graph layer.
- Independent: hover + code-lens providers (FR-001 surface) ∥ compass/memory
  panel (FR-002 surface) — disjoint extension files; both depend only on the
  shell's client and status interfaces.
- Strictly ordered: Phase 1 shell → Phases 2/3 providers — providers import
  the shell's activation path, HTTP client, and status indicator; same
  codebase, shared files.
- Strictly ordered: Phases 2/3 → Phase 4 packaging — packaging owns
  `package.json` metadata and release CI that the feature phases also touch,
  and consumes their built artifact.
- Strictly ordered: Phase 4 → Phase 5 — parity work targets the frozen
  contract and proven UX; spec defers the second IDE.

## Checkpoints
<!-- Exit condition per phase; verify before starting the next. -->
- **After Phase 1**: on an indexed repo the new editor endpoints answer JSON
  for callers/callees/blast radius, compass, and memory; the installed
  extension shows the green indicator with zero manual config and the
  degraded indicator with the server stopped, editor responsive throughout.
  Verify: `ls src/cairn/dashboard/routes/` (new route module present;
  survey S1 verify) and `grep -n "DEFAULT_PORT"
  src/cairn/mcp_server/lifecycle.py` (lifecycle precedent the extension's
  server start follows; survey S2 verify).
- **After Phase 2**: hover and code-lens render counts plus a depth-limited
  radius for a known symbol; with the server stopped they render the
  indicator only. Verify: `grep -n "^def get_callers\|^def get_callees\|^def
  impact_analysis" src/cairn/mcp_server/tools_graph.py` (survey S3 verify) —
  the API reuses this query layer, not a fork.
- **After Phase 3**: opening a file with compass/memory content renders both
  in the panel. Verify: `grep -n "def get_compass"
  src/cairn/mcp_server/tools_compass.py` (survey S3 evidence) — the endpoint
  reuses it.
- **After Phase 4**: the built artifact installs on a clean machine via
  manual vsix install; marketplace metadata complete.
- **After Phase 5**: the JetBrains plugin builds and demos hover/lens,
  compass/memory, and degraded states on a real repo.

## Risks & mitigations
- Risk: packaging and marketplace processes differ per IDE (spec risk) →
  mitigation: one IDE first (FR-005); manual vsix install exercised from the
  Phase-1 checkpoint onward; packaging decision recorded as a D-###.
- Risk: the editor API over the existing dashboard stack proves insufficient
  (spec assumption) → mitigation: Phase 1 proves the contract end-to-end
  before editor features build on it; survey records both pieces it needs —
  the dashboard HTTP stack (S1) and the query layer (S3: `get_callers`,
  `get_callees`, `impact_analysis` in `src/cairn/mcp_server/tools_graph.py`,
  `get_compass` in `tools_compass.py`).
- Risk: no in-repo extension precedent (survey: no IDE extension code
  anywhere) → mitigation: the Phase-1 shell is deliberately thin — activate,
  start/attach server, status — surfacing tooling and packaging unknowns
  earliest.

## Assumptions
- The editor JSON API reuses the dashboard starlette stack with no new
  Python deps (survey supporting evidence); existing JSON endpoints
  (`/graph/neighbors`, `/graph/inspect` in `src/cairn/dashboard/routes/graph.py`)
  serve the dashboard UI, not the editor contract — the S1 gap stands.
- The extension codebase is a separate repository (survey, proposal §6.2);
  plan phases name areas, not repo locations.
- Team context: none recorded — solo, PR-per-milestone (spawn payload
  default).

## Delivery
Branch per milestone off `feat/ide-extensions` (spec branch), one
conventional-commit PR per milestone through the full shipping procedure;
docs updates ride the PR that lands them. Phase 5 starts its own
branch/PR cadence when begun.
