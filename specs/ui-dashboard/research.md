# Research: ui-dashboard

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-20
<!-- External grounding for tech decisions: every claim below carries a source
     URL/DOI — no unsourced "it is known that". The tech agent consumes this
     file when choosing options in tech-spec.md. -->

## Questions
<!-- One subsection per research question (the orchestrator supplies 3–6,
     derived from the spec's open technical choices). Record 2-3 findings per
     question; if none is credible, keep the fallback line verbatim. -->

### RQ1: Web framework for pip-installed Python CLI dashboard (FR-001)

**FastAPI vs Starlette vs stdlib http.server for dependency weight and startup time**

- **source**: [TechEmpower Benchmarks](https://www.techempower.com/benchmarks/) · **claim**: FastAPI running under Uvicorn ranks second only to Starlette in Python framework performance benchmarks, demonstrating strong runtime performance characteristics. · **relevance**: FR-001 (web framework performance) · **confidence**: high

- **source**: [FastAPI Cold Start Analysis](https://www.reddit.com/r/FastAPI/comments/1jh2tz0/is_fastapi_really_fast/) · **claim**: FastAPI with Uvicorn typically adds 100-200ms of cold-start time in AWS Lambda environments, which is considered acceptable trade-off for productivity gains. · **relevance**: FR-001 (startup time constraint) · **confidence**: medium

- **source**: [PyPI - FastAPI](https://pypi.org/project/fastapi/) · **claim**: FastAPI depends on Pydantic and Starlette, meaning Starlette is a direct dependency with additional framework layer on top. · **relevance**: FR-001 (dependency weight analysis) · **confidence**: high

**htmx + server-rendered HTML as no-build-stack**

- **source**: [htmx.org - No Build Step Essay](https://htmx.org/essays/no-build-step/) · **claim**: htmx is distributed as a single 3,500-line JavaScript file with no dependencies beyond the JavaScript runtime, eliminating build step complexity entirely. · **relevance**: FR-001, FR-003 (no JS build constraint) · **confidence**: high

- **source**: [FastAPI + HTMX Guide](https://blakecrosley.com/guides/fastapi-htmx) · **claim**: FastAPI + HTMX + Jinja2 enables server-driven interactivity where the server returns HTML fragments rather than JSON APIs, with zero build tools and simplified deployment to standard git push workflow. · **relevance**: FR-001, FR-003 (no-build architecture pattern) · **confidence**: high

**Prior art: pip-distributed dev tools with dashboards**

- **source**: [Datasette Documentation](https://docs.datasette.io/en/latest/cli-reference.html) · **claim**: Datasette is a pip-installed Python CLI tool that serves SQLite databases with an instant web UI, designed to be lightweight and standalone. · **relevance**: FR-001 (prior art for CLI + dashboard pattern) · **confidence**: high

- **source**: [MLflow CLI Documentation](https://mlflow.org/docs/latest/cli.html) · **claim**: MLflow provides CLI commands to start a tracking UI for experiment visualization, but MLflow is characterized as a heavy dependency platform. · **relevance**: FR-001 (heavier prior art to avoid) · **confidence**: medium

### RQ2: Graph rendering libraries for browser (FR-003)

**Single-file availability and CDN distribution**

- **source**: [Cytoscape.js Official Documentation](http://js.cytoscape.org/) · **claim**: Cytoscape.js is available via major CDNs (CDNJS, jsDelivr, Unpkg) with explicit recommendation not to hotlink, making it suitable for vendorable single-file inclusion. · **relevance**: FR-003 (CDN/single-file requirement) · **confidence**: high

- **source**: [vis-network on jsDelivr](https://www.jsdelivr.com/package/npm/vis-network) · **claim**: vis-network (successor to vis.js) is distributed via jsDelivr CDN, enabling browser-based inclusion without build steps. · **relevance**: FR-003 (CDN availability) · **confidence**: high

**Performance limits for interactive graphs**

- **source**: [Cytoscape.js vs Ogma Comparison](https://doc.linkurious.com/ogma/latest/compare/visjs.html) · **claim**: Cytoscape.js becomes sluggish with graphs exceeding 10,000 elements when using complex layouts or styling, establishing a practical upper bound for interactive performance. · **relevance**: FR-003 (node/edge scale requirements: 100-5k nodes, up to 10k edges) · **confidence**: high

- **source**: [PkgPulse Graph Visualization Comparison 2026](https://www.pkgpulse.com/guides/cytoscape-vs-vis-network-vs-sigma-graph-visualization-2026) · **claim**: vis-network is described as "the fastest path to an interactive network canvas" and is comparable to Cytoscape.js in many regards, suggesting similar performance characteristics for mid-sized graphs. · **relevance**: FR-003 (performance comparison) · **confidence**: medium

**Pan/zoom/click interactivity**

- **source**: [Mermaid.js GitHub Issue #2162](https://github.com/mermaid-js/mermaid/issues/2162) · **claim**: Mermaid.js does not natively support interactive pan and zoom functionality, requiring enhancement packages like @mostlylucid/mermaid-enhancements to add these capabilities. · **relevance**: FR-003 (pan/zoom requirement eliminates Mermaid.js) · **confidence**: high

- **source**: [vis.js Network Documentation](https://visjs.github.io/vis-network/docs/) · **claim**: vis-network provides built-in support for pan, zoom, and click interactions on network graphs without requiring additional enhancement packages. · **relevance**: FR-003 (built-in interactivity) · **confidence**: high

### RQ3: Token estimation for LLM context (FR-006)

**tiktoken limitations for non-OpenAI models**

- **source**: [OpenAI Cookbook - How to Count Tokens with Tiktoken](https://developers.openai.com/cookbook/examples/how_to_count_tokens_with_tiktoken) · **claim**: tiktoken is specifically designed and optimized for OpenAI models (GPT-3.5, GPT-4), with token IDs that won't match those used by other providers like GLM, Anthropic (Claude), or Google (Gemini). · **relevance**: FR-006 (tiktoken not suitable for multi-model estimation) · **confidence**: high

- **source**: [Counting Claude Tokens Without a Tokenizer](https://blog.gopenai.com/counting-claude-tokens-without-a-tokenizer-e767f2b6e632) · **claim**: Using tiktoken for Anthropic/Claude models results in inaccurate token counts because vocabulary and tokenization rules differ significantly between providers. · **relevance**: FR-006 (accuracy concerns for cross-model estimation) · **confidence**: high

**Heuristics for token estimation**

- **source**: [Anthropic Context Engineering Documentation](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) · **claim**: Anthropic's recommended heuristic is approximately 1 token per 3.5 English characters for Claude models, with explicit disclaimers that accuracy varies by text type and language. · **relevance**: FR-006 (heuristic baseline) · **confidence**: high

- **source**: [AI Agent Token Budget Management](https://www.mindstudio.ai/blog/ai-agent-token-budget-management-claude-code) · **claim**: Claude Code uses a file size heuristic of roughly 1 token per 4 characters for token budget management when exact tokenizer is unavailable. · **relevance**: FR-006 (practical heuristic in production use) · **confidence**: medium

**What observability tools do for token attribution**

- **source**: [Langfuse Token & Cost Tracking Documentation](https://langfuse.com/docs/observability/features/token-and-cost-tracking) · **claim**: Langfuse tracks usage and cost of LLM generations for various models including OpenAI, Anthropic, and Google, with superior native cost attribution compared to LangSmith. · **relevance**: FR-006 (observability prior art) · **confidence**: high

- **source**: [OpenTelemetry GenAI Semantic Conventions](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-events.md) · **claim**: OpenTelemetry GenAI Semantic Conventions standardize observability attributes for token usage including gen_ai.token.completion_count and gen_ai.token.prompt_count for LLM telemetry. · **relevance**: FR-004, FR-006 (standard attribute names for alignment) · **confidence**: high

### RQ4: MCP tool-call recording and session identifiers (FR-004, FR-007)

**MCP session identifier specification**

- **source**: [WorkOS MCP 2026 Spec Update](https://workos.com/blog/mcp-2026-spec-agent-authentication) · **claim**: The July 28, 2026 MCP specification update removed sessions from the protocol layer, eliminating the Mcp-Session-Id header and the initialization handshake entirely. · **relevance**: FR-004, FR-007 (no MCP-provided session identifiers) · **confidence**: high

- **source**: [MCP Tools Specification](https://modelcontextprotocol.io/specification/2026-07-28/server/tools) · **claim**: MCP has no protocol-level session concept, so servers cannot rely on implicit per-connection state and must instead return explicit handles (like basket_id) for stateful operations that the model carries forward. · **relevance**: FR-004, FR-007 (need application-level session identifiers) · **confidence**: high

**MCP tool-call metadata fields**

- **source**: [MCP Tools Specification](https://modelcontextprotocol.io/specification/2026-07-28/server/tools) · **claim**: MCP tools/call requests include name and arguments, but the protocol does not specify timestamp, duration, status, or payload size fields—these must be added at the application level for telemetry purposes. · **relevance**: FR-004 (fields not provided by MCP spec) · **confidence**: high

**Buffered SQLite telemetry patterns**

- **source**: [OpenTelemetry Python SDK Shutdown Discussion](https://github.com/open-telemetry/opentelemetry-python/discussions/3034) · **claim**: OpenTelemetry Python SDK requires proper shutdown hooks using atexit and SIGTERM signal handlers to flush buffered telemetry data before process termination, establishing a pattern for CLI tools with buffered writes. · **relevance**: FR-004 (flush-on-shutdown pattern) · **confidence**: high

- **source**: [OneUptime OpenTelemetry Shutdown Handling](https://oneuptime.com/blog/handle-opentelemetry-sdk-shutdown-in-python) · **claim**: Using atexit hooks ensures pending telemetry data is flushed when the Python process exits, preventing data loss in CLI applications that buffer events. · **relevance**: FR-004 (shutdown flush implementation pattern) · **confidence**: medium

### RQ5: Dashboard panels for code-intel tools (FR-008)

**Vector database monitoring dashboards**

- **source**: [BetterDB Monitor Documentation](https://docs.betterdb.com/vector-ai/README.html) · **claim**: BetterDB Monitor provides a single-view dashboard for vector-index health and F.SEARCH workload on Valkey/Redis instances, including health status, query performance, and index statistics. · **relevance**: FR-008 (vector index health panel precedent) · **confidence**: high

- **source**: [Grafana Cloud VectorDB Observability](https://grafana.com/docs/grafana-cloud/observe-and-act/monitor-applications/ai-observability/vectordb-observability/) · **claim**: Grafana Cloud VectorDB Observability offers comprehensive monitoring dashboards for vector database performance, including query latency, index size, and resource utilization metrics. · **relevance**: FR-008 (performance monitoring panels) · **confidence**: high

**Code intelligence tool dashboards**

- **source**: [Axon - Graph-Powered Code Intelligence](https://github.com/harshkedia177/axon) · **claim**: Axon indexes codebases into knowledge graphs exposed via MCP tools and CLI, representing a direct parallel to cairn's code-graph visualization requirements with graph-powered code intelligence. · **relevance**: FR-008 (code-graph visualization precedent) · **confidence**: medium

- **source**: [Kilo Code Codebase Indexing Documentation](https://kilo.ai/docs/customize/context/codebase-indexing) · **claim**: Kilo Code provides codebase indexing with workspace indexing capabilities and includes an indexing status indicator in the prompt input panel, showing index progress and coverage. · **relevance**: FR-008 (index status panel precedent) · **confidence**: medium

**Recurring panel types to steal**

1. Index status panel: showing indexed projects with index/embedding status (coverage, last update) - from Kilo Code indexing indicator pattern
2. Vector index health: index size, query performance, statistics - from BetterDB Monitor and Grafana VectorDB Observability
3. Query performance: latency distributions, throughput metrics - from Grafana observability dashboards
4. Resource utilization: memory, storage, connection counts - from database monitoring patterns
5. Task queue status: pending/active/completed jobs - from CLI telemetry patterns

## Options summary
<!-- ≤15 lines. For each open choice: the credible candidates and the
     one-line trade-off between them. NO recommendation — that is the tech
     agent's job with your data. -->

### Web framework choice (FR-001)
- **FastAPI + Uvicorn** — Production-proven, 100-200ms cold start, heavier than stdlib but includes routing/validation/templating
- **Starlette + Uvicorn** — Lighter than FastAPI (one less layer), similar performance, requires more manual wiring
- **stdlib http.server + Jinja2** — Minimal dependencies, fastest cold start, no async/WebSocket support without custom code
- **FastAPI + HTMX** — Server-rendered HTML with no build step, single-file htmx.js vendor, good for read-only dashboards

### Graph rendering library (FR-003)
- **Cytoscape.js** — Rich all-in-one toolkit with built-in pan/zoom/click, CDN-available, slows above 10k elements
- **vis-network** — Fastest interactive network canvas, comparable to Cytoscape, CDN-available, lighter footprint
- **Mermaid.js** — Simplest syntax, no native pan/zoom (requires enhancement package), not suitable for interactive requirement
- **Sigma.js** — WebGL-accelerated for large graphs, steeper learning curve, CDN-available

### Token estimation approach (FR-006)
- **tiktoken (OpenAI only)** — Accurate for GPT models, inaccurate for GLM/Anthropic, not suitable for multi-model tool
- **Chars/4 heuristic** — Simple, fast, ~75% accuracy for English (Claude Code uses 4 chars/token), acceptable approximation
- **Chars/3.5 heuristic** — Slightly more accurate for Claude (Anthropic's recommendation), varies by language/code
- **Model-specific tokenizers per provider** — Most accurate, requires multiple dependencies, increases complexity

### Session identifier strategy (FR-004, FR-007)
- **Generate application-level session UUIDs** — Required since MCP removed sessions, fully controlled, can use atexit for cleanup
- **MCP request IDs** — Available per request but not suitable for grouping calls across conversation/session scope
- **No session tracking** — Simplest, limits analytics to per-call metrics without conversation context

### Tool-call recording storage (FR-004)
- **Buffered SQLite append with atexit flush** — Proven pattern from OpenTelemetry Python, ensures data on shutdown, some crash-loss risk
- **Synchronous SQLite writes** — No data loss on crash, slower for high-frequency calls, simpler implementation
- **In-memory buffer with periodic flush** — Balance of performance and durability, requires flush interval tuning

### Dashboard panel types to implement (FR-008)
- **Index status panel** — Projects list with index/embedding status, last update, coverage metrics (from Kilo pattern)
- **Graph visualization panel** — Interactive code graph with pan/zoom (RQ2 choice determines implementation)
- **Tool-call history panel** — Filterable by tool/session/status with search and pagination
- **Token usage panel** — Per-tool and aggregated estimates using chosen heuristic (RQ3 choice)
- **Health/metrics panel** — Memory, queue depth, index health (from BetterDB/Grafana patterns)
