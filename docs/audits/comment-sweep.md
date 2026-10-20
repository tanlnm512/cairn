# Comment-style sweep delivery record

## Findings index — all 0 by priority

This record is a sweep delivery ledger, not an audit; zero findings are tracked here.

### P0 — None (0)

### P1 — None (0)

### P2 — None (0)

### P3 — None (0)

## Seeded remaining count

Seeded from `docs/audits/comment-style-baseline.json`: 1213 fingerprints across 188 files.

| Phase | Area task | Seeded fingerprints |
|---|---|---:|
| 2 | T003 · `src/cairn/telemetry/` | 54 |
| 2 | T004 · `src/cairn/memory/` | 51 |
| 3 | T005 · `src/cairn/graph/embeddings.py`, `src/cairn/graph/schema.py` | 82 |
| 3 | T006 · `src/cairn/graph/builder.py`, `incremental.py`, `scanner.py`, `dataflow.py` | 94 |
| 3 | T007 · remaining `src/cairn/graph/` files | 158 |
| 3 | T008 · `src/cairn/mcp_server/` | 96 |
| 3 | T009 · `src/cairn/cli/` | 126 |
| 3 | T010 · `src/cairn/dashboard/` | 83 |
| 3 | T011 · `src/cairn/parsers/` | 107 |
| 3 | T012 · `src/cairn/knowledge/` | 70 |
| 3 | T013 · `src/cairn/agent_install/` | 66 |
| 4 | T014 · long-tail reserve, all paths unassigned above | 226 |

## Migration ledger

