# Cairn Docs — Index

Agent-oriented documentation, grounded in the current codebase. Each page
states when to read it; every name, command, and env var on these pages is
verbatim from the source.

| Question you have | Read |
|---|---|
| What is this system and how do the pieces fit? | [architecture.md](architecture.md) |
| How does code get into the graph? (`cairn build` / `update`) | [indexing.md](indexing.md) |
| How does a query become ranked results? | [retrieval.md](retrieval.md) |
| How do documents and memories get stored and retrieved? | [knowledge-and-memory.md](knowledge-and-memory.md) |
| What MCP tools exist and what do they return? | [mcp-tools.md](mcp-tools.md) |
| What CLI commands exist? | [cli-reference.md](cli-reference.md) |
| How do I configure cairn (cairn.json, env vars, extras)? | [configuration.md](configuration.md) |

## Procedures (kept from the previous doc set)

- [review-checklist.md](review-checklist.md) — the PR review/audit gate for every change.
- [release-checklist.md](release-checklist.md) — pre-release verification and the release procedure.

## Diagrams

Standalone HTML, open in any browser:

- [system-architecture.html](diagrams/system-architecture.html) — surfaces → core engines → storage.
- [indexing-pipeline.html](diagrams/indexing-pipeline.html) — scan to atomic `.kg` swap.
- [retrieval-pipeline.html](diagrams/retrieval-pipeline.html) — hybrid retrieval and the rerank gate.
- [doc-ingestion-pipeline.html](diagrams/doc-ingestion-pipeline.html) — staged doc ingestion with dry-run default.

## Conventions used across these docs

- Identifiers in `backticks` are verbatim module / symbol / command / env-var names.
- Tables carry enumerable facts; prose carries flow and rationale.
- No line numbers — use `cairn def <symbol>` or `explore` to jump to current source.
