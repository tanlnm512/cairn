# Full System Audit

Run a comprehensive health check across all layers. Validation and maintenance
steps are bulk operations — CLI is the correct surface for these (Golden Rule 10).

1. `cairn status` -- overall system status (cross-layer health; no MCP aggregate)
2. `cairn validate` -- OKF conformance
3. `cairn compass validate` -- check for stale file references
4. `cairn compass gaps` -- find modules without compass coverage
5. `cairn compass flow-gaps` -- find undocumented business flows
6. `cairn memory stats` -- memory distribution by tier
7. `cairn memory decay` -- clean up stale memories

For targeted MCP-side checks during the audit:
- `recall_memory("<area>", tier="tribal")` to spot-check tribal-memory quality
- `search_knowledge("<module>")` to verify a knowledge doc's graph bridge
- `impact_analysis + cross_repo_deps` together (Golden Rule 9) to validate any
  blast-radius claims in existing docs

Report:
- Conformance/stale-reference issues
- Coverage gaps that need compass generation
- Memory tier distribution and health
- Recommendations for improving coverage
