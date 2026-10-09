# Spec: exact-edge-precision

**Status**: draft
**Effort**: large
**Created**: 2026-10-09
**Branch**: TBD

## What

Cut the ambiguous-edge mass of cairn's two heaviest languages — Python and
TypeScript — by upgrading name-only call edges to `exact` resolution:
SCIP indexers first (`scip-typescript`; the scip-indexing-v2 overlay
groundwork), the pyright LSP pass where SCIP cannot reach (Python's
stdlib/vendored corners, `--lsp` today opt-in).

## Why

Measured baseline (2026-10-09, published in `docs/benchmarks.md` —
"Resolution precision baseline"): Python carries 4,584 ambiguous edges on
the self store (9.4% of 48,878) and 213 on the t2 corpus (7.7%);
TypeScript is second at 25 (3.4%). Ambiguous edges are excluded from
precise blast radius (`get_callers`, `impact_analysis` default), so every
upgraded edge directly widens the trusted-impact surface for the two
languages agents use most.

## Business value

Success metric: the measured exact-ratio delta published in
`docs/benchmarks.md` for both languages, with precise-impact coverage
(blast radius) re-measured before/after on the same corpora. No change to
resolution-label semantics or the critic/verification contract.

## User stories

### US1 — TypeScript exact edges via SCIP (P1)
As an agent working across a TS monorepo, I want `cairn build` to apply a
scip-typescript index when configured, so call edges resolve `exact`
instead of `ambiguous`.

**Acceptance criteria**:
- AC1: a workspace with a configured scip-typescript index upgrades
  TS ambiguous call edges to exact on build (FR-001).
- AC2: absent the index/binary, the build degrades to today's
  tree-sitter edges with a recorded skip (existing overlay contract).

### US2 — Python exact edges by default where pyright exists (P1)
As an agent on a Python repo, I want the pyright upgrade pass to run
without remembering `--lsp`, so the 9.4% ambiguous mass shrinks with zero
workflow change.

**Acceptance criteria**:
- AC1: with pyright importable, `cairn build` runs
  `upgrade_ambiguous_edges` automatically and reports the upgraded count
  (FR-002).
- AC2: without pyright, the build is byte-identical to today's (notice
  only) (FR-003).

## Requirements

- **FR-001**: TypeScript workspaces with a configured
  `scip.indexes.typescript` entry apply the SCIP overlay on every build;
  edge upgrades land in the same transaction with disagreement accounting
  (existing `_apply_scip_overlay` contract).
- **FR-002**: `cairn build` auto-enables the pyright pass when pyright is
  importable; `--lsp` remains the explicit override; `--no-lsp` opts out.
- **FR-003**: Absent pyright or the SCIP runtime, builds keep today's
  edge counts and record the skip reason (no new hard dependencies; the
  deterministic-build guarantee holds — LLM-free, offline).
- **FR-004**: The before/after exact-ratio for both languages is
  published in `docs/benchmarks.md` with reproduction commands.
- **FR-005**: The auto-enabled pass runs under a dual cap — wall-clock
  budget (default 60 s) and per-build edge budget (default 2,000 edges),
  whichever hits first; both overridable via `cairn.json` (`lsp.budget_seconds`,
  `lsp.edge_budget`). `--lsp` forces an unbounded pass, `--no-lsp` skips.
  Budget stops are reported (`budget_hit`, probe count) and amortize across
  builds: upgrades persist, the pass resumes from the remainder. *(Ruled
  2026-10-09; defaults await calibration against a real pyright run.)*

## Quality attributes

- Build wall-clock: auto-pass must not add unbounded time; FR-005 rules
  the cap.
- Hermetic CI: pyright-dependent tests use the existing fake-transport
  pattern (`tests/test_graft_parity_lsp.py`), never the network.

## Scope

**In**: TS SCIP application + config authoring helper; pyright
auto-enable with budget; published deltas. **Out (deferred)**: scip-python
indexing (pyright covers it); other languages; remote LSP servers.

## Assumptions & risks

- Risk: pyright pass cost scales with ambiguous-edge count → mitigation:
  FR-005 budget cap; the pass is idempotent and re-runnable.
- Risk: scip-typescript binary availability varies by host → mitigation:
  existing bounded auto-generation + skip accounting; degrade, never fail.
- Risk: disagreement between SCIP and tree-sitter edges confuses agents →
  mitigation: existing disagreement counters surface in build output.
