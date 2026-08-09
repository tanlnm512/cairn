# Phase 3 — Memory Moat (Plan)

> **Roadmap milestone:** Month 3 — Turn proof into the memory moat.
> **Read alongside:** [`spec.md`](spec.md), [`tech-guide.md`](tech-guide.md).

## Sequencing

3.1 and 3.2 are independent (recall-side vs build-side) and run in parallel.

```
Week 1:  3.1 symbol-link schema + recall-side verification  ║  3.2 update-path hint detection
Week 2:  3.1 stale-flag surfacing (CLI + MCP)               ║  3.2 hint surfacing + tests
Week 3:  integration, edge cases (rename, multi-link), release
```

## Tasks

### 3.1 — Graph-grounded memory recall verdict
**Done when:** a memory citing a deleted symbol shows a `stale` flag on recall.

*Status: `recall_memory` (`tools_memory.py:59`) **already** runs `_graph_verification` per match (`tools_memory.py:124`) with one reused conn and surfaces `refs-verified=<fraction>` (`tools_memory.py:128`) — its docstring documents this. The only gap is the discrete `stale` flag + threshold on top of that fraction.*

The sub-tasks (tracked in [`task.md`](task.md) § 3.1):
1. ~~Run `_graph_verification` on each recalled memory~~ — already done (`tools_memory.py:124`). Skip.
2. `refs-verified=<fraction>` is already appended (`tools_memory.py:128`); **remaining**: derive and append a `stale` flag when the fraction falls below the chosen threshold.
3. Choose and document the stale threshold deliberately (e.g. `< 1.0` = some ref stale; `< 0.5` = mostly stale). Do not silently pick.
4. Reuse Phase 2.2's verdict shape so docs + memory share one trustworthiness vocabulary.
5. Test: record a memory citing a backtick-quoted symbol, delete the symbol, recall → `refs-verified < 1.0` (already works) AND `stale` flag (Phase 3 adds). Record a memory citing a real symbol, recall → no flag.

**Risk:** `_graph_verification` inspects backtick-quoted refs only — memories mentioning a symbol without backticks are not verified. Keep this consistent with the doc layer; do not invent a new extraction rule for memory. Performance: N lookups for N recalled memories — already incurred today; accept for now, cache in Phase 4 if it bites.

### 3.2 — Memory-triggered build hints
**Done when:** editing a memory-anchored file produces an actionable, graph-grounded warning.

*Status: `cairn update` already surfaces warnings and runs memory decay (`cli/update.py:57-86`). The gap is no edit→memory cross-reference.*

The sub-tasks (tracked in [`task.md`](task.md) § 3.2):
1. After `cairn update` detects changed files, scan tribal memories for backtick refs (reuse `src/cairn/refs.py`'s `extract_file_refs` / `extract_symbol_refs` — shared by critic and scoring) resolving to symbols in changed files.
2. If matches, emit a warning via the existing `display.warning` channel: "N memor(y/ies) reference symbols in <file> — verify: <paths>." (Keep it a warning; `cairn update` must not fail on memory state.)
3. Confirm UX says "symbol changed" not "memory broken" for the rename case (a renamed symbol is a new symbol_id).
4. Test: build a repo, record a memory citing a symbol in file X (backtick-quoted), edit file X, `cairn update` → warning present. Edit an unrelated file → no warning.

**Risk:** false positives — only flag explicit backtick refs resolving to a changed file's symbols, never loose textual mentions. The scan over all tribal memories per changed file could be expensive on large corpora; accept for now, an index `symbol_id → memory_path` is the Phase 4 optimization.

## Definition of done (phase level)

Phase 3 lands when **both** hold:

1. A memory citing a deleted/renamed symbol is flagged stale on recall (3.1), with unlinked memories unaffected.
2. `cairn update` after editing a memory-anchored file surfaces the relevant memories (3.2), with no warning for unanchored edits.

## Scope guard (anti-creep)

| Tempting addition | Belongs in |
|-------------------|------------|
| `cairn memory digest` lifecycle cadence view | Roadmap "Beyond" |
| Auto-promote grounded memories to compass/wiki | Phase 4 refinement |
| First SCIP-leverage language (C/C++, Rust) | Roadmap "Beyond" (Month 3 is memory-only by design) |
| Cross-memory deduplication via shared symbol links | Phase 4 |
| Semantic memory search (embeddings) | Already exists via `[semantic]`; not a Phase 3 task |
