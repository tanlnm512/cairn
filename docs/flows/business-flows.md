# Cairn business flows

Durable context map of the cairn workspace (`rust/crates/*`), traced from source.
All paths are relative to the repo root; line numbers verified against the working tree.
Canonical reference: flow IDs F1–F9 are stable across PRs, tests, and
[coverage.md](coverage.md); line numbers drift — re-verify against the tree
before citing. Registry and refresh commands: [index.md](index.md).

Workspace layout: `cairn-store` (SQLite schema/conn/swap), `cairn-parse` (tree-sitter
parsers), `cairn-build` (scan/parse/resolve/overlay/embedding index), `cairn-query`
(all read-side logic + knowledge/memory/wiki/tasks), `cairn-daemon` (streamable-HTTP
MCP server + shared-daemon lifecycle), `cairn-cli` (clap CLI, dashboard, agent
wiring), `xtask` (dev/release tooling only — not a business flow).

Entry surfaces (from source, not docs):

- MCP: 26 tools registered in `rust/crates/cairn-daemon/src/tools.rs:141`
  (`catalog()`), dispatched by name at `tools.rs:484` (`dispatch()`).
- CLI: `rust/crates/cairn-cli/src/cli.rs:39-47` flattens three families:
  `CoreCommand` (`cmds_core.rs:26-95`), `KnowledgeCommand` (`cmds_knowledge.rs:31-92`),
  `SystemCommand` (`cmds_system.rs:15-212`); dispatch at `cli.rs:71-75`.
- Dashboard: axum router at `rust/crates/cairn-cli/src/dashboard/mod.rs:1833`
  (~35 routes, read-only, loopback-only; enforced in `cmds_dashboard.rs:55`).

Storage layout: one SQLite store per workspace at
`~/.cairn/<store-key>/.kg` with the `.knowledge/` bundle beside it
(`rust/crates/cairn-cli/src/store_paths.rs:102-120`); registry of workspaces at
`~/.cairn/workspaces.json` (`rust/crates/cairn-store/src/paths.rs:83-106`).

## F1: Graph indexing / build pipeline

- Entry points:
  - `rust/crates/cairn-cli/src/cmds_core.rs:819` — `build()` (CLI `build`)
  - `rust/crates/cairn-cli/src/cmds_core.rs:961` — `import_scip()` (CLI `import-scip`)
  - `rust/crates/cairn-cli/src/cmds_core.rs:1193` — `update()` (CLI `update`; git-diff incremental)
  - `rust/crates/cairn-cli/src/cmds_system.rs` Sync → `rust/crates/cairn-cli/src/cmds_lifecycle.rs:206` (CLI `sync`; mtime drift)
  - Drift self-repair on read paths: `rust/crates/cairn-cli/src/cmds_core.rs:200-262` (`fresh_conn`/`refresh_probe` → `incremental::repair_changed`)
- Modules/crates: `cairn-build` (scanner, builder, resolver, incremental, closure,
  dataflow, communities, scip), `cairn-parse`, `cairn-store` (schema, swap)
- Steps (full build):
  1. `cmds_core.rs:819 build()` → `cairn_build::builder::build_graph_with` (`rust/crates/cairn-build/src/builder.rs:158`); build lock via `cairn_store::swap::build_lock` (`rust/crates/cairn-store/src/swap.rs:47`).
  2. Guard: refuse to keep/replace an existing store when the scan finds nothing (`builder.rs:172-181`, `builder.rs:230-243`).
  3. Scan workspace → `scanner::scan_workspace` (`rust/crates/cairn-build/src/scanner.rs:606`); repos discovered via `discover_repo_records` (`scanner.rs:225`), respecting gitignore (`cairn-build/src/gitignore.rs`).
  4. Full build writes into a staged store (`swap.rs:42 staging_path`) and swaps atomically (`builder.rs:200-212` → `swap.rs:163 install_staged`); `--repo` filter rebuilds one repo in place with a `repo_build_state` marker (`builder.rs:319-330`).
  5. Populate (`builder.rs:254`): repos upsert (`builder.rs:297`), then a rayon parse pool (`builder.rs:337-362`) calling `parse_language_file` (`rust/crates/cairn-parse` dispatch at `rust/crates/cairn-build/src/parse_dispatch.rs:20`; 16 languages — python/php/ruby/dart/kotlin/java/go/rust/c/cpp/csharp/swift/objc/ts/js).
  6. Insert pass: `insert_parsed_file` (`builder.rs:388`) writes files/symbols/edges/imports; parse failures become `parse_errors` rows, never aborts.
  7. Resolve pass: `crate::resolver::resolve_repo_edges` per repo (`builder.rs:423-429`) pins edge targets → exact/ambiguous/unresolved counts.
  8. Import edges materialized (`builder.rs:455 materialize_import_edges`), then configured SCIP indexes applied as an edges-only overlay, degrading to tree-sitter edges on failure (`builder.rs:464` → `apply_scip` at `builder.rs:510`; importer in `rust/crates/cairn-build/src/scip/`).
  9. Post-build derivatives (from the CLI build handler): transitive closure `build_transitive_closure` (`rust/crates/cairn-build/src/closure.rs:25`, called at `cmds_core.rs:841,1004`), build-run telemetry `record_build_run` (`rust/crates/cairn-cli/src/cmds_lifecycle.rs:258`, called at `cmds_core.rs:2537`).
  10. Incremental `update`: `incremental::incremental_update` (`rust/crates/cairn-build/src/incremental.rs:446`, called at `cmds_core.rs:1292`); single-file path `reindex_paths` (`incremental.rs:32`, called at `cmds_core.rs:1235`); drift detection `detect_changed` (`incremental.rs:650`). After the reindex the handler runs the memory-decay pass (`cmds_core.rs:1328-1342`, see F6).
