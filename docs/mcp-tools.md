# MCP Tools

← [README](../README.md)

Read this when you need the tool surface: what the 26 tools are, how they're
grouped, and how the server behaves. For per-tool empirical quirks, see the
"Tool Quirks" table in [AGENTS.md](../AGENTS.md) — it is kept there so every
agent session loads it.

## Server

- Implementation: the `cairn-daemon` crate on the official `rmcp` SDK
  (`server.rs`, tool dispatch in `tools.rs` over the `cairn-query` tool
  modules).
- Transport: Streamable HTTP daemon on `:9876` — one shared process serves
  every client (`cairn connect` ensures it is running; `cairn serve
  start|stop|status|restart` manage it, `run` for the foreground daemon).
  Read-write by default; `--read-only` opens the store read-only so the
  write tools fail instead of landing. Bearer-key auth gates every path but
  `GET /healthz` on non-loopback binds (see [deployment.md](deployment.md)).
- Every tool call is instrumented into `tool_metrics` (duration, status,
  payload sizes) — this is what `/history` and `/tokens` on the dashboard show.
- Each call opens its own SQLite connection — read-only for the pure read
  tools, writable for the ones that may land rows (`explore`'s memory credit,
  `recall_memory`'s read-through refs, `record_memory`, `knowledge_delete`,
  `generate_flow`). `knowledge_add` and `knowledge_status` write the OKF
  bundle directly (no SQLite connection) and are refused in read-only mode;
  `wiki_generate` reads over a read-only connection and writes pages to the
  bundle.

## The 26 tools by layer

**L1 — Graph** (`cairn-query` `tools_nav.rs`, `tools_repo.rs`,
`tools_search.rs`, `tools_flow.rs`; 12):

| Tool | Purpose |
|---|---|
| `explore` | recommended first call: verbatim source + call paths + depth-2 blast radius in one answer |
| `find_definition` | locate a symbol's definition |
| `get_callers` / `get_callees` | direct call relationships (precise by default, `fuzzy=True` for candidates) |
| `impact_analysis` | recursive blast radius (precise; pair with `cross_repo_deps` for public APIs) |
| `semantic_search` | the hybrid pipeline: BM25 + dense vectors fused by RRF; vectors come from the configured `CAIRN_EMBED_BACKEND` (hash by default, or an OpenAI-compatible embeddings server) |
| `search_symbols` | FTS5 name search (wildcards, substrings, camelCase) |
| `repo_map` | deterministic repository orientation with counts, hubs, hotspots, and dropped counts |
| `file_api` | body-free symbol records for one indexed file; `signature` is null when unavailable |
| `cross_repo_deps` | cross-repo consumers of a repo's API |
| `visualize_graph` | Mermaid/DOT/JSON rendering of a subgraph |
| `path` | shortest structural path between two symbols over the live graph (same hop chain the `cairn path` CLI prints) |

**L1 — Federation** (`tools_federation.rs`, 1):

| Tool | Purpose |
|---|---|
| `federated_search` | one query across every registered workspace store: merged ranking, per-workspace attribution, lexical fallback for embedding-less stores, dropped stores named; `shared_embed=true` opt-in serves stamp-compatible stores from one embedding backend |

**L2/L3 — Knowledge base + Compass** (`tools_km.rs`, `tools_flow.rs`; 5):

| Tool | Purpose |
|---|---|
| `ask_compass` | cross-layer router (graph + wiki + compass + memory) |
| `get_compass` | 25–35 line navigation guide for a module |
| `search_knowledge` | search OKF documents (`type_filter="Wiki"` for architecture docs) |
| `trace_flow` | execution-order trace through the graph |
| `generate_flow` | write a flow compass (the one non-read-only tool here) |

**L4 — Memory** (`tools_km.rs`, 2):

| Tool | Purpose |
|---|---|
| `record_memory` | capture decision / pattern / mistake / workaround (redacted + scored) |
| `recall_memory` | symbol/title-keyed hybrid recall |

Lifecycle verbs are CLI-only: `cairn memory <verb>` where verb is
`digest`, `evolve`, `promote`, `demote`, `forget` (delete), or `decay`.

**L5 — Knowledge documents** (`tools_km.rs` + `tools_wiki.rs`, 6):

| Tool | Purpose |
|---|---|
| `knowledge_add` | add a document concept |
| `knowledge_search` | semantic search over knowledge docs |
| `knowledge_delete` | remove a document (destructive) |
| `knowledge_status` | set/query document status |
| `trace_workflow` | workflow-definition execution trace |
| `wiki_generate` | plan a repo's wiki and queue its page-writing tasks (`repo`, `pages`, `refine_catalog`, `diagrams`, `force`) |

## Wiki generation

The wiki is the agent-facing knowledge surface for the whole workspace —
code or documents. It stores two kinds with disjoint jobs: the plan
manifest (`.knowledge/_wiki/manifest.json`) records pipeline intent only
(pages, seeds, task linkage, queue attempts), while promoted `Wiki-Article`
concepts under `wiki/pages/{repo}/{page_id}` are the only record of what
exists. Lifecycle state is derived at read time — never stored.

`wiki_generate` plans the deterministic page outline (overview + top modules)
and queues the writing work — cairn never calls an LLM itself, so agents
finish the wiki through the task queue:

1. Call `wiki_generate`; it returns the page plan plus the queued
   `wiki-page` task ids — or, with `refine_catalog=True`, the single
   `wiki-catalog` task id and re-run guidance (page tasks queue only after
   that task completes and its refined outline validates; invalid entries
   revert to the deterministic plan).
2. For each task id (keyed by the qualified `{repo}/{page_id}`
   resource): `cairn task show <id>` → `cairn task claim <id>` →
   write the article per the task's output spec (markdown ending in a
   `## Sources` footer; Mermaid fences only when diagrams were requested) →
   `cairn task complete <id> --result-file <path>`.
3. The deterministic critic verifies every reference against the graph. A
   passing completion is promoted as a `Wiki-Article` concept under
   `wiki/pages/{repo}/{page_id}` with the verified sources in its
   frontmatter; a failing one spawns a bounded revise cycle.
4. Track progress with `cairn wiki status` (per-page derived state
   plus a fresh/stale verdict against the repo's current HEAD; a page
   without promoted content is always `unknown`), re-queue failures
   with `cairn wiki retry`; `cairn wiki search` and compass routing surface
   the promoted articles, `cairn wiki export --dir DIR [--force]` writes
   them out as markdown files (frontmatter preserved), and
   `cairn wiki enrich [<page-id>|--all]` queues critic-gated append-only
   extensions of promoted pages. Queue maintenance never needs store
   surgery: `cairn task drop <id>` abandons a pending/in-progress task and
   `cairn task list --kind-prefix wiki-page` lists every chain hop.

## Choosing tools

`explore` first for almost any "how does X work" question; escalate only
along its known limits (recursive callers → `impact_analysis`; execution
order → `trace_flow`; why/decisions → `ask_compass` / `recall_memory`;
synonyms → `semantic_search`). The full decision tree ships inside the
installed skill
(`rust/crates/cairn-cli/templates/agent_integration/skill/references/decision-tree.md`,
written by `cairn install-agents`).

## CLI fallback

If MCP is unavailable, every layer has a CLI equivalent — see
[cli-reference.md](cli-reference.md).