| file :: symbol | landing (memory type + title / docs target) |
|---|---|
| src/cairn/telemetry/cli_metrics.py :: build_row | memory · decision · CLI telemetry rows are redacted before buffering |
| src/cairn/telemetry/cli_metrics.py :: _flush_cli_metrics | memory · pattern · Telemetry flushes snapshot before clearing |
| src/cairn/telemetry/cli_metrics.py :: _reset_for_tests | memory · pattern · Reset telemetry subsystem state without restarting the shared flusher |
| src/cairn/telemetry/events.py :: event-name catalog | memory · decision · Telemetry event names and enums are shared contracts |
| src/cairn/telemetry/events.py :: _coerce_attrs | memory · decision · Telemetry attrs are centrally bounded and privacy-scrubbed |
| src/cairn/telemetry/events.py :: emit | memory · decision · Telemetry gates are read fresh and never propagate failures |
| src/cairn/telemetry/events.py :: warn_once | memory · decision · Separate operational warnings from telemetry quality signals |
| src/cairn/telemetry/otel.py :: _get_logger | memory · decision · OTLP export is strictly lazy and failure-latched |
| src/cairn/telemetry/otel.py :: _flush_otlp | memory · decision · OTLP exports are synchronous, bounded, and at-least-once |
| src/cairn/telemetry/sink.py :: configure_conn | memory · pattern · Telemetry sinks inject connections and share one flusher |
| src/cairn/telemetry/sink.py :: _prune | memory · decision · Prune telemetry by timestamps inside the flush transaction |
| src/cairn/memory/privacy.py :: strip_private_data | memory · decision · Memory privacy redacts URI credentials before secret patterns |
| src/cairn/memory/promotion.py :: capture_memory | memory · decision · Memory capture is the redaction and supersession chokepoint |
| src/cairn/memory/promotion.py :: _is_session_bookkeeping | memory · pattern · Session bookkeeping progress counts apply to titles only |
| src/cairn/memory/promotion.py :: record_references_batch | memory · pattern · Memory reference analytics are scrubbed and batched |
| src/cairn/memory/promotion.py :: search_memory | memory · decision · Memory search fuses lexical and semantic ranks |
| src/cairn/memory/promotion.py :: promote_memory | memory · pattern · Memory tier moves carry embeddings without re-embedding |
| src/cairn/memory/promotion.py :: DEFAULT_CRITIC_SCORE | memory · decision · Memory neutral critic score is one shared float |
| src/cairn/memory/recurrence.py :: failure_signature | memory · pattern · Failure signatures normalize volatile error noise |
| src/cairn/memory/scoring.py :: _freshness | memory · pattern · Manual memory sources bypass freshness decay |
| src/cairn/memory/stance.py :: reflect_store | memory · pattern · Stance reflection writes only changed stance keys |
| src/cairn/memory/store.py :: write_validity | memory · pattern · Memory validity extensions own indexed projections |
| src/cairn/memory/store.py :: store_memory | memory · decision · Memory filenames carry collision-safe unique suffixes |
| src/cairn/memory/store.py :: share_memory | memory · pattern · Shared memory rows are validated idempotent pointers |
| src/cairn/memory/store.py :: delete_memory | memory · decision · Memory deletion is namespace-guarded and exact-match |
| src/cairn/graph/embeddings.py :: current_model | memory · decision · Embedding model stamps scope persistence and vec0 tables |
| src/cairn/graph/embeddings.py :: CHUNK_VARIANTS | memory · decision · Embedding chunk variants preserve an identity floor |
| src/cairn/graph/embeddings.py :: MV_KINDS | memory · decision · Multi-vector embeddings use per-kind staleness |
| src/cairn/graph/embeddings.py :: _MODEL_CACHE_LOCK | memory · pattern · Embedding caches serialize first-writer resolution |
| src/cairn/graph/embeddings.py :: _SESSION_STAMP_OVERRIDE | memory · decision · Embedding ladder adoptions yield to explicit configuration |
| src/cairn/graph/embeddings.py :: _alias_preflight | memory · decision · Embedding alias gates prove parity before writes |
| src/cairn/graph/embeddings.py :: _server_probe_available | memory · pattern · Server availability probes settle on one verdict |
| src/cairn/graph/embeddings.py :: warn_hash_fallback_once | memory · decision · Implicit hash fallback stays observable |
| src/cairn/graph/embeddings.py :: download_model | memory · pattern · Model downloads run in a child process |
| src/cairn/graph/embeddings.py :: ensure_semantic_deps | memory · decision · Semantic installs are ABI-scoped and repairable |
| src/cairn/graph/embeddings.py :: _embed_server | memory · decision · Server embedding requests validate before and after I/O |
| src/cairn/graph/embeddings.py :: _purge_embedding_rows | memory · pattern · Embedding deletes align vec0 rowids transactionally |
| src/cairn/graph/embeddings.py :: embed_all | memory · decision · Bulk embedding preserves rowids and defers ANN rebuild |
| src/cairn/graph/embeddings.py :: embed_all | memory · decision · Multivector opt-out preserves legacy summaries |
| src/cairn/graph/embeddings.py :: embed_symbols | memory · pattern · Targeted embeddings sync ANN in the write transaction |
| src/cairn/graph/embeddings.py :: rename_memory_embedding | memory · decision · Memory embeddings follow concept identity |
| src/cairn/graph/embeddings.py :: chunk_memory_body | memory · pattern · Memory chunks honor guidance markers |
| src/cairn/graph/embeddings.py :: embed_memory_concepts | memory · pattern · Memory embedding batches isolate unread concepts |
| src/cairn/graph/schema.py :: EDGE_RESOLUTION_MIGRATION | memory · decision · Edge resolution values encode resolver trust |
| src/cairn/graph/schema.py :: EDGE_SOURCE_MIGRATION | memory · pattern · Additive graph migrations avoid FTS trigger drift |
| src/cairn/graph/schema.py :: IMPORTS_LOCAL_ALIAS_MIGRATION | memory · decision · Parser nulls mean no evidence |
| src/cairn/graph/schema.py :: TOOL_METRICS_SOURCE_MIGRATION | memory · decision · Tool metric nulls encode absent evidence |
| src/cairn/graph/schema.py :: note_contention | memory · decision · Lock-contention telemetry filters schema failures |
| src/cairn/graph/schema.py :: _maybe_backfill_fts | memory · pattern · FTS backfill uses token presence not row count |
| src/cairn/graph/schema.py :: _maybe_backfill_memory_validity | memory · pattern · Memory validity backfill follows the opened store |
| src/cairn/graph/schema.py :: rebuild_term_df | memory · decision · term_df rebuild prefers FTS vocabulary |
| src/cairn/graph/schema.py :: get_db | memory · pattern · Graph schema initialization is path-scoped and atomic |
| src/cairn/graph/schema.py :: get_db | memory · decision · Read-only SQLite URIs quote path delimiters |
| src/cairn/graph/schema.py :: swap_db_file | memory · decision · Database swaps checkpoint WAL before replace |
| src/cairn/graph/schema.py :: copy_telemetry_tables | memory · decision · Whole-file swaps preserve analytics history |
| src/cairn/graph/schema.py :: build_lock | memory · decision · Build lock files are never unlinked |
| src/cairn/graph/builder.py :: _record_skips | memory · pattern · Skip recording is scoped and best-effort |
| src/cairn/graph/builder.py :: _insert_results | memory · decision · Graph commit cadence follows the storage destination |
| src/cairn/graph/builder.py :: _apply_scip_overlay | memory · decision · SCIP overlays degrade without aborting live builds |
| src/cairn/graph/builder.py :: _build_graph_impl | memory · decision · Interrupted rebuild markers bracket durable writes |
| src/cairn/graph/builder.py :: _build_graph_impl | memory · decision · Import edge materialization waits for every module symbol |
| src/cairn/graph/builder.py :: build_graph | memory · decision · Full graph builds stage in memory and persist once |
| src/cairn/graph/builder.py :: record_build_run | memory · decision · Build-run telemetry is direct structured analytics |
| src/cairn/graph/builder.py :: insert_parsed_file | memory · pattern · Module symbols defer to same-stem code symbols |
| src/cairn/graph/builder.py :: insert_parsed_file | memory · pattern · Same-file edge lookup stays linear in file symbols |
| src/cairn/graph/builder.py :: insert_parsed_file | memory · decision · Contains edges bypass target resolution |
| src/cairn/graph/builder.py :: insert_parsed_file | memory · pattern · Resolver signals travel in abstain-safe tuple slots |
| src/cairn/graph/builder.py :: materialize_import_edges | memory · decision · Import targets match unique suffixes store-wide |
| src/cairn/graph/builder.py :: _clear_repo | memory · decision · Repo deletion demotes cross-repo targets before symbols |
| src/cairn/graph/builder.py :: _clear_repo | memory · decision · Repo rebuilds purge embeddings and ANN rows together |
| src/cairn/graph/incremental.py :: reindex_paths | memory · decision · Incremental reindex preserves incoming edge names |
| src/cairn/graph/incremental.py :: reindex_paths | memory · pattern · Tracked file identity honors stored repo and portable path |
| src/cairn/graph/incremental.py :: reindex_paths | memory · decision · Incremental file replacement is transactional |
| src/cairn/graph/incremental.py :: reindex_paths | memory · decision · Unavailable parsers converge to fresh-build skips |
| src/cairn/graph/incremental.py :: reindex_paths | memory · decision · Embedding follows reindex commit boundaries |
| src/cairn/graph/incremental.py :: reindex_paths | memory · pattern · Resolver repair tracks changed candidate counts |
| src/cairn/graph/incremental.py :: incremental_update | memory · decision · Incremental updates refresh derived indexes |
| src/cairn/graph/incremental.py :: incremental_update | memory · decision · Incremental writes serialize with graph rebuilds |
| src/cairn/graph/incremental.py :: incremental_update | memory · pattern · Derived prestate is captured before row deletion |
| src/cairn/graph/incremental.py :: incremental_update | memory · decision · Incremental build runs stay caller-scoped |
| src/cairn/graph/incremental.py :: _maintain_derived_indexes | memory · pattern · Derived maintenance seeds from repaired graph deltas |
| src/cairn/graph/incremental.py :: _changed_source_files | memory · decision · Change detection covers untracked and gitless repos |
| src/cairn/graph/scanner.py :: DEFAULT_SKIP_DIRS | memory · decision · Default scan skips keep generated code out of the graph |
| src/cairn/graph/scanner.py :: discover_repos | memory · decision · Nested repositories require explicit discovery |
| src/cairn/graph/scanner.py :: resolve_file_path | memory · decision · Stored graph paths remain repo-relative |
| src/cairn/graph/scanner.py :: _GITIGNORE_CACHE | memory · pattern · Nested gitignore verdicts cache per repo root |
| src/cairn/graph/scanner.py :: _is_minified | memory · decision · Filename markers skip vendored minified bundles |
| src/cairn/graph/scanner.py :: classify_file | memory · decision · Include overrides ignore filters but not generated-file caps |
| src/cairn/graph/dataflow.py :: CLOSURE_MAX_DEPTH | memory · decision · Closure depth supports one deeper impact queries |
| src/cairn/graph/dataflow.py :: _row_is_public | memory · pattern · One public-symbol predicate keeps dataflow parity |
| src/cairn/graph/dataflow.py :: _SQLITE_IN_CHUNK | memory · decision · SQLite IN batches stay portable and deterministic |
| src/cairn/graph/dataflow.py :: _compute_dataflow_row | memory · pattern · Full and incremental dataflow share row semantics |
| src/cairn/graph/dataflow.py :: build_dataflow_index | memory · decision · Dataflow symbol caps bound BFS work |
| src/cairn/graph/dataflow.py :: _closure_rows | memory · decision · Scoped closure reproduction preserves full ordering |
| src/cairn/graph/dataflow.py :: build_transitive_closure | memory · decision · Closure seeds only structural resolved edges |
| src/cairn/graph/dataflow.py :: maintain_transitive_closure | memory · pattern · Scoped closure rebuild matches its full restriction |
| src/cairn/graph/dataflow.py :: maintain_dataflow_index | memory · pattern · Dataflow maintenance follows changed caller names |
| src/cairn/graph/dataflow.py :: impact_from_closure | memory · decision · Closure impact trades DFS details for bounded coverage |
| src/cairn/graph/dataflow.py :: closure_has_seed_cycle | memory · decision · Seed cycles route impact queries to DFS |
| src/cairn/graph/__init__.py :: __getattr__ | memory · decision · Graph lock observability stays on the public package API |
| src/cairn/graph/__init__.py :: __getattr__ | memory · pattern · Package lazy exports use import_module |
| src/cairn/graph/ann_index.py :: warn_ann_fallback_once | memory · decision · ANN fallback warnings distinguish degradation from opt-out |
| src/cairn/graph/ann_index.py :: _SOURCE_PREFIX | memory · decision · ANN source tables keep isolated vec0 indexes |
| src/cairn/graph/ann_index.py :: _table_name | memory · decision · ANN table sources are closed and identifier-safe |
| src/cairn/graph/ann_index.py :: try_load | memory · pattern · sqlite-vec loading degrades without raising |
| src/cairn/graph/ann_index.py :: sync_index_row | memory · pattern · vec0 updates delete and insert in the caller transaction |
| src/cairn/graph/ann_index.py :: delete_index_rows | memory · decision · ANN deletions prevent rowid-vector reuse |
| src/cairn/graph/ann_index.py :: ann_query | memory · decision · ANN query failures mean caller fallback |
| src/cairn/graph/ann_index.py :: ann_query | memory · decision · ANN contention excludes ordinary query failures |
| src/cairn/graph/ann_index.py :: index_row_count | memory · pattern · ANN row counts detect wholesale index drift |
| src/cairn/graph/blast.py :: _git | memory · pattern · Git diff parsing tolerates late binary bytes |
| src/cairn/graph/config.py :: load_config | memory · decision · Malformed graph config falls back to defaults |
| src/cairn/graph/config.py :: _as_name_table | memory · pattern · Graph config coercion preserves declared keys |
| src/cairn/graph/cross_repo.py :: _escape_like | memory · pattern · Cross-repo SQL escapes namespace wildcards |
| src/cairn/graph/cross_repo.py :: _load_namespaces | memory · decision · Namespace maps resolve per workspace |
| src/cairn/graph/cross_repo.py :: cross_repo_deps | memory · decision · Cross-repo matches are dot-bounded |
| src/cairn/graph/embed_ladder.py :: check_parity | memory · decision · Embedding parity samples are deterministic and vacuous-safe |
| src/cairn/graph/embed_ladder.py :: _LADDER_LOCK | memory · pattern · Ladder evaluation serializes check-then-act |
| src/cairn/graph/embed_ladder.py :: set_session_stamp | memory · decision · Ladder aliases retain the stored corpus stamp |
| src/cairn/graph/embed_ladder.py :: notify_degradation | memory · decision · Embedding degradation lines bypass telemetry gating |
| src/cairn/graph/embed_ladder.py :: evaluate_ladder | memory · pattern · Ladder verdicts cache by backend state |
| src/cairn/graph/embed_ladder.py :: _config_value | memory · decision · Ladder probes share the embed config choke point |
| src/cairn/graph/explore.py :: _read_source_spans | memory · pattern · Explore source budgets finish whole symbols |
| src/cairn/graph/explore.py :: _ambiguous_dispatch | memory · decision · Ambiguous dispatch remains queryable |
| src/cairn/graph/explore.py :: explore | memory · pattern · Explore defers semantic imports to fusion |
| src/cairn/graph/explore.py :: explore | memory · decision · Explore marks hash-vector weakness |
| src/cairn/graph/federation.py :: iter_stores | memory · decision · Federated stores derive their own layouts |
| src/cairn/graph/federation.py :: federated_search | memory · decision · Federated search fuses ranks not raw scores |
| src/cairn/graph/federation.py :: _stamps_share_backend | memory · decision · Shared query embeddings require exact stamp parity |
| src/cairn/graph/federation.py :: _shared_query_embed | memory · pattern · Federated embedding seams swap under lock |
| src/cairn/graph/federation.py :: _classify_store | memory · pattern · Store classification stays filesystem-only |
| src/cairn/graph/lexical.py :: _is_fts_prefix_pattern | memory · decision · Lexical search unions FTS prefixes with LIKE substrings |
| src/cairn/graph/lexical.py :: _terms_to_fts | memory · decision · Term queries sanitize and OR FTS tokens |
| src/cairn/graph/lexical.py :: _search_like | memory · pattern · LIKE fallback escapes only user literals |
| src/cairn/graph/lexical.py :: _fts_search_or_like | memory · decision · Lexical degradation separates lock contention |
| src/cairn/graph/model_warmup.py :: warm_models_in_background | memory · pattern · Model warmup stays off test and boot critical paths |
| src/cairn/graph/model_warmup.py :: warm_models | memory · pattern · Model warmup isolates per-model failures |
| src/cairn/graph/model_warmup.py :: _warm_embedder | memory · decision · Model warmup never downloads weights |
| src/cairn/graph/model_warmup.py :: _load_with_offline_guard | memory · pattern · Cached model loads retry once online |
| src/cairn/graph/prf.py :: _IDF_NO_DF | memory · pattern · PRF degraded IDF stays positive |
| src/cairn/graph/queries.py :: semantic_search | memory · pattern · Query facades lazily load semantic search |
| src/cairn/graph/query_enrich.py :: ENRICH_DF_MAX_FRACTION | memory · decision · Query enrichment drops ubiquitous terms strictly |
| src/cairn/graph/query_enrich.py :: _is_identifier_shaped | memory · pattern · Query enrichment recognizes explicit and structural identifiers |
| src/cairn/graph/query_enrich.py :: enrich | memory · decision · Query enrichment stays pure and preserves originals |
| src/cairn/graph/reranker.py :: _RERANKER_CACHE_LOCK | memory · pattern · Reranker loads serialize cache access |
| src/cairn/graph/reranker.py :: _rerank_marker_path | memory · decision · Reranker downloads auto-enable by marker |
| src/cairn/graph/reranker.py :: download_reranker_model | memory · pattern · Reranker downloads run in a child process |
| src/cairn/graph/reranker.py :: RERANK_MAX_LENGTH | memory · decision · Rerank truncation length is pinned |
| src/cairn/graph/reranker.py :: _structured_candidate_text | memory · pattern · Rerank extraction cannot lose chunk data |
| src/cairn/graph/reranker.py :: _truncate_candidate | memory · decision · Rerank truncation preserves the query verbatim |
| src/cairn/graph/reranker.py :: rerank | memory · decision · Rerank failures preserve fused order |
| src/cairn/graph/resolver.py :: build_symbol_index | memory · decision · Resolver excludes module symbols |
| src/cairn/graph/resolver.py :: build_import_index | memory · pattern · Resolver aliases record explicit imports only |
| src/cairn/graph/resolver.py :: build_members_index | memory · pattern · Member resolution trusts graph type symbols |
| src/cairn/graph/resolver.py :: _arity_unique_match | memory · decision · Arity resolves only unique exact matches |
| src/cairn/graph/resolver.py :: resolve_edge | memory · decision · Receiver types outrank file scope |
| src/cairn/graph/resolver.py :: _import_aware_candidates | memory · pattern · Import reachability recognizes direct and containing imports |
| src/cairn/graph/resolver.py :: resolve_repo_edges | memory · decision · Resolver tiers span the workspace symbol index |
| src/cairn/graph/resolver.py :: repair_incoming_edges | memory · pattern · Incoming edge repair follows recreated names |
| src/cairn/graph/semantic.py :: _fused_confident | memory · decision · Rerank gating requires margin and exact names |
| src/cairn/graph/semantic.py :: _mapping_rows | memory · pattern · Semantic reads normalize row access |
| src/cairn/graph/semantic.py :: _candidates_from_ann_hits | memory · decision · ANN candidates deduplicate at best score |
| src/cairn/graph/semantic.py :: _term_df_lookup | memory · pattern · Semantic DF lookups stay memoized and indexed |
| src/cairn/graph/semantic.py :: semantic_search | memory · decision · Semantic retrieval keeps one embed call per pass |
| src/cairn/graph/taint.py :: build_registry | memory · decision · Taint override categories replace defaults safely |
| src/cairn/graph/taint.py :: find_paths | memory · decision · Taint walks follow resolution-aware edges |
| src/cairn/graph/taint.py :: _paths_from_seed | memory · pattern · Seed path walks expand destinations |
| src/cairn/graph/taint.py :: _fuzzy_neighbors | memory · pattern · Fuzzy graph hops fan to same-repo definitions |
| src/cairn/graph/tests.py :: TEST_PATH_PATTERNS | memory · decision · Test detection patterns stay conservative |
| src/cairn/graph/traversal.py :: STRUCTURAL_EDGE_KINDS | memory · decision · Graph traversal excludes service edges by default |
| src/cairn/graph/traversal.py :: get_callers | memory · decision · Graph fuzzy mode preserves name evidence |
| src/cairn/graph/traversal.py :: impact_analysis | memory · decision · Closure impact preserves DFS parity |
| src/cairn/graph/traversal.py :: trace_flow | memory · pattern · Flow traces key nodes by identity |
| src/cairn/graph/watcher.py :: FileWatcherService | memory · decision · Watcher events defer IO to debounce batches |
| src/cairn/graph/watcher.py :: _flush | memory · decision · Watcher flushes never overlap updates |
| src/cairn/graph/watcher.py :: _flush | memory · pattern · Deleted files bypass full scan filters |
| src/cairn/graph/watcher.py :: _flush | memory · decision · Pending watcher rows use exact dual path forms |
| src/cairn/graph/watcher.py :: _flush | memory · pattern · Watcher clears restored-content leftovers |
| src/cairn/graph/watcher.py :: _path_forms | memory · decision · Watcher path forms resolve symlinks exactly |
| src/cairn/mcp_server/_server_core.py :: tool config resolution | memory · decision · MCP tools resolve config through env-backed helpers, not per-request context |
| src/cairn/mcp_server/_server_core.py :: mcp singleton | memory · decision · The FastMCP singleton pins its log level to protect CLI output |
| src/cairn/mcp_server/_server_core.py :: mcp singleton | memory · workaround · Suppress pydantic-settings incomplete-field warnings narrowly at FastMCP construction |
| src/cairn/mcp_server/_server_core.py :: _conn | memory · pattern · MCP read connections pool per thread with inode revalidation |
| src/cairn/mcp_server/_server_core.py :: _rw_conn | memory · decision · MCP write tools take a separate writable connection that may contend with CLI writers |
| src/cairn/mcp_server/_server_core.py :: _staleness_banner | memory · pattern · MCP staleness banners read pending_sync written only by the live watcher |
| src/cairn/mcp_server/_server_core.py :: _build_age_str | memory · pattern · MCP status surfaces stay independent of CLI imports |
| src/cairn/mcp_server/_server_core.py :: _health_block | memory · decision · MCP health flags ANN degradations only when sqlite-vec was expected |
| src/cairn/mcp_server/embed_buffering.py :: _flush | memory · pattern · Memory embed flush retries failed batches and escalates chronic failures |
| src/cairn/mcp_server/lifecycle.py :: render_plist | memory · decision · The SSE LaunchAgent defaults to a read-only daemon |
| src/cairn/mcp_server/lifecycle.py :: _is_cairn_serve_cmdline | memory · decision · Stray serve detection anchors on argv tokens, not substrings |
| src/cairn/mcp_server/lifecycle.py :: _db_holder_pids | memory · pattern · lsof exit 1 distinguishes no-holders from verification failure |
| src/cairn/mcp_server/lifecycle.py :: find_strays | memory · decision · Stray serve kills require anchored cmdlines and verified DB ownership |
| src/cairn/mcp_server/lifecycle.py :: terminate_pid | memory · pattern · Terminate-then-kill re-verifies cmdlines to avoid reused pids |
| src/cairn/mcp_server/lifecycle.py :: sse_responds | memory · pattern · SSE liveness probes HTTP, not bare TCP accepts |
| src/cairn/mcp_server/metric_buffering.py :: MAX_RESULT_CHARS | memory · decision · MCP tool results are centrally capped before client token limits |
| src/cairn/mcp_server/metric_buffering.py :: configure_conn | memory · pattern · MCP metric buffering imports telemetry lazily to avoid boot cycles |
| src/cairn/mcp_server/metric_buffering.py :: _log_metric | memory · decision · Read-only MCP servers skip tool_metrics writes entirely |
| src/cairn/mcp_server/metric_buffering.py :: instrument | memory · pattern · instrument preserves signatures for FastMCP schemas |
| src/cairn/mcp_server/server.py :: _drain_buffered_telemetry | memory · pattern · Server watchdog drains buffers directly because os._exit skips atexit |
| src/cairn/mcp_server/server.py :: _install_exit_watchdog | memory · decision · The MCP exit watchdog never reads stdin |
| src/cairn/mcp_server/server.py :: verify_tool_count | memory · pattern · Tool-count guard runs at server start, not import time |
| src/cairn/mcp_server/server.py :: run (model warmup) | memory · decision · MCP boot warms semantic models in a background thread |
| src/cairn/mcp_server/server.py :: run (live watcher) | memory · decision · The live file watcher starts on the shared server boot path |
| src/cairn/cli/bench.py :: _profile_class | memory · decision · Bench profile comparisons bucket hosted-runner identity |
| src/cairn/cli/bench.py :: _resolve_baseline_file | memory · pattern · Bench baseline errors fail before suites run |
| src/cairn/cli/bench.py :: _swe_bench_workspaces | memory · decision · SWE-bench workspaces are commit-keyed warm caches |
| src/cairn/cli/bench.py :: _persistable_swe_bench_report | memory · pattern · Persisted bench reports drop wall-clock figures |
| src/cairn/cli/core.py :: build (staging lock) | memory · decision · Staging builds serialize against real-store writers |
| src/cairn/cli/core.py :: build (no-store exit) | memory · decision · Build failures keep the store-exists exit contract |
| src/cairn/cli/core.py :: build (telemetry carry) | memory · pattern · Staged swaps carry analytics history forward |
| src/cairn/cli/embed.py :: _resolve_adopted_model (empty-corpus gate) | memory · decision · Server-model adoption requires a stored corpus |
| src/cairn/cli/embed.py :: embed (hash fallback warning) | memory · decision · Hash-backend warnings distinguish silent fallback from choice |
| src/cairn/cli/embed.py :: embed (alias pin) | memory · decision · Adopted server models pin config, not restamps |
| src/cairn/cli/embed.py :: embed (build_runs row) | memory · pattern · Incremental CLI passes record sparse build_runs rows |
| src/cairn/cli/memory.py :: memory_capture (queued fallback) | memory · decision · Queued memory extraction redacts before persistence |
| src/cairn/cli/serve.py :: serve_start | memory · decision · The shared SSE daemon replaces per-client stdio servers |
| src/cairn/cli/system/doctor.py :: _check_embeddings | memory · decision · Hash-backend warnings distinguish silent fallback from choice |
| src/cairn/cli/system/doctor.py :: _check_embed_server | memory · decision · Doctor samples embedding degradations before resetting caches |
| src/cairn/cli/system/doctor.py :: _spawnable_workspace_entry | memory · decision · Doctor spawns only install-shaped workspace registrations |
| src/cairn/cli/system/doctor.py :: _check_environment | memory · decision · Doctor spawns only install-shaped workspace registrations |
| src/cairn/cli/system/doctor.py :: _registration_findings | memory · decision · Registration probes fail only on provably different stores |
| src/cairn/cli/system/doctor.py :: _run_doctor | memory · decision · Read-only diagnostics never materialize a missing store |
| src/cairn/cli/system/report.py :: _open_report_conn | memory · decision · Read-only diagnostics never materialize a missing store |
| src/cairn/cli/system/metrics.py :: _gather_quality | memory · decision · Retrieval empty-rate is scoped to semantic searches |
| src/cairn/cli/system/sync.py :: _run_sync (build_runs row) | memory · pattern · Incremental CLI passes record sparse build_runs rows |
| src/cairn/dashboard/app.py :: is_hx_request | memory · pattern · Dashboard htmx routes share one seam and honor history restores |
| src/cairn/dashboard/app.py :: create_app (asset version) | memory · pattern · Dashboard asset versions bust stale browser caches |
| src/cairn/dashboard/app.py :: create_app (server probe) | memory · decision · Dashboard server probes are process-local and lock verdict publication |
| src/cairn/dashboard/app.py :: resolve_selection | memory · decision · Dashboard store selection accepts registry keys only |
| src/cairn/dashboard/data.py :: get_graph | memory · decision · Empty graph filters draw capped overviews, not empty graphs |
| src/cairn/dashboard/data.py :: inspect_symbol | memory · pattern · Graph inspection resolves one definition and reports truncation honestly |
| src/cairn/dashboard/data.py :: symbol_candidates | memory · decision · Exact symbol candidates expose ambiguity instead of a silent first row |
| src/cairn/dashboard/data.py :: symbol_suggest | memory · pattern · Symbol suggestions escape user wildcards and surface short names first |
| src/cairn/dashboard/data.py :: _serve_probes | memory · pattern · Dashboard health probes serve stale while revalidating in the background |
| src/cairn/dashboard/data.py :: get_health (ANN mootness) | memory · decision · ANN health probes report unknown when embeddings are moot |
| src/cairn/dashboard/data.py :: get_database_schema | memory · pattern · Database schema inference never overrides declared foreign keys |
| src/cairn/dashboard/data.py :: list_knowledge_docs | memory · decision · Knowledge filter counts are computed before filtering |
| src/cairn/dashboard/data.py :: get_knowledge_graph | memory · pattern · Knowledge graph edges require resolvable endpoints |
| src/cairn/dashboard/data.py :: _wiki_ref_map | memory · decision · Wiki symbol links come only from verified references |
| src/cairn/dashboard/data.py :: list_history | memory · decision · History paging is keyset-based and treats null timestamps as pre-window |
| src/cairn/dashboard/data.py :: _estimate_divisor | memory · decision · Exact token estimates calibrate from bounded window samples |
| src/cairn/dashboard/data.py :: get_tool_tokens | memory · pattern · Token views distinguish unknown truncation from zero truncation |
| src/cairn/dashboard/data.py :: get_session_chains | memory · pattern · Session chains never split on unknown timestamps |
| src/cairn/dashboard/markdown.py :: _inline | memory · pattern · Markdown link escaping neutralizes href quotes without double escapes |
| src/cairn/dashboard/routes/history.py :: register (export rows) | memory · decision · Dashboard exports reuse view query seams without cursor filters |
| src/cairn/dashboard/routes/knowledge.py :: _knowledge_catalog | memory · decision · Knowledge family options union classifier seeds with corpus values |
| src/cairn/dashboard/routes/knowledge.py :: register (route order) | memory · decision · Knowledge graph routes precede the two-segment document catchall |
| src/cairn/dashboard/routes/settings.py :: _reject_cross_site | memory · decision · Loopback dashboards still reject cross-site form posts |
| src/cairn/dashboard/routes/settings.py :: _settings_context | memory · decision · Settings forms never reflect or clear stored API keys |
| src/cairn/dashboard/routes/wiki.py :: _wiki_page_repo | memory · decision · Repo-qualified wiki URLs keep colliding page ids addressable |
| src/cairn/dashboard/tokenizer.py :: _probe_tokenizer | memory · decision · Dashboard tokenizer probes only cached local models |
| src/cairn/dashboard/workspaces.py :: _count_tool_calls | memory · decision · Workspace call counts distinguish zero from unreadable |
| src/cairn/dashboard/workspaces.py :: probe_stores | memory · decision · Workspace probes cap all DB opens and expose capped counts |
| src/cairn/parsers/_registry.py :: _load_language_capsule | memory · decision · Parser registry prefers built-in languages before plugins |
| src/cairn/parsers/_scip_pb2.py :: provenance header | memory · decision · Vendored SCIP gencode carries its protobuf floor |
| src/cairn/parsers/base.py :: BODY_MAX_CHARS | memory · decision · Parser bodies cap embedding context before docstring duplication |
| src/cairn/parsers/kotlin.py :: _parse_call | memory · pattern · Kotlin call edges resolve operator-invoke properties to types |
| src/cairn/parsers/kotlin.py :: _emit_type_references | memory · decision · Kotlin signature references stay body-free |
| src/cairn/parsers/php.py :: _bare_ns_name | memory · pattern · PHP parser edges use bare namespace tails |
| src/cairn/parsers/ruby.py :: _visit_type_decl | memory · pattern · Ruby extends edges use bare source names |
| src/cairn/parsers/routes.py :: _JSX_ROUTE_RE | memory · decision · Route heuristics stay bounded and labeled |
| src/cairn/parsers/service_calls.py :: detect_service_calls | memory · decision · Service-call detection uses bounded regex scans |
| src/cairn/parsers/scip_importer.py :: _resolve_scip_edge | memory · decision · SCIP edges resolve by definition occurrence positions |
| src/cairn/knowledge/doc_link.py :: doc-link pipeline | memory · decision · Doc-link proposals stay scoped, mirrored, and deduplicated |
| src/cairn/knowledge/index.py :: rebuild_knowledge_index | memory · decision · Knowledge index rebuilds are idempotent and never guess pointers |
| src/cairn/knowledge/index.py :: supersede_chain | memory · decision · Supersede walks stay deterministic on stale or branched chains |
| src/cairn/knowledge/islands.py :: queue_doc_link_tasks | memory · decision · Knowledge islands queue disconnected pairs without duplicates |
| src/cairn/knowledge/ingest/parser.py :: frontmatter recovery | memory · pattern · Malformed ingest frontmatter degrades without losing relationships |
| src/cairn/knowledge/search.py :: _visible | memory · decision · Knowledge search hides archived history but keeps superseded docs |
| src/cairn/knowledge/search.py :: _find_related | memory · decision · Knowledge lexical expansion trusts extracted and derived edges only |
| src/cairn/knowledge/search.py :: _semantic_search | memory · decision · Knowledge semantic retrieval degrades observably and scans bundle metadata |
| src/cairn/knowledge/store.py :: add_document | memory · decision · Knowledge persistence redacts free text before slugs and storage |
| src/cairn/knowledge/store.py :: _refuse_out_of_namespace | memory · decision · Knowledge mutations guard the namespace at store chokepoints |
| src/cairn/knowledge/store.py :: update_status | memory · decision · Knowledge status transitions move forward only |
| src/cairn/knowledge/store.py :: delete_document | memory · pattern · Knowledge deletion cleans derived state without owning transactions |
| src/cairn/knowledge/workflow.py :: workflow storage/sync | memory · decision · Workflow steps stay structured and sync in place |
| src/cairn/knowledge/ingest/classifier.py :: classify_doc | memory · decision · Knowledge classification prefers overrides and exact token matches |
| src/cairn/knowledge/ingest/adapters.py :: _dir_rule_matches | memory · pattern · Ingest directory skips match nested repo path prefixes (re-recorded; first landing aged raw-tier) |
| src/cairn/knowledge/ingest/identity.py :: _stable_id | memory · decision · Ingest identities remain distinct under the slug cap |
| src/cairn/knowledge/ingest/adr.py :: detect_supersede_relationships | memory · decision · ADR supersede detection stays directional and directory-scoped |
| src/cairn/knowledge/ingest/executor.py :: execute_manifest | memory · pattern · Approved ingest resolves refs, preserves inferred links, and refreshes indexes |
| src/cairn/knowledge/ingest/executor.py :: verify_manifest | memory · decision · Manifest verification uses exact whole-store counts |
| src/cairn/knowledge/ingest/staging.py :: stage_outbox | memory · pattern · Staged knowledge output mirrors approved store output |
| src/cairn/agent_install/__init__.py :: verify_registration | memory · decision · Agent registration probes isolate the written store |
| src/cairn/agent_install/__init__.py :: _verify_results | memory · pattern · Installer verification skips unprobeable registrations |
| src/cairn/agent_install/__init__.py :: install (registration verification) | memory · decision · Agent install verification serves every caller |
| src/cairn/agent_install/__init__.py :: uninstall | memory · decision · Agent uninstall mirrors install scope |
| src/cairn/agent_install/__init__.py :: install_cross_tool | memory · pattern · Cross-tool agent assets remain workspace-owned |
| src/cairn/agent_install/__init__.py :: _PROBE_TIMEOUT_S | memory · pattern · Registration probe timeouts avoid healthy-install flakes |
| src/cairn/agent_install/__init__.py :: _registration_entry | memory · pattern · Registration readers cover every client MCP shape |
| src/cairn/agent_install/__init__.py :: _registration_argv | memory · pattern · Registration argv readers span invocation shapes |
| src/cairn/agent_install/__init__.py :: install (transport default) | memory · decision · Agent installs default to shared SSE transport |
| src/cairn/agent_install/_common.py :: module boundary | memory · decision · Agent install shared helpers stay sibling-free |
| src/cairn/agent_install/_common.py :: _uninstall_bases | memory · pattern · Uninstall bases may overlap safely |
| src/cairn/agent_install/_common.py :: MCP config generators | memory · pattern · MCP configs embed only non-default homes |
| src/cairn/agent_install/_common.py :: _claude_hook_command | memory · decision · Hook commands stay shell-safe and matchable |
| src/cairn/agent_install/_common.py :: _claude_agent_md | memory · pattern · Claude agent prompts preload the shared skill |
| src/cairn/agent_install/_common.py :: _omp_agent_md | memory · decision · omp agents inherit session models |
| src/cairn/agent_install/detect.py :: claude_desktop_config_path | memory · pattern · Claude Desktop config is global and per-OS |
| src/cairn/agent_install/detect.py :: _hooks_have_cairn | memory · decision · Claude settings detection separates hooks from MCP |
| src/cairn/agent_install/merge.py :: _atomic_write_text | memory · decision · Agent config writes use same-directory atomic renames |
| src/cairn/agent_install/merge.py :: _load_json_or_none | memory · decision · Malformed agent configs are backed up before replacement |
| src/cairn/agent_install/merge.py :: _write_file / _merge_json_file | memory · pattern · Agent dry runs preserve real idempotence verdicts |
| src/cairn/agent_install/merge.py :: _write_tree | memory · pattern · Skill packages install as complete trees |
| src/cairn/agent_install/merge.py :: _already_installed | memory · decision · Partial hook installs heal on reinstall |
| src/cairn/agent_install/merge.py :: _entry_entrypoints / _strip_hooks | memory · pattern · Hook ownership matching spans client shapes and legacy paths |
| src/cairn/agent_install/merge.py :: _rm_tree_if_cairn | memory · decision · Cairn-scoped directory removal refuses broader paths |
| src/cairn/agent_install/merge.py :: _rm_if_ours | memory · decision · Generated-file uninstall requires exact content match |
| src/cairn/agent_install/merge.py :: _strip_mcp_opencode | memory · workaround · OpenCode uninstall also strips legacy mcp.json |
| src/cairn/agent_install/clients/claude.py :: install_claude (global MCP) | memory · decision · Claude global MCP registrations use user scope |
| src/cairn/agent_install/clients/claude.py :: install_claude / clients/droid.py :: install_droid | memory · pattern · Claude and Droid CLI registrations carry store env |
| src/cairn/agent_install/clients/claude_desktop.py :: mcp_config_json_desktop | memory · decision · Claude Desktop always uses stdio with explicit workspace |
| src/cairn/agent_install/clients/droid.py :: install_droid (MCP fallback) | memory · decision · Droid MCP registrations have a file fallback |
| src/cairn/agent_install/clients/droid.py :: install_droid (command shape) | memory · pattern · Droid stdio commands stay one argument |
| src/cairn/agent_install/clients/droid.py :: uninstall | memory · decision · Droid registration removal ignores file scope |
| src/cairn/agent_install/clients/opencode.py :: _opencode_config_path | memory · decision · OpenCode global config is read and detectable |
| src/cairn/agent_install/clients/opencode.py :: install_opencode (asset reach) | memory · decision · OpenCode consumes only shared agent skills |
| src/cairn/agent_install/clients/zcode.py :: install_zcode (global scope) | memory · decision · ZCode global assets and MCP paths split |
| src/cairn/agent_install/clients/zcode.py :: _strip_mcp_zcode | memory · pattern · ZCode teardown prunes empty MCP containers |
| src/cairn/agent_install/clients/agy.py :: agy_mcp_config_json | memory · decision · AGY remote MCP entries use serverUrl only |
| src/cairn/agent_install/clients/kilo.py :: _kilo_config_path / clients/omp.py :: _omp_mcp_path | memory · pattern · Kilo and omp global paths follow client discovery |

## Final tally

| Metric | Value |
|---|---:|
| Seeded baseline | 1213 |
| Final remaining | 226 |
| Fingerprints removed | 987 (81.3%) |
| FR-001 floor (≤606) / plan target (≤550) | beaten by 380 / 324 |
| Migration ledger rows | 321 |
| Memory records landed | 317 (telemetry 11 · memory 14 · graph 154 · mcp 24 · cli 18 · dashboard 28 · parsers 10 · knowledge 20 · agent_install 38; 4 cli ledger rows collapse into shared landings) |
| Regen stability | `scripts/regen_scip_pb2.sh` emits the trimmed header (D-010) |

Swept areas now at zero violations: telemetry, memory, graph (all files), mcp_server, cli, dashboard, parsers, knowledge, agent_install. The 226 residual is the long-tail reserve (bench, compass, wiki, llm, eval, paths, skillgen, pack, hooks, review, okf, refs, viz, utils) — owned by the shrink-only ratchet, not this spec.
