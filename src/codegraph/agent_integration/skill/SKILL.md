---
name: codegraph
description: >
  Codebase intelligence via a local code graph. Provides precise symbol
  lookup, caller/callee/impact analysis across multiple repos, module
  "compass" navigation guides, business flow tracing, architectural wiki,
  semantic code search, and persistent agent memory (decisions/patterns/
  mistakes). Activate when the user asks about code structure, call graphs,
  dependencies, blast radius of a change, or needs context before editing a
  file. Also activate for "what breaks if I change X" and cross-repo impact
  questions.
---

# Codegraph: Codebase Intelligence

This file carries what's needed for almost every query. Deeper material
lives alongside it and is loaded on demand:

- `references/tools.md` -- full signature + description for every MCP tool
- `references/golden-rules.md` -- full rationale/examples for each rule below
- `references/tool-behaviors.md` -- empirically-verified gotchas per tool
- `references/task-queue.md` -- LLM synthesis task queue (compass/wiki generation)
- `references/cli-fallback.md` -- `cg` CLI commands for when MCP is unavailable
- `scripts/impact_guard.py` -- run instead of raw `impact_analysis`/`cg impact`
  when a name might be common/lifecycle-shaped (see Rule 6)
- `evals/` -- scenario specs for the rules most likely to be silently violated

## Available MCP Tools (via codegraph MCP server)

Full descriptions: `references/tools.md`. Names by layer:

- **Graph (L1):** explore, semantic_search, find_definition, get_callers, get_callees, impact_analysis, search_symbols, cross_repo_deps, visualize_graph
- **Knowledge Base + Compass (L2/3):** search_knowledge, get_compass, trace_flow, generate_flow
- **Memory (L4):** memory_digest, recall_memory, record_memory, memory_promote, memory_demote, memory_delete, memory_decay
- **Knowledge Documents (L5):** knowledge_add, knowledge_search, knowledge_delete, knowledge_status, trace_workflow
- **Router:** ask_compass

## Workflow: explore-first

Start with `explore` for any structural question, drill down with layer-specific
tools only when `explore` is thin.

### For almost any question -- "how does X work", a flow, surveying an area:
1. `explore(query)` -- returns source + call paths + blast radius in one call
2. If `explore` is thin, reach for `ask_compass(query)` (cross-layer routing:
   graph + wiki + compass + memory), then the specific layer tool.

### Before editing a file, ALWAYS:
1. `ask_compass(file_path="<path>")` -- load compass + memory for this file
2. `find_definition(symbol)` -- where it lives
3. `get_callers(symbol)` -- who depends on it (within-repo, precise by default)
4. `cross_repo_deps(repo)` -- cross-repo blast radius (impact_analysis does NOT cross repos)
5. `impact_analysis(symbol)` -- recursive within-repo blast radius (precise by default; only if making breaking changes)

### Resolution-aware querying (precise vs fuzzy)
`get_callers`, `get_callees`, and `impact_analysis` default to **precise**:
they only follow edges the resolver could pin to exactly one definition. An
edge left unresolved is either ambiguous (multiple candidates with the same
name) or external (stdlib/library call not in the workspace).

Interpreting results:
- **Empty precise result ≠ "no callers".** It means "no *resolvable* callers."
  Before concluding a symbol is unused, retry with `fuzzy=True`.
- **Precise is ground truth for blast radius.** Use it for "what breaks if I
  change this signature". It will not be inflated by name collisions.
- **Fuzzy is a candidate list, not truth.** Use it to explore (find all call
  sites of a common name, audit touch-points) and then verify each candidate
  against the actual code. A fuzzy result for `invoke` can span 200+ sites
  across repos and languages that merely share the name.
- **`resolution` label in results:** `exact` = trusted; `ambiguous` = the
  resolver found multiple candidates and declined to guess (the right call);
  `unresolved` = external/stdlib.

When precise is right: impact analysis, refactoring, signature changes.
When fuzzy is right: auditing, dead-code hunting, exploring unfamiliar code.

### For PO/business questions ("what's the impact of changing X?")
1. Call `knowledge_search("X")` — finds business docs by meaning
2. Read the doc's `affects_repos` / `affects_modules` metadata
3. Call `cross_repo_deps(repo)` and `impact_analysis(symbol)` for code impact
4. Return combined answer: business rules + code blast radius

