# Phase 3 — Memory Moat (Business Test Case)

> **Roadmap milestone:** Month 3 — Turn proof into the memory moat.
> **Read alongside:** [`spec.md`](spec.md), [`plan.md`](plan.md).
> **Purpose:** prove, in user-facing scenarios, that the phase delivered value.

## The user this phase serves

**Persona:** the **heavy-agent user** — someone running coding agents daily
across a changing codebase. Their pain: agents re-learn the same lessons
every session because nothing persists, and when something *is* persisted
(via cairn memory), it goes stale silently when the code changes. They have
seen mem0/Letta/Zep and found them generic — memory unconnected to the actual
symbols, free to drift from the code.

**Value delivered by Phase 3:** memory becomes *live* relative to the code.
When a memory cites a symbol that's been renamed or deleted, the user is told
on recall. When they edit a file that a memory depends on, `cairn update`
tells them. This is the property no general memory tool has — memory that
knows when it has gone stale because it is grounded in the graph.

## Acceptance scenarios

### Scenario A — "A stale memory is flagged on recall, not served as truth"
**Maps to:** 3.1. **Strengthens:** promise #2 (extended to memory).
**Spec "Done when":** a memory citing a deleted symbol is surfaced as stale on recall.

```
GIVEN a tribal memory whose body backtick-cites a symbol (e.g. `ApiFactory`)
 WHEN the cited symbol is deleted (or renamed) in a subsequent edit + rebuild
 AND an agent calls recall_memory matching that memory
 THEN the recall output shows refs-verified < 1.0 (already works today)
  AND a stale flag (Phase 3 adds this on top of the fraction)
  AND the agent is told to verify before relying on the memory
```

**Demonstrating command (developer-facing test that encodes the scenario):**
```bash
pytest tests/ -k "recall and stale" -v
# records a memory citing a symbol, deletes the symbol, recalls → stale flag
```

**Why this matters:** this is the memory-layer analog of Phase 1's
`exact`/`target_id` invariant. A memory served as truth when its cited symbol
no longer exists is the silent-wrong-data failure cairn exists to prevent.
The flag turns silent drift into a loud, actionable signal — the same trust
contract the graph already enforces, now covering memory.

### Scenario B — "Editing a memory-anchored file warns me about the memory"
**Maps to:** 3.2. **Strengthens:** promises #1 + #2 (linking edits to memory).
**Spec "Done when":** editing a memory-anchored file produces an actionable, graph-grounded warning.

```
GIVEN a tribal memory backtick-citing a symbol in file X
 WHEN the user edits file X and runs cairn update
 THEN the update output includes a warning naming the memory and file
  AND editing an unrelated file produces no such warning
```

**Demonstrating command (developer-facing test):**
```bash
pytest tests/ -k "update and memory_hint" -v
# builds a repo, records a memory citing a symbol in file X,
# edits X, runs cairn update → warning; edits Y → no warning
```

**Why this matters:** this closes the loop in the other direction — when the
*graph* changes, the user is told which *memories* are affected before they
rely on impact results. It makes memory and graph a single coupled system:
edit the code → hear about the memory; recall the memory → hear about the
code. No general memory tool offers this because none is grounded in a code
graph.

## Phase exit criteria (both required)

- [ ] Scenario A: recall of a memory whose cited symbol is gone shows a stale flag; recall of a healthy memory shows none.
- [ ] Scenario B: `cairn update` after editing a memory-anchored file warns; editing an unrelated file does not.

## What this phase does NOT prove

- It does **not** verify memories that mention symbols *without* backticks —
  backtick extraction is the shared contract with the doc layer. A deliberate
  future change could broaden extraction; Phase 3 keeps consistency.
- It does **not** cache the verification (per-recall and per-edit scans are
  O(memories)). A `symbol_id → memory_path` index is a Phase 4 optimization.
- It does **not** auto-promote grounded memories or add a lifecycle-cadence
  view (`cairn memory digest` improvements) — those are roadmap "Beyond" items.

Phase 3's job is narrow but strategic: extend the verification contract from
the graph to the memory layer, so the moat cairn owns — code-grounded memory
verified against the graph — is real, not aspirational.
