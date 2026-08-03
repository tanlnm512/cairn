# `cg` CLI Reference

The `cg` command is the human-facing interface to codegraph (package
`cg-intel`). It builds the local code graph, manages knowledge/memory,
generates module guides, and runs the MCP server that AI agents consume.

> Run `cg --help` for the live, authoritative command list. This page
> documents every command as it is registered in `src/codegraph/cli/`.

---

## Global options

| Option | Description |
|--------|-------------|
| `--version` | Print the installed version (`cg-intel <version>`). |
| `--help` | Show help for the group or any subcommand. |

The version is reported from the installed package metadata. `cg version`
and `cg upgrade --check` are the dedicated version commands.

### Common per-command conventions

- `--db` overrides the SQLite graph DB path (default: the central store for
  the current workspace under `~/.codegraph/<key>/.kg`).
- `--knowledge` overrides the `.knowledge/` bundle path (default:
  `<store>/.knowledge`).
- `--json` (`as_json`) emits machine-readable JSON instead of the themed
  console output — useful for piping and CI.
- Resolution-aware graph commands (`callers`, `callees`, `impact`) default
  to **precise** (resolved edges only); `--fuzzy` adds unresolved name-only
  edges. See [MCP tools](./mcp-tools.md) for the precise-vs-fuzzy rules,
  which apply identically to the CLI.

---

## Command groups

### `cg serve` — MCP server and SSE daemon

`cg serve` is a group with `invoke_without_command=True`: running `cg serve`
with no subcommand runs the MCP server in the foreground over **stdio**
(the mode MCP clients spawn). Pass `--port` (or use a subcommand) for SSE.

The group owns the persistent **SSE daemon** (macOS launchd), the shared,
read-only, contention-safe model that replaces one-stdio-server-per-client.

| Subcommand | Description |
|------------|-------------|
| `cg serve` | Foreground stdio server (the MCP-client spawn mode). |
| `cg serve run` | Foreground server: stdio by default, SSE with `--port`. |
| `cg serve start` | Install + start the persistent SSE daemon (macOS launchd). Idempotent, auto-restarts, starts at login. |
| `cg serve stop` | Unload the LaunchAgent and kill stray `cg serve` processes. |
| `cg serve status` | Health check: launchd state, pid, SSE response, strays, DB lock holders. |
| `cg serve restart` | Stop then start. |

**`cg serve run`** options:
- `--db PATH` — SQLite DB path (default: central store).
- `--port N` — run over SSE on this port.
- `--read-only / --read-write` — open the graph DB read-only (default for the
  shared SSE daemon) so it never contends with `cg build`/`cg embed`. The
  serving-time write paths silently no-op; write tools still open a writable
  connection as needed.

**`cg serve start` / `restart`** options: `--port 9876` (default), `--host 127.0.0.1`.

> SSE `start`/`stop`/`status`/`restart` are macOS-only (launchd). On other
> platforms run `cg serve --port 9876` under a process supervisor.

### `cg memory` — agent memory (13 subcommands)

`cg memory` records and curates agent learnings (decisions, patterns,
mistakes, workarounds) across the tiers raw → drafts → tribal → canonical.

| Subcommand | Description |
|------------|-------------|
| `cg memory record TYPE TITLE` | Record a learning. `TYPE`: `decision\|pattern\|mistake\|workaround`. |
| `cg memory search QUERY` | Search past memories (`--tier` filter). Shows a live `refs-verified` fraction per result. |
| `cg memory capture` | Extract learnings from a session transcript (session-end hook). Routes via the memory-extract LLM task; queues if no agent is available. |
| `cg memory list` | List memories (`--tier`, `--tag` filters). Shows `refs-verified` when `--db` resolves. |
| `cg memory stats` | Memory statistics by tier. |
| `cg memory digest` | Top tribal memories by score — session-orientation digest (`--limit`, `--db` for `refs-verified`). |
| `cg memory promote PATH` | Force-promote a memory to canonical (compass/wiki). |
| `cg memory decay` | Expire raw memories >7d, archive tribal >90d stale. |
| `cg memory batch-critic` | Run critic pass on queued draft memories. |
| `cg memory forget PATH` | Permanently delete a memory and its cross-session refs. |
| `cg memory demote PATH` | Demote a memory to a lower tier (`--tier raw\|archived`); rejects promotions. |
| `cg memory purge` | Delete old archived memories (`--max-days 90`, `--dry-run`). CLI-only — not exposed as MCP. |
| `cg memory consolidate` | Consolidate redundant raw memories into unified tribal knowledge. |