### After completing a task, ALWAYS:
1. `cg update` to refresh the graph
2. `record_memory` for learnings (decision | pattern | mistake | workaround)

## When to Use Which Tool

| Situation | Tool | Why |
|-----------|------|-----|
| "Where is ApiFactory defined?" | find_definition | Precise lookup |
| "What calls this function?" | get_callers (precise; fuzzy for common names) | Within-repo graph traversal |
| "What breaks if I change X?" | impact_analysis + cross_repo_deps (or `scripts/impact_guard.py` if X might be a common name) | Impact is within-repo (precise by default; fuzzy=True if suspiciously small); cross-repo needs cross_repo_deps |
| "How does dispatch work?" | ask_compass, then search_knowledge(type_filter="Wiki") | Router first; drill down if thin |
| "I need to edit PaymentVM.kt" | ask_compass(file_path="...") | Load compass + memory |
| "What traps exist in flavors?" | get_compass or search_knowledge(type_filter="Pattern") | Tribal knowledge |
| "Why did we choose StateFlow?" | recall_memory (by symbol/title) | Past decision |
| "Complex question" | ask_compass | Routes to all layers |

## Known Tool Behaviors

Empirically-verified gotchas (ask_compass empty skeletons, recall_memory
substring matching, impact_analysis within-repo-only and common-name blowups,
search_symbols wildcards, semantic_search fusion scoring) -- full table in
`references/tool-behaviors.md`. Honor them to avoid empty results or
misleading counts.

## Golden Rules (avoid wasted tool calls)

Full rationale + examples for each: `references/golden-rules.md`.

1. **Resolve to unique name first.** Run `search_symbols("X", kind="class")` before calling `get_callers`/`get_callees`/`impact_analysis`. Ambiguous names return `[]` silently.
2. **One question = one tool.** No parallel fan-out. Sequential chains are fine (each step depends on previous). Use `explore` as the one-call aggregator.
3. **search_symbols is the default entry point.** FTS5 + BM25. For concept-based queries, use `semantic_search`. `explore` orchestrates both.
4. **Index auto-refreshes at boot.** No per-query freshness check. Run `cg stats` from CLI to verify.
5. **typeHierarchy limited by tree-sitter.** Works for Kotlin/Java. Empty/incomplete for TypeScript, Python, Swift. Don't retry.
6. **impact_analysis needs specific names; guard against response blowups.** Common names (`get`, `create`, lifecycle methods like `onCreate`) explode into hundreds/thousands of name-collision hits, which also risks a large-MCP-response warning. Use qualified names, prefer `cached=True` for public symbols, and cap `depth<=2` when the symbol is reached via a common/lifecycle name. If the result still returns 100+ impacted symbols for a narrow feature, treat it as a name-collision artifact — don't quote the raw count in your report, note it as noise and fall back to `get_callers` for the specific hop you need. Or just run `scripts/impact_guard.py <symbol>`, which does this check for you.
7. **explore before ask_compass** for structural questions. `explore` is pure L1 (always works); `ask_compass` depends on compass/wiki coverage.
8. **Precise first, fuzzy retry.** Empty precise ≠ 'no callers'. Precise = ground truth for refactoring. Fuzzy = hypothesis for auditing.
9. **impact_analysis + cross_repo_deps always together.** Neither alone gives the full multi-repo picture.
10. **Mutations via CLI, reads via MCP.** `cg update`, `cg build`, `cg task claim/complete`, `cg memory purge` are CLI-only.

## Memory Capture Workflow

After completing a task, always check if there are learnings to capture:

1. Did you make an architectural decision? -> record_memory(type="decision")
2. Did you discover a code pattern? -> record_memory(type="pattern")
3. Did you make an error that others should avoid? -> record_memory(type="mistake")
4. Did you use a workaround? -> record_memory(type="workaround")

Record with a title that future symbol-keyed recall can find (include the
symbol name, e.g., "ApiFactory uses per-flavor base URLs").

## CLI Fallback

If MCP tools are unavailable, use the `cg` CLI -- full command list in
`references/cli-fallback.md`.

## LLM Task Queue

Codegraph queues compass/wiki/flow synthesis as OKF tasks rather than calling
an LLM directly -- claim/synthesize/complete workflow and task kinds in
`references/task-queue.md`. Flow tasks (`flow-synthesize`) trace the downward
call chain and produce a 5-section flow compass; on critic pass they are
auto-promoted to `compass/flow-<entry>`.
