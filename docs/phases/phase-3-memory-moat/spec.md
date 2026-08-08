# Phase 3 — Memory Moat (Specification)

> **Roadmap milestone:** Month 3 — Turn proof into the memory moat.
> **Goal:** the verified foundation (Phases 1–2) becomes the thing no
> competitor has — **trustworthy, code-grounded memory**.
> **Theme:** memory is the hero layer; the graph is the ground truth it hangs on.

## Context

The strategic finding is blunt: on the graph axis cairn is commoditized
(codebase-memory-mcp matches the resolution scheme at ~38k stars), and on the
memory axis the incumbents (mem0 / Letta / Zep) are *generic* — they let an
LLM silently rewrite memory and none ground it in code symbols. cairn's
defensible intersection is **code-grounded memory verified against the
graph**. Phases 1–2 made the graph trustworthy and proved it. Phase 3 extends
that trust to the memory layer.

Today, cairn's memory is tribal and lifecycle-managed (promote/demote/evolve/
decay/digest) and **already** grounded in symbols on recall: both
`memory_digest` and `recall_memory` surface a `refs-verified` fraction via
`_graph_verification` (checks backtick-quoted file/symbol refs against the
graph, see tech-guide). What is **missing** is a discrete `stale` flag derived
from that fraction — today the fraction is shown but no boolean verdict is
computed, so an agent must eyeball the number. And `cairn update` does not
warn when an edited file is referenced by a memory. Phase 3 closes those two
gaps — adding the verdict on top of the existing verification, and the
edit→memory link.

## In-scope items

| ID | Item | Strengthens promise | Done when |
|----|------|---------------------|-----------|
| 3.1 | Graph-grounded memory recall verdict: `_graph_verification` already runs on recall and surfaces a `refs-verified` fraction (`tools_memory.py:124,128`); add a discrete `stale` flag + threshold on top of it. | **#2** (extended to memory) | A memory citing a deleted symbol shows `refs-verified < 1.0` (already works) AND a `stale` flag (Phase 3 adds) on recall. |
| 3.2 | Memory-triggered build hints: after `cairn update`, warn when an edited file contains symbols cited by memories (reusing the shared ref extractors in `src/cairn/refs.py`). | **#1 + #2** (linking edits to memory) | Editing a memory-anchored file produces an actionable, graph-grounded warning. |

3.1 adds a discrete verdict on top of the verification that already runs on
recall. 3.2 closes the loop in the other direction — when the graph changes,
memory-anchored-file edits warn. Together they make memory *live* relative to
the code, which no general
memory tool does. Both reuse existing mechanisms (the `_graph_verification`
function, the shared ref extractors in `src/cairn/refs.py`, the update path's
warning channel) — Phase 3 is primarily a surfacing phase, not a new-build
phase.

## Out of scope

- **New languages / SCIP wiring.** Phase 4. (The roadmap moved 3.3 — the
  first SCIP-leverage language — to "Beyond 3 months" because memory is the
  heart of the positioning and wins the capacity contest.)
- **Visible memory-lifecycle cadence** (`cairn memory digest` showing
  graduating/expiring). Roadmap "Beyond" item; useful but not contract-
  strengthening.
- **Auto-promoting memories to compass/wiki** via graph grounding. The
  promotion machinery exists; making it graph-aware is a Phase 4 refinement.
- **Changing the memory store schema wholesale.** 3.1 reuses the existing
  live `_graph_verification` computation (no schema change in v1); a cached
  `symbol_id → memory_path` index is a Phase 4 optimization.

## Dependencies

- 3.1 and 3.2 are **independent of each other** (3.1 is recall-side, 3.2 is
  build-side) and can run in parallel.
- 3.1 benefits from Phase 2.2 (the visible critic) — the same "verdict"
  surfacing used for docs applies to stale-memory flags.
- Both are soft-prerequisite on Phase 1.3 (the critic invariant) — extending
  the critic concept assumes the critic itself is tested.

## Risks

- **Verification coverage is bounded by backtick extraction.** `_graph_verification`
  checks only backtick-quoted refs — a memory mentioning `ApiFactory` without
  backticks is not verified. Mitigation: accept this (it matches the doc
  layer's contract); document it so users know to backtick the symbols they
  care about.
- **False positives in 3.2.** Editing a file that *happens* to be referenced
  by a memory shouldn't spam warnings. Mitigation: warn only on explicit
  backtick refs resolving to a changed file's symbols, never loose mentions.
- **Symbol identity across rename.** A renamed symbol is a new `symbol_id`;
  the memory's cited (old) symbol won't match — it surfaces as stale via 3.1.
  That is *desired* (the memory needs re-confirmation), but the UX must say
  "symbol changed" not "memory broken."
- **Performance on large corpora.** Both 3.1 (per-recall verification) and 3.2
  (per-changed-file memory scan) are O(memories). Mitigation: accept for v1;
  a cached index is the Phase 4 optimization.
