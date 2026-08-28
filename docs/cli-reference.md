# CLI Reference

Read this when MCP tools aren't available or you're scripting cairn. Every
command runs through the `cairn` entry point (`cairn.cli:main`, Click).
Pass `--db` / `--workspace` explicitly in scripts — `CAIRN_HOME` and friends
are read at process start, not per call.

## Build & lifecycle

| Command | Purpose |
|---|---|
| `cairn init` | interactive first-time setup (runs a build) |
| `cairn build` | full workspace rebuild (see [indexing.md](indexing.md)) |
| `cairn update [--file <path>]` | incremental reindex (git-diff driven) |
| `cairn stats` | graph statistics |
| `cairn checkpoint` | snapshot the store |
| `cairn config` | show effective configuration (`--json` emits `cairn_home`/`workspace`/`db`/`knowledge` as one JSON document — read-only, registers nothing; the scripting/probe surface) |
| `cairn uninstall` | remove cairn integration artifacts |

## Query

| Command | Purpose |
|---|---|
| `cairn def <symbol>` | find a definition |
| `cairn callers <symbol>` | who calls this |
| `cairn callees <symbol>` | what this calls |
| `cairn search <query>` | symbol search (FTS5) |
| `cairn semantic <query>` | hybrid semantic search |
| `cairn impact <symbol>` | what breaks if changed (within-repo) |
| `cairn deps <repo>` | cross-repo dependency map |
| `cairn tree <path>` | file/module symbol tree |
| `cairn ask "<question>"` | natural-language query across layers |
| `cairn context <file>` | compass + memory context for a file |

## Embeddings & rerank

| Command | Purpose |
|---|---|
| `cairn embed` | compute/refresh symbol embeddings (`--multivector`, `--build-index`, `--install-deps`, `--download-model`, `--adopt-server-model [ID]` to make a parity-verified server fallback permanent) |
| `cairn download-reranker` | fetch the CrossEncoder reranker |

## Knowledge

Group: `cairn knowledge …`

| Subcommand | Purpose |
|---|---|
| `ingest` | staged doc ingestion; `--file`/`--dir`/`--repo` sources, `--ingest` to execute, `--include-drafts`, `--outbox` (see [knowledge-and-memory.md](knowledge-and-memory.md)) |
| `add` / `import` / `remove` | manual document management |
| `search` / `list` / `embed` / `export` | query and maintain the bundle |
| `status <doc_id> <new_status>` | lifecycle transitions |
| `workflow add|trace|sync` | workflow definitions and traces |

## Memory

Group: `cairn memory …`

| Subcommand | Purpose |
|---|---|
| `record <type> "<title>"` | capture a memory (decision/pattern/mistake/workaround) |
| `search` / `list` / `digest` / `stats` | recall and inspect |
| `evolve` / `promote` / `demote` / `forget` | lifecycle |
| `decay` / `purge` / `consolidate` / `batch-critic` / `embed` / `capture` | maintenance |

## Serving & surfaces

| Command | Purpose |
|---|---|
| `cairn serve run|start|stop|status|restart` | MCP server (stdio foreground / SSE daemon `:9876`). `start`/`stop`/`restart` manage the macOS launchd LaunchAgent; `start` embeds `CAIRN_HOME`/`CAIRN_WORKSPACE`/`CAIRN_DB`/`CAIRN_KNOWLEDGE` into the plist when a custom home is in effect. On Linux these exit 1 — run `cairn serve --port 9876` under a process supervisor, or prefer stdio. |
| `cairn dashboard [--db] [--port]` | loopback dashboard `:8765` (read-only views + Settings) |
| `cairn install-agents` / `uninstall-agents` | wire cairn into AI clients (claude, droid, zcode, cursor, opencode, kilo, omp, agy, claude-desktop). With a custom `CAIRN_HOME`, generated stdio registrations embed `env.CAIRN_HOME` (claude global scope via `claude mcp add -e`), hook commands embed the assignment, and each written stdio registration is spawn-verified — the report shows per-client `verify: PASS/FAIL` naming both stores on mismatch. SSE and CLI-registered clients are skipped with a note; `--dry-run` never spawns. |

## Compass / wiki / tasks / dataflow

| Command | Purpose |
|---|---|
| `cairn compass generate|list|validate|gaps|flow|flow-gaps` | module navigation guides |
| `cairn wiki generate|search` | architecture wiki |
| `cairn task list|show|claim|complete` | LLM synthesis task queue |
| `cairn dataflow build|lookup` | precomputed impact index |

## Health & ops

| Command | Purpose |
|---|---|
| `cairn status` | build state, parse errors, resource health |
| `cairn doctor` | degradation check (exit 0 = PASS/WARN, 1 = FAIL). 10 checks: the 9 store-internal ones plus `environment` — store existence, client-registration consistency (FAIL on a provable wrong-store or unreachable SSE endpoint, WARN on stale missing-env registrations), platform/transport supportability (SSE on non-macOS ⇒ WARN), binary coherence. Emitted last, also on the db-unavailable degraded path. |
| `cairn metrics` / `report` | tool-metrics and health reports |
| `cairn validate` / `validate-paths` / `verify` | store integrity checks |
| `cairn bench` | performance suites |
| `cairn eval` | retrieval evaluation |
| `cairn viz` | render graph diagrams |
| `cairn import-scip` | import a SCIP index |
| `cairn hooks install|uninstall` | git hooks |
| `cairn version` / `upgrade` | version and self-upgrade |
| `cairn sync` | sync pending watcher edits |

Global: `-v/--verbose` for debug logging.
