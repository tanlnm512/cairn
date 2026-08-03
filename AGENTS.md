# Codebase Intelligence System

This workspace uses a local knowledge graph (cairn) for codebase intelligence.
All AI coding agents working in this workspace should use these tools.

## MCP Server
- Name: `cairn` (auto-connected at session start)
- Transport: stdio
- 26 tools across 5 layers: graph (9), knowledge base + compass (5), memory (7), knowledge (5)
  (`explore` is the recommended first call -- it aggregates the graph layer;
  `ask_compass` is the cross-layer router)

## Workflow: explore-first

### For almost any question -- "how does X work", a flow, surveying an area:
1. Call `explore(query)` FIRST. It returns matching symbols' verbatim source
   grouped by file, the call paths between them (including ambiguous dispatch
   hops), and a blast-radius summary -- one call, one answer.
2. Reach for the specific tools only to drill down when `explore` is thin:
   - `ask_compass(query)` -- cross-layer routing (graph + wiki + compass + memory)
   - `get_callers` / `get_callees` / `impact_analysis` -- deeper call-graph traversal
   - `search_knowledge` / `recall_memory` -- knowledge-layer questions `explore` doesn't cover

### Before editing a file, ALWAYS:
1. Call `ask_compass(file_path="<path>")` to load compass + memory context
2. Call `find_definition` for any symbol you need to understand
3. Call `get_callers` to understand who depends on what you are changing (within-repo)
4. Call `cross_repo_deps(repo_name)` for cross-repo blast radius
5. Call `impact_analysis(symbol_name)` if making breaking changes (within-repo recursive)

### Resolution-aware querying (precise vs fuzzy)
`get_callers`, `get_callees`, and `impact_analysis` default to **precise**:
they only follow edges the resolver could pin to exactly one definition.

- **Empty precise result ≠ "no callers".** It means "no *resolvable* callers."
  Before concluding a symbol is unused, retry with `fuzzy=True`.
- **Precise is ground truth for blast radius** — not inflated by name collisions.
- **Fuzzy is a candidate list, not truth** — verify each against actual code.
  A fuzzy result for `invoke` can span 200+ sites across repos/languages that
  merely share the name.
- **`resolution` label:** `exact` = trusted; `ambiguous` = multiple candidates,
  resolver declined to guess; `unresolved` = external/stdlib.

When precise is right: impact, refactoring, signature changes.
When fuzzy is right: auditing, dead-code hunting, exploring unfamiliar code.

### When you need architectural context:
- Call `get_compass(module_name)` for a 25-35 line navigation guide
- Call `search_knowledge(query, type_filter="Wiki")` for feature/architecture documentation

### When you need past decisions:
- Call `recall_memory(query)` -- symbol/title-keyed, NOT full-text. Query by
  symbol name or title tokens ("ApiFactory", "backoff"), not natural language.

### After completing a task, ALWAYS:
1. Run `cg update` to refresh the graph with your changes
2. Call `record_memory` for any learnings:
   - type="decision" for architectural choices made
   - type="pattern" for reusable code patterns discovered
   - type="mistake" for errors others should avoid
   - type="workaround" for non-obvious solutions used
3. Set confidence (0.0-1.0) based on how sure you are

## Tool Quirks (empirically verified)

| Tool | Behavior | Workaround |
|------|----------|------------|
| `ask_compass` | Routes correctly but returns empty body skeletons when wiki/compass coverage is thin. | Drill down with the specific layer tool; don't treat empty response as "no info exists". |
| `recall_memory` | Multi-token lexical matching, with a semantic fallback when lexical search comes up empty. | Natural-language and multi-token queries ("backoff retry policy") work, not just single symbol tokens. |
| `impact_analysis` | Within-repo by default, but includes cross-repo consumer reach in its output. Precise mode only follows resolved edges, so common names can under-report. | Pair with `cross_repo_deps(repo)` for the full picture. Use `fuzzy=True` when precise impact looks suspiciously small for a widely-used symbol. |
| `search_symbols` | FTS5 + phrase splitting handles underscored tokens (`*core_ui_v4*` matches). Substring and camelCase patterns also match via a LIKE-based pass (the FTS5 `*` wildcard is prefix-only and the tokenizer doesn't split camelCase, so non-prefix queries fall back to LIKE). | Wildcards and substring queries both work, on underscored and camelCase names. |
| `get_callers`/`impact_analysis` on a Kotlin class invoked via `operator fun invoke` | Bare calls of the standard Android UseCase idiom (`someUseCase(params)` against a DI-injected property) resolve to the callee's declared type. | `this.someUseCase(params)` (explicit receiver) is a narrower remaining gap; cross-check with `fuzzy=True` or a grep if that shape looks under-reported. |
| `semantic_search` | Defaults to RRF fusion (BM25 + vector, `CAIRN_FUSION=1` default): the returned `score` is a rank-fusion number (~0.01-0.02), not cosine similarity, regardless of the `threshold` argument. Real cosine scores (0.3-0.6+ for genuinely on-topic hits with `local`/`BAAI/bge-m3`) only show when fusion is off. | Rank order is meaningful either way. Set `CAIRN_FUSION=0` if you need the score to reflect actual match strength (e.g. deciding how confident a hit is), not just relative order. |
| `ann_backend_enabled` | On by default: `CAIRN_ANN_BACKEND` unset resolves to `sqlite-vec`. It degrades silently to the brute-force cosine scan if the extension fails to load. | Set `CAIRN_ANN_BACKEND=off` to force the brute-force scan. |

## LLM Task Queue (agent-decoupled synthesis)
Cairn never calls an LLM directly. To generate compass/wiki with LLM quality:
- `cg task list --status pending` -- see queued work
- `cg task show <id>` -> `cg task claim <id>` -> `cg task complete <id> --result-file <path>`
- The deterministic critic fact-checks every result; only graph-verified files/symbols allowed.

## CLI Fallback (if MCP tools are unavailable):
- `cg def <symbol>` -- find definition
- `cg callers <symbol>` -- who calls this
- `cg impact <symbol>` -- what breaks if changed (within-repo)
- `cg deps <repo>` -- cross-repo dependency map
- `cg context <file>` -- load context for a file
- `cg ask "<question>"` -- natural language query across all layers
- `cg memory record <type> "<title>"` -- capture a learning

## Knowledge Files

The `.knowledge/` directory (in cairn/) contains OKF markdown files:
- `compass/` -- module navigation guides (25-35 lines each)
- `wiki/` -- architectural documentation
- `memory/tribal/` -- past decisions, patterns, mistakes
- `memory/raw/` -- ephemeral captures (do not read)
- `memory/drafts/` -- awaiting quality review (do not read)

You can read these files directly when MCP is unavailable.