- Data stores: `.kg` SQLite tables `repos, files, symbols, edges, imports, parse_errors, skipped_files, pending_sync, repo_build_state, build_runs, transitive_edges, dataflow, communities, symbol_communities, rationale` (schema at `rust/crates/cairn-store/src/schema.rs:11-331`); staged store file + atomic swap (`swap.rs`); optional repo-root `cairn.json` SCIP config (`rust/crates/cairn-build/src/config.rs`; this repo carries none).
- Output surfaces: CLI stdout (summary via `display`/`Rail`); `build_runs` rows feed `cairn metrics --builds` and the dashboard history views.
- Test touchpoints: `builder_parity.rs`, `builder_scip_overlay.rs`, `incremental_detect.rs`, `incremental_graft.rs`, `resolver_semantics.rs`, `scip_import.rs`, `closure_semantics.rs` (cairn-build/tests); `import_scip_output.rs`, `update_file.rs`, `init_hooks.rs`, `impact_guard.rs` (cairn-cli/tests); `parser_golden.rs` (cairn-parse/tests); inline `#[cfg(test)]` in builder/resolver/scip/embeddings modules.
- Notes: the unwired `FileWatcherService` and its `CAIRN_WATCH` knob were removed; freshness is enforced pull-style by CLI read commands (`cmds_core.rs` drift probe + repair).

## F2: Graph query & navigation path

- Entry points:
  - MCP: `find_definition`, `get_callers`, `get_callees`, `impact_analysis`, `path`, `explore`, `trace_flow`, `visualize_graph`, `repo_map`, `file_api`, plus `search_symbols` — catalog rows at `rust/crates/cairn-daemon/src/tools.rs:143-285`; dispatch arms at `tools.rs:486-632`.
  - CLI: `def` `cmds_core.rs:1354`, `callers` `:1435`, `search` `:1470`, `callees` `:1502`, `impact` `:1542`, `path` `:2024`, `taint` `:2102`, `grep` `:2553`, `map` `:2629`, `tree` `:2000`, `blast` `:2988`, `rationale` `:2708`, `viz` `:3624`, `pack` (`rust/crates/cairn-cli/src/pack.rs:633`), `context` `:3224`, `review` `:3296`, `dataflow` `:3471/3490`, `communities` `:3569`.
  - Dashboard: graph/inspect/suggest/neighbors routes (`dashboard/mod.rs:1841-1846`) over `dashboard/data.rs` (`inspect_symbol` `data.rs:272`).
- Modules/crates: `cairn-query` (`tools_nav.rs`, `tools_search.rs` lexical half, `tools_flow.rs`, `tools_repo.rs`, `graph.rs`, `blast.rs`, `grep.rs`, `review.rs`, `skillgen.rs`), `cairn-cli` (handlers, pack)
- Steps (get_callers, representative):
  1. MCP `call_tool` (`rust/crates/cairn-daemon/src/server.rs:84`) → `tools::dispatch` (`tools.rs:521`) → `nav::callers_text` (`rust/crates/cairn-query/src/tools_nav.rs:478`).
  2. `callers_data` resolves the symbol (exact → qualified → substring, `tools_nav.rs:94-113`) and reads precise edges from `edges` (resolved `target_id`), retrying fuzzy (name-match, `resolution='unresolved'`) when precise is empty; test-symbol filtering by path patterns (`tools_nav.rs:568-579`).
  3. Staleness banner from `pending_sync` (`tools_nav.rs:514-564`).
  4. Core traversal lives in `graph.rs` submodules, re-exported at `graph.rs:5-16`: `get_callers` `:128`, `get_callees` `:181`, `impact_analysis` `:573` (closure fast path via `transitive_edges`, DFS fallback; recursive callers), `trace_flow` `:1004`, `find_paths` `:1427`, taint `mod taint` `:1107`, viz `mod viz` `:1999`.
  5. `explore` (`rust/crates/cairn-query/src/tools_flow.rs:182`) aggregates: lexical seeds + RRF semantic expansion (`tools_flow.rs:199-222`) → 1-hop callers/callees → source spans → depth-2 blast radius (`tools_flow.rs:292`) → memory + rationale rendering (bundle-aware).
  6. CLI `impact` (`cmds_core.rs:1542`) adds the collision guard: cycles + result size over threshold → fall back to precomputed `dataflow` row (`cmds_core.rs:1578-1586`, `rust/crates/cairn-build/src/dataflow.rs:248 get_dataflow`).
