# Post-Work Knowledge Capture

After completing a coding task. Golden Rule 10: reads + per-item writes via MCP,
bulk mutations via CLI.

1. Run `cairn update` to refresh the graph with any code changes (mutation — CLI only)
2. Analyze what was done in this session:
   - What files were changed?
   - What decisions were made?
   - Were there any errors or gotchas encountered?
3. For each learning, call `record_memory(type, title, body, confidence)` (MCP write tool):
   Types: decision, pattern, mistake, workaround
   IMPORTANT: the title should contain the key symbol name, because recall_memory
   is symbol/title-keyed, not full-text. Good: "ApiFactory uses per-flavor base URLs".
   Bad: "we changed how URLs work". Future sessions search by symbol.
4. Run `cairn memory batch-critic` to process pending drafts (bulk mutation — CLI only)
5. If any memory scored above 0.7, suggest:
   `cairn memory promote <path>` (bulk tier change — CLI only)
   Or via MCP: `memory_promote("<path>")` for a single promotion.

Report what was captured and any memories worth promoting.
