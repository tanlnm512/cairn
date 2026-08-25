# Specs index

`scaffold.sh` registers new specs here. Specs move to `archive/` when done.

## Active

- [kotlin-grammar-fwcd](kotlin-grammar-fwcd/spec.md) — done (12/12 tasks 2026-08-25; fwcd grammar vendored in-tree as cairn._tree_sitter_kotlin abi3 ext, KotlinParser ported (fix round 1: navigation_suffix receiver), ERROR-scan + modern fixtures, cibuildwheel matrix, tree-sitter-kotlin dep removed; 2137 tests green, golden byte-identical)

## Archive

- [docs-human-readable](archive/docs-human-readable/spec.md) — done (10/10 tasks 2026-08-18; orientation blocks ×15 pages, 30-row artifact inventory + gap fills, back-links + scripts/check_doc_links.py; all 12 TCs green, sealed bytes untouched)
- [ui-dashboard](archive/ui-dashboard/spec.md) — done (17/17 tasks 2026-08-20; `cairn dashboard` read-only Starlette UI — projects/embed status, vis-network graph, tool history/tokens/chains over extended `tool_metrics`, health/memory/tasks panels; 25 TCs, 67 new tests, missing-DB state D-010)
- [ui-dashboard-live-updates](archive/ui-dashboard-live-updates/spec.md) — done (10/10 tasks 2026-08-20; poll-based auto-refresh on history/chains/tokens — re-arming loop w/ visibility gate, pause/resume + disconnected banner, filter/scroll preservation, 30-cycle soak w/ id-set equality; 2061 tests green)
- [ui-dashboard-cross-links](archive/ui-dashboard-cross-links/spec.md) — done (9/9 tasks 2026-08-20; tokens→history + history→chains row links w/ window carry via _links.html macro, chains session filter, /graph de-orphaned in both navs, node inspect via select+anchor (D-004 gesture split vs doubleClick expand); 2047 tests green)
- [ui-dashboard-traffic-scale](archive/ui-dashboard-traffic-scale/spec.md) — done (11/11 tasks 2026-08-20; keyset pagination + (invoked_at, id) index, 24h/7d/30d/all windows across history/tokens/chains, bounded chains w/ expand, 10.5k-call render-budget proof; 2021 tests green)
- [ui-dashboard-workspace-launcher](archive/ui-dashboard-workspace-launcher/spec.md) — done (9/9 tasks 2026-08-20; /workspaces overview w/ registry∪dirs four-state enumeration + stat-first budgeted probes, restart-free ?store= switching across all views w/ full link carry, 220-store render budget <2s, byte-identical guard; 2078 tests green)
- [cli-usage-recording](archive/cli-usage-recording/spec.md) — done (10/10 tasks 2026-08-20; cli_metrics buffered sink + _RecordingGroup wrapper (D-004..D-007), tool_metrics.source column default mcp, history source display/filter, tokens inclusion under cli:* names, term:/tmux:/cli: session identity never 'unknown', CAIRN_TELEMETRY docs; 2091 tests green)
- [ui-dashboard-graph-nav](archive/ui-dashboard-graph-nav/spec.md) — done (11/11 tasks 2026-08-20; symbol search-to-focus w/ disambiguation candidates endpoint, doubleClick node expansion via /graph/neighbors, force/hier layout toggle w/ camera preservation + URL persistence; 2038 tests green)
- [ui-dashboard-polish](archive/ui-dashboard-polish/spec.md) — done (11/11 tasks 2026-08-20; startup probe prewarm w/ stale-while-revalidate cache (D-005), tool_metrics truncation-magnitude columns (D-002), configurable retention CAIRN_TOOL_METRICS_MAX_ROWS/_AGE in sink._prune (D-004) + health Retention card, tokenizer-mode helper w/ per-window calibration (D-006), tokens-view mode label + per-tool truncation counts (unknown≠zero), /history|tokens .csv/.json export riding resolve_selection+filters, dark theme w/ localStorage persistence; 2123 tests green)
- [benchmark-datasource](archive/benchmark-datasource/spec.md) — done (20/20 tasks, merged via #35 2026-08-16)
- [retrieval-quality](archive/retrieval-quality/spec.md) — done (24/24 tasks, merged via #37 2026-08-16; SC-1 shortfall documented in benchmarks/quality/ablation.md)
- [retrieval-quality-v2](archive/retrieval-quality-v2/spec.md) — done (24/24 tasks 2026-08-17; k-fold + DS-v2 evidence base; 3 candidates cleared the DS-v1 guard, all refuted zero-shot transfer; document branch — no ship, verdict in benchmarks/quality/ablation.md)

Note: sealed benchmark artifacts (`benchmarks/quality/ablation.json`,
`benchmarks/datasource/ds2/*`) retain their historical `specs/<name>/...`
provenance citations — those records are immutable (blob-pinned or sealed);
resolve such paths against `archive/<name>/` or the relevant merge-commit
tree.
