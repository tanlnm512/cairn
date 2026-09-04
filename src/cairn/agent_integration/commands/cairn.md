# Cairn Status

Run `cairn status` and summarize the output (cross-layer health — no single MCP
tool aggregates this; the CLI is the right surface). Show:
- Graph health (repos, symbols, last updated)
- Compass coverage (modules covered vs gaps)
- Memory distribution (by tier)
- Any warnings or issues

For a quick MCP-side read of the knowledge layers (no CLI): `recall_memory()`
for top tribal memories, and `search_knowledge(query, type_filter="Compass")`
to spot-check compass coverage of a specific module.

If any layer looks stale, suggest remediation commands (all CLI — bulk mutations):
- `cairn build` if graph is empty/corrupt
- `cairn compass gaps` then `cairn compass generate <module>` for missing compass
- `cairn compass flow-gaps` then `cairn compass flow <entry> --as-workflow` for missing flow docs
- `cairn memory batch-critic` if drafts are piling up
