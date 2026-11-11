# Cairn business flows

Durable context map of the cairn workspace (`rust/crates/*`), traced from source.
All paths are relative to the repo root; code is located by `file::symbol`, not
line number — find a symbol with `cairn def <symbol>` or `rg -n "fn <symbol>"`.
Canonical reference: flow IDs F1–F9 are stable across PRs, tests, and
[coverage.md](coverage.md). Registry and refresh commands: [index.md](index.md).

Workspace layout: `cairn-store` (SQLite schema/conn/swap), `cairn-parse` (tree-sitter
parsers), `cairn-build` (scan/parse/resolve/overlay/embedding index), `cairn-query`
(all read-side logic + knowledge/memory/wiki/tasks), `cairn-daemon` (streamable-HTTP
MCP server + shared-daemon lifecycle), `cairn-cli` (clap CLI, dashboard, agent
wiring), `xtask` (dev/release tooling only — not a business flow).

Entry surfaces (from source, not docs):

- MCP: 26 tools registered in `rust/crates/cairn-daemon/src/tools.rs::catalog`
  (`catalog()`), dispatched by name at `tools.rs::dispatch`.
- CLI: `rust/crates/cairn-cli/src/cli.rs` flattens three families:
  `CoreCommand` (`cmds_core.rs`), `KnowledgeCommand` (`cmds_knowledge.rs`),
  `SystemCommand` (`cmds_system.rs`); dispatch in `cli.rs`.
- Dashboard: axum router in `rust/crates/cairn-cli/src/dashboard/mod.rs`
  (~35 routes, read-only except settings, loopback-only; enforced in
  `cmds_dashboard.rs`).

Storage layout: one SQLite store per workspace at
`~/.cairn/<store-key>/.kg` with the `.knowledge/` bundle beside it
(`rust/crates/cairn-cli/src/store_paths.rs::resolve_store`); registry of workspaces at
`~/.cairn/workspaces.json` (`rust/crates/cairn-store/src/paths.rs`).

## F1: Graph indexing / build pipeline

- Entry points:
  - `rust/crates/cairn-cli/src/cmds_core.rs::build` (CLI `build`)
  - `rust/crates/cairn-cli/src/cmds_core.rs::import_scip` (CLI `import-scip`)
  - `rust/crates/cairn-cli/src/cmds_core.rs::update` (CLI `update`; git-diff incremental; the `--file` branch reindexes one file)
  - `rust/crates/cairn-cli/src/cmds_lifecycle.rs::run_sync` (CLI `sync`; mtime drift)
  - Drift self-repair on read paths: `cmds_core.rs::fresh_conn`/`refresh_probe` → `incremental::repair_changed`
- Modules/crates: `cairn-build` (scanner, builder, resolver, incremental, closure,
  dataflow, communities, scip), `cairn-parse`, `cairn-store` (schema, swap)
- Steps (full build):
  1. `cmds_core.rs::build` → `cairn_build::builder::build_graph_with`; build lock via `cairn_store::swap::build_lock`.
  2. Guard: refuse to keep/replace an existing store when the scan finds nothing (`builder.rs`; always on, independent of `--staging`).
  3. Scan workspace → `scanner::scan_workspace`; repos discovered via `discover_repo_records`, respecting gitignore (`cairn-build/src/gitignore.rs`).
  4. Full build writes into a staged store (`swap.rs::staging_path`) and swaps atomically (`swap.rs::install_staged`); `--repo` filter rebuilds one repo in place with a `repo_build_state` marker (`builder.rs`).
  5. Populate: repos upsert, then a rayon parse pool calling `parse_language_file` (`rust/crates/cairn-build/src/parse_dispatch.rs`; 15 languages — python/php/ruby/dart/kotlin/java/go/rust/c/cpp/csharp/swift/objc/ts/js).
  6. Insert pass: `builder.rs::insert_parsed_file` writes files/symbols/edges/imports; parse failures become `parse_errors` rows, never aborts.
  7. Resolve pass: `crate::resolver::resolve_repo_edges` per repo pins edge targets → exact/ambiguous/unresolved counts.
  8. Import edges materialized (`builder.rs::materialize_import_edges`), then configured SCIP indexes applied as an edges-only overlay via `builder.rs::apply_scip_overlay`, degrading to tree-sitter edges on failure (importer in `rust/crates/cairn-build/src/scip/`).
  9. Post-build derivatives (from the CLI build handler): transitive closure `closure.rs::build_transitive_closure`, build-run telemetry `record_build_run`.
  10. Incremental `update`: `incremental.rs::incremental_update` (opens the store under `swap::open_locked`); single-file path `incremental.rs::reindex_paths` (also under the build lock); drift detection `incremental.rs::detect_changed`. After the reindex the handler runs the memory-decay pass (see F6).
