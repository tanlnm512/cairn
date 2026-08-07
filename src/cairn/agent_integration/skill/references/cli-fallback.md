# Cairn: CLI Fallback

If MCP tools are unavailable, use CLI commands (`cairn` is on PATH in the venv):

- `cairn def <symbol>` -- find definition
- `cairn callers <symbol>` -- who calls this
- `cairn impact <symbol>` -- what breaks if changed (within-repo). See Golden
  Rule 6 (`references/golden-rules.md`) before running this on a common name
  -- prefer `scripts/impact_guard.py <symbol>` if it's available in this
  environment.
- `cairn deps <repo>` -- cross-repo dependency map
- `cairn context <file>` -- load context for a file
- `cairn ask "<question>"` -- router across all layers
- `cairn memory record <type> "<title>"` -- capture a learning
- `cairn memory forget <path>` -- permanently delete a memory
- `cairn memory demote <path> --tier raw` -- demote to lower tier
- `cairn memory purge --dry-run` -- purge old archived (CLI-only, dangerous)
- `cairn knowledge remove <doc_id>` -- delete knowledge doc + embeddings
- `cairn knowledge status <doc_id> <status>` -- update doc_status
- `cairn compass flow <entry> [--as-workflow] [--max-steps N] [--use-llm] [--dry-run]` -- trace a
  business flow's call chain and generate a flow compass (+ optional workflow)
- `cairn compass flow-gaps [--min-edges 5] [--generate] [--limit N]` -- find undocumented
  business flows; `--generate` batch-generates flow compasses (no workflows — use
  `cairn compass flow --as-workflow` per flow for that)
- `cairn uninstall [--full|--agents-only|--graph-only]` -- full teardown (agents, hooks, graph, binary)