`record` options: `--body`, `--resource`, `--confidence 0.7`, `--db`, `--knowledge`.

### `cg knowledge` — business knowledge ingestion and search

`cg knowledge` ingests and searches business documents (specs, business-rules,
decisions) and is the parent of the `workflow` subgroup.

| Subcommand | Description |
|------------|-------------|
| `cg knowledge add` | Ingest a business knowledge document (`--title` required). |
| `cg knowledge import DIR` | Batch-ingest all `.md` files from a directory. |
| `cg knowledge search QUERY` | Search knowledge docs (lexical + semantic + graph bridge). |
| `cg knowledge list` | List documents (`--type`, `--status`, `--tag` filters). |
| `cg knowledge embed` | Build the knowledge embedding index. |
| `cg knowledge impact QUERY` | Search knowledge + full graph impact bridge. |
| `cg knowledge remove DOC_ID` | Delete a document and its embedding rows. |
| `cg knowledge status DOC_ID NEW_STATUS` | Update `doc_status` (`active/superseded/archived`). |
| `cg knowledge export` | Export the `.knowledge` bundle to a directory or `.tar.gz` (`--out`). |

`add` options: `--file` or `--body` (one required), `--title` (required),
`--type spec` (`business-rule|spec|decision`), `--tags`, `--affects`,
`--affects-modules`, `--epic`, `--resource`.

#### `cg knowledge workflow` — ordered procedural workflows

A workflow is a knowledge doc with `doc_type="workflow"`; the subgroup adds
step-aware operations on top. `list`, `status`, `remove`, and `search` work
on workflows unchanged via the parent commands.

| Subcommand | Description |
|------------|-------------|
| `cg knowledge workflow add` | Add a workflow with an ordered list of steps. |
| `cg knowledge workflow trace REF` | Trace a workflow's ordered steps by title, slug, or concept_id. |
| `cg knowledge workflow sync [REF]` | Detect and refresh stale workflows after code changes (`--all`, `--dry-run`). |

`add` options: `--title` (required), `--step` (repeatable,
`name::description[::symbol[::file]]`) or `--steps-file PATH` (YAML/JSON list),
`--tags`, `--affects`, `--affects-modules`, `--resource`.

`sync` options: `--all`, `--dry-run`, `--max-steps 20`, `--db`, `--knowledge`.

### `cg compass` — module navigation guides

`cg compass` generates, lists, validates, and gaps-checks OKF compass files
(25-35 line module navigation guides). The deterministic generator is
graph-sourced; `--use-llm` routes to agent-decoupled synthesis.

| Subcommand | Description |
|------------|-------------|
| `cg compass generate MODULE` | Generate a compass for a module. |
| `cg compass list` | List compass files. |
| `cg compass validate` | Critic-check all compass files against the graph. |
| `cg compass gaps` | List modules without compass coverage. |
| `cg compass flow ENTRY` | Generate a compass for a business **flow** traced from an entry-point symbol. |
| `cg compass flow-gaps` | Find rich call chains that lack a flow compass (`--generate` for batch mode). |

