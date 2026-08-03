# Cairn: CLI Fallback

If MCP tools are unavailable, use CLI commands (`cg` is on PATH in the venv):

- `cg def <symbol>` -- find definition
- `cg callers <symbol>` -- who calls this
- `cg impact <symbol>` -- what breaks if changed (within-repo). See Golden
  Rule 6 (`references/golden-rules.md`) before running this on a common name
  -- prefer `scripts/impact_guard.py <symbol>` if it's available in this
  environment.
- `cg deps <repo>` -- cross-repo dependency map
- `cg context <file>` -- load context for a file
- `cg ask "<question>"` -- router across all layers
- `cg memory record <type> "<title>"` -- capture a learning
- `cg memory forget <path>` -- permanently delete a memory
- `cg memory demote <path> --tier raw` -- demote to lower tier
- `cg memory purge --dry-run` -- purge old archived (CLI-only, dangerous)
- `cg knowledge remove <doc_id>` -- delete knowledge doc + embeddings
- `cg knowledge status <doc_id> <status>` -- update doc_status
- `cg compass flow <entry> [--as-workflow] [--max-steps N] [--use-llm] [--dry-run]` -- trace a
  business flow's call chain and generate a flow compass (+ optional workflow)
- `cg compass flow-gaps [--min-edges 5] [--generate] [--limit N]` -- find undocumented
  business flows; `--generate` batch-generates them
- `cg compass flow-gaps --generate --as-workflow` -- batch-generate flow compasses + workflows
- `cg uninstall [--full|--agents-only|--graph-only]` -- full teardown (agents, hooks, graph, binary)
