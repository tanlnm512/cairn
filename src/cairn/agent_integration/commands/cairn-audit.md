# Full System Audit

Run a comprehensive health check across all layers. Validation and maintenance
steps are bulk operations — CLI is the correct surface for these (Golden Rule 10).

1. `cg status` -- overall system status (cross-layer health; no MCP aggregate)
2. `cg validate` -- OKF conformance
3. `cg compass validate` -- check for stale file references
4. `cg compass gaps` -- find modules without compass coverage
5. `cg compass flow-gaps` -- find undocumented business flows
6. `cg memory stats` -- memory distribution by tier
7. `cg memory decay` -- clean up stale memories

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
