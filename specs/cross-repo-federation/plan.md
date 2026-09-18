# Plan: cross-repo-federation

**Spec**: [spec.md](spec.md) | **Created**: 2026-09-17

Team context: solo, one PR per milestone (spawn payload default; branch
`feat/cross-repo-federation` per spec.md).

## Milestones
<!-- Each milestone = a phase in task.md. -->
| Phase | Milestone | Delivers (demoable) | FRs | Depends on |
|-------|-----------|---------------------|-----|------------|
| 1     | Federated search end-to-end | One query over every registered store returns merged results with per-repo attribution, via a new CLI command and an MCP tool; lexical fallback covers stores without embeddings; missing/locked/unindexed stores are named in the output | FR-001, FR-004 | — |
| 2     | Cross-repo ask | `cairn ask --all-repos <question>` composes an answer routed across registered stores with per-repo attribution | FR-003 | Phase 1 |
| 3     | Shared embedding backend | Stores sharing a compatible embedding backend are served by one shared backend (optional flag); mixed-backend stores still fuse at rank level | FR-002 | Phase 1 (shared core files); merges before Phase 4 |
| 4     | Regression gate | Full suite plus survey verify commands green; single-store tool behavior unchanged | FR-005 | Phases 1–3 |

Ordering note: Phase 1 is the largest milestone but ships first — cross-store
score comparability is the spec's stated risk, and rank-level fusion must be
proven over fixture stores before the surfaces and ask build on it
(risky-first overrides smallest-first here).

## Dependencies
- Phase 1 → Phase 2: the ask router fans out across "the federation"
  (spec.md What), consuming Phase 1's store enumeration, per-store
  retrieval, and unavailable-store reporting. Plan assumption: ask reuses
  the Phase 1 core rather than a parallel fan-out — verify at tech-spec
  time; if decoupled, Phase 2 still consumes the registry-probe/report helper.
- Phase 1 → Phase 3: Phase 3 modifies the federation core's backend
  selection (same files) — strictly ordered.
- Phase 2 ∥ Phase 3: file-disjoint (cli/ask_context.py + ask routing vs
  federation core + backend wiring). Concurrent is eligible; Phase 2 merges
  first because Phase 3 changes the core Phase 2 consumes. Solo default:
  serial 2 → 3.
- Phase 4 trails all code phases (regression tests read final behavior).
- Existing code touched read-only by Phase 1: `paths.py` `_load_registry`
  (writers: `cairn init` only — caller check, this session),
  `graph/fusion.py` RRF (import), `graph/semantic.py` `semantic_search`
  (wrapped per store). `dashboard/workspaces.py` `enumerate_stores` is the
  multi-store enumeration precedent but is dashboard-owned — not edited.

## Parallelization map
<!-- Which work areas are independent (different files/subsystems, no shared
     state) and can be developed concurrently, and which are strictly
     sequential. The task-breaker turns this into [P] markers per task. -->
- Independent: CLI adapter ∥ MCP adapter — disjoint files (new federated
  search command in cli/query.py or a new cli module vs MCP tool in
  mcp_server/tools_graph.py beside the existing `semantic_search` tool);
  both are thin adapters over the Phase 1 core API and run concurrently
  once that API exists.
- Independent: Phase 2 ask routing ∥ Phase 3 shared backend — disjoint
  files (cli/ask_context.py vs federation core/backend wiring); merge-order
  caveat above.
- Strictly ordered: federation core → CLI ∥ MCP adapters — the core
  produces the fan-out/merge/attribution/report API the adapters consume.
- Strictly ordered: Phase 1 core → Phase 3 — same module; Phase 3 edits the
  default backend path Phase 1 establishes.
- Strictly ordered: all code areas → Phase 4 regression gate.

Serial spine: federation core → (CLI ∥ MCP adapters) → ask --all-repos →
shared-backend option → regression gate.

## Checkpoints
<!-- Exit condition per phase; verify before starting the next. -->
- **After Phase 1**: a federated query over fixture stores (≥2 with
  embeddings or lexical indexes; plus 1 missing, 1 locked, 1 unindexed)
  returns merged, per-repo-attributed results and names every dropped store.
  Verify: new federation test suite over the multi-store fixture harness
  (hermetic-store precedent: tests/test_hermetic_stores_root.py) + the new
  CLI command (name per tech-spec) against fixture stores;
  `grep -n "REGISTRY_FILE\|def register_workspace" src/cairn/paths.py`
  (survey S1) and
  `grep -rn "RRF\|reciprocal" src/cairn/graph/fusion.py src/cairn/graph/semantic.py | head -5`
  (survey S3) still pass.
- **After Phase 2**: `cairn ask --all-repos "<question>"` over fixture
  stores returns one answer with per-repo attribution; single-store
  `cairn ask` output unchanged. Verify: ask fixture tests +
  `grep -n "all-repos" src/cairn/cli/ask_context.py`.
- **After Phase 3**: with ≥2 stores on a compatible shared backend, the
  federation serves them from one backend instance; a mixed-backend set
  still returns rank-fused attributed results. Verify: shared-backend
  fixture test + Phase 1 federation tests unchanged.
- **After Phase 4**: full existing suite plus all new suites green; survey
  verify commands S1–S4 pass; single-store commands produce unchanged
  output on a single-store workspace. Verify: `pytest` full run + survey.md
  verify commands.

## Risks & mitigations
- Risk: score incomparability across stores with different embedding
  backends → rank-level RRF fusion, the approach the hybrid stack already
  uses (survey S3); raw scores are never merged across stores.
- Risk: a missing/locked/unindexed store degrades results silently →
  FR-004 contract: probe every registry store and name every dropped store
  in query output; fixtures cover all three states.
- Risk: ask routing assumed to reuse the Phase 1 core (plan assumption
  above) → verify at tech-spec; worst case Phase 2 consumes only the
  registry-probe/report helper and derives its own routing.
- Assumption: registered workspaces are trusted; no cross-store
  authorization in this spec (spec.md).

## Delivery
Branch `feat/cross-repo-federation` (spec.md). Solo: one PR per milestone,
in dependency order (Phase 2 before Phase 3 if both are in flight). Within
a milestone, code + tests + docs land together, never per task. Each PR
follows the repo shipping workflow (branch → pre-commit → conventional
commit → PR audit checklist → CI → post-merge `cairn update`).
