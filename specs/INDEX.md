# Specs index

`scaffold.sh` registers new specs here. Specs move to `archive/` when done.

## Active

- [docs-human-readable](docs-human-readable/spec.md) — done (10/10 tasks 2026-08-18; orientation blocks ×15 pages, 30-row artifact inventory + gap fills, back-links + scripts/check_doc_links.py; all 12 TCs green, sealed bytes untouched)
- [ui-dashboard](ui-dashboard/spec.md) — done (17/17 tasks 2026-08-20; `cairn dashboard` read-only Starlette UI — projects/embed status, vis-network graph, tool history/tokens/chains over extended `tool_metrics`, health/memory/tasks panels; 25 TCs, 67 new tests, missing-DB state D-010)
- [ui-dashboard-live-updates](ui-dashboard-live-updates/spec.md) — done (10/10 tasks 2026-08-20; poll-based auto-refresh on history/chains/tokens — re-arming loop w/ visibility gate, pause/resume + disconnected banner, filter/scroll preservation, 30-cycle soak w/ id-set equality; 2061 tests green)
- [ui-dashboard-cross-links](ui-dashboard-cross-links/spec.md) — done (9/9 tasks 2026-08-20; tokens→history + history→chains row links w/ window carry via _links.html macro, chains session filter, /graph de-orphaned in both navs, node inspect via select+anchor (D-004 gesture split vs doubleClick expand); 2047 tests green)
- [ui-dashboard-traffic-scale](ui-dashboard-traffic-scale/spec.md) — done (11/11 tasks 2026-08-20; keyset pagination + (invoked_at, id) index, 24h/7d/30d/all windows across history/tokens/chains, bounded chains w/ expand, 10.5k-call render-budget proof; 2021 tests green)
- [ui-dashboard-workspace-launcher](ui-dashboard-workspace-launcher/spec.md) — done (9/9 tasks 2026-08-20; /workspaces overview w/ registry∪dirs four-state enumeration + stat-first budgeted probes, restart-free ?store= switching across all views w/ full link carry, 220-store render budget <2s, byte-identical guard; 2078 tests green)
- [cli-usage-recording](cli-usage-recording/spec.md) — draft (record CLI invocations beside MCP tool calls, source-labeled, buffered flush-on-exit, opt-out; pipeline docs filled 2026-08-20, implementation not started)
- [ui-dashboard-graph-nav](ui-dashboard-graph-nav/spec.md) — done (11/11 tasks 2026-08-20; symbol search-to-focus w/ disambiguation candidates endpoint, doubleClick node expansion via /graph/neighbors, force/hier layout toggle w/ camera preservation + URL persistence; 2038 tests green)
- [ui-dashboard-polish](ui-dashboard-polish/spec.md) — draft (warm health, tokenizer mode + labeling, truncation stats, retention, CSV/JSON export, dark theme; pipeline docs filled 2026-08-20, implementation not started)

## Archive

- [benchmark-datasource](archive/benchmark-datasource/spec.md) — done (20/20 tasks, merged via #35 2026-08-16)
- [retrieval-quality](archive/retrieval-quality/spec.md) — done (24/24 tasks, merged via #37 2026-08-16; SC-1 shortfall documented in benchmarks/quality/ablation.md)
- [retrieval-quality-v2](archive/retrieval-quality-v2/spec.md) — done (24/24 tasks 2026-08-17; k-fold + DS-v2 evidence base; 3 candidates cleared the DS-v1 guard, all refuted zero-shot transfer; document branch — no ship, verdict in benchmarks/quality/ablation.md)

Note: sealed benchmark artifacts (`benchmarks/quality/ablation.json`,
`benchmarks/datasource/ds2/*`) retain their historical `specs/<name>/...`
provenance citations — those records are immutable (blob-pinned or sealed);
resolve such paths against `archive/<name>/` or the relevant merge-commit
tree.