- Data stores: `.kg` SQLite tables `repos, files, symbols, edges, imports, parse_errors, skipped_files, pending_sync, repo_build_state, build_runs, transitive_edges, dataflow, communities, symbol_communities, rationale` (schema in `rust/crates/cairn-store/src/schema.rs`); staged store file + atomic swap (`swap.rs`); optional repo-root `cairn.json` SCIP config (`rust/crates/cairn-build/src/config.rs`; this repo carries none).
- Output surfaces: CLI stdout (summary via `display`/`Rail`); `build_runs` rows feed `cairn metrics --builds` and the dashboard history views.
- Test touchpoints: `builder_parity.rs`, `builder_scip_overlay.rs`, `incremental_detect.rs`, `incremental_graft.rs`, `resolver_semantics.rs`, `scip_import.rs`, `closure_semantics.rs` (cairn-build/tests); `import_scip_output.rs`, `update_file.rs`, `init_hooks.rs`, `impact_guard.rs` (cairn-cli/tests); `parser_golden.rs` (cairn-parse/tests); inline `#[cfg(test)]` in builder/resolver/scip/embeddings modules.
- Notes: freshness is enforced pull-style by CLI read commands (`cmds_core.rs` drift probe + repair); `update --file` and `embed` take the same build lock as a full build.

## F2: Graph query & navigation path

- Entry points:
  - MCP: `find_definition`, `get_callers`, `get_callees`, `impact_analysis`, `path`, `explore`, `trace_flow`, `visualize_graph`, `repo_map`, `file_api`, plus `search_symbols` — catalog rows in `rust/crates/cairn-daemon/src/tools.rs::catalog`; dispatch arms in `tools.rs::dispatch`.
  - CLI: `def`, `callers`, `search`, `callees`, `impact`, `path`, `taint`, `grep`, `map`, `tree`, `blast`, `rationale`, `viz`, `pack` (`rust/crates/cairn-cli/src/pack.rs`), `context`, `review`, `dataflow`, `communities` — handlers in `cmds_core.rs`.
  - Dashboard: graph/inspect/suggest/neighbors routes (`dashboard/mod.rs`) over `dashboard/data.rs::inspect_symbol`.
- Modules/crates: `cairn-query` (`tools_nav.rs`, `tools_search.rs` lexical half, `tools_flow.rs`, `tools_repo.rs`, `graph.rs`, `blast.rs`, `grep.rs`, `review.rs`, `skillgen.rs`), `cairn-cli` (handlers, pack)
- Steps (get_callers, representative):
  1. MCP `call_tool` (`rust/crates/cairn-daemon/src/server.rs`) → `tools::dispatch` → `nav::callers_text` (`rust/crates/cairn-query/src/tools_nav.rs`).
  2. `callers_data` resolves the symbol by exact `symbols.name` match and reads precise edges from `edges` (resolved `target_id`), retrying fuzzy (name-match, `resolution='unresolved'`) when precise is empty; test-symbol filtering by path patterns. The exact → qualified → substring ladder belongs to `tools_nav.rs::find_definition`, not to `get_callers`.
  3. Staleness banner from `pending_sync`.
  4. Core traversal lives in `graph.rs` submodules: `get_callers`, `get_callees`, `impact_analysis` (closure fast path via `transitive_edges`, DFS fallback; recursive callers), `trace_flow`, `find_paths`, `mod taint`, `mod viz`.
  5. `explore` (`rust/crates/cairn-query/src/tools_flow.rs`) aggregates: lexical seeds + RRF semantic expansion → 1-hop callers/callees → source spans → depth-2 blast radius → memory + rationale rendering (bundle-aware).
  6. CLI `impact` (`cmds_core.rs`) adds the collision guard: cycles + result size over threshold → fall back to precomputed `dataflow` row (`rust/crates/cairn-build/src/dataflow.rs::get_dataflow`).