- Data stores: `.kg` tables `symbols, files, edges, imports, transitive_edges, dataflow, pending_sync, rationale, communities`; source files read from the workspace for spans (`tools_flow.rs read_source_spans`).
- Output surfaces: MCP text result (60k-char truncation at `server.rs:115`); CLI stdout (tables/JSON via `json_compat`); dashboard HTML/JSON fragments.
- Test touchpoints: `nav_golden.rs`, `flow_golden.rs`, `km_golden.rs` (cairn-query/tests), `daemon_tools.rs` (cairn-daemon/tests), `impact_guard.rs`, `dashboard_parity.rs` (cairn-cli/tests); inline tests in `graph.rs:2909`, `tools_nav/tests.rs`, `blast.rs`, `review.rs`, `grep.rs`, `skillgen.rs`.
- Notes: precise-vs-fuzzy resolution semantics are the core contract (empty precise ≠ unused). `pack.rs:633` builds a token-budgeted context pack (seeds → 1-hop expansion → centrality rank → source render); `review.rs:66 build_pack` assembles the PR review pack with memory guard (`build_pre_submit` `review.rs:96`).

## F3: Semantic search & embedding index

- Entry points:
  - MCP: `semantic_search` (`tools.rs:155-167`, dispatch `tools.rs:498-509`).
  - CLI: `semantic` `rust/crates/cairn-cli/src/cmds_core.rs:2254`; `embed` `:2374` (index build, calls `cairn_build::embeddings::embed_all` at `cmds_core.rs:2424` → `rust/crates/cairn-build/src/embeddings.rs:429`).
  - Dashboard: `/embeddings` route (`dashboard/mod.rs:1864`).
- Modules/crates: `cairn-query::tools_search` (query side), `cairn-build::embeddings` (index side), `cairn-store::vec0` (sqlite-vec virtual tables), `cairn-query::embed_native` (feature-gated bge-m3)
- Steps:
  1. Backend resolution per call: `semantic_env` (`rust/crates/cairn-query/src/tools_search.rs:435`) resolves `CAIRN_EMBED_BACKEND` via `resolve_backend` (`tools_search.rs:396`) → Hash (deterministic 256-dim, `hash_vec` `tools_search.rs:471`) | Server (OpenAI-compatible embeddings-server, transport at `tools_search.rs:518+`, probe before use) | Native (bge-m3, cold cache degrades to hash, `tools_search.rs:419-432`).
  2. `semantic_search_response` (`tools_search.rs:1385`) gates on backend availability and embedding count (`embeddings` table for the resolved model stamp).
  3. `semantic_search` (`tools_search.rs:1259`): dense retrieve (ANN via sqlite-vec vec0 table when `ann_backend_enabled` `tools_search.rs:382`, else brute cosine; vec0 naming at `tools_search.rs:963`) → RRF fusion with BM25 (`search_symbols` sparse leg, `rrf_fuse`, default on via `CAIRN_FUSION`, `tools_search.rs:389`) → optional cross-encoder rerank stage (currently unavailable: `reranker_available()` returns false, `tools_search.rs:365`).
  4. Index build: `cairn embed` → `embeddings::embed_all` (`rust/crates/cairn-build/src/embeddings.rs:429`) writes `embeddings` + vec0 rows; knowledge docs via `embed_knowledge` (`embeddings.rs:713`, wired from `cairn knowledge embed` at `rust/crates/cairn-cli/src/cmds_knowledge.rs:2484`), memories via `embed_memory_concepts` (`embeddings.rs:856`, wired at `cmds_core.rs:3440`).
