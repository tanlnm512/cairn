# Tasks: on-demand-paths

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
**Lifecycle**: v2
Status reflects code state per [survey.md](survey.md), not intent.
**Delivered**: pending — delivery evidence; the orchestrator writes `commit @ <sha>` here

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| 1 | 3 | 3 |
| 2 | 2 | 2 |
| 3 | 1 | 1 |
| 4 | 1 | 1 |
| **Σ** | 7 | 7 |

## Phase 1: Path query core + CLI (FR-001, FR-003, FR-007, NFR-003)
<!-- Checkpoint: cairn path prints one shortest hop chain on a closure-free
     store; taint suite green untouched; tests/test_path_query.py green. -->
- [x] T001 (implemented) [P] Add the symbol-id-seeded shortest-path walk beside taint's BFS core, failing-test-first (FR-001, FR-003, FR-007)
  - done 2026-10-05 — done 2026-10-05 — pytest tests/test_path_query.py -q green (19 tests incl. rebuild-parity); walk contract consumed verbatim by T002/T004
  - Interface produced: one public walk function in `src/cairn/graph/taint.py` taking resolved seed-id sets + `fuzzy` + `max_depth`, returning one shortest path per (from, to) pair in deterministic order (tech-spec D-001/D-002) — the contract T002/T003/T004 consume.
  - Touches:
    - `src/cairn/graph/taint.py`
    - `tests/test_path_query.py`
    - `tests/fixtures/on-demand-paths/` (orchestrator-ruled shared fixture tree; files: `provision.sh`)
    - `tests/fixtures/on-demand-paths/chain/chain.py`, `tests/fixtures/on-demand-paths/islands/islands.py`, `tests/fixtures/on-demand-paths/fork/fork.py`, `tests/fixtures/on-demand-paths/fanout/fanout.py`, `tests/fixtures/on-demand-paths/fuzzy/fuzzy.py`, `tests/fixtures/on-demand-paths/fuzzy/fuzzy_alt.py`, `tests/fixtures/on-demand-paths/fuzzy/fuzzy_main.py`, `tests/fixtures/on-demand-paths/update/update.py`, `tests/fixtures/on-demand-paths/depth/depth.py`
- [x] T002 (implemented) Add the `cairn path --from --to` CLI command mirroring taint's endpoint contract (after T001 — consumes T001's public walk function: seed-id sets + fuzzy + max_depth signature from `src/cairn/graph/taint.py`) (FR-001, FR-003)
  - done 2026-10-05 — done 2026-10-05 — TC-001..TC-009 + TC-018 shell runs green (proofs 22/22); pytest tests/test_path_query.py -q 19 passed
  - Touches:
    - `src/cairn/cli/paths.py`
    - `src/cairn/cli/__init__.py`
    - `tests/test_path_query.py`
    - `docs/cli-reference.md`
- [x] T003 (implemented) Add an infra-marked perf gate: max-depth-4 path query on the 1000-file corpus under 2s wall (after T001 — consumes T001's public walk function to drive the timed query) (NFR-003)
  - done 2026-10-05 — done 2026-10-05 — pytest tests/test_scaling_gate.py -q -m infra green (path gate 0.398s vs 2.0 budget); TC-019 green
  - Touches:
    - `src/cairn/bench/scaling_suite.py`
    - `tests/test_scaling_gate.py`

## Phase 2: MCP parity (FR-002)
<!-- Checkpoint: count pins read 26 in server, tests, and installer blurb
     together; the path tool returns the CLI's chain. -->
- [x] T004 (implemented) Add the MCP `path` tool delegating to the walk function (after T001 — consumes T001's public walk function signature from `src/cairn/graph/taint.py`) (FR-002)
  - done 2026-10-05 — done 2026-10-05 — pytest tests/test_mcp_path_tool.py -q 5 passed; output byte-parity with CLI pinned
  - Touches:
    - `src/cairn/mcp_server/tools_graph.py`
    - `tests/test_mcp_path_tool.py`
- [x] T005 (implemented) Bump `_EXPECTED_TOOL_COUNT` 25→26 and move every count pin in lockstep (after T004 — consumes T004's registered `path` tool name that `verify_tool_count` counts) (FR-002)
  - done 2026-10-05 — done 2026-10-05 — count pins 26 across server/README/AGENTS/docs/skill files; test_agent_surface.py + test_status_resource_health.py green (D-011 sites)
  - Touches:
    - `src/cairn/mcp_server/server.py`
    - `src/cairn/agent_install/_common.py`
    - `README.md`
    - `AGENTS.md`
    - `docs/architecture.md`
    - `docs/mcp-tools.md`
    - `tests/test_status_resource_health.py`

## Phase 3: Closure opt-in (FR-004, FR-006)
<!-- Checkpoint: default build/update do zero closure work; --with-closure
     row set matches a direct build_transitive_closure run. -->
- [x] T006 (implemented) [P] Add `--with-closure` to build and import-scip and demote both call sites, pinning default-empty / flag-on-byte-identical / update-skips, failing-test-first (FR-004, FR-006)
  - done 2026-10-05 — done 2026-10-05 — pytest tests/test_closure_opt_in.py -q 6 passed 0 xfailed; TC-012..TC-016 shell green; D-008 gate + D-013 incremental-dataflow restore
  - Touches:
    - `src/cairn/cli/core.py`
    - `tests/test_closure_opt_in.py`

## Phase 4: Impact depth contract (FR-005)
<!-- Checkpoint: closure-free impact depth numbers equal closure-era
     shortest-path depths; full parity sweep green. -->
- [x] T007 (implemented) Add min-depth tracking to the live DFS and re-point the parity pins at the closure-free contract (after T006 — consumes T006's default: fresh stores have an empty `transitive_edges`, which the closure-free fixtures mirror) (FR-005)
  - done 2026-10-05 — done 2026-10-05 — pytest tests/test_traversal_parity.py -q green incl. closure-free depth-parity owner; TC-017 diff green (closure-on == closure-off)
  - Touches:
    - `src/cairn/graph/traversal.py`
    - `tests/test_traversal_parity.py`

## Conventions
- `- [ ]` todo · `(in-progress)` claimed · `(implemented)` landed —
      implementation evidence durable before the one all-at-once tick ·
      `- [x]` done + proof note: `done <date> — <test/command that proves it>`
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
- Every task cites FR-### or an applicable NFR-###; a task with neither is
  scope creep — fix the spec first
- Every code task carries a `Touches:` block (repo-relative files,
  directories, or globs); `[P]` tasks whose touches overlap are chained, not
  spawned together