- Data stores: `.kg` tables `symbols, files, edges, imports, transitive_edges, dataflow, pending_sync, rationale, communities`; source files read from the workspace for spans (`tools_flow.rs read_source_spans`).
- Output surfaces: MCP text result (truncation capped, overridable via `CAIRN_MAX_RESULT_CHARS` — see F8); CLI stdout (tables/JSON via `json_compat`); dashboard HTML/JSON fragments.
- Test touchpoints: `nav_golden.rs`, `flow_golden.rs`, `km_golden.rs` (cairn-query/tests), `daemon_tools.rs` (cairn-daemon/tests), `impact_guard.rs`, `dashboard_parity.rs` (cairn-cli/tests); inline tests in `graph.rs`, `tools_nav/tests.rs`, `blast.rs`, `review.rs`, `grep.rs`, `skillgen.rs`.
- Notes: precise-vs-fuzzy resolution semantics are the core contract (empty precise ≠ unused). `pack.rs` builds a token-budgeted context pack (seeds → 1-hop expansion → centrality rank → source render); `review.rs::build_pack` assembles the PR review pack with memory guard (`build_pre_submit`).

## F3: Semantic search & embedding index

- Entry points:
  - MCP: `semantic_search` (catalog + dispatch in `rust/crates/cairn-daemon/src/tools.rs`).
  - CLI: `semantic` and `embed` handlers in `rust/crates/cairn-cli/src/cmds_core.rs` (embed calls `cairn_build::embeddings::embed_all`).
  - Dashboard: `/embeddings` route (`dashboard/mod.rs`).
- Modules/crates: `cairn-query::tools_search` (query side), `cairn-build::embeddings` (index side), `cairn-store::vec0` (sqlite-vec virtual tables), `cairn-query::embed_native` (feature-gated bge-m3)
- Steps:
  1. Backend resolution per call: `tools_search.rs::semantic_env` resolves `CAIRN_EMBED_BACKEND` via `resolve_backend` → Hash (deterministic 256-dim, `hash_vec`) | Server (OpenAI-compatible embeddings-server, probe before use) | Native (bge-m3, cold cache degrades to hash).
  2. `semantic_search_response` gates on backend availability and embedding count (`embeddings` table for the resolved model stamp).
  3. `semantic_search`: dense retrieve (ANN via sqlite-vec vec0 table when `ann_backend_enabled`, else brute cosine; vec0 naming in `tools_search.rs`) → RRF fusion with BM25 (`search_symbols` sparse leg, `rrf_fuse`, default on via `CAIRN_FUSION`) → optional cross-encoder rerank stage (in-process bge-reranker-v2-m3 behind the `local-embed` feature, `rerank_native.rs`; default builds degrade to the fused order).
  4. Index build: `cairn embed` → `embeddings::embed_all` writes only `embeddings` rows; the vec0 ANN index is rebuilt separately by `--build-index` or automatically when the ANN backend is enabled. Knowledge docs via `embed_knowledge` (wired from `cairn knowledge embed`), memories via `embed_memory_concepts`.
- Data stores: `.kg` tables `embeddings` (`schema.rs`), `embeddings_mv` plus the vec0 virtual tables defined in `rust/crates/cairn-store/src/vec0.rs`, `knowledge_embeddings`, `memory_embeddings`; native model cache under the cairn home.
- Output surfaces: MCP text (provenance-labeled rows, render in `tools_search.rs`); CLI stdout/JSON; dashboard embeddings view.
- Test touchpoints: `golden_search.rs` (cairn-query/tests), `embeddings_parity.rs` (cairn-build/tests), `native_embed.rs` (cairn-query/tests), `daemon_tools.rs`; inline `tools_search/tests.rs`.
- Notes: rerank runs in `local-embed` builds (bge-reranker-v2-m3 via fastembed; scores sigmoid-normalized, tail pruned at `best - CAIRN_RERANK_MIN_MARGIN`); default builds degrade to fused order. Scores are RRF fusion numbers by default (documented in AGENTS.md; matches code).

## F4: Knowledge base + compass + cross-layer routing

