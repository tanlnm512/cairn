# MCP Tools

Read this when you need the tool surface: what the 28 tools are, how they're
grouped, and how the server behaves. For per-tool empirical quirks, see the
"Tool Quirks" table in [AGENTS.md](../AGENTS.md) — it is kept there so every
agent session loads it.

## Server

- Implementation: FastMCP, singleton in `src/cairn/mcp_server/_server_core.py`.
- Transports: **stdio** (default, one process per client) or **SSE daemon**
  on `:9876` (`cairn serve start|stop|status|restart`, `run` for foreground).
- Boot sequence (`server.py:run`): verify exactly 28 tools registered →
  parent-death watchdog (stdio) → boot catch-up reindex (`ensure_fresh_force`)
  → memory decay → live file watcher (`[watch]` extra).
- Every tool call is instrumented into `tool_metrics` (duration, status,
  payload sizes) — this is what `/history` and `/tokens` on the dashboard show.
- `CAIRN_READ_ONLY=1` makes the server refuse write tools.
- Resource `cairn://status` exposes live server status.

## The 28 tools by layer

**L1 — Graph** (`tools_graph.py`, 9):

| Tool | Purpose |
|---|---|
| `explore` | recommended first call: verbatim source + call paths + depth-2 blast radius in one answer |
| `find_definition` | locate a symbol's definition |
| `get_callers` / `get_callees` | direct call relationships (precise by default, `fuzzy=True` for candidates) |
| `impact_analysis` | recursive blast radius (precise; pair with `cross_repo_deps` for public APIs) |
| `semantic_search` | the hybrid pipeline from [retrieval.md](retrieval.md) |
| `search_symbols` | FTS5 name search (wildcards, substrings, camelCase) |
| `cross_repo_deps` | cross-repo consumers of a repo's API |
| `visualize_graph` | Mermaid/DOT/JSON rendering of a subgraph |

**L2/L3 — Knowledge base + Compass** (`tools_compass.py`, 5):

| Tool | Purpose |
|---|---|
| `ask_compass` | cross-layer router (graph + wiki + compass + memory) |
| `get_compass` | 25–35 line navigation guide for a module |
| `search_knowledge` | search OKF documents (`type_filter="Wiki"` for architecture docs) |
| `trace_flow` | execution-order trace through the graph |
| `generate_flow` | write a flow compass (the one non-read-only tool here) |

**L4 — Memory** (`tools_memory.py`, 8):

| Tool | Purpose |
|---|---|
| `record_memory` | capture decision / pattern / mistake / workaround (redacted + scored) |
| `recall_memory` | symbol/title-keyed hybrid recall |
| `memory_digest` | recent-memory digest |
| `memory_evolve` / `memory_promote` / `memory_demote` | version, raise, lower a memory |
| `memory_decay` | run the decay cycle |
| `memory_delete` | remove a memory (destructive) |

**L5 — Knowledge documents** (`tools_knowledge.py` + `tools_wiki.py`, 6):

| Tool | Purpose |
|---|---|
| `knowledge_add` | add a document concept |
| `knowledge_search` | semantic search over knowledge docs |
| `knowledge_delete` | remove a document (destructive) |
| `knowledge_status` | set/query document status |
| `trace_workflow` | workflow-definition execution trace |
| `wiki_generate` | plan a repo's wiki and queue its page-writing tasks (`repo`, `pages`, `refine_catalog`, `diagrams`, `force`) |

## Wiki generation

`wiki_generate` plans the deterministic page outline (overview + top modules)
and queues the writing work — cairn never calls an LLM itself, so agents
finish the wiki through the task queue:

1. Call `wiki_generate`; it returns the page plan plus the queued
   `wiki-page` task ids — or, with `refine_catalog=True`, the single
   `wiki-catalog` task id and re-run guidance (page tasks queue only after
   that task completes and its refined outline validates; invalid entries
   revert to the deterministic plan).
2. For each task id: `cairn task show <id>` → `cairn task claim <id>` →
   write the article per the task's output spec (markdown ending in a
   `## Sources` footer; Mermaid fences only when diagrams were requested) →
   `cairn task complete <id> --result-file <path>`.
3. The deterministic critic verifies every reference against the graph. A
   passing completion is promoted as a `Wiki-Article` concept under
   `wiki/pages/{repo}/{page_id}` with the verified sources in its
   frontmatter; a failing one spawns a bounded revise cycle.
4. Track progress with `cairn wiki status` (per-page state plus a
   fresh/stale verdict against the repo's current HEAD), re-queue failures
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
installed skill (`src/cairn/agent_integration/skill/references/decision-tree.md`).

## CLI fallback

If MCP is unavailable, every layer has a CLI equivalent — see
[cli-reference.md](cli-reference.md).
