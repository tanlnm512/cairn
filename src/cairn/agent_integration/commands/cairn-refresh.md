# Manual Full Refresh

Run all maintenance tasks manually. These are bulk operations — CLI is the
correct surface (Golden Rule 10: mutations via CLI).

1. `cairn build` -- full graph rebuild (all repos)
2. `cairn wiki generate` -- regenerate wiki for each repo
3. `cairn compass validate` -- check compass references
4. `cairn compass gaps` -- find uncovered modules
5. For each gap (max 5): `cairn compass generate <module>`
6. `cairn compass flow-gaps` -- find undocumented business flows
7. For each flow gap (max 5): `cairn compass flow <entry> --as-workflow`
8. `cairn memory batch-critic` -- process pending drafts
9. `cairn memory decay` -- clean up

This is the manual equivalent of the weekly cron job.
Expect it to take 15-30 minutes depending on codebase size.
Report a summary of everything that was refreshed.