- Entry points:
  - MCP: `knowledge_add/search/delete/status`, `trace_workflow`, `get_compass`, `search_knowledge`, `ask_compass`, `generate_flow` — catalog + dispatch in `rust/crates/cairn-daemon/src/tools.rs`.
  - CLI: `knowledge *` (`rust/crates/cairn-cli/src/cmds_knowledge.rs::run_knowledge`), `compass generate/list/validate/gaps/flow` (`run_compass`; core `rust/crates/cairn-query/src/tools_km.rs::generate_compass`), `workflow add/trace`, `ask` (`rust/crates/cairn-cli/src/cmds_core.rs`; `--all-repos` fans out), `context`, `skill generate` (→ `rust/crates/cairn-query/src/skillgen.rs`), `validate`/`validate-paths`/`verify`.
- Modules/crates: `cairn-query::tools_km` (OKF concept store, routing), `cairn-query::ingest` (doc parsing/classification), `cairn-query::skillgen`, `cairn-cli::cmds_knowledge`
- Steps:
  1. All knowledge lives as OKF markdown concepts in the `.knowledge/` bundle: `tools_km.rs::Bundle` with path-escape validation, lock guard (`BundleLockGuard`), read/write/list (`read_concept`/`write_concept`), and an append-only update log.
  2. `knowledge_add` → `add_document` — writes the concept file; `knowledge_search` → `search_knowledge_docs` hybrid over `knowledge_embeddings` + lexical.
  3. `ask_compass` (`tools_km.rs`) routes an intent query via `route_query` across graph, wiki, compass, knowledge (L5 documents), and memory layers; empty-layer results render an explicit "coverage is thin" degradation note (the known `ask_compass` quirk from AGENTS.md). File-path-only mode loads per-file context (`ask_compass_file_context`).
  4. `get_compass` reads `compass/<module>.md` from the bundle; `generate_compass` derives it from a call-graph trace (CLI `compass generate`, optionally queueing LLM polish through the F5 task queue).
  5. `generate_flow` / `trace_workflow` produce and replay procedural workflow docs (steps verified against the graph for staleness).
  6. `search_knowledge` is the cross-layer search (Wiki|Pattern|Compass|Memory type filter) over the bundle.
- Data stores: `.knowledge/` directory tree (concepts, `compass/`, `wiki/`, `memory/`, `_tasks/`); `.kg` tables `knowledge_embeddings`, `knowledge_edges`, `knowledge_doc_refs` (written by `tasks.rs::rebuild_knowledge_index`).
- Output surfaces: MCP text; CLI stdout; dashboard `/knowledge/*`, `/wiki/*`, `/memory` routes.
- Test touchpoints: `km_golden.rs` (cairn-query/tests), `daemon_tools.rs`; inline tests in `tools_km.rs`, `ingest.rs`, `skillgen.rs`.
- Notes: `ask_compass` returning empty skeletons on thin coverage is real behavior, not a bug report artifact. OKF conformance gates (`validate`/`verify`) re-use the same deterministic critic as the task queue.

## F5: Wiki generation via LLM task queue

- Entry points:
  - MCP: `wiki_generate` (catalog + dispatch in `rust/crates/cairn-daemon/src/tools.rs`).
  - CLI: `wiki generate|search|status|retry|export|enrich` (`rust/crates/cairn-cli/src/cmds_knowledge.rs::run_wiki`); `task list|show|claim|complete|drop` (`run_task`; `task_complete` calls `tasks::complete_task`).
- Modules/crates: `cairn-query::tools_wiki`, `cairn-query::tasks` (queue + critic), `cairn-cli::cmds_knowledge`
- Steps:
  1. `wiki_generate` → `wiki_generate_text` → `run_wiki_generate` → `build_page_plan` derives the deterministic page plan from the graph (module clustering, symbol counts).
  2. `--refine-catalog` first queues a single catalog task; otherwise one `wiki-page` task per page is queued into the bundle's `_tasks/` dir (`tasks.rs::TASK_DIR`, `create_task`). Task state machine: pending → in-progress (`claim_task`) → done/failed/dropped; a claimed task is reclaimable only while its claim marker has gone stale (`CLAIM_STALE_SECONDS`).
  3. An agent (human or LLM) writes the result and calls `task complete` → `complete_task`: ownership check, private-data scrub for every result, result concept persisted, claim marker removed.
  4. Deterministic critic: `run_critic` fact-checks the result against the graph; wiki pages must end with a `## Sources` footer whose file/symbol refs all resolve (`resolve_sources`/`file_exists`). The critic is skipped for `wiki-catalog`, `doc-link`, and `skill-polish` tasks.
  5. Pass → page promoted into `.knowledge/wiki/` with sources metadata; fail → a `-revise` child task is spawned with the errors, up to `MAX_REVISE_CYCLES`.
  6. Read model: `wiki status` derives planned/queued/in-progress/promoted/failed/dropped at read time (`derived_state`); `wiki retry` re-queues failed chains (`retry_failed_pages`); `wiki enrich` queues append-only extensions (`queue_enrich_tasks`). Doc-link tasks cross-link knowledge docs (`queue_doc_link_tasks`, edge validation).
