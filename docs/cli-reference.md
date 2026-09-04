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
| `cairn install-agents` / `uninstall-agents` | wire cairn into AI clients (claude, droid, zcode, cursor, opencode, kilo, omp, agy, claude-desktop). With a custom `CAIRN_HOME`, generated stdio registrations embed `env.CAIRN_HOME` (CLI-registered clients carry it too: claude global scope via `claude mcp add -e`, droid via `droid mcp add --env`), hook commands embed the assignment, and each written stdio registration is spawn-verified — the report shows per-client `verify: PASS/FAIL` naming both stores on mismatch. SSE and CLI-registered clients are skipped with a note; `--dry-run` never spawns. |

## Compass / wiki / tasks / dataflow

| Command | Purpose |
|---|---|
| `cairn compass generate|list|validate|gaps|flow|flow-gaps` | module navigation guides |
| `cairn wiki generate --llm [--pages N] [--refine-catalog] [--diagrams] [--force] [--repo R]` | agent-decoupled wiki generation: plans a deterministic page outline (overview + top modules by incoming reference degree, capped by `--pages`, default 10) and queues one pending `wiki-page` task per page (keyed by the qualified `{repo}/{page_id}` resource) for any agent to claim and complete — a passing completion is critic-gated and promoted as a wiki article with a verified `## Sources` footer; a failing one runs the bounded revise cycle. Incremental via the `_wiki/manifest.json` manifest: unchanged, already-promoted pages are skipped unless `--force` re-queues every page; an empty/unindexed graph exits 1. `--refine-catalog` queues one `wiki-catalog` refinement task first — re-run the command after it completes to queue the page tasks from the validated refined outline. `--diagrams` instructs writers to include Mermaid fences. Without `--llm`, the deterministic single-summary generation is unchanged (its output is an `Architecture-Report` diagnostic at `reports/architecture/{repo}` — outside the critic-gated wiki page surface) |
| `cairn wiki status` | per-page generation state, derived at read time (planned / queued / in-progress / promoted / failed / dropped), with aggregate counts, joined from the manifest and live task state; each page also carries a staleness verdict — `fresh` when its recorded commit sha equals the repo's current HEAD, `stale` when both are present and differ, `unknown` when either is unavailable — with `fresh=… stale=… unknown=…` in the totals line |
| `cairn wiki retry` | re-queue exactly the failed pages — derived from the live task chain and promoted content, never a stored verdict (a done task with no passing critic verdict counts, so stuck chains are reachable) — as fresh task chains (cumulative queue-attempt count preserved); promoted pages untouched, and dropped tasks stay dropped — drop is terminal |
| `cairn wiki search <query>` | search the wiki (promoted articles + deterministic summaries) |
| `cairn wiki export --dir DIR [--force]` | write every promoted page as `DIR/{repo}/{page_id}.md` (OKF frontmatter preserved) and report the exported count; a non-empty target directory is refused unless `--force` is passed |
| `cairn wiki enrich [<page-id>] [--repo R] [--all]` | queue one `wiki-page-enrich` task per promoted page — either a single `page-id` or `--all` (never both), optionally scoped with `--repo`; the task's facts carry page identity and fresh seeds (never the body); the critic-passing completion reads the promoted body at completion time, appends the new sections, and merges the new `## Sources` entries into the frontmatter. Requires an already-promoted page |
| `cairn task list|show|claim|complete|drop` | LLM synthesis task queue; `list` filters by `--status`, `--kind`, or `--kind-prefix PREFIX` (e.g. `--kind-prefix wiki-page` lists every chain hop), `drop` abandons a pending or in-progress task — terminal: done tasks are refused and a dropped task is never claimable again (dropping an in-progress task releases its claim marker so the resource can be re-queued) |
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
