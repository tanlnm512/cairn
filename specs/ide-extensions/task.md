# Tasks: ide-extensions

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
Status reflects code state per [survey.md](survey.md), not intent.
All tasks open: survey S1–S3 are DONE (dashboard HTTP stack, SSE-daemon
precedent, graph/compass query layer already exist); each task's work is the
gap its item names (no editor-facing JSON API, no editor consumer).
**Before-audit**: pending — the orchestrator writes `passed @ <sha>` here

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| 1     | 4     | 0    |
| 2     | 2     | 0    |
| 3     | 1     | 0    |
| 4     | 2     | 0    |
| 5     | 3     | 0    |
| **Σ** | 12    | 0    |

## Phase 1: Zero-config spine (FR-003, FR-004)
<!-- Checkpoint: on an indexed repo the new editor endpoints answer JSON for callers/callees/blast radius, compass, and memory; the installed extension shows the green indicator with zero manual config and the degraded indicator with the server stopped, editor responsive throughout. Verify: `ls src/cairn/dashboard/routes/` (new route module present) and `grep -n "DEFAULT_PORT" src/cairn/mcp_server/lifecycle.py` (the lifecycle precedent the extension's server start must not collide with). -->
- [ ] T002 [P] Pin the frozen editor JSON contract with contract tests (FR-003, FR-006)
  * Touches: new `tests/test_dashboard_editor_contract.py` — nothing else.
  * Pins the three endpoint paths + JSON response shapes against
    `create_app` (`src/cairn/dashboard/app.py`): symbol endpoint (callers
    count + callees count + depth-limited precise blast radius), file
    endpoint (module compass excerpt + relevant memories), status endpoint
    (indexed-store + server state). This file IS the D-004 freeze: the
    parity surface FR-006 targets and the extension repo's API.
  * Failing-test-first (C-02): lands red; T001 is done only when it passes.
- [ ] T001 (after T002) Implement the editor JSON API route module (FR-003)
  * Consumes: T002's `tests/test_dashboard_editor_contract.py` (shapes it
    must satisfy). Touches: new `src/cairn/dashboard/routes/editor.py`;
    one `editor.register(routes, context)` line in `create_app`
    (`src/cairn/dashboard/app.py`) — the pattern core/graph/history/
    memory/knowledge/wiki/settings already follow.
  * GET/read-only only — `test_full_route_pass_leaves_db_byte_identical`
    (tests/test_dashboard_readonly.py) sweeps every new route. No
    module-level server-stack or `cairn.mcp_server` imports (hermetic-import
    guards). Handlers call the traversal/data layer directly:
    `get_callers`/`get_callees`/`impact_analysis` shapes for the symbol
    endpoint, the `get_compass` layer for the file endpoint (D-002, no new
    Python deps). Serves the FR-001/FR-002/FR-004 surfaces; this is the
    only in-repo code of the whole spec.
  * Verify before implementing: `ls src/cairn/dashboard/routes/` and
    `grep -n "^def get_callers\|^def get_callees\|^def impact_analysis" src/cairn/mcp_server/tools_graph.py`.