- Data stores: `.knowledge/_tasks/` (task + result concepts), `.knowledge/wiki/` (promoted pages + manifest), `.kg` read-only for critic fact-checks and plan building; `knowledge_edges` for doc links.
- Output surfaces: MCP text (queued task ids); CLI stdout; dashboard `/tasks`, `/wiki` routes.
- Test touchpoints: `wiki_plan.rs` (plan→queue contract), `daemon_tools.rs` (wiki_generate arm); inline tests in `tools_wiki.rs` and `tasks.rs` (`cfg(test)`).
- Notes: the critic is fully deterministic (no LLM in the loop) — the LLM only writes results; the graph is ground truth. Cairn never calls an LLM itself anywhere in the workspace.

## F6: Memory lifecycle

- Entry points:
  - MCP: `record_memory`, `recall_memory` (catalog + dispatch in `rust/crates/cairn-daemon/src/tools.rs`).
  - CLI: `memory record|reflect|evolve|search|timeline|capture|list|stats|digest|promote|decay|embed|batch-critic|forget|demote|purge|consolidate|share|check` (`rust/crates/cairn-cli/src/cmds_knowledge.rs::run_memory`).
  - Implicit: decay pass runs after every `cairn update` (`rust/crates/cairn-cli/src/cmds_core.rs`); agent session hooks capture memories (`rust/crates/cairn-cli/src/agent_hooks.rs`, `hooks run` entrypoint in `cmds_system.rs`).
- Modules/crates: `cairn-query::tools_km` (memory section), `cairn-cli::agent_hooks`
- Steps:
  1. Record: `record_memory` → `capture_memory`: strip private data → bundle lock → supersession detection (an explicit `supersedes` concept id — same type, latest — replaces the old concept and carries the chain in `memory_supersedes`; a same-type same-title match alone never supersedes, it is reported back as `possible_duplicate`) → `create_memory` → `score_memory` (graph-verification of file/symbol refs, where a body with no refs scores 0.5, and the weighted score counts `critic_score` — weights sum to 1.0 so a ref-less memory at default confidence lands in drafts, not tribal) → tier by score (`tier_for_score`; raw/drafts/tribal/archived) → `store_memory` writes the concept under `.knowledge/memory/<tier>/`.
  2. Recall: `recall_memory` → `search_memory`: lexical + `memory_embeddings` semantic leg, refs-verified fractions recomputed live, `as_of` point-in-time filter, optional cross-agent merge (`shared_recall_entries`). Read-through refs crediting writes `memory_refs` rows keyed by the MCP session id (`record_references_batch`).
  3. Quality loop: `reflect_store` sets each memory's stance and refreshes `memory_refs_baseline` — it never moves tiers; the tier comes from the score. Tier moves happen in `evolve_memory` (drafts→tribal rescore), `batch_critic` (drafts rescored: promote to tribal, keep as drafts, or archive below the raw cutoff — the archive path moves the concept to `memory/archived/`), `promote_memory` / `demote_memory`, and `consolidate_memories`.
  4. Decay/purge: `decay` (raw after 7d / tribal after 90d-default staleness measured from the last recall, falling back to creation time) — invoked by `cairn update` (skippable with `--no-decay`) and `cairn memory decay`; `purge` deletes archived older than N days. Every memory write path (capture, reflect, evolve, consolidate, promote, demote, decay, batch-critic) runs under the bundle lock, and tier moves rewrite `memory_refs`/`memory_embeddings` keys onto the new concept id.
  5. Sharing: `memory share/check` exchange memory concepts between agents' stores with symbol-scoped overlap checks (`MemoryGroup::Share/Check`; agent-id validation `validate_agent_id`).
  6. Embedding for semantic recall: `memory embed` → `embed_memory_concepts` (`rust/crates/cairn-build/src/embeddings.rs`).
