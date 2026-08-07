# `cairn` CLI Reference

The `cairn` command is the human-facing interface to cairn (package
`cairn-intel`). It builds the local code graph, manages knowledge/memory,
generates module guides, and runs the MCP server that AI agents consume.

> Run `cairn --help` for the live, authoritative command list. This page
> documents every command as it is registered in `src/cairn/cli/`.

---

## Global options

| Option | Description |
|--------|-------------|
| `--version` | Print the installed version (`cairn-intel <version>`). |
| `--help` | Show help for the group or any subcommand. |

The version is reported from the installed package metadata. `cairn version`
and `cairn upgrade --check` are the dedicated version commands.

### Common per-command conventions

- `--db` overrides the SQLite graph DB path (default: the central store for
  the current workspace under `~/.cairn/<key>/.kg`).
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

### `cairn serve` — MCP server and SSE daemon

`cairn serve` is a group with `invoke_without_command=True`: running `cairn serve`
with no subcommand runs the MCP server in the foreground over **stdio**
(the mode MCP clients spawn). Pass `--port` (or use a subcommand) for SSE.

The group owns the persistent **SSE daemon** (macOS launchd), the shared,
read-only, contention-safe model that replaces one-stdio-server-per-client.

| Subcommand | Description |
|------------|-------------|
| `cairn serve` | Foreground stdio server (the MCP-client spawn mode). |
| `cairn serve run` | Foreground server: stdio by default, SSE with `--port`. |
| `cairn serve start` | Install + start the persistent SSE daemon (macOS launchd). Idempotent, auto-restarts, starts at login. |
| `cairn serve stop` | Unload the LaunchAgent and kill stray `cairn serve` processes. |
| `cairn serve status` | Health check: launchd state, pid, SSE response, strays, DB lock holders. |
| `cairn serve restart` | Stop then start. |

**`cairn serve run`** options:
- `--db PATH` — SQLite DB path (default: central store).
- `--port N` — run over SSE on this port.
- `--read-only / --read-write` — open the graph DB read-only (default for the
  shared SSE daemon) so it never contends with `cairn build`/`cairn embed`. The
  serving-time write paths silently no-op; write tools still open a writable
  connection as needed.

**`cairn serve start` / `restart`** options: `--port 9876` (default), `--host 127.0.0.1`.

> SSE `start`/`stop`/`status`/`restart` are macOS-only (launchd). On other
> platforms run `cairn serve --port 9876` under a process supervisor.

### `cairn memory` — agent memory (14 subcommands)

`cairn memory` records and curates agent learnings (decisions, patterns,
mistakes, workarounds) across the tiers raw → drafts → tribal → canonical.

| Subcommand | Description |
|------------|-------------|
| `cairn memory record TYPE TITLE` | Record a learning. `TYPE`: `decision\|pattern\|mistake\|workaround`. |
| `cairn memory evolve PATH` | Revise a memory; creates a new version that supersedes the old (`--title`, `--body`). |
| `cairn memory search QUERY` | Search past memories (`--tier` filter). Shows a live `refs-verified` fraction per result. |
| `cairn memory capture` | Extract learnings from a session transcript (session-end hook). Routes via the memory-extract LLM task; queues if no agent is available. |
| `cairn memory list` | List memories (`--tier`, `--tag` filters). Shows `refs-verified` when `--db` resolves. |
| `cairn memory stats` | Memory statistics by tier. |
| `cairn memory digest` | Top tribal memories by score — session-orientation digest (`--limit`, `--db` for `refs-verified`). |
| `cairn memory promote PATH` | Force-promote a memory to canonical (compass/wiki). |
| `cairn memory decay` | Expire raw memories >7d, archive tribal >90d stale. |
| `cairn memory batch-critic` | Run critic pass on queued draft memories. |
| `cairn memory forget PATH` | Permanently delete a memory and its cross-session refs. |
| `cairn memory demote PATH` | Demote a memory to a lower tier (`--tier raw\|archived`); rejects promotions. |
| `cairn memory purge` | Delete old archived memories (`--max-days 90`, `--dry-run`). CLI-only — not exposed as MCP. |
| `cairn memory consolidate` | Consolidate redundant raw memories into unified tribal knowledge. |

`record` options: `--body`, `--resource`, `--confidence 0.7`, `--db`, `--knowledge`.

### `cairn knowledge` — business knowledge ingestion and search

`cairn knowledge` ingests and searches business documents (specs, business-rules,
decisions) and is the parent of the `workflow` subgroup.