- [ ] T003 [P] Scaffold the VS Code extension shell: activate, spawn/attach server, status item (FR-003)
  * New extension repo — no IDE code exists in-repo (survey supporting
    evidence). Touches: `src/extension.ts` (activation),
    `src/server.ts` (spawn/attach + health-check), `src/status.ts`
    (status-bar item).
  * On activation: health-check `127.0.0.1:8765`; if absent, spawn
    `cairn dashboard` with cwd = workspace root as a child process and poll
    until the status endpoint answers (D-003: attach-or-start, never
    blind-bind; one server per workspace root; no daemon registration, no
    MCP config). Green indicator when served + indexed. Ships its test
    cases (C-02) against recorded spawn/attach behaviors.
  * Contract source: the endpoint paths + shapes T002 pins in
    tests/test_dashboard_editor_contract.py (read-only consumer; the two
    tasks stay parallel per plan.md's API ∥ shell split).
  * Verify before implementing: `grep -n "DEFAULT_PORT" src/cairn/mcp_server/lifecycle.py` (the port that must NOT collide) and `grep -n "DEFAULT_PORT" src/cairn/dashboard/app.py`.
- [ ] T004 (after T003) Implement graceful degradation in the shell status layer (FR-004)
  * Consumes: T003's `src/server.ts` health-check state and
    `src/status.ts` status-bar item — extends them, shared files, hence
    chained. Touches those + the degraded-state export other surfaces read.
  * Server stopped or workspace unindexed → non-intrusive degraded
    indicator; editor never blocked; degradation derived from the one
    health-check, consumed by all surfaces (no per-provider logic).
  * Ships its test cases (C-02): stopped-server and unindexed paths render
    the indicator and never throw.

## Phase 2: Inline blast radius (FR-001)
<!-- Checkpoint: hover and code-lens render counts plus a depth-limited radius for a known symbol; with the server stopped they render the indicator only. Verify: `grep -n "^def get_callers\|^def get_callees\|^def impact_analysis" src/cairn/mcp_server/tools_graph.py` — the API reuses this query layer, not a fork. -->
- [ ] T005 [P] Implement the hover provider (FR-001)
  * Touches: new `src/providers/hover.ts`. Consumes: the shell's HTTP
    client + degraded-state export (T003/T004); the symbol-endpoint shape
    pinned in T002's `tests/test_dashboard_editor_contract.py`.
  * On symbol hover: one symbol-endpoint request; render caller/callee
    counts + the depth-limited precise blast radius from the response —
    depth is bounded in the request, never client-side. Unknown symbols
    render nothing, never an error surface. Ships its test cases (C-02)
    against fixture responses.
  * Verify before implementing: `grep -n "^def get_callers\|^def get_callees\|^def impact_analysis" src/cairn/mcp_server/tools_graph.py`.
- [ ] T006 [P] Implement the code-lens provider (FR-001)
  * Touches: new `src/providers/codeLens.ts`. Consumes: the same shell
    client/degraded export as T005 and the same one symbol endpoint (same
    response T005 renders — no second endpoint, no per-provider requests
    when degraded).
  * Render caller/callee counts above symbols; unknown symbols render no
    lens. Ships its test cases (C-02) against fixture responses.
  * Verify before implementing: `grep -n "^def get_callers\|^def get_callees\|^def impact_analysis" src/cairn/mcp_server/tools_graph.py`.

## Phase 3: Compass & memory panel (FR-002)
<!-- Checkpoint: opening a file with compass/memory content renders both in the panel. Verify: `grep -n "def get_compass" src/cairn/mcp_server/tools_compass.py` — the endpoint reuses it. -->
- [ ] T007 [P] Implement the compass/memory panel for the active file (FR-002)
  * Touches: new `src/panel/compass.ts` + its registration in
    `src/extension.ts`. Consumes: the shell's HTTP client + degraded-state
    export (T003/T004); the file-endpoint shape pinned in T002's
    `tests/test_dashboard_editor_contract.py`.
  * On active-file change: one file-endpoint request; render the module
    compass excerpt + relevant memories; empty content = empty panel, not
    an error. Throttle rapid file switches: one in-flight request, latest
    wins. Ships its test cases (C-02) against fixture responses.
  * Verify before implementing: `grep -n "def get_compass" src/cairn/mcp_server/tools_compass.py`.

## Phase 4: Packaging & install (FR-005)
<!-- Checkpoint: the built artifact installs on a clean machine via manual vsix install; marketplace metadata complete. -->
- [ ] T008 Complete extension packaging metadata for the vsix build (FR-005)
  * Touches: extension repo `package.json` (publisher-less vsix metadata,
    activation events, contributions, repository/readme/icon for the
    listing) + the vsix build config. In-repo wheel untouched — the editor
    module is JSON-only, no new package-data.
  * D-001: VS Code first; vsix packaging needs no publisher account.
  * Verify before implementing: `python -m pytest tests/test_dashboard_packaging.py`.
- [ ] T009 (after T008) Add release CI and exercise manual vsix install on a clean machine (FR-005)
  * Consumes: T008's manifest metadata (the vsix build inputs); shares the
    packaging files, hence chained. Touches: release CI workflow + install
    docs (marketplace listing + the always-available manual
    `--install-extension` path).
  * Done when: the built vsix installs and activates on a clean machine via
    the manual path.

## Phase 5: JetBrains parity (FR-006)
<!-- Checkpoint: the JetBrains plugin builds and demos hover/lens, compass/memory, and degraded states on a real repo. -->
- [ ] T010 Scaffold the JetBrains plugin with server attach and status parity (FR-006)
  * Touches: new JetBrains plugin project (separate repo per FR-005/D-001
    sequencing): plugin descriptor, server attach/health-check with D-003
    semantics (attach-or-start, per-workspace, no daemon), status bar +
    degraded indicator. Consumes: the frozen D-004 contract — same three
    endpoints at `127.0.0.1:8765`; shapes read from T002's
    `tests/test_dashboard_editor_contract.py`.
  * Verify before implementing: `grep -n "DEFAULT_PORT" src/cairn/dashboard/app.py`.
- [ ] T011 [P] (after T010) Port hover and code-lens providers to JetBrains parity (FR-006)
  * Touches: the plugin's hover + code-lens provider sources (new files).
    Consumes: T010's client + status interfaces; the one symbol endpoint
    (same response the VS Code providers render); unknown symbols render
    nothing; no requests when degraded.
  * Verify before implementing: `grep -n "^def get_callers\|^def get_callees\|^def impact_analysis" src/cairn/mcp_server/tools_graph.py`.
- [ ] T012 [P] (after T010) Port the compass/memory panel to JetBrains parity (FR-006)
  * Touches: the plugin's tool-window panel source (new file). Consumes:
    T010's client + status interfaces; the file endpoint; empty content =
    empty panel; one in-flight request, latest wins.
  * Verify before implementing: `grep -n "def get_compass" src/cairn/mcp_server/tools_compass.py`.

## Conventions
- `- [ ]` todo · `(in-progress)` claimed · `- [x]` done + proof note:
      `done <date> — <test/command that proves it>`
- Dropped: `- [ ] ~~T004~~ dropped <date> (D-###)` — never delete the line;
  dropped tasks stay visible with the decision that killed them
- `[P]` = parallelizable (default — no shared files, no upstream task);
  chained tasks note `(after T###)` and name the exact interface they
  consume from their upstream — symbols, signatures, file formats; serial
  runs need a reason, parallel runs need none
- Fix rounds append `(fix <n>/5)` to the entry — the cap survives resume
  only if the count lives here, in the status holder. From round 2 on, an
  implementer's scratch note (what was tried, why it failed) may live at
  `notes/T###.md` — the one file an implementer may write under specs/,
  never read by check.py, never counted as status
- Every task cites its FR-###; tasks with no FR are scope creep — fix the
  spec first