- Data stores: `.knowledge/memory/{raw,drafts,tribal,archived}/` concept files + update log; `.kg` tables `memory_refs`, `memory_failure_signatures` (recurrence gate for `--recurrence-key`), `memory_embeddings`, `memory_validity`.
- Output surfaces: MCP text (score/tier/refs-verified); CLI stdout; dashboard `/memory`, `/memory/{tier}/{slug}` routes.
- Test touchpoints: `km_golden.rs` (cairn-query/tests), `daemon_tools.rs`; inline tests in `tools_km.rs`.
- Notes: tiers are directories — the concept's file location is its tier, and the score/tier can be recomputed at any time from the graph. `explore` (F2) credits the memories it renders into `memory_refs` in the query layer (`tools_flow.rs`); the daemon passes the session id through.

## F7: Federation & cross-repo analysis

- Entry points:
  - MCP: `federated_search`, `cross_repo_deps` (catalog + dispatch in `rust/crates/cairn-daemon/src/tools.rs`).
  - CLI: `federated-search` (renders `tools_federation::federated_search_data`), `deps`, `ask --all-repos`, `tree` — handlers in `rust/crates/cairn-cli/src/cmds_core.rs`.
- Modules/crates: `cairn-query::tools_federation`, `cairn-query::graph` (`mod cross_repo`), `cairn-store::paths` (registry), `cairn-cli`
- Steps:
  1. `federated_search_text` → `federated_search_data`: enumerate registered stores from `~/.cairn/workspaces.json` (`iter_stores` → `load_registry`).
  2. Per store: classify missing/unindexed/ok from the filesystem; open `.kg` read-only and run semantic search, degrading to BM25-only when the store has no embeddings for the current model (`search_store`); a failing store is named and skipped, never fatal.
  3. Merge per-store rankings with RRF (`rrf_fuse`), attribute each hit with its owning workspace path.
  4. `cross_repo_deps`: imports in `repo` resolving to another repo's namespace → deps; reverse direction → dependents. On the MCP path namespaces resolve per workspace via `graph.rs::resolve_namespaces` from `cairn.json` or the default table only; the `CAIRN_REPO_NAMESPACES` env override applies only to the CLI `deps` path, which passes it as `env_raw`.