- Data stores: `.kg` tables `embeddings` (`schema.rs:150`), `embeddings_mv`/vec0 virtual tables (`schema.rs:161`, `rust/crates/cairn-store/src/vec0.rs`), `knowledge_embeddings` (`schema.rs:174`), `memory_embeddings` (`schema.rs:209`); native model cache under the cairn home.
- Output surfaces: MCP text (provenance-labeled rows, render at `tools_search.rs:1337`); CLI stdout/JSON; dashboard embeddings view.
- Test touchpoints: `golden_search.rs` (cairn-query/tests), `embeddings_parity.rs` (cairn-build/tests), `native_embed.rs` (cairn-query/tests), `daemon_tools.rs`; inline `tools_search/tests.rs`.
- Notes: rerank is scaffolded but no native cross-encoder ships (`tools_search.rs:364-367`), so `rerank=true` silently degrades to fused order. Scores are RRF fusion numbers by default (documented in AGENTS.md; matches code).

## F4: Knowledge base + compass + cross-layer routing

- Entry points:
  - MCP: `knowledge_add/search/delete/status`, `trace_workflow`, `get_compass`, `search_knowledge`, `ask_compass`, `generate_flow` — catalog `tools.rs:316-461`, dispatch `tools.rs:667-761`.
  - CLI: `knowledge *` (`rust/crates/cairn-cli/src/cmds_knowledge.rs:837 run_knowledge`; `knowledge_add` `:1642`, import `:1704`, search `:1861`, embed `:2456`, rebuild-index `:2393`), `compass generate/list/validate/gaps/flow` (`cmds_knowledge.rs:1158 run_compass`; core `generate_compass` `rust/crates/cairn-query/src/tools_km.rs:5074`), `workflow add/trace` (`cmds_knowledge.rs:2930/2992`), `ask` (`rust/crates/cairn-cli/src/cmds_core.rs:3105`; `--all-repos` fans out `:3133`), `context` (`cmds_core.rs:3224`), `skill generate` (`cmds_knowledge.rs:2685` → `rust/crates/cairn-query/src/skillgen.rs:38`), `validate`/`validate-paths`/`verify` (`cmds_knowledge.rs:2224/2249/2323`).
- Modules/crates: `cairn-query::tools_km` (OKF concept store, routing), `cairn-query::ingest` (doc parsing/classification), `cairn-query::skillgen`, `cairn-cli::cmds_knowledge`
- Steps:
  1. All knowledge lives as OKF markdown concepts in the `.knowledge/` bundle: `Bundle` (`rust/crates/cairn-query/src/tools_km.rs:876`) with path-escape validation, lock guard (`BundleLockGuard` `tools_km.rs:1064`), read/write/list (`read_concept`/`write_concept` `tools_km.rs:935`), and an append-only update log.
  2. `knowledge_add` (`tools_km.rs:6521`) → `add_document` — writes the concept file; `knowledge_search` (`:6581`) → `search_knowledge_docs` (`:3163`) hybrid over `knowledge_embeddings` + lexical.
  3. `ask_compass` (`tools_km.rs:6878`) routes an intent query via `route_query` (`tools_km.rs:6789`) across graph/wiki/compass/memory layers; empty-layer results render an explicit "coverage is thin" degradation note (the known `ask_compass` quirk from AGENTS.md, confirmed in code at `tools_km.rs:6903-6913`). File-path-only mode loads per-file context (`ask_compass_file_context` `tools_km.rs:6969`).
  4. `get_compass` (`tools_km.rs:6725`) reads `compass/<module>.md` from the bundle; `generate_compass` (`tools_km.rs:5074`) derives it from a call-graph trace (CLI `compass generate`, optionally queueing LLM polish through the F5 task queue).
  5. `generate_flow` (`tools_km.rs:4359`) / `trace_workflow` (`:6691`) produce and replay procedural workflow docs (steps verified against the graph for staleness).
  6. `search_knowledge` (`tools_km.rs:6739`) is the cross-layer search (Wiki|Pattern|Compass|Memory type filter) over the bundle.
- Data stores: `.knowledge/` directory tree (concepts, `compass/`, `wiki/`, `memory/`, `_tasks/`); `.kg` tables `knowledge_embeddings`, `knowledge_edges`, `knowledge_doc_refs` (`schema.rs:174-209`, written by `tasks::rebuild_knowledge_index` `rust/crates/cairn-query/src/tasks.rs:1523`).
- Output surfaces: MCP text; CLI stdout; dashboard `/knowledge/*`, `/wiki/*`, `/memory` routes.
- Test touchpoints: `km_golden.rs` (cairn-query/tests), `daemon_tools.rs`; inline tests in `tools_km.rs`, `ingest.rs`, `skillgen.rs`.
- Notes: `ask_compass` returning empty skeletons on thin coverage is real behavior, not a bug report artifact. OKF conformance gates (`validate`/`verify`) re-use the same deterministic critic as the task queue.

## F5: Wiki generation via LLM task queue

- Entry points:
  - MCP: `wiki_generate` (`tools.rs:463-477`, dispatch `tools.rs:768-781`).
  - CLI: `wiki generate|search|status|retry|export|enrich` (`rust/crates/cairn-cli/src/cmds_knowledge.rs:1111 run_wiki`); `task list|show|claim|complete|drop` (`cmds_knowledge.rs:1219 run_task`; `task_complete` calls `tasks::complete_task` at `cmds_knowledge.rs:1380`).