| Subcommand | Description |
|------------|-------------|
| `cairn knowledge add` | Ingest a business knowledge document (`--title` required). |
| `cairn knowledge import DIR` | Batch-ingest all `.md` files from a directory. |
| `cairn knowledge search QUERY` | Search knowledge docs (lexical + semantic + graph bridge). |
| `cairn knowledge list` | List documents (`--type`, `--status`, `--tag` filters). |
| `cairn knowledge embed` | Build the knowledge embedding index. |
| `cairn knowledge impact QUERY` | Search knowledge + full graph impact bridge. |
| `cairn knowledge remove DOC_ID` | Delete a document and its embedding rows. |
| `cairn knowledge status DOC_ID NEW_STATUS` | Update `doc_status` (`active/superseded/archived`). |
| `cairn knowledge export` | Export the `.knowledge` bundle to a directory or `.tar.gz` (`--out`). |

`add` options: `--file` or `--body` (one required), `--title` (required),
`--type spec` (`business-rule|spec|decision`), `--tags`, `--affects`,
`--affects-modules`, `--epic`, `--resource`.

#### `cairn knowledge workflow` — ordered procedural workflows

A workflow is a knowledge doc with `doc_type="workflow"`; the subgroup adds
step-aware operations on top. `list`, `status`, `remove`, and `search` work
on workflows unchanged via the parent commands.

| Subcommand | Description |
|------------|-------------|
| `cairn knowledge workflow add` | Add a workflow with an ordered list of steps. |
| `cairn knowledge workflow trace REF` | Trace a workflow's ordered steps by title, slug, or concept_id. |
| `cairn knowledge workflow sync [REF]` | Detect and refresh stale workflows after code changes (`--all`, `--dry-run`). |

`add` options: `--title` (required), `--step` (repeatable,
`name::description[::symbol[::file]]`) or `--steps-file PATH` (YAML/JSON list),
`--tags`, `--affects`, `--affects-modules`, `--resource`.

`sync` options: `--all`, `--dry-run`, `--max-steps 20`, `--db`, `--knowledge`.

### `cairn compass` — module navigation guides

`cairn compass` generates, lists, validates, and gaps-checks OKF compass files
(25-35 line module navigation guides). The deterministic generator is
graph-sourced; `--use-llm` routes to agent-decoupled synthesis.

| Subcommand | Description |
|------------|-------------|
| `cairn compass generate MODULE` | Generate a compass for a module. |
| `cairn compass list` | List compass files. |
| `cairn compass validate` | Critic-check all compass files against the graph. |
| `cairn compass gaps` | List modules without compass coverage. |
| `cairn compass flow ENTRY` | Generate a compass for a business **flow** traced from an entry-point symbol. |
| `cairn compass flow-gaps` | Find rich call chains that lack a flow compass (`--generate` for batch mode). |