- Data stores: `~/.cairn/workspaces.json` registry; each registered store's `.kg` opened read-only; `.kg` `imports`/`edges` for cross-repo evidence.
- Output surfaces: MCP text (per-workspace attribution + dropped-store list); CLI stdout.
- Test touchpoints: `flow_golden.rs` (cross_repo_deps golden), `daemon_tools.rs` (dispatch smoke), `federation_walk.rs` (store-walk contract); see docs/flows/coverage.md.
- Notes: one shared walk — the CLI `federated-search` renders `tools_federation::federated_search_data` (output verified byte-identical when the CLI's duplicate walk was removed); stores open through `cairn_store::conn::open_read_only`, which registers sqlite-vec so embedding stores take the semantic leg. Cross-repo reach also surfaces inside `impact_analysis` output (per AGENTS.md; the graph layer includes cross-repo consumer reach).

## F8: MCP daemon serving & shared-daemon lifecycle

- Entry points:
  - CLI: `serve [start|stop|status|restart]` (`rust/crates/cairn-cli/src/cmds_serve.rs`), `connect` (`rust/crates/cairn-cli/src/cmds_connect.rs`), `install-agents`/`hooks` wiring (`rust/crates/cairn-cli/src/cmds_agents.rs`, `agent_install.rs`).
  - HTTP: `POST /mcp` (streamable HTTP MCP), `GET /healthz` (`rust/crates/cairn-daemon/src/server.rs`).
- Modules/crates: `cairn-daemon` (server, tools, auth, conn_pool, healthz, lifecycle), `cairn-cli` (cmds_serve, cmds_connect, cmds_doctor)
- Steps:
  1. `cairn connect`: reuse-guard against the live daemon's `/healthz` db field (`reuse_decision` — a daemon serving a foreign store aborts), then under `mcp-connect.lock` → `lifecycle::ensure_daemon_running` starts the shared daemon on `127.0.0.1:9876` (`DEFAULT_PORT`), holding the `mcp-daemon.lock` start lock, sweeping stray same-store daemons first (`sweep_strays`); a keyless non-loopback bind is refused before any plist write (`keyless_remote_bind_refused`), and a start that loads but never turns healthy unloads the launchd job instead of crash-looping. macOS persists it as a launchd plist (`render_plist`/`write_plist_file`) under `~/Library/LaunchAgents`, with the log under `~/Library/Logs`.
  2. Serving: `serve_config` refuses keyless non-loopback binds, builds `ToolCtx` (per-instance connection pool, `conn_pool.rs`; store-swap invalidation reopens pooled conns) and mounts the rmcp `StreamableHttpService` at `/mcp` with `/healthz` beside it; optional bearer gate middleware (`bearer_gate`, key resolution in `rust/crates/cairn-daemon/src/auth.rs`).
  3. MCP call: `CairnServer::call_tool` → `spawn_blocking(tools::dispatch)` → text result capped (`truncate_result`, default 60k chars, overridden by `CAIRN_MAX_RESULT_CHARS`) → success/error `CallToolResult`.
  4. Health: `healthz::payload` reports store reachability, read-only flag, degradation count; the full store path is only exposed to loopback peers (`db_field`).
  5. Agent wiring: `install-agents` writes client configs/skills from `rust/crates/cairn-cli/templates/agent_integration/`; git post-commit/post-checkout hooks drive `cairn update`; `doctor --fix` repoints stale stdio/SSE registrations to the daemon URL (`rust/crates/cairn-cli/src/cmds_doctor.rs`).
- Data stores: one store per daemon (a daemon serving a foreign workspace is refused, not adopted); `.kg` (pooled, WAL pragmas per `tools.rs` tests); `~/.cairn/` daemon home: pidfile, log, launchd plist, `mcp-connect.lock`, `mcp-daemon.lock` (`lifecycle.rs`); client config files under `.claude/` etc.
- Output surfaces: MCP JSON-RPC over streamable HTTP (write tools `record_memory`/`knowledge_add`/`knowledge_delete`/`wiki_generate` exposed alongside reads, gated per the key policy); healthz JSON; CLI lifecycle messages; log file.
- Test touchpoints: `daemon_tools.rs`, `auth_lifecycle.rs`, `tool_metrics.rs`, `tests/common/mod.rs` (cairn-daemon/tests); inline tests in `tools.rs`, `conn_pool.rs`, `auth.rs`, `lifecycle.rs`, `cmds_connect.rs`, `cmds_serve.rs`.
- Notes: the daemon records a `tool_metrics` row per dispatched call (`ToolCtx::record_tool_metrics`, best-effort, read-only stores skip); doctor/dashboard history read it.

## F9: Read-only web dashboard

- Entry points:
  - CLI: `cairn dashboard` (`rust/crates/cairn-cli/src/cmds_dashboard.rs`, default port 8765; loopback guard; serve in the same file).
  - HTTP: router in `rust/crates/cairn-cli/src/dashboard/mod.rs` — overview `/`, `/workspaces`, `/projects`, `/health`, graph exploration `/graph*`, palette, `/communities`, `/history(.csv|.json)`, `/tokens*`, `/chains`, `/memory`, `/tasks`, `/knowledge*`, `/wiki*`, `/embeddings`, `/database`, `/settings` (+POST save/parity-check), editor endpoints `/editor/*`.
- Modules/crates: `cairn-cli::dashboard` (mod, data, kb, env, settings, markdown, shell, workspaces, assets), reading through `cairn-query`/direct SQL
- Steps:
  1. `AppState::new` holds optional db path + knowledge dir; stores enumerated from `CAIRN_HOME`.
  2. Graph/data pages query via `dashboard/data.rs` helpers over `open_store`: symbol inspect/suggest, health, history from `tool_metrics`/`build_runs`, communities, editor payloads.
  3. Knowledge/wiki/memory pages render bundle concepts through `dashboard/kb.rs` + `markdown.rs`; the settings POST writes `~/.cairn/config.json` unless `CAIRN_READ_ONLY` is set (then it refuses and reports that nothing was saved), mirrored with the CLI env/config chain (`dashboard/settings.rs`, parity-checked).
  4. Static assets/templates embedded from `rust/crates/cairn-cli/dashboard-assets/` (`dashboard/assets.rs`); the shell is server-rendered (`dashboard/shell.rs`).
- Data stores: `.kg` read-only; `.knowledge/` read-only; `~/.cairn/config.json` (settings writes); `~/.cairn/workspaces.json` (store enumeration).
- Output surfaces: dashboard HTML/CSV/JSON views (loopback only, read-only by construction except settings).
- Test touchpoints: `dashboard_parity.rs` (cairn-cli/tests); inline tests across `dashboard/*.rs` (`data.rs`, `kb.rs`, `env.rs`, `settings.rs`, `markdown.rs`, `shell.rs`, `workspaces.rs`, `assets.rs`).
- Notes: settings save is the only mutating route (and refuses under `CAIRN_READ_ONLY`); parity with CLI behavior is enforced by `rust/crates/cairn-cli/src/parity.rs` + the dashboard_parity test.

## Summary

| Flow | Entry surface | Core modules | Store | Test touchpoints (count) | Notes |
|---|---|---|---|---|---|
| F1 Indexing/build | CLI build/update/import-scip/sync | cairn-build (scanner/builder/resolver/incremental/scip/closure/dataflow), cairn-parse | .kg (repos/files/symbols/edges/imports/…), staged swap | 13 | pull-style drift repair keeps freshness; update --file and embed hold the build lock |
| F2 Graph query | MCP nav/flow tools + CLI def/callers/…/pack/review | cairn-query (tools_nav, tools_flow, graph, blast, review, grep) | .kg (symbols/edges/transitive_edges/dataflow/pending_sync) | 6 (+inline graph/tools_nav) | impact collision guard falls back to dataflow; staleness banners from pending_sync |
| F3 Semantic search | MCP semantic_search + CLI semantic/embed | cairn-query::tools_search, cairn-build::embeddings, cairn-store::vec0 | .kg (embeddings, embeddings_mv/vec0, knowledge_embeddings, memory_embeddings) | 4 | rerank live in local-embed builds (bge-reranker-v2-m3); hash fallback is deterministic |
| F4 Knowledge/compass | MCP knowledge_*/ask_compass/get_compass + CLI knowledge/compass/ask/context/skill | cairn-query::tools_km, ingest, skillgen | .knowledge/ bundle + .kg knowledge_* tables | 2 (+inline) | ask_compass empty-skeleton behavior on thin coverage is by design |
| F5 Wiki/task queue | MCP wiki_generate + CLI wiki/task | cairn-query::tools_wiki, tasks | .knowledge/_tasks/, .knowledge/wiki/ | 1 (+inline tools_wiki/tasks) | Deterministic critic gates promotion (skipped for catalog/doc-link/skill-polish); revise loop capped |
| F6 Memory lifecycle | MCP record/recall + CLI memory * + update-hook decay | cairn-query::tools_km (memory), cairn-cli::agent_hooks | .knowledge/memory/{raw,drafts,tribal,archived} + .kg memory_* tables | 2 (+inline) | Decay piggybacks on `cairn update`; refs crediting via session ids |
| F7 Federation/cross-repo | MCP federated_search/cross_repo_deps + CLI federated-search/deps/ask --all-repos | cairn-query::tools_federation, graph::cross_repo | ~/.cairn/workspaces.json + per-store .kg (read-only) | 3 | one shared walk (federated_search_data); contract pinned (federation_walk.rs) |
| F8 MCP daemon | CLI serve/connect/install-agents + HTTP /mcp /healthz | cairn-daemon (server/tools/auth/conn_pool/healthz/lifecycle) | .kg (pooled), ~/.cairn daemon home (mcp-connect.lock, mcp-daemon.lock), launchd plist | 5 | every dispatched call records a tool_metrics row (read-only stores skip) |
| F9 Dashboard | CLI dashboard + HTTP routes | cairn-cli::dashboard | .kg/.knowledge read-only, ~/.cairn/config.json | 1 (+inline dashboard/*) | Loopback-only; settings save is the sole mutating route (refuses under CAIRN_READ_ONLY) |

Cross-cutting: `rrf_fuse` (`tools_search.rs`) is a tri-flow primitive — semantic search (F3), federation merge (F7, `tools_federation.rs`), and memory recall (F6, `tools_km.rs`) all rank through it; rerank provenance (`SemanticMatch.reranked`, F3) also renders in the CLI federated-search JSON (F7). Every read surface resolves the same store layout (`store_paths.rs`);
every MCP tool is a thin dispatch arm over a `cairn-query` function, so CLI and MCP
share one core; `xtask` (golden recording, CLI smoke, parity, release bump) is dev
tooling, excluded from the business-flow set.
