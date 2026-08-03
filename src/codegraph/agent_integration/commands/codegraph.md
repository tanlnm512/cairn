# Codegraph Status

Run `cg status` and summarize the output (cross-layer health — no single MCP
tool aggregates this; the CLI is the right surface). Show:
- Graph health (repos, symbols, last updated)
- Compass coverage (modules covered vs gaps)
- Memory distribution (by tier)
- Any warnings or issues

For a quick MCP-side read of the knowledge layers (no CLI): `memory_digest()`
for top tribal memories, and `search_knowledge(query, type_filter="Compass")`
to spot-check compass coverage of a specific module.

If any layer looks stale, suggest remediation commands (all CLI — bulk mutations):
- `cg build` if graph is empty/corrupt
- `cg compass gaps` then `cg compass generate <module>` for missing compass
- `cg compass flow-gaps` then `cg compass flow <entry> --as-workflow` for missing flow docs
- `cg memory batch-critic` if drafts are piling up
