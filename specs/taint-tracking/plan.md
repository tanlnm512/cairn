# Plan: taint-tracking

**Spec**: [spec.md](spec.md) | **Created**: 2026-09-17

## Milestones
<!-- Each milestone = a phase in task.md. -->
| Phase | Milestone | Delivers (demoable) | FRs | Depends on |
|-------|-----------|---------------------|-----|------------|
| 1     | Taint core (sources/sinks config + propagation + `cairn taint`) | On a fixture with a known CWE-pattern flow, `cairn taint --from <src> --to <sink>` prints the full inter-procedural path with a resolution label per hop; exact-only by default, `--fuzzy` opt-in; zero findings on a clean fixture | FR-001, FR-002, FR-003, FR-005 | — |
| 2     | Security warnings in explore + blast | With a diff intersecting a taint path, `cairn blast` and `explore` output a warning badge naming the path; nothing on clean code | FR-004 | Phase 1 |

Riskiest work (propagation false positives, spec §Assumptions & risks) is pulled
into Phase 1 so the precision contract is proven before any surface depends on it.

## Dependencies
- Phase 2 consumes the taint path query Phase 1 produces (paths + per-hop
  resolution labels); without it the warning surfaces have nothing to name.
- Inside Phase 1, the source/sink registry contract (the dataclass/config
  schema both sides agree on) is the only shared artifact; after that,
  config+defaults and the propagation engine proceed concurrently.
- Inside Phase 2, explore-integration and blast-integration are independent
  of each other (disjoint files, evidence below) and run concurrently.
- Substrate is existing, not new work: dataflow index and `transitive_edges`
  closure (survey S1, S2) and `edges.resolution` gating precedent (survey S3).

## Parallelization map
<!-- Which work areas are independent (different files/subsystems, no shared
     state) and can be developed concurrently, and which are strictly
     sequential. The task-breaker turns this into [P] markers per task. -->
- Independent: **A · source/sink config + defaults** ∥ **B · propagation pass + `cairn taint` CLI** —
  disjoint files: A extends workspace config (`src/cairn/graph/config.py`,
  `load_config` :77 with typed `_as_string_list` :137 / `_as_string_dict` :159
  parsers — session grep, not surveyed); B adds a new `src/cairn/graph/taint.py`
  and a new `src/cairn/cli/taint.py` mirroring the existing `cairn dataflow`
  group pattern (`src/cairn/cli/dataflow.py`). B reads only existing tables
  (`edges`, `transitive_edges`) and the A-agreed registry contract.
- Independent: **C · explore warning** ∥ **D · blast warning** — disjoint files:
  C touches `src/cairn/graph/explore.py` + `src/cairn/mcp_server/tools_graph.py`
  (explore builds its own `blast_radius` at explore.py :258/:342 and never
  imports `compute_blast` — session grep); D touches `src/cairn/graph/blast.py`
  (`compute_blast` :471, survey S4) + `src/cairn/cli/blast.py` (sole importer of
  `compute_blast`, cli/blast.py :10/:41 — session grep). To keep C ∥ D disjoint,
  the shared warning formatter ships with the taint core (area B) and both
  surfaces only call it.
- Strictly ordered: **A+B → C, A+B → D** — the surfaces consume what the core
  produces (taint paths intersecting queried/changed symbols, with labels);
  no surface work starts before the core's query output exists and is
  checkpoint-verified.

## Checkpoints
<!-- Exit condition per phase; verify before starting the next. -->
- **After Phase 1**: fixture with a CWE-pattern flow yields the full path from
  `cairn taint --from <src> --to <sink>` with a resolution label on every hop;
  clean fixture yields zero findings; config override replaces a default
  source/sink. Verify: new taint fixture tests pass (`pytest` on the fixture
  suite) and `cairn taint --from ... --to ...` output greps for a resolution
  label per hop.
- **After Phase 2**: with a diff intersecting a taint path, both surfaces emit
  the warning naming the path; clean diff emits none. Verify: surface
  assertions in the fixture tests, plus the survey's own probes still hold
  (`grep -n "def explore" src/cairn/mcp_server/tools_graph.py`,
  `grep -n "def compute_blast" src/cairn/graph/blast.py`).

## Risks & mitigations
- Risk: false positives erode trust (spec risk) → exact-edges-only default
  (FR-003), conservative sink matching, clean-fixture zero-FP gate in the
  Phase 1 checkpoint.
- Risk: call-graph-level precision boundary disappoints (spec assumption) →
  state the boundary in `cairn taint` help/output; no intra-procedural
  modeling in scope.
- Risk: paths longer than the closure cap are missed (`CLOSURE_MAX_DEPTH = 4`,
  survey S2 gap) → document the cap as a known boundary at Phase 1 checkpoint;
  raising it is out of scope unless the checkpoint fails.
- Risk: two warning renderers drift (C vs D) → single shared formatter owned
  by the taint core; surfaces only invoke it.

## Assumptions (not evidenced in survey.md)
- Module placement `src/cairn/graph/taint.py` / `src/cairn/cli/taint.py` is a
  plan assumption; survey only establishes no taint code exists anywhere
  (survey §Supporting evidence).
- Extending `src/cairn/graph/config.py` for taint overrides is cited from this
  session's grep, not survey.md.
- Fixture file locations and harness are left to the task-breaker/test docs.

## Delivery
Team context: none recorded — assumed solo, PR-per-milestone. Branch
`feat/taint-tracking` (spec.md): Phase 1 lands as PR #1, Phase 2 as PR #2;
code + docs together per PR, never per task.
