# cairn MCP Tools Reference

← [Docs index](README.md)

The `cairn` MCP server (started by `cairn serve`) exposes the codebase
intelligence graph to AI agents. It is a FastMCP server implemented in
`src/cairn/mcp_server/` and registers **27 tools** across 4 layers,
plus an index-status resource.
Read it when deciding which tool to call for a question, or when a tool's
parameters or output shape need checking.

> **`explore(query)` is the recommended first call** — it aggregates the
> graph layer (verbatim source + call paths + blast radius) in one response.

Tool list and signatures below are verified from the `@mcp.tool()`-decorated
functions in `tools_graph.py`, `tools_compass.py`, `tools_memory.py`, and
`tools_knowledge.py`. For the full per-tool help, an agent can call
`list_tools` at runtime.

## Contents

| Section | What it covers |
|---------|----------------|
| [`## Layer 1 — Graph (9 tools)`](#layer-1--graph-9-tools) | The structural tools (`explore` first, then definition/callers/impact/search/deps/viz), behavioral notes, and the `structured` output mode. |
| [`## Layer 2 — Knowledge base + compass + router (5 tools)`](#layer-2--knowledge-base--compass--router-5-tools) | Compass retrieval, bundle-level knowledge search, the `ask_compass` router, and the two flow tools. |
| [`## Layer 4 — Memory (8 tools)`](#layer-4--memory-8-tools) | The memory-tier tools, from `memory_digest` orientation to record/evolve/promote/decay. |
| [`## Layer 5 — Knowledge (business docs + workflows) (5 tools)`](#layer-5--knowledge-business-docs--workflows-5-tools) | Business-document ingestion and search, and ordered workflow tracing. |
| [`## Resource: index status`](#resource-index-status) | The `cairn://status` resource: freshness, build stats, and store health. |
| [`` `## Workflow and resolution notes (from `AGENTS.md`)` ``](#workflow-and-resolution-notes-from-agentsmd) | The explore-first, before-editing, and after-task recipes, plus the freshness model. |

---

## Layer 1 — Graph (9 tools)

The structural layer. `explore` is the one-call entry point; the rest drill
into specific relationships.

| Tool | Purpose |
|------|---------|
| `explore(query)` | One-call answer to "how does X work": matching symbols' verbatim source grouped by file, the call paths between them (incl. ambiguous dispatch hops), and a blast-radius summary. **Recommended first move.** |
| `find_definition(name)` | Where a symbol is defined (`file:line`, kind, qualified name). 3-step fallback (exact → qualified → substring LIKE), so works as a shortcut for confident names. |
| `get_callers(name, fuzzy?, limit?, structured?)` | Who calls this symbol. Precise by default; `fuzzy=True` adds name-only call sites. |
| `get_callees(name, fuzzy?, limit?, structured?)` | What this symbol calls. Precise by default; `fuzzy=True` includes unresolved/external calls. |
| `impact_analysis(name, depth?, fuzzy?, cached?, limit?, structured?)` | Recursive blast radius (callers up to `depth`). Reports total + by-depth + affected tests + cross-repo consumers. |
| `semantic_search(query, limit?, include_callers?, structured?, rerank?)` | Concept search — finds code by meaning. Requires the `semantic` extra. |
| `search_symbols(pattern, kind?, structured?)` | Lexical symbol search (`*` wildcards, BM25-ranked). The default discovery entry point. |
| `cross_repo_deps(repo, limit?)` | Cross-repo dependency map: what `repo` depends on, what depends on it. |
| `visualize_graph(scope?, symbol?, module?, repo?, depth?, format?)` | Generate a diagram (Mermaid/DOT/JSON) of a graph scope. |

### Behavioral notes (graph layer)

- **Precise vs fuzzy** (`get_callers`, `get_callees`, `impact_analysis`):
  default to **precise** (resolved edges only). Empty precise results mean
  "no *resolvable* callers", not "no callers exist" — these tools
  **auto-retry with `fuzzy=True`** and label those rows as unverified
  candidates, so you don't have to remember the retry. Precise is ground
  truth for blast radius; fuzzy is a candidate list to verify. `resolution`
  labels: `exact` (trusted), `ambiguous` (multiple candidates, resolver
  declined), `unresolved` (external/stdlib).
- **`impact_analysis`**: within-repo by default, but includes cross-repo
  consumer reach in its output. Pair with `cross_repo_deps(repo)` for the
  full picture. Precise mode only follows resolved edges, so common names can
  under-report — use `fuzzy=True` when impact looks suspiciously small for a
  widely-used symbol. `cached=True` returns O(1) precomputed dataflow
  (populated by `cairn build`/`cairn sync`), falling back to live analysis.
- **`semantic_search`**: defaults to **RRF fusion** (BM25 + vector,
  `CAIRN_FUSION=1` default). The returned `score` is a rank-fusion number
  (~0.01-0.02), **not** cosine similarity, regardless of the `threshold`
  argument. Each result's `provenance` (`semantic`, `bm25`, or
  `fused(bm25+semantic)`) is shown. Rank order is meaningful either way; set
  `CAIRN_FUSION=0` for true 0..1 cosine scores. Rerank runs when enabled
  (auto-on after `cairn download-reranker`, or `CAIRN_RERANK=1`); each reranked
  result is labelled `[rerank X.XX]`. In auto mode (default) the rerank stage
  is skipped when the fused ranking is already decisive (margin over the RRF
  scores ≥ `CAIRN_RERANK_MIN_MARGIN` and the top hit is an exact name match);
  the per-call `rerank` parameter overrides that — `None`/omitted = auto (the
  gate decides), `True` = force the rerank stage when enabled (bypasses the
  confidence gate; `CAIRN_RERANK=0` still wins), `False` = never rerank. If the
  configured rerank model is missing/evicted from the cache, it falls back to
  the hybrid order rather than failing. Set `CAIRN_RERANK=0` to force it off.
- **`search_symbols`**: FTS5 + phrase splitting handles underscored tokens,
  and unions in a LIKE-based substring pass for non-prefix patterns, so
  wildcards and substring queries both work on underscored and camelCase names.
- **`explore`**: pure L1, always concrete. Reach for the specific tools only
  to drill down when `explore` is thin.

### `structured` output

`get_callers`, `get_callees`, `impact_analysis`, `semantic_search`, and
`search_symbols` accept `structured=True` to return a typed model
(FastMCP derives `outputSchema` from it) instead of the prose string —
agents read fields directly without regex. Default `False` preserves the
prose return for backward compatibility.

---

## Layer 2 — Knowledge base + compass + router (5 tools)

Bundle/OKF-read and cross-layer routing.

| Tool | Purpose |
|------|---------|
| `get_compass(module)` | Get the compass navigation guide for a module (returns the OKF compass body). |
| `search_knowledge(query, type_filter?, limit?, full_body?)` | Search the knowledge base (wiki, compass, patterns, memory). The bundle-level search; see Layer 5 for the business-docs `knowledge_search`. |
| `ask_compass(query, file_path?)` | Natural-language across all layers. Routes to graph/wiki/compass/memory. **The router tool.** |
| `trace_flow(entry, max_depth?)` | Trace the downward call chain from an entry-point symbol — ordered sequence, branch points, terminal calls. Read-only. |
| `generate_flow(entry, as_workflow?, max_steps?)` | Generate a flow compass (and optionally a workflow) from a call-graph trace. Write tool; critic-gated. |

### Behavioral notes (knowledge base + compass)

- **`ask_compass`** is the router (the cross-layer router; it lives in Layer
  2). It routes correctly but can return thin body skeletons when
  wiki/compass coverage is thin — **don't treat an empty response as "no info
  exists"**; drill down with the specific layer tool. It explicitly flags when
  every layer came up empty. With `file_path` set and no query, it runs in
  file-path-aware mode (auto-loads compass + wiki + memory for that file).
  For structural questions prefer `explore()` (pure L1, always concrete); use
  `ask_compass` for cross-layer context.
- **`search_knowledge`** here is the bundle-level search (the OKF search over
  all concept types). The Layer 5 `knowledge_search` is the business-docs
  search with a graph bridge — they are different tools.
- **`trace_flow`** vs **`generate_flow`**: `trace_flow` is read-only (just the
  trace); `generate_flow` synthesizes a deterministic compass body, runs the
  critic gate, and writes the concept (a write tool). With `as_workflow=True`,
  `generate_flow` also writes a Knowledge-workflow doc from the traced steps.

> Note on grouping: all docs now use the canonical layer counts — graph (9),
> knowledge base + compass (5: `get_compass`, `search_knowledge`, `ask_compass`,
> `trace_flow`, `generate_flow`), memory (8), knowledge (5) — summing to 27,
> which the live `mcp` instance enforces via an assertion in `server.py`.
> `explore` is registered in the graph layer (1 of its 9) but acts as the
> aggregator/front-door; `ask_compass` is the cross-layer router.
> Layer numbering runs 1, 2, 4, 5: it follows the router's L1–L5 taxonomy
> (L1 graph, L2 wiki, L3 compass, L4 memory, L5 knowledge), and wiki (L2) +
> compass (L3) share one tool group — so there is no Layer 3 section.

---

## Layer 4 — Memory (8 tools)

Agent memory across the tiers raw → drafts → tribal → canonical. The
session-orientation entry point is `memory_digest`, not a recall query.

| Tool | Purpose |
|------|---------|
| `memory_digest(limit?)` | Top tribal memories by score. **Call this once for session orientation** before reaching for `recall_memory`. Each result shows a live `refs-verified` fraction. |
| `recall_memory(query, tier?, include_superseded?)` | Search past decisions, patterns, mistakes, workarounds. Increments refs. Each result shows a live `refs-verified` fraction — a low value flags a memory citing a renamed/removed symbol. Superseded (revised) memories are hidden by default; set `include_superseded=true` to audit revision history. |
| `record_memory(type, title, body, resource?, confidence?)` | Capture a learning. `type`: `decision\|pattern\|mistake\|workaround`. Automatically supersedes a near-duplicate existing memory (semantic cosine ≥0.85) instead of creating a parallel record. |
| `memory_evolve(memory_path, title?, body?)` | Revise a memory: create a new version that supersedes the old one, inheriting the version chain. The old memory is marked superseded and hidden from recall. |
| `memory_promote(memory_path)` | Force-promote a memory to canonical (compass/wiki), bypassing tiers. |
| `memory_demote(memory_path, tier?)` | Demote a memory to a lower tier (`raw`/`archived`); rejects promotions. |
| `memory_delete(memory_path)` | Permanently delete a memory and its cross-session refs. **Irreversible.** |
| `memory_decay(raw_max_days?, tribal_max_stale?)` | Time-based archival: expire raw >7d, archive tribal >90d stale. |

### Behavioral notes (memory)

- **`recall_memory`** is **symbol/title-keyed, not full-text** by default.
  Query by symbol name or title tokens ("ApiFactory", "backoff"), not
  natural-language prose. Matching is token-based with a **semantic fallback**
  when lexical search comes up empty, so multi-token queries like
  "backoff retry policy" do work. Each result shows a **live-recomputed
  refs-verified fraction** (backtick-quoted file/symbol refs that still exist
  in the graph now), not just the score cached at write time — a low value
  means the memory may cite a renamed/removed file/symbol; verify before
  relying on it.
- **`record_memory`**: for decision/mistake/workaround, structure `body` as
  the fact/rule itself, then a `Why:` line (the reasoning) and a
  `How to apply:` line. Don't record what's cheaper to re-derive than to
  recall (facts the graph answers, plain git history, ephemeral session state).
- **`memory_delete`** scope-checks that the resolved concept stays inside the
  `memory/` namespace before deleting — it refuses to delete a compass/wiki/
  knowledge doc via a crafted `memory_path`. `knowledge_delete` applies the
  same guard to the `knowledge/` namespace.

> CLI-only memory ops (not exposed as MCP): `cairn memory capture` (session
> transcript extraction), `cairn memory consolidate`, `cairn memory purge`, and
> `cairn memory batch-critic`. See [cli-reference.md](./cli-reference.md).

---

## Layer 5 — Knowledge (business docs + workflows) (5 tools)

The PO/product ingestion path for business documents, plus ordered
procedural workflows.

| Tool | Purpose |
|------|---------|
| `knowledge_add(title, body, doc_type, tags?, affects_modules?, affects_repos?, resource?, epic_link?)` | Ingest a business document. `doc_type`: `business-rule\|spec\|decision`. Tags and `affects_*` are comma-separated. |
| `knowledge_search(query, limit?)` | Search business knowledge docs by meaning. **Bridges to the code graph**: docs with `affects_repos` get `cross_repo_deps` results appended. |
| `knowledge_delete(doc_id)` | Delete a knowledge document and its embedding rows. **Irreversible.** |
| `knowledge_status(doc_id, new_status)` | Update `doc_status` (`active → superseded → archived`). |
| `trace_workflow(ref)` | Trace a procedural workflow's ordered steps by title, slug, or concept_id. |

### Behavioral notes (knowledge)

- **`knowledge_search`** is the business-docs layer (different from the
  Layer 2 bundle-level `search_knowledge`). It bridges to the code graph:
  documents tagged with `affects_repos` get `cross_repo_deps` results
  appended. Lexical search works without the semantic extra; semantic adds
  recall. If the corpus isn't embedded, run `cairn knowledge embed`.
- **`trace_workflow`**: a workflow is a knowledge doc with `doc_type="workflow"`
  (see `cairn knowledge workflow` in the CLI). Each step may carry a
  `symbol`/`file` — follow those into `find_definition`/`get_callers` to jump
  from the procedure into the actual code.

---

## Resource: index status

In addition to the 27 tools, the server exposes a browsable resource:

| Resource | Purpose |
|----------|---------|
| `cairn://status` | Index freshness + build stats + health for the current workspace (symbol/edge/file counts, edges-resolved fraction, files pending reindex), plus a `health` block: active backend degradations (embeddings hash fallback, ANN unavailable), pending-sync count, last-build age, and the 24h tool error rate. Read via `read_resource("cairn://status")` to decide whether to trust a graph query or first prompt `cairn update` — and whether the store is degraded before blaming the query. |

---

## Workflow and resolution notes (from `AGENTS.md`)

### explore-first

For almost any structural question ("how does X work", a flow, surveying an
area):

1. Call `explore(query)` **FIRST** — one call, one answer.
2. Reach for specific tools only to drill down when `explore` is thin:
   - `ask_compass(query)` — cross-layer routing
   - `get_callers` / `get_callees` / `impact_analysis` — deeper call-graph traversal
   - `search_knowledge` / `recall_memory` — knowledge-layer questions

### Before editing a file

1. `ask_compass(file_path="<path>")` to load compass + memory context.
2. `find_definition` for any symbol you need to understand.
3. `get_callers` for within-repo dependents of what you're changing.
4. `cross_repo_deps(repo_name)` for cross-repo blast radius.
5. `impact_analysis(symbol_name)` for breaking changes (within-repo recursive).

### After completing a task

1. Run `cairn update` to refresh the graph with your changes.
2. `record_memory` for any learnings (`decision`/`pattern`/`mistake`/`workaround`),
   with `confidence` (0.0-1.0).

### Freshness

Freshness is maintained two ways. At boot, the server runs a one-time
catch-up (diffing the files table against disk) to absorb edits made while it
was down. Then — with the `[watch]` extra installed and `CAIRN_WATCH` not
disabled — it starts a **live file watcher** so source edits made while the
server runs are reindexed within a ~2s debounce window, without a restart or
an explicit `cairn update`. Each changed file is first marked in
`pending_sync`, so concurrent MCP readers immediately see a staleness banner
on results touching that file until the reindex lands. Without the `[watch]`
extra (or with `CAIRN_WATCH=0`, or in read-only mode), freshness falls back to
boot catch-up + explicit `cairn update` — in that degraded mode, edits made
while the server is up require a server restart (or `cairn build`) to show up
in results. Use the `cairn://status` resource for the aggregate freshness
picture.
