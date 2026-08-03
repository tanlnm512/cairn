# Manual Full Refresh

Run all maintenance tasks manually. These are bulk operations — CLI is the
correct surface (Golden Rule 10: mutations via CLI).

1. `cg build` -- full graph rebuild (all repos)
2. `cg wiki generate` -- regenerate wiki for each repo
3. `cg compass validate` -- check compass references
4. `cg compass gaps` -- find uncovered modules
5. For each gap (max 5): `cg compass generate <module>`
6. `cg compass flow-gaps` -- find undocumented business flows
7. For each flow gap (max 5): `cg compass flow <entry> --as-workflow`
8. `cg memory batch-critic` -- process pending drafts
9. `cg memory decay` -- clean up

This is the manual equivalent of the weekly cron job.
Expect it to take 15-30 minutes depending on codebase size.
Report a summary of everything that was refreshed.
