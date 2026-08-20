# Specs index

`scaffold.sh` registers new specs here. Specs move to `archive/` when done.

## Active

- [docs-human-readable](docs-human-readable/spec.md) — done (10/10 tasks 2026-08-18; orientation blocks ×15 pages, 30-row artifact inventory + gap fills, back-links + scripts/check_doc_links.py; all 12 TCs green, sealed bytes untouched)
- [ui-dashboard](ui-dashboard/spec.md) — done (17/17 tasks 2026-08-20; `cairn dashboard` read-only Starlette UI — projects/embed status, vis-network graph, tool history/tokens/chains over extended `tool_metrics`, health/memory/tasks panels; 25 TCs, 67 new tests, missing-DB state D-010)
- [ui-dashboard-live-updates](ui-dashboard-live-updates/spec.md) — draft Stage 0 (auto-refresh history/chains/tokens, pause + connection state; poll-first, streaming deferred)
- [ui-dashboard-cross-links](ui-dashboard-cross-links/spec.md) — draft Stage 0 (tokens→history, history→chains, projects→graph, node→neighborhood; context-preserving links)
- [ui-dashboard-traffic-scale](ui-dashboard-traffic-scale/spec.md) — draft Stage 0 (pagination, 24h/7d/30d/all time windows, bounded chains, <2s at 10k+ calls)
- [ui-dashboard-workspace-launcher](ui-dashboard-workspace-launcher/spec.md) — draft Stage 0 (overview of every local store — size/freshness/call count; restart-free switching; read-only guard)
- [cli-usage-recording](cli-usage-recording/spec.md) — draft Stage 0 (record CLI invocations beside MCP tool calls, source-labeled, buffered flush-on-exit, opt-out)
- [ui-dashboard-graph-nav](ui-dashboard-graph-nav/spec.md) — draft Stage 0 (symbol search-to-focus with disambiguation, node expand, layout toggle)
- [ui-dashboard-polish](ui-dashboard-polish/spec.md) — draft Stage 0 (warm health, tokenizer mode + labeling, truncation stats, retention, CSV/JSON export, dark theme)

## Archive

- [benchmark-datasource](archive/benchmark-datasource/spec.md) — done (20/20 tasks, merged via #35 2026-08-16)
- [retrieval-quality](archive/retrieval-quality/spec.md) — done (24/24 tasks, merged via #37 2026-08-16; SC-1 shortfall documented in benchmarks/quality/ablation.md)
- [retrieval-quality-v2](archive/retrieval-quality-v2/spec.md) — done (24/24 tasks 2026-08-17; k-fold + DS-v2 evidence base; 3 candidates cleared the DS-v1 guard, all refuted zero-shot transfer; document branch — no ship, verdict in benchmarks/quality/ablation.md)

Note: sealed benchmark artifacts (`benchmarks/quality/ablation.json`,
`benchmarks/datasource/ds2/*`) retain their historical `specs/<name>/...`
provenance citations — those records are immutable (blob-pinned or sealed);
resolve such paths against `archive/<name>/` or the relevant merge-commit
tree.