- Modules/crates: `cairn-query::tools_wiki`, `cairn-query::tasks` (queue + critic), `cairn-cli::cmds_knowledge`
- Steps:
  1. `wiki_generate` → `wiki_generate_text` (`rust/crates/cairn-query/src/tools_wiki.rs:1060`) → `run_wiki_generate` (`:1027`) → `build_page_plan` (`:251`) derives the deterministic page plan from the graph (module clustering, symbol counts).
  2. `--refine-catalog` first queues a single catalog task; otherwise one `wiki-page` task per page is queued into the bundle's `_tasks/` dir (`TASK_DIR` `rust/crates/cairn-query/src/tasks.rs:18`; `create_task` `:294`). Task state machine: pending → in-progress (`claim_task` `:421`, stale-claim reclaim after 1h `CLAIM_STALE_SECONDS` `:20`) → done/failed/dropped.
  3. An agent (human or LLM) writes the result and calls `task complete` → `complete_task` (`tasks.rs:721`): ownership check, private-data scrub for memory tasks, result concept persisted (`tasks.rs:798-812`), claim marker removed.
  4. Deterministic critic: `complete_with_critic` (`tasks.rs:838`) → `run_critic` (`:1097`) fact-checks the result against the graph; wiki pages must end with a `## Sources` footer whose file/symbol refs all resolve (`resolve_sources`/`file_exists`, `tasks.rs:850-893`).
  5. Pass → page promoted into `.knowledge/wiki/` with sources metadata; fail → a `-revise` child task is spawned with the errors, up to `MAX_REVISE_CYCLES = 3` (`tasks.rs:906-921`).
  6. Read model: `wiki status` derives planned/queued/in-progress/promoted/failed/dropped at read time (`DERIVED_STATES` `tools_wiki.rs:1136`, `derived_state` `:1174`); `wiki retry` re-queues failed chains (`retry_failed_pages` `:1255`); `wiki enrich` queues append-only extensions (`queue_enrich_tasks` `:1336`). Doc-link tasks cross-link knowledge docs (`queue_doc_link_tasks` `tasks.rs:1717`, edge validation `:1307`).
- Data stores: `.knowledge/_tasks/` (task + result concepts), `.knowledge/wiki/` (promoted pages + manifest), `.kg` read-only for critic fact-checks and plan building; `knowledge_edges` for doc links.
- Output surfaces: MCP text (queued task ids); CLI stdout; dashboard `/tasks`, `/wiki` routes.
- Test touchpoints: `daemon_tools.rs` (wiki_generate arm); inline tests in `tools_wiki.rs` and `tasks.rs` (`cfg(test)`).
- Notes: the critic is fully deterministic (no LLM in the loop) — the LLM only writes results; the graph is ground truth. Cairn never calls an LLM itself anywhere in the workspace.

## F6: Memory lifecycle

- Entry points:
  - MCP: `record_memory` (`tools.rs:302-315`, dispatch `tools.rs:648-666`), `recall_memory` (`tools.rs:286-300`, dispatch `tools.rs:633-647`).
  - CLI: `memory record|reflect|evolve|search|timeline|capture|list|stats|digest|promote|decay|embed|batch-critic|forget|demote|purge|consolidate|share|check` (`rust/crates/cairn-cli/src/cmds_knowledge.rs:327-544` enum, `run_memory` `:962`).
  - Implicit: decay pass runs after every `cairn update` (`rust/crates/cairn-cli/src/cmds_core.rs:1328-1342`); agent session hooks capture memories (`rust/crates/cairn-cli/src/agent_hooks.rs`, `hooks run` entrypoint `cmds_system.rs:236-243`).