`generate` options: `MODULE` (argument), `--repo`, `--use-llm`, `--db`, `--knowledge`,
`--dry-run` (run the critic, print the verdict, write nothing), `--show-rejections`
(with `--use-llm`, print every revise cycle's critic trace).

`flow` options: `ENTRY` (argument), `--dry-run` (run the critic, print the verdict,
write nothing), `--as-workflow`, `--max-steps 20`, `--use-llm`, `--db`, `--knowledge`.

`flow-gaps` options: `--min-edges 5`, `--generate`, `--limit 0` (0 = all),
`--dry-run`, `--db`, `--knowledge`.

> **Critic-gated writes.** `compass generate` and `compass flow` both run the
> deterministic critic (backtick file/symbol references verified against the
> graph) and **refuse to write on critic failure** — a hallucinated symbol can
> never land in a compass doc. `wiki generate` runs the same critic and surfaces
> the verdict/errors, but **does still write** the concept on failure (see the
> generator docstring) so the body is never silently lost; treat the printed
> errors as must-fix. `--dry-run` shows the verdict without writing for either.

### `cairn wiki` — architectural wiki

| Subcommand | Description |
|------------|-------------|
| `cairn wiki generate` | Generate architectural wiki concepts (`--repo`, or all repos if omitted). |
| `cairn wiki search QUERY` | Search the wiki. |

`generate` options: `--repo`, `--db`, `--knowledge`, `--dry-run` (run the critic,
print each verdict, write nothing), `--show-rejections` (print critic
errors/warnings for each concept).

### `cairn dataflow` — precomputed dataflow index

`cairn dataflow` manages the precomputed dataflow index over public symbols
(populated during `cairn build`/`cairn sync`).

| Subcommand | Description |
|------------|-------------|
| `cairn dataflow build` | Build the dataflow index from scratch. |
| `cairn dataflow dataflow-lookup SYMBOL` | Look up precomputed dataflow for a symbol. |

`dataflow-lookup` options: `SYMBOL` (argument), `--db`, `--json`.

### `cairn hooks` — git hook management

| Subcommand | Description |
|------------|-------------|
| `cairn hooks install` | Install post-commit hooks across discovered repos. |
| `cairn hooks uninstall` | Remove post-commit hooks. |

Both take `--workspace`; `install` also takes `--cairn-dir`.

### `cairn task` — LLM task queue

`cairn task` is the agent-decoupled task queue. Cairn never calls an LLM
directly; any agent with the cairn skill processes pending tasks.

| Subcommand | Description |
|------------|-------------|
| `cairn task list` | List tasks (`--status`, `--kind` filters). |
| `cairn task show TASK_ID` | Show a task's full body (facts + output spec). |
| `cairn task claim TASK_ID` | Claim a pending task (sets status in-progress). |
| `cairn task complete TASK_ID` | Mark a task done; runs the deterministic critic automatically. |

`complete` options: `--result` or `--result-file PATH` (one required).

---

## Top-level commands

These are registered directly on `cairn` (bare `@main.command()`).

### Setup, build, and lifecycle

| Command | Description |
|---------|-------------|
| `cairn init` | Register this workspace with cairn's central store and build the graph. |
| `cairn config` | Show resolved store paths (`--list` all workspaces, `--mcp-config` prints a path-free `.mcp.json` snippet). Also echoes the resolved [SCIP](./scip.md) config and whether each index file exists. |
| `cairn build` | Build (or rebuild) the code graph; also builds dataflow + transitive closure. |
| `cairn stats` | Show graph statistics (repos, symbols, edges, by-repo/by-kind/skipped tables). |
| `cairn checkpoint` | Checkpoint the graph DB's WAL back into the main file (TRUNCATE). |
| `cairn update` | Incremental graph update from git diff (or `--file` for a single changed file). |
| `cairn sync` | Manually re-index changed files (escape hatch when the watcher is disabled). |

`init` options: `--workspace`, `--from-legacy DIR` (migrate a legacy
`cairn/.kg`), `--no-build`, `--import-docs` (ingest `docs/**/*.md`).

`build` options: `--repo`, `--workspace`, `--db`, `-v/--verbose`, `--staging`
(build to temp DB and atomic-swap for zero downtime). When `cairn.json`
declares [SCIP](./scip.md) indexes, languages whose index file exists are
imported from SCIP (exact resolution) and skipped by tree-sitter; the summary
panel reports per-language SCIP symbol counts.

`update` options: `--repo`, `--file PATH` (single-file, for PostToolUse hooks),
`--workspace`, `--db`. Runs memory decay after reindex.

### Graph queries (L1)

The navigation tools; the recommended first move from code is `cairn def` or
`cairn impact`. For agent-facing aggregation see the MCP `explore` tool in
[mcp-tools.md](./mcp-tools.md).

| Command | Description |
|---------|-------------|
| `cairn def SYMBOL` | Find where a SYMBOL is defined. |
| `cairn callers SYMBOL` | Find all callers of SYMBOL (precise; `--fuzzy` for name-only). |
| `cairn callees SYMBOL` | Find what a SYMBOL calls (precise; `--fuzzy` includes unresolved). |
| `cairn impact SYMBOL` | Recursive impact analysis (precise; `--fuzzy`, `--depth`). |
| `cairn search PATTERN` | Search symbols by PATTERN (`*` wildcards, `--kind` filter). |
| `cairn deps REPO` | Cross-repo dependencies for REPO. |
| `cairn tree REPO` | Directory/package structure of REPO with symbol counts. |
| `cairn viz` | Generate visual diagrams from the graph (Mermaid/DOT/JSON). |

`def`/`callers`/`callees`/`impact`/`deps`/`tree`/`viz` share `--db` and `--json`.
`impact` adds `--depth 10`; `callers`/`callees`/`impact` add `--fuzzy`.

`viz` options: `--format mermaid|dot|json`, `--scope symbol|module|impact|repo|deps`,
`--symbol`, `--module`, `--repo`, `--depth 3`, `--output FILE`, `--embed`, `--db`.

### Semantic (embeddings)

| Command | Description |
|---------|-------------|
| `cairn embed` | Build the semantic embedding index over the symbol corpus. |
| `cairn semantic QUERY` | Semantic (concept) search: find code by meaning. |

`embed` options: `--db`, `--batch-size 64`, `--limit`, `--no-reap`,
`--build-index`, `--install-deps`, `--download-model`.

`--install-deps` installs the semantic dependencies (torch +
sentence-transformers) into the shared `~/.cairn/lib/` directory, which
survives reinstalls, then exits without building the index. This is the
recommended one-time way to get the default `BAAI/bge-m3` model — run
`cairn embed --install-deps`, then `cairn embed` to build the index.

`semantic` options: `QUERY` (argument), `--db`, `--limit 20`, `--threshold 0.3`,
`--json`, `--include-callers`. Set `CAIRN_RERANK=1` for a cross-encoder
rerank stage.

### Natural-language and context

| Command | Description |
|---------|-------------|
| `cairn ask QUESTION` | Natural-language question across all layers (compass router). |
| `cairn context FILE_PATH` | Load relevant context (compass + memory + wiki) for a file. |

`ask` options: `--db`, `--knowledge`, `--json`. `context` options: `--knowledge`.

### Knowledge base management

| Command | Description |
|---------|-------------|
| `cairn validate` | Check OKF conformance of the `.knowledge/` bundle. |
| `cairn validate-paths` | Check all concepts for stale file/symbol references against the graph (`--mark`). |
| `cairn import-scip SCIP_FILE` | Import compiler-grade symbol bindings from a SCIP index file. |

`import-scip` options: `SCIP_FILE` (argument), `--db`, `--repo default`,
`--format proto|json` (`proto` is the default and what real indexers emit;
`json` is the legacy shape). See [scip.md](./scip.md) for generating indexes
and wiring them into `cairn.json` for build-time hybrid indexing.

### System, metrics, and eval

| Command | Description |
|---------|-------------|
| `cairn metrics` | Report MCP tool invocation metrics (calls, avg latency, error rate). |
| `cairn status` | System status and health across all layers. |
| `cairn eval` | Run retrieval evaluation harness across L1/L5 corpora (`--corpus`, `--json`). |
| `cairn bench` | Run performance or scalability benchmarks. |

`metrics` options: `--db`, `--tool NAME`, `--json`.
`status` options: `--db`, `--knowledge`.
`eval` options: `--db`, `--knowledge`, `--corpus L1|L5|all`, `--queries PATH`, `--json`.
`bench` options: `--suite perf|scaling`, `--workspace`, `--sizes`, `--n-files`,
`--complexity`, `--embed-backend`, `--json`, `--save`, `--compare`, `--threshold`,
`--repeats`.

### Agent integration and lifecycle

| Command | Description |
|---------|-------------|
| `cairn install-agents` | Wire cairn into detected AI coding clients (Claude Code/Claude Desktop/Cursor/Droid/ZCode). |
| `cairn uninstall-agents` | Remove cairn entries from AI client configs (idempotent). |
| `cairn uninstall` | Full teardown: agent wiring, hooks, graph store, and the `cairn` binary. |
| `cairn version` | Print the installed cairn version. |
| `cairn upgrade` | Upgrade cairn in place (detects install method). |

`install-agents` options: `--client` (repeatable:
`claude|claude-desktop|cursor|droid|zcode|agy|opencode|all`), `--workspace`,
`--scope workspace|global` (where to write configs; if omitted, prompts),
`--yes`/`-y` (skip the prompt; install for detected-not-installed clients),
`--force`, `--dry-run`, `--git-hooks`, `--sse`, `--stdio`, `--sse-url`.
With no flags it runs interactively: lists detected clients, their install
state, and prompts for which to install plus the config scope.

`uninstall` options: `--full` (entire `~/.cairn`), `--agents-only`,
`--hooks-only`, `--graph-only`, `--package-only`, `--client`, `--workspace`,
`--dry-run`, `-y/--yes`.

`upgrade` options: `--check` (only check, don't upgrade).

`cairn upgrade` updates cairn in place from PyPI. It queries
`pypi.org/pypi/cairn-intel/json` for the latest version, compares it to the
installed version under PEP 440, and re-installs via whichever package
manager cairn was installed with — `uv tool`, `pipx`, or `pip` (the install
method is auto-detected by inspecting `uv tool list` / `pipx list` / the
current interpreter path). `--check` prints both versions without changing
anything. If PyPI is unreachable, it prints the manual command instead
(`pip install --upgrade cairn-intel`).

`cairn version` prints the installed version from package metadata, falling
back to the source-tree version (with a `(source checkout)` marker) for
editable installs.

---

## Where to look next

- For the **MCP server** tool surface that AI agents consume, see
  [mcp-tools.md](./mcp-tools.md) (27 tools across 5 layers; `explore` is the
  recommended first call).
- For the explore-first workflow, precise-vs-fuzzy rules, and per-tool quirks,
  see `AGENTS.md` in the workspace root.
