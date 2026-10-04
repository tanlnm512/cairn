# Plan: on-demand-paths

**Spec**: [spec.md](spec.md) | **Created**: 2026-10-04

## Milestones
<!-- Each milestone = a phase in task.md. -->
| Phase | Milestone | Delivers (demoable) | Requirements | Depends on |
|-------|-----------|---------------------|--------------|------------|
| 1     | Path query core + CLI | `cairn path --from A --to B` prints the shortest ordered hop chain on a closure-free store; taint behavior unchanged | FR-001, FR-003, FR-007, NFR-003 | — |
| 2     | MCP parity | `path` tool returns the CLI's chain over stdio/SSE/HTTP; tool count reads 26 everywhere it is pinned | FR-002 | Phase 1 |
| 3     | Closure opt-in | Default `cairn build`/`import-scip`/`update` do zero closure work; `--with-closure` output byte-identical | FR-004, FR-006 | — (parallel with Phase 1) |
| 4     | Impact depth contract | Closure-free `impact_analysis` reports shortest-path depths matching closure-era output | FR-005 | Phase 3 |

Phase 1 first: the shared-BFS-core refactor is the spec's riskiest assumption
(taint tests must stay green untouched), so it lands and is gated before
anything builds on it. Phase 3 is file-disjoint from the walk spine and runs
concurrently. Phase 4's pin re-point targets the closure-absent default that
Phase 3 creates.

## Dependencies
- Phase 1 → Phase 2: the MCP `path` tool and the tool-count bump consume the
  Phase-1 walk function signature and the registered CLI/tool surface.
- Phase 3 → Phase 4: the parity pins re-point at the DFS min-depth contract
  "when the closure is absent" — the absent-by-default posture is what
  Phase 3 ships; the min-depth implementation itself is file-disjoint and can
  be developed during Phase 3.
- No other coupling: the walk spine (`src/cairn/graph/taint.py`,
  `src/cairn/cli/paths.py`, `src/cairn/mcp_server/tools_graph.py`) and the
  demotion (`src/cairn/cli/core.py`) share no files.

## Parallelization map
<!-- Which work areas are independent (different files/subsystems, no shared
     state) and can be developed concurrently, and which are strictly
     sequential. The task-breaker turns this into [P] markers per task. -->
- Independent: walk spine (graph BFS variant + CLI command + MCP tool +
  count/docs pins + path perf gate) ∥ closure demotion (build/import-scip
  flags) — disjoint files: `graph/taint.py`, `cli/paths.py`, `cli/__init__.py`,
  `mcp_server/*`, `bench/scaling_suite.py`, doc pins vs `cli/core.py`; the
  demotion gates two call sites and touches no walk code.
- Independent: path perf gate (`bench/scaling_suite.py`,
  `tests/test_scaling_gate.py`) ∥ MCP tool (`mcp_server/tools_graph.py`) —
  disjoint files, both consume only the Phase-1 walk function.
- Strictly ordered: BFS core variant → CLI command → MCP tool → count-pin
  bump — each step consumes the prior's public surface (walk function
  signature; registered tool name; actual tool count 26 that
  `verify_tool_count` and the installer-blurb cross-check pin).
- Strictly ordered: closure demotion → parity-pin re-point — the re-pointed
  pins assert behavior on closure-absent stores, which is the default the
  demotion creates.

## Checkpoints
<!-- Exit condition per phase; verify before starting the next. -->
- **After Phase 1**: a `cairn path --from <name> --to <name>` query on a
  closure-free store prints one shortest hop chain with file:line per hop and
  a "no path within N hops" miss path; `python3 -m pytest tests/ -k taint -q`
  green untouched; `python3 -m pytest tests/test_path_query.py -q` green.
- **After Phase 2**: `python3 -m pytest tests/test_status_resource_health.py
  tests/test_agent_surface.py tests/test_mcp_path_tool.py -q` green — count
  pins read 26 in server, tests, and installer blurb together; the `path`
  tool returns the same chain the CLI prints.
- **After Phase 3**: default build leaves `transitive_edges` empty and a
  following `cairn update` performs no closure maintenance; `cairn build
  --with-closure` row set matches a direct `build_transitive_closure` run;
  `python3 -m pytest tests/test_closure_opt_in.py tests/test_incremental_derived.py
  tests/test_workflow_audit_fixes.py -q` green.
- **After Phase 4**: closure-free `impact_analysis` depth numbers equal
  closure-era shortest-path depths on the parity corpus;
  `python3 -m pytest tests/test_traversal_parity.py -q` green; full sweep
  `python3 -m pytest tests/test_scaling_gate.py tests/test_traversal_parity.py
  tests/test_incremental_derived.py -q -m "not infra"` green.

## Risks & mitigations
- Risk: shared-BFS refactor regresses taint → mitigation: Phase 1 lands the
  variant first and the taint suite (`python3 -m pytest tests/ -k taint -q`)
  is its explicit gate; taint tests stay untouched (spec assumption).
- Risk: fuzzy paths explode on large fan-out graphs → mitigation: the depth
  bound caps traversal and the printed-path cap bounds output (FR-001/FR-003).
- Risk: demotion flips characteristics the traversal-parity and budget pins
  assert → mitigation: pin updates land in the same phase as the flip, never
  silently; the scaling gate times `build_transitive_closure` directly and is
  unaffected by the CLI flip.
- Risk: demotion contradicts scip-indexing-v2 D-007 (closure default-built) →
  mitigation: a superseding decision (D-004 in tech-spec.md) records the
  measured cost and verified fallback coverage; the closure stays available
  and byte-identical under the flag.

## Delivery
Solo, branch `feat/on-demand-paths`, one PR per phase (4 PRs), each landing
its phase's code + tests + docs together — never per task. Docs pins
(README, AGENTS.md, docs/, installer blurb) move in the same PR as the count
bump they pin. After merge: `cairn update` + memory capture per workspace
rules.