- Modules/crates: `cairn-query::tools_km` (memory section), `cairn-cli::agent_hooks`
- Steps:
  1. Record: `record_memory` (`tools_km.rs:6482`) → `capture_memory` (`:2709`): strip private data → bundle lock → supersession detection (same type+title supersedes the old concept, chain carried in `memory_supersedes`) → `create_memory` (`:1228`) → `score_memory` (`:2238`, graph-verification of file/symbol refs `:2124`) → tier by score (`tier_for_score` `:1176`; raw/drafts/tribal/archived, `TIERS` `:1154`) → `store_memory` (`:1371`) writes the concept under `.knowledge/memory/<tier>/`.
  2. Recall: `recall_memory` (`:6248`) → `search_memory` (`:2417`): lexical + `memory_embeddings` semantic leg, refs-verified fractions recomputed live, `as_of` point-in-time filter, optional cross-agent merge (`shared_recall_entries`). Read-through refs crediting writes `memory_refs` rows keyed by the MCP session id (`record_references_batch` `:2372`).
  3. Quality loop: `reflect_store` (`:1711`) raw→drafts, `evolve_memory` (`:1860`) drafts→tribal, `batch_critic` (`:1941`), `consolidate_memories` (`:1992`), `promote_memory` (`:1575`) / `demote_memory` (`:1546`).
  4. Decay/purge: `decay` (`:1666`, raw after 7d / tribal after 90d defaults) — invoked by `cairn update` and `cairn memory decay`; `purge` deletes archived older than N days.
  5. Sharing: `memory share/check` exchange memory concepts between agents' stores with symbol-scoped overlap checks (`MemoryGroup::Share/Check`, `cmds_knowledge.rs:505-544`; agent-id validation `validate_agent_id` `tools_km.rs:1192`).
  6. Embedding for semantic recall: `memory embed` → `embed_memory_concepts` (`rust/crates/cairn-build/src/embeddings.rs:856`).
- Data stores: `.knowledge/memory/{raw,drafts,tribal,archived}/` concept files + update log; `.kg` tables `memory_refs` (`schema.rs:101`), `memory_failure_signatures` (`:111`, recurrence gate for `--recurrence-key`), `memory_embeddings` (`:209`), `memory_validity` (`:221`).
- Output surfaces: MCP text (score/tier/refs-verified); CLI stdout; dashboard `/memory`, `/memory/{tier}/{slug}` routes.
- Test touchpoints: `km_golden.rs` (cairn-query/tests), `daemon_tools.rs`; inline tests in `tools_km.rs`.
- Notes: tiers are advisory labels on directories, not DB state; the score/tier can be recomputed at any time from the graph. `explore` (F2) credits the memories it renders into `memory_refs` (`tools.rs:581-585`).

## F7: Federation & cross-repo analysis

- Entry points:
  - MCP: `federated_search` (`tools.rs:451-462`, dispatch `tools.rs:762-767`), `cross_repo_deps` (`tools.rs:258-269`, dispatch `tools.rs:606-616`).
  - CLI: `federated-search` (`rust/crates/cairn-cli/src/cmds_core.rs:1782`, renders `tools_federation::federated_search_data`), `deps` (`cmds_core.rs:1703`), `ask --all-repos` (`cmds_core.rs:3133`), `tree` (`:2000`).
- Modules/crates: `cairn-query::tools_federation`, `cairn-query::graph` (`mod cross_repo` `graph.rs:1816`), `cairn-store::paths` (registry), `cairn-cli`
- Steps:
  1. `federated_search_text` (`rust/crates/cairn-query/src/tools_federation.rs:135`) → `federated_search` (`:77`): enumerate registered stores from `~/.cairn/workspaces.json` (`iter_stores` `:23` → `load_registry` `paths.rs:83`).
  2. Per store: classify missing/unindexed/ok from the filesystem (`:28`); open `.kg` read-only and run semantic search, degrading to BM25-only when the store has no embeddings for the current model (`search_store` `:56-75`); a failing store is named and skipped, never fatal.
  3. Merge per-store rankings with RRF (`rrf_fuse` `:122`), attribute each hit with its owning workspace path.
  4. `cross_repo_deps` (`graph.rs:1890`, namespaces resolved per workspace via `resolve_namespaces` `graph.rs:1844` env/config/default table `tools_nav.rs:13-24`): imports in `repo` resolving to another repo's namespace → deps; reverse direction → dependents.
