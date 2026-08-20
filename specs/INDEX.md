# Specs index

`scaffold.sh` registers new specs here. Specs move to `archive/` when done.

## Active

- [docs-human-readable](docs-human-readable/spec.md) — done (10/10 tasks 2026-08-18; orientation blocks ×15 pages, 30-row artifact inventory + gap fills, back-links + scripts/check_doc_links.py; all 12 TCs green, sealed bytes untouched)
- [ui-dashboard](ui-dashboard/spec.md) — done (17/17 tasks 2026-08-20; `cairn dashboard` read-only Starlette UI — projects/embed status, vis-network graph, tool history/tokens/chains over extended `tool_metrics`, health/memory/tasks panels; 25 TCs, 67 new tests, missing-DB state D-010)
- [ui-dashboard-live-updates](ui-dashboard-live-updates/spec.md) — draft (auto-refresh history/chains/tokens, pause + connection state; poll-first, streaming deferred; pipeline docs filled 2026-08-20, implementation not started)
- [ui-dashboard-cross-links](ui-dashboard-cross-links/spec.md) — draft (tokens→history, history→chains, projects→graph, node→neighborhood; context-preserving links; pipeline docs filled 2026-08-20, implementation not started)
- [ui-dashboard-traffic-scale](ui-dashboard-traffic-scale/spec.md) — done (11/11 tasks 2026-08-20; keyset pagination + (invoked_at, id) index, 24h/7d/30d/all windows across history/tokens/chains, bounded chains w/ expand, 10.5k-call render-budget proof; 2021 tests green)
- [ui-dashboard-workspace-launcher](ui-dashboard-workspace-launcher/spec.md) — draft (overview of every local store — size/freshness/call count; restart-free switching; read-only guard; pipeline docs filled 2026-08-20, implementation not started)
- [cli-usage-recording](cli-usage-recording/spec.md) — draft (record CLI invocations beside MCP tool calls, source-labeled, buffered flush-on-exit, opt-out; pipeline docs filled 2026-08-20, implementation not started)
- [ui-dashboard-graph-nav](ui-dashboard-graph-nav/spec.md) — draft (symbol search-to-focus with disambiguation, node expand, layout toggle; pipeline docs filled 2026-08-20, implementation not started)
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
