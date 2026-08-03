# Cairn: Golden Rules (avoid wasted tool calls)

SKILL.md carries a condensed, one-line version of each rule for quick
reference. This file has the full rationale, examples, and workarounds --
come here when the condensed version isn't enough to act correctly.

These rules prevent the most common failure modes: silent empty results, noisy
fan-out, stale data, and misinterpretation of what tools can and cannot do.

## Rule 1 — Resolve to a unique qualified name before calling nav tools

`get_callers`, `get_callees`, and `impact_analysis` take a bare `name` string
and rely on the resolver's 5-tier strategy (same-file → import-aware →
same-repo → global → ambiguous). If the name is ambiguous or doesn't match a
symbol exactly, you get `[]` — a **silent empty result** with no explanation.

**Do this first:**
```
search_symbols("PaymentProcessor", kind="class")
    →  xyz.be.customer.networking.PaymentProcessor
```
**Then call nav tools with that qualified name.**

`find_definition` has a built-in 3-step fallback (exact name → qualified name →
substring LIKE) so it works as a shortcut when you're fairly confident in the
name — but it can return up to 50 results on substring match, which is noisy.
`search_symbols` with BM25 ranking is the safer entry point.

## Rule 2 — One discovery question = one tool (no parallel fan-out)

Never fire 5 tools in parallel for the same question. Each tool returns
deterministic L1 graph data — combining multiple results means synthesizing
(L2 territory), not reading ground truth.

**Exception: sequential pre-edit chains are fine** — each call depends on the
previous:
```
ask_compass(file_path)      →  get symbol name + context
find_definition(symbol)     →  confirm location + repo
get_callers(symbol)         →  within-repo dependents
cross_repo_deps(repo)       →  cross-repo dependents
impact_analysis(symbol)     →  recursive blast radius
```
The `explore` tool exists as the one-call aggregator: FTS5 search → 1-hop
callers+callees → verbatim source → blast radius (depth 2) → ambiguous dispatch
hops. Use it when one tool is enough.

## Rule 3 — search_symbols is the default entry point for discovery

`search_symbols` uses FTS5 with BM25 ranking — fast, ranked by relevance,
handles wildcards and underscore-split names. It's the reliable first step from
"something about payments" to the exact symbol.

**Complementary entry point:** `semantic_search` finds code by *meaning*
(synonyms, paraphrases, cross-language concepts) when no token matches. The
`explore` tool orchestrates both automatically (FTS5 first, semantic fallback
when FTS5 finds < 3 results).

## Rule 4 — Index is auto-refreshed at boot; no per-query freshness check

The MCP server runs a one-time catch-up at startup (`ensure_fresh_force()`)
that absorbs edits made while the server was down. After that, tool calls do
NOT re-check file freshness per-query (removed for concurrency safety —
edits made while `cg serve` is running require a server restart to appear).

There is **no MCP tool for index status**. Run `cg stats` from the CLI if you
need to verify. If you suspect stale data: `cg update` → restart server.

## Rule 5 — typeHierarchy is limited by tree-sitter parsing; don't retry

Inheritance edges (`extends`/`implements`) are only as good as what tree-sitter
emits. This works well for **Kotlin/Java** (class/interface/enum edges are
reliable). For **TypeScript** (structural typing, generic interfaces),
**Python** (dynamic inheritance, mixins), and **Swift** (protocol conformance),
expect empty or incomplete results. This is a parser-level limitation, not a
resolver bug.

**Don't retry** type hierarchy queries for these languages. Use `explore` or
`semantic_search` for conceptual type relationships instead.

## Rule 6 — impact_analysis needs specific names; guard against response blowups

`impact_analysis` traverses callers recursively by name. Common names like
`get`, `create`, `handle`, `render`, or lifecycle methods like `onCreate` match
dozens to thousands of symbols across repos and produce noisy, bloated
results — large enough to trigger a client-side "large MCP response" warning.

- Use a **specific qualified name** (`PaymentProcessor.create`, not `create`)
- Prefer `cached=True` for public symbols — the dataflow index is precomputed
  for the module-level public API surface (Java/Kotlin `public` modifier,
  Python non-underscore)
- For leaf/utility functions, skip `impact_analysis` entirely and use
  `get_callers` directly for the single hop you need
- If the symbol is reached through a common/lifecycle method name
  (`onCreate`, `init*`, `render`) and the result balloons past ~100 impacted
  symbols, treat it as a name-collision artifact, not real blast radius —
  cap `depth<=2`, note the collision, and don't paste the raw count into a
  report.
- **Use `scripts/impact_guard.py <symbol>`** instead of calling the raw tool
  when you're unsure whether a name is "common enough" to explode. It runs
  the same query, inspects the `cycles` field the resolver already returns,
  and prints a clear collision warning (with a `cg dataflow lookup` fallback)
  instead of a misleading total. See `evals/rule06-impact-common-name.md` for
  the exact failure this guards against.

## Rule 7 — explore before ask_compass for structural questions

`explore` is pure L1 graph data — deterministic, no dependency on compass/wiki
coverage. It always returns concrete results: source spans, call paths, blast
radius, ambiguous dispatch hops.

`ask_compass` routes through the compass router (L2-L5) which depends on
wiki/compass/memory coverage. When coverage is thin, it returns empty body
skeletons.

**For "how does X work" / "what calls Y":** `explore` first.
**For business context, past decisions, tribal knowledge:** `ask_compass` first.

## Rule 8 — Precise first, fuzzy retry; never the reverse

`get_callers`, `get_callees`, and `impact_analysis` default to **precise**
mode (only resolved edges). Empty precise result ≠ "no callers" — it means
"no *resolvable* callers."

1. Always start with precise (default)
2. If empty or suspiciously small for a widely-used symbol → retry `fuzzy=True`
3. Never start with fuzzy — it inflates results with name collisions

Precise is ground truth for refactoring. Fuzzy is a hypothesis generator for
auditing and dead-code hunting.

## Rule 9 — impact_analysis + cross_repo_deps always together

`impact_analysis` only traverses callers **within one repo**. For a multi-repo
workspace (cairn's core use case), neither tool alone gives the full
"what breaks" picture.

Always call both:
```
impact_analysis(symbol)     →  within-repo recursive caller chain
cross_repo_deps(repo)       →  which repos import from yours
```

## Rule 10 — Mutations via CLI, reads via MCP

Graph mutations and dangerous bulk operations are **CLI-only by design**:
- `cg update` / `cg build` — refresh graph
- `cg stats` — index status
- `cg dataflow build` — rebuild precomputed dataflow
- `cg task claim/complete` — LLM task processing
- `cg memory purge --dry-run` — bulk delete

MCP tools are read-only for L1 (graph) and narrowly scoped for L4/L5 writes
(individual memory/knowledge CRUD). Never try to bulk-delete or rebuild the
graph through MCP tools.

Exception: `generate_flow` is an MCP write that creates a `compass/flow-*`
doc (critic-gated) — the one generation tool available without the CLI. Use
it when you need to document a flow mid-session without switching to the CLI.