- Data stores: `~/.cairn/workspaces.json` registry; each registered store's `.kg` opened read-only; `.kg` `imports`/`edges` for cross-repo evidence.
- Output surfaces: MCP text (per-workspace attribution + dropped-store list); CLI stdout.
- Test touchpoints: `flow_golden.rs` (cross_repo_deps golden), `daemon_tools.rs` (dispatch smoke), `federation_walk.rs` (store-walk contract); see docs/flows/coverage.md.
- Notes: one shared walk — the CLI `federated-search` renders `tools_federation::federated_search_data` (output verified byte-identical when the CLI's duplicate walk was removed); stores open through `cairn_store::conn::open_read_only`, which registers sqlite-vec so embedding stores take the semantic leg. Cross-repo reach also surfaces inside `impact_analysis` output (per AGENTS.md; the graph layer includes cross-repo consumer reach).

## F8: MCP daemon serving & shared-daemon lifecycle

- Entry points:
  - CLI: `serve [start|stop|status|restart]` (`rust/crates/cairn-cli/src/cmds_system.rs:183-235` enum, `rust/crates/cairn-cli/src/cmds_serve.rs`), `connect` (`rust/crates/cairn-cli/src/cmds_connect.rs:68`), `install-agents`/`hooks` wiring (`rust/crates/cairn-cli/src/cmds_agents.rs`, `agent_install.rs`).
  - HTTP: `POST /mcp` (streamable HTTP MCP), `GET /healthz` (`rust/crates/cairn-daemon/src/server.rs:26-27`).
- Modules/crates: `cairn-daemon` (server, tools, auth, conn_pool, healthz, lifecycle), `cairn-cli` (cmds_serve, cmds_connect, cmds_doctor)
- Steps:
  1. `cairn connect` (`cmds_connect.rs:68`): reuse-guard against the live daemon's `/healthz` db field (`reuse_decision` `:27` — a daemon serving a foreign store aborts), then under `mcp-connect.lock` → `lifecycle::ensure_daemon_running` (`rust/crates/cairn-daemon/src/lifecycle.rs:991`) starts the shared daemon on `127.0.0.1:9876` (`DEFAULT_PORT` `lifecycle.rs:12`), sweeping stray same-store daemons first (`sweep_strays`, called at `cmds_connect.rs:102`). macOS persists it as a launchd plist (`render_plist`/`write_plist_file` `lifecycle.rs:144/249`).
  2. Serving: `serve_config` (`server.rs:179`) refuses keyless non-loopback binds (`:183-188`), builds `ToolCtx` (per-instance connection pool, `rust/crates/cairn-daemon/src/conn_pool.rs:131 read/139 primary`; store-swap invalidation reopens pooled conns) and mounts the rmcp `StreamableHttpService` at `/mcp` with `/healthz` beside it (`server.rs:210-217`); optional bearer gate middleware (`bearer_gate` `:151`, key resolution in `rust/crates/cairn-daemon/src/auth.rs`).
  3. MCP call: `CairnServer::call_tool` (`server.rs:84`) → `spawn_blocking(tools::dispatch)` (`server.rs:96`) → text result capped at 60k chars (`truncate_result` `:115`) → success/error `CallToolResult`.
  4. Health: `healthz::payload` (`rust/crates/cairn-daemon/src/healthz.rs:28-44`) reports store reachability, read-only flag, degradation count; the full store path is only exposed to loopback peers (`db_field` `:50`).
  5. Agent wiring: `install-agents` writes client configs/skills from `rust/crates/cairn-cli/templates/agent_integration/`; git post-commit/post-checkout hooks drive `cairn update`; `doctor --fix` repoints stale stdio/SSE registrations to the daemon URL (`rust/crates/cairn-cli/src/cmds_doctor.rs`).
- Data stores: `.kg` (pooled, WAL pragmas per `tools.rs` tests); `~/.cairn/` daemon home: pidfile, log, launchd plist, `mcp-connect.lock` (`lifecycle.rs:35-59`); client config files under `.claude/` etc.
- Output surfaces: MCP JSON-RPC over streamable HTTP; healthz JSON; CLI lifecycle messages; log file.
- Test touchpoints: `daemon_tools.rs`, `auth_lifecycle.rs`, `tests/common/mod.rs` (cairn-daemon/tests); inline tests in `server.rs`-adjacent modules (`tools.rs`, `conn_pool.rs`, `auth.rs`, `lifecycle.rs`, `cmds_connect.rs`, `cmds_serve.rs`).
- Notes: `tool_metrics` (`schema.rs:249`) is read by `metrics`/doctor/dashboard (`rust/crates/cairn-cli/src/cmds_doctor.rs:934-966`, `dashboard/data.rs`) but no production code in this workspace writes it (only xtask golden fixtures and a dashboard test insert). Provenance of rows `(unverified)` — likely external MCP clients or a legacy writer.

## F9: Read-only web dashboard

- Entry points:
  - CLI: `cairn dashboard` (`rust/crates/cairn-cli/src/cmds_dashboard.rs`, default port 8765; loopback guard at `cmds_dashboard.rs:55`; serve at `:79-106`).
  - HTTP: router at `rust/crates/cairn-cli/src/dashboard/mod.rs:1833-1871` — overview `/`, `/workspaces`, `/projects`, `/health`, graph exploration `/graph*`, palette, `/communities`, `/history(.csv|.json)`, `/tokens*`, `/chains`, `/memory`, `/tasks`, `/knowledge*`, `/wiki*`, `/embeddings`, `/database`, `/settings` (+POST save/parity-check), editor endpoints `/editor/*`.
- Modules/crates: `cairn-cli::dashboard` (mod, data, kb, env, settings, markdown, shell, workspaces, assets), reading through `cairn-query`/direct SQL
- Steps:
  1. `AppState::new` (`dashboard/mod.rs:127`) holds optional db path + knowledge dir; stores enumerated from `CAIRN_HOME` (`mod.rs:137`).
  2. Graph/data pages query via `dashboard/data.rs` helpers over `open_store` (`data.rs:1253`): symbol inspect/suggest (`:231-272`), health (`:439`), history from `tool_metrics`/`build_runs` (`:705`, `:840`), communities (`:1062`), editor payloads (`:1187-1251`).
  3. Knowledge/wiki/memory pages render bundle concepts through `dashboard/kb.rs` + `markdown.rs`; settings page writes `~/.cairn/config.json` values (POST `/settings/save`) mirrored with the CLI env/config chain (`dashboard/settings.rs`, parity-checked).
  4. Static assets/templates embedded from `rust/crates/cairn-cli/dashboard-assets/` (`dashboard/assets.rs`); the shell is server-rendered (`dashboard/shell.rs`).
- Data stores: `.kg` read-only; `.knowledge/` read-only; `~/.cairn/config.json` (settings writes); `~/.cairn/workspaces.json` (store enumeration).
- Output surfaces: dashboard HTML/CSV/JSON views (loopback only, read-only by construction except settings).
- Test touchpoints: `dashboard_parity.rs` (cairn-cli/tests); inline tests across `dashboard/*.rs` (`data.rs`, `kb.rs`, `env.rs`, `settings.rs`, `markdown.rs`, `shell.rs`, `workspaces.rs`, `assets.rs`).
- Notes: settings save is the only mutating route; parity with CLI behavior is enforced by `rust/crates/cairn-cli/src/parity.rs` + the dashboard_parity test.

## Summary

| Flow | Entry surface | Core modules | Store | Test touchpoints (count) | Notes |
|---|---|---|---|---|---|
| F1 Indexing/build | CLI build/update/import-scip/sync | cairn-build (scanner/builder/resolver/incremental/scip/closure/dataflow), cairn-parse | .kg (repos/files/symbols/edges/imports/…), staged swap | 13 | unwired watcher + CAIRN_WATCH knob removed; pull-style drift repair keeps freshness |
| F2 Graph query | MCP nav/flow tools + CLI def/callers/…/pack/review | cairn-query (tools_nav, tools_flow, graph, blast, review, grep) | .kg (symbols/edges/transitive_edges/dataflow/pending_sync) | 6 (+inline graph/tools_nav) | impact collision guard falls back to dataflow; staleness banners from pending_sync |
| F3 Semantic search | MCP semantic_search + CLI semantic/embed | cairn-query::tools_search, cairn-build::embeddings, cairn-store::vec0 | .kg (embeddings, embeddings_mv/vec0, knowledge_embeddings, memory_embeddings) | 4 | Rerank scaffolded but unavailable; hash fallback is deterministic |
| F4 Knowledge/compass | MCP knowledge_*/ask_compass/get_compass + CLI knowledge/compass/ask/context/skill | cairn-query::tools_km, ingest, skillgen | .knowledge/ bundle + .kg knowledge_* tables | 2 (+inline) | ask_compass empty-skeleton behavior on thin coverage is by design |
| F5 Wiki/task queue | MCP wiki_generate + CLI wiki/task | cairn-query::tools_wiki, tasks | .knowledge/_tasks/, .knowledge/wiki/ | 1 (+inline tools_wiki/tasks) | Deterministic critic gates promotion; revise loop capped at 3 |
| F6 Memory lifecycle | MCP record/recall + CLI memory * + update-hook decay | cairn-query::tools_km (memory), cairn-cli::agent_hooks | .knowledge/memory/{raw,drafts,tribal,archived} + .kg memory_* tables | 2 (+inline) | Decay piggybacks on `cairn update`; refs crediting via session ids |
| F7 Federation/cross-repo | MCP federated_search/cross_repo_deps + CLI federated-search/deps/ask --all-repos | cairn-query::tools_federation, graph::cross_repo | ~/.cairn/workspaces.json + per-store .kg (read-only) | 3 | one shared walk (federated_search_data); contract pinned (federation_walk.rs) |
| F8 MCP daemon | CLI serve/connect/install-agents + HTTP /mcp /healthz | cairn-daemon (server/tools/auth/conn_pool/healthz/lifecycle) | .kg (pooled), ~/.cairn daemon home, launchd plist | 2 (+tests/common) | tool_metrics has no production writer `(unverified)` |
| F9 Dashboard | CLI dashboard + HTTP routes | cairn-cli::dashboard | .kg/.knowledge read-only, ~/.cairn/config.json | 1 (+inline dashboard/*) | Loopback-only; settings save is the sole mutating route |

Cross-cutting: every read surface resolves the same store layout (`store_paths.rs:102`);
every MCP tool is a thin dispatch arm over a `cairn-query` function, so CLI and MCP
share one core; `xtask` (golden recording, CLI smoke, parity, release bump) is dev
tooling, excluded from the business-flow set.
