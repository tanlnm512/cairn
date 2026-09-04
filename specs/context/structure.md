# Code structure: cairn

**Created**: 2026-08-28 | **Baseline**: 0.16.0 @ fe7a7f09edb015d6a8fb12cd5d0f1b06ed07f5c3
**Refreshed**: 2026-08-31 @ e002f9b (wiki-enhancements survey — tool count 28, wiki CLI subcommands, wiki package, agent_install line refs, mypy hard gate); line refs re-verified @ 0eaccf8 (v0.18.0); re-verified @ 7663989 (2026-09-04 — registration audit paths #104/#105, wiki export/enrich, system.py line refs re-anchored)
This repo's first spec-context baseline. Module map of `src/cairn/`, entry points,
and per-area ownership. All paths cited from file reads in the baseline session.

## Package facts
- Distribution `cairn-intel` v0.18.0 (pyproject.toml:7), src-layout (`[tool.setuptools.packages.find] where = ["src"]`,
  pyproject.toml:159-160). Console script: `cairn = "cairn.cli:main"` (pyproject.toml:153-154).
- Templates shipped as package data: `cairn.agent_integration` = `**/*`,
  `cairn.dashboard` templates/static (pyproject.toml:162-167) — install-agents and the
  dashboard work from a wheel install, not just a checkout.

## Entry points
1. `cairn` CLI → `src/cairn/cli/__init__.py:15` (`from .main import main`) → the click
   group in `src/cairn/cli/main.py`; every `cli/*.py` module imports `main` and decorates
   subcommands onto it (grep this session: agents, ask_context, bench, compass, core,
   dashboard, dataflow, download_reranker, embed, hooks_viz, knowledge, memory, query,
   serve, system, task, tree, uninstall, update, upgrade, validate).
2. MCP server → `cairn serve` / `cairn serve run` → `src/cairn/cli/serve.py:65-85`
   `_serve_foreground` sets `CAIRN_DB`/`CAIRN_KNOWLEDGE`/`CAIRN_READ_ONLY` then calls
   `src/cairn/mcp_server/server.py:run()` (stdio default, SSE with `--port`).
3. Agent-client hooks → `python -m cairn.hooks.claude_hooks <post_edit|session_end|post_tool_failure>`
   (`src/cairn/hooks/claude_hooks.py:175-185`); `src/cairn/hooks/cursor_hooks.py` re-exports
   the same handlers under Cursor's event names.
4. SSE daemon → `cairn serve start` writes a LaunchAgent plist
   (`~/Library/LaunchAgents/dev.cairn.sse.plist`, `src/cairn/mcp_server/lifecycle.py:22-36`)
   running `cairn serve run --port N --read-only` — macOS-only.

## Module map (src/cairn/)

| area | files (key) | owns |
|---|---|---|
| `paths.py` | single module | THE resolution substrate: `CAIRN_HOME` import-time binding (31-33), registry `workspaces.json` (35, `register_workspace` 193), `config.json` substrate (40, 214+), `resolve_workspace` (335), `resolve_store`/`StorePaths` (152, 355), `store_key` (167), shared-lib injection (`_inject_shared_libs` 123). Nearly every spec about stores/env touches this file first. |
| `agent_install/` | `_common.py`, `detect.py`, `merge.py`, `clients/{claude,claude_desktop,cursor,droid,zcode,agy,opencode,kilo,omp}.py`, `__init__.py` | `install-agents`/`uninstall-agents` engine. `_common`: CLIENTS list (9 clients, _common.py:19), `resolve_cg_command` (67-76), `mcp_config_json` (92-117), hook-command builder `_claude_hook_command` (125-127), instruction bodies (`_INSTRUCTIONS_BODY` 284, `_claude_instructions` 422, `_agents_instructions` 432 — the AGENTS.md/CLAUDE.md text; refreshed @ e002f9b, tool-count line at :440). `detect`: `detect_clients` (41-119), `check_installed` (150-218 — also covers the client-CLI user-scope registration files: claude `~/.claude.json`, droid `~/.factory/mcp.json`), `claude_desktop_config_path` (16-31). `merge`: atomic writes (25-54), `_merge_json_file` (159-205), `_already_installed` incl. env-dict compare (212-271), strip helpers (438-576). `__init__`: `install`/`uninstall` dispatch + `_INSTALLERS`/`_UNINSTALLERS` tables (246-258), `sse_daemon_reachable` (190), install-reach matrix comment (141-172). |
| `hooks/` | `claude_hooks.py`, `cursor_hooks.py`, `git_hooks.py` | runtime hook handlers (PATH-resolved `cairn` subprocess, claude_hooks.py:26-53) and the git post-commit installer (`POST_COMMIT_TEMPLATE` git_hooks.py:32-38, shell-injection guard 16-29). |
| `mcp_server/` | `server.py`, `_server_core.py`, `lifecycle.py`, `tools_{graph,compass,knowledge,memory,wiki}.py`, `structured.py`, `metric_buffering.py`, `embed_buffering.py` | MCP surface: boot guard + watchdog + run() (server.py:177-374; store check 213-234), FastMCP singleton + `_conn`/`_store`/`_bundle` + status resource (_server_core.py: 78, 81, 159, 222, 454), launchd daemon (lifecycle.py: cg_bin 45, render_plist 64, strays 262/371, sse_responds 424), 28 tools across 4 layers (`_EXPECTED_TOOL_COUNT = 28`, server.py:56; tools_wiki.py:wiki_generate is the 28th). |
| `cli/` | `main.py`, `_helpers.py`, `core.py`, `serve.py`, `system.py`, `agents.py`, `uninstall.py`, + query/memory/knowledge/task/compass/wiki/embed/bench/dashboard/dataflow/tree/update/validate/upgrade/hooks_viz/ask_context/download_reranker | command surface. `core.py`: init/build/config (`cairn config` paths echo 150-224). `system.py`: metrics/status/eval/sync/doctor/report (doctor checks `_check_schema` 743 … `_check_environment` 1569-1656, registration-audit tables `_REG_WS_FILES` 1353 / `_REG_HOME_FILES` 1362 + `_client_config_paths` 1374, `_run_doctor` 1680, render/json `_render_doctor` 1732 + doctor command 1750, report privacy gate `_scrub_doctor` 1839 + report command 2050). `agents.py`: install-agents CLI (10-206). `serve.py`: serve group + daemon lifecycle (89-241). `uninstall.py`: store teardown incl. lazy `_cairn_home()` (30-32). `wiki.py`: wiki group (generate/search/status/retry/export/enrich). `display.py`: echo helpers + vertical-rail flow (`rail` 517). (Refreshed 2026-08-31 @ 264647ae: added `_helpers.py`, `wiki.py`.) |
| `graph/` | `schema.py`, `builder.py`, `incremental.py`, `scanner.py`, `resolver.py`, `queries.py`, `explore.py`, `semantic.py`, `embeddings.py`, `embed_ladder.py`, `ann_index.py`, `reranker.py`, `watcher.py`, `dataflow.py`, `cross_repo.py`, `fusion.py`, `lexical.py`, `prf.py`, `traversal.py`, `stats.py`, `config.py`, `model_warmup.py`, `vector_math.py`, `tokenize.py`, `query_enrich.py`, `tests.py` | L1 graph: SQLite schema + `get_db` (schema.py:729-760), build/incremental update, symbol resolution, retrieval (BM25+vector fusion), embeddings backends/ladder, ANN, watcher. |
| `knowledge/` | `store.py`, `ingest/{staging,parser,identity,executor,convert,config,classifier,adapters}.py` | business-doc ingestion into the OKF knowledge base. |
| `memory/` | `store.py`, `promotion.py`, `privacy.py`, `scoring.py`, `store_protocol.py` | agent memory tiers (raw/drafts/tribal), promotion/decay, privacy filter used by report redaction. |
| `okf/` | `concept.py`, `bundle.py`, `conformance.py`, `provenance.py`, `utils.py` | OKF markdown concept store (`.knowledge/`) read/written by compass/wiki/memory. |
| `parsers/` | `python_parser.py`, `kotlin.py`, `typescript.py`, `swift.py`, `go.py`, `java.py`, `ruby.py`, `php.py`, `objc.py`, `dart.py`, `csharp.py`, `c_family.py`, `scip_indexers.py`, `scip_importer.py`, `_scip_pb2.py`, `_registry.py`, `base.py`, `service_calls.py`, `routes.py`, `inference/` | tree-sitter language adapters + SCIP consumption. |
| `retrieval/` | `vector_scan.py`, `protocols.py` | cosine scan + retrieval protocols. |
| `telemetry/` | `events.py`, `sink.py`, `otel.py`, `cli_metrics.py` | local events/tool_metrics buffering, optional OTLP export. |
| `viz/`, `wiki/`, `llm/`, `bench/`, `eval.py`, `dashboard/`, `utils/`, `refs.py` | renderers+query / wiki generation pipeline (catalog planner, manifest, pipeline, refine validator, sources parser + generator) / LLM task queue / perf suites + datasource / retrieval eval harness / FastAPI-ish dashboard + escape-first markdown renderer (dashboard/markdown.py) / logging+git helpers (utils/git.py: `_run_git` — used by the incremental updater, graph/incremental.py:766 — and `get_current_commit` rev-parse HEAD) / symbol ref helpers | peripheral surfaces. |

## Cross-cutting invariants (relevant to any install/env spec)
- Env → store: `CAIRN_WORKSPACE` (workspace pin) > cwd ancestor-walk; `CAIRN_DB`/
  `CAIRN_KNOWLEDGE` (hard path overrides); `CAIRN_HOME` (home root, import-time).
  All resolved through `paths.resolve_store` (paths.py:305-318).
- Config files are generated, never copied: every client config points at the
  `cairn` binary resolved at install time (`resolve_cg_command`), with no env block
  except claude-desktop's `CAIRN_WORKSPACE` pin (claude_desktop.py:32-35).
- Writes to user configs are atomic (tmp + os.replace, merge.py:25-54) and merges
  never clobber malformed files (backup-then-fresh, merge.py:119-205).
- Doctor/report are read-only by contract (`_run_doctor` system.py:1680,
  doctor command 1750, `_db_unavailable_results` 1657, report privacy gate
  `_scrub_doctor` 1839) — a missing store FAILs schema instead of being
  materialized.