`generate` options: `MODULE` (argument), `--repo`, `--use-llm`, `--db`, `--knowledge`,
`--dry-run` (run the critic, print the verdict, write nothing), `--show-rejections`
(with `--use-llm`, print every revise cycle's critic trace).

`flow` options: `ENTRY` (argument), `--dry-run` (run the critic, print the verdict,
write nothing), `--as-workflow`, `--max-steps 20`, `--use-llm`, `--db`, `--knowledge`.

`flow-gaps` options: `--min-edges 5`, `--generate`, `--limit 0` (0 = all),
`--dry-run`, `--db`, `--knowledge`.

> **Critic-gated writes.** `compass generate`, `compass flow`, and `wiki generate`
> all run the deterministic critic (backtick file/symbol references verified
> against the graph). `--dry-run` shows the verdict without writing; the critic's
> errors/warnings are surfaced so you can see *why* a body was rejected. An agent
> may hallucinate, but a hallucinated symbol can never land in a compass/wiki doc.

### `cg wiki` — architectural wiki

| Subcommand | Description |
|------------|-------------|
| `cg wiki generate` | Generate architectural wiki concepts (`--repo`, or all repos if omitted). |
| `cg wiki search QUERY` | Search the wiki. |

`generate` options: `--repo`, `--db`, `--knowledge`, `--dry-run` (run the critic,
print each verdict, write nothing), `--show-rejections` (print critic
errors/warnings for each concept).

### `cg dataflow` — precomputed dataflow index

`cg dataflow` manages the precomputed dataflow index over public symbols
(populated during `cg build`/`cg sync`).

| Subcommand | Description |
|------------|-------------|
| `cg dataflow build` | Build the dataflow index from scratch. |
| `cg dataflow dataflow-lookup SYMBOL` | Look up precomputed dataflow for a symbol. |

`dataflow-lookup` options: `SYMBOL` (argument), `--db`, `--json`.

### `cg hooks` — git hook management

| Subcommand | Description |
|------------|-------------|
| `cg hooks install` | Install post-commit hooks across discovered repos. |
| `cg hooks uninstall` | Remove post-commit hooks. |

Both take `--workspace`; `install` also takes `--codegraph-dir`.

### `cg task` — LLM task queue

`cg task` is the agent-decoupled task queue. Codegraph never calls an LLM
directly; any agent with the codegraph skill processes pending tasks.

| Subcommand | Description |
|------------|-------------|
| `cg task list` | List tasks (`--status`, `--kind` filters). |
| `cg task show TASK_ID` | Show a task's full body (facts + output spec). |
| `cg task claim TASK_ID` | Claim a pending task (sets status in-progress). |
| `cg task complete TASK_ID` | Mark a task done; runs the deterministic critic automatically. |

`complete` options: `--result` or `--result-file PATH` (one required).

---

## Top-level commands

These are registered directly on `cg` (bare `@main.command()`).

### Setup, build, and lifecycle

| Command | Description |
|---------|-------------|
| `cg init` | Register this workspace with codegraph's central store and build the graph. |
| `cg config` | Show resolved store paths (`--list` all workspaces, `--mcp-config` prints a path-free `.mcp.json` snippet). |
| `cg build` | Build (or rebuild) the code graph; also builds dataflow + transitive closure. |
| `cg stats` | Show graph statistics (repos, symbols, edges, by-repo/by-kind/skipped tables). |
| `cg checkpoint` | Checkpoint the graph DB's WAL back into the main file (TRUNCATE). |
| `cg update` | Incremental graph update from git diff (or `--file` for a single changed file). |
| `cg sync` | Manually re-index changed files (escape hatch when the watcher is disabled). |

`init` options: `--workspace`, `--from-legacy DIR` (migrate a legacy
`codegraph/.kg`), `--no-build`, `--import-docs` (ingest `docs/**/*.md`).

`build` options: `--repo`, `--workspace`, `--db`, `-v/--verbose`, `--staging`
(build to temp DB and atomic-swap for zero downtime).

`update` options: `--repo`, `--file PATH` (single-file, for PostToolUse hooks),
`--workspace`, `--db`. Runs memory decay after reindex.

### Graph queries (L1)

The navigation tools; the recommended first move from code is `cg def` or
`cg impact`. For agent-facing aggregation see the MCP `explore` tool in
[mcp-tools.md](./mcp-tools.md).

| Command | Description |
|---------|-------------|
| `cg def SYMBOL` | Find where a SYMBOL is defined. |
| `cg callers SYMBOL` | Find all callers of SYMBOL (precise; `--fuzzy` for name-only). |
| `cg callees SYMBOL` | Find what a SYMBOL calls (precise; `--fuzzy` includes unresolved). |
| `cg impact SYMBOL` | Recursive impact analysis (precise; `--fuzzy`, `--depth`). |
| `cg search PATTERN` | Search symbols by PATTERN (`*` wildcards, `--kind` filter). |
| `cg deps REPO` | Cross-repo dependencies for REPO. |
| `cg tree REPO` | Directory/package structure of REPO with symbol counts. |
| `cg viz` | Generate visual diagrams from the graph (Mermaid/DOT/JSON). |

`def`/`callers`/`callees`/`impact`/`deps`/`tree`/`viz` share `--db` and `--json`.
`impact` adds `--depth 10`; `callers`/`callees`/`impact` add `--fuzzy`.

`viz` options: `--format mermaid|dot|json`, `--scope symbol|module|impact|repo|deps`,
`--symbol`, `--module`, `--repo`, `--depth 3`, `--output FILE`, `--embed`, `--db`.

### Semantic (embeddings)

| Command | Description |
|---------|-------------|
| `cg embed` | Build the semantic embedding index over the symbol corpus. |
| `cg semantic QUERY` | Semantic (concept) search: find code by meaning. |

`embed` options: `--db`, `--batch-size 64`, `--limit`, `--no-reap`,
`--build-index`, `--install-deps`, `--download-model`.

`--install-deps` installs the semantic dependencies (torch +
sentence-transformers) into the shared `~/.codegraph/lib/` directory, which
survives reinstalls, then exits without building the index. This is the
recommended one-time way to get the default `BAAI/bge-m3` model — run
`cg embed --install-deps`, then `cg embed` to build the index.

`semantic` options: `QUERY` (argument), `--db`, `--limit 20`, `--threshold 0.3`,
`--json`, `--include-callers`. Set `CODEGRAPH_RERANK=1` for a cross-encoder
rerank stage.

### Natural-language and context

| Command | Description |
|---------|-------------|
| `cg ask QUESTION` | Natural-language question across all layers (compass router). |
| `cg context FILE_PATH` | Load relevant context (compass + memory + wiki) for a file. |

`ask` options: `--db`, `--knowledge`, `--json`. `context` options: `--knowledge`.

### Knowledge base management

| Command | Description |
|---------|-------------|
| `cg validate` | Check OKF conformance of the `.knowledge/` bundle. |
| `cg validate-paths` | Check all concepts for stale file/symbol references against the graph (`--mark`). |
| `cg import-scip SCIP_FILE` | Import compiler-grade symbol bindings from a SCIP index file. |

`import-scip` options: `SCIP_FILE` (argument), `--db`, `--repo default`.

### System, metrics, and eval

| Command | Description |
|---------|-------------|
| `cg metrics` | Report MCP tool invocation metrics (calls, avg latency, error rate). |
| `cg status` | System status and health across all layers. |
| `cg eval` | Run retrieval evaluation harness across L1/L5 corpora (`--corpus`, `--json`). |
| `cg bench` | Run performance or scalability benchmarks. |

`metrics` options: `--db`, `--tool NAME`, `--json`.
`status` options: `--db`, `--knowledge`.
`eval` options: `--db`, `--knowledge`, `--corpus L1|L5|all`, `--queries PATH`, `--json`.
`bench` options: `--suite perf|scaling`, `--workspace`, `--sizes`, `--n-files`,
`--complexity`, `--embed-backend`, `--json`, `--save`, `--compare`, `--threshold`,
`--repeats`.

### Agent integration and lifecycle

| Command | Description |
|---------|-------------|
| `cg install-agents` | Wire codegraph into detected AI coding clients (Claude Code/Claude Desktop/Cursor/Droid/ZCode). |
| `cg uninstall-agents` | Remove codegraph entries from AI client configs (idempotent). |
| `cg uninstall` | Full teardown: agent wiring, hooks, graph store, and the `cg` binary. |
| `cg version` | Print the installed codegraph version. |
| `cg upgrade` | Upgrade codegraph in place (detects install method). |

`install-agents` options: `--client` (repeatable:
`claude|claude-desktop|cursor|droid|zcode|agy|opencode|all`), `--workspace`,
`--scope workspace|global` (where to write configs; if omitted, prompts),
`--yes`/`-y` (skip the prompt; install for detected-not-installed clients),
`--force`, `--dry-run`, `--git-hooks`, `--sse`, `--stdio`, `--sse-url`.
With no flags it runs interactively: lists detected clients, their install
state, and prompts for which to install plus the config scope.

`uninstall` options: `--full` (entire `~/.codegraph`), `--agents-only`,
`--hooks-only`, `--graph-only`, `--package-only`, `--client`, `--workspace`,
`--dry-run`, `-y/--yes`.

`upgrade` options: `--check` (only check, don't upgrade).

---

## Where to look next

- For the **MCP server** tool surface that AI agents consume, see
  [mcp-tools.md](./mcp-tools.md) (26 tools across 5 layers; `explore` is the
  recommended first call).
- For the explore-first workflow, precise-vs-fuzzy rules, and per-tool quirks,
  see `AGENTS.md` in the workspace root.
