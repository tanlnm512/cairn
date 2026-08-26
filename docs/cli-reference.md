# `cairn` CLI Reference

← [Docs index](README.md)

The `cairn` command is the human-facing interface to cairn (package
`cairn-intel`). It builds the local code graph, manages knowledge/memory,
generates module guides, and runs the MCP server that AI agents consume.
Consult it when you need any command's exact subcommands, options, or
defaults — or to find which group a command lives under.

> Run `cairn --help` for the live, authoritative command list. This page
> documents every command as it is registered in `src/cairn/cli/`.

## Contents

| Section | What it covers |
|---------|----------------|
| [`## Global options`](#global-options) | The `--version`/`--help` flags and the conventions every subcommand shares (`--db`, `--json`, precise-vs-fuzzy defaults). |
| [`## Command groups`](#command-groups) | The grouped subcommands — `serve`, `memory`, `knowledge` (+ `workflow`), `compass`, `wiki`, `dataflow`, `hooks`, `task`. |
| [`## Top-level commands`](#top-level-commands) | Every bare command, grouped by purpose: setup/build, graph queries, semantic, natural-language, knowledge management, metrics/eval, agent lifecycle. |
| [`## Where to look next`](#where-to-look-next) | Pointers to the MCP tool surface and the explore-first workflow. |

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

### `cairn memory` — agent memory (15 subcommands)

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
| `cairn memory embed` | Backfill semantic embeddings for memories captured before embedding existed, or after a model swap (`--batch-size 64`, `--reap/--no-reap` to also delete rows whose memory no longer exists — default on). Ongoing capture/evolve embed on their own; this catches up the rest. |

`record` options: `--body`, `--resource`, `--confidence 0.7`, `--db`, `--knowledge`.

### `cairn knowledge` — business knowledge ingestion and search

`cairn knowledge` ingests and searches business documents (specs, business-rules,
decisions) and is the parent of the `workflow` subgroup.

| Subcommand | Description |
|------------|-------------|
| `cairn knowledge add` | Ingest a business knowledge document (`--title` required). |
| `cairn knowledge import DIR` | Batch-ingest all `.md` files from a directory. |
| `cairn knowledge ingest` | Stage multi-source documents (fed markdown, repo doc-tree scans, converted pdf/docx) into an OKF outbox + dry-run manifest; `--ingest` writes them. |
| `cairn knowledge search QUERY` | Search knowledge docs (lexical + semantic + graph bridge). |
| `cairn knowledge list` | List documents (`--type`, `--status`, `--tag` filters). |
| `cairn knowledge embed` | Build the knowledge embedding index. |
| `cairn knowledge impact QUERY` | Search knowledge + full graph impact bridge. |
| `cairn knowledge remove DOC_ID` | Delete a document and its embedding rows. |
| `cairn knowledge status DOC_ID NEW_STATUS` | Update `doc_status` (`active/superseded/archived`). |
| `cairn knowledge export` | Export the `.knowledge` bundle to a directory or `.tar.gz` (`--out`). |

`add` options: `--file` or `--body` (one required), `--title` (required),
`--type spec` (`business-rule|spec|decision`), `--tags`, `--affects`,
`--affects-modules`, `--epic`, `--resource` (canonical URI),
`--description` (one-line summary; defaults to the title).

`ingest` options: `--file` (repeatable, fed markdown file), `--dir`
(repeatable, fed markdown directory), `--repo` (repeatable, repository
root to scan for docs), `--ingest` (approve: write staged manifest rows
into the store, then embed), `--include-drafts` (ingest draft-status docs
tagged `draft` instead of skipping), `--outbox` (staging directory;
default `<workspace>/.cairn/ingest-outbox`). One of `--file`, `--dir`, or
`--repo` is required; without `--ingest` nothing is written to the store.

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
| `cairn config` | Show resolved store paths (`--list` all workspaces, `--mcp-config` prints a path-free `.mcp.json` snippet, `--db` prints only the resolved graph DB path — machine-readable, for scripting). Also echoes the resolved [SCIP](./scip.md) config and whether each index file exists. |
| `cairn build` | Build (or rebuild) the code graph; also builds dataflow + transitive closure. |
| `cairn stats` | Show graph statistics (repos, symbols, edges, by-repo/by-kind/skipped tables). |
| `cairn checkpoint` | Checkpoint the graph DB's WAL back into the main file (TRUNCATE). |
| `cairn update` | Incremental graph update from git diff (or `--file` for a single changed file). |
| `cairn sync` | Manually re-index changed files (escape hatch when the watcher is disabled). |

`init` options: `--workspace`, `--from-legacy DIR` (migrate a legacy
`cairn/.kg`), `--no-build`, `--import-docs` (ingest `docs/**/*.md`).

`build` options: `--repo`, `--workspace`, `--db`, `-v/--verbose`, `--staging`
(build to temp DB and atomic-swap for zero downtime; **cannot be combined with
`--repo`** — a staged single-repo build would swap in a DB containing only that
repo, silently deleting every other repo's graph, so the combination is
rejected). When `cairn.json`
declares [SCIP](./scip.md) indexes, languages whose index file exists are
imported from SCIP (exact resolution) and skipped by tree-sitter; the summary
panel reports per-language SCIP symbol counts.

`update` options: `--repo`, `--file PATH` (single-file, for PostToolUse hooks),
`--workspace`, `--db`, `--knowledge` (knowledge bundle path, for the
post-update memory staleness scan). Runs memory decay after reindex.

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
| `cairn download-reranker` | Download the CrossEncoder reranker weights and enable reranking. |

`embed` options: `--db`, `--batch-size 64`, `--limit`, `--no-reap`,
`--build-index`, `--install-deps`, `--download-model`, `--multivector`.

`--multivector` also embeds name-only and docstring-only vectors (stored in
`embeddings_mv`) and rebuilds a `vecmv_` ANN index beside the main one. It is
off by default: a default build stores one vector per symbol, unchanged. Every
embed pass — flag or not — also refreshes the persisted `term_df` table, so
enrichment's IDF signal stays current with the embedded corpus.

`--install-deps` installs the semantic dependencies (torch +
sentence-transformers) into the shared `~/.cairn/lib/cp<version>/` directory
(scoped per Python ABI so different interpreters don't corrupt one wheel set), which
survives reinstalls, then exits without building the index. This is the
recommended one-time way to get the default `BAAI/bge-m3` model — run
`cairn embed --install-deps`, then `cairn embed` to build the index.
`--download-model` pre-fetches the embedder weights into the local
HuggingFace cache behind a single live progress line (the downloader's
per-file output is captured and shown only on failure, like `cairn upgrade`
and `cairn download-reranker`).

`download-reranker` options: `--model` (default `BAAI/bge-reranker-base`, the
natural pair for the bge-m3 embedder; honors `$CAIRN_RERANK_MODEL` if set).
Fetches the weights into the local HuggingFace cache (behind a single live
progress line — the downloader's own per-file output is captured and shown
only on failure, like `cairn upgrade` and `cairn embed --download-model`)
and writes a `~/.cairn/rerank_enabled` marker so reranking is on for
subsequent queries — no `CAIRN_RERANK=1` needed. Set `CAIRN_RERANK=0` to turn it back off. If the
configured model is later missing/evicted from the cache, queries fall back to
the hybrid (vector + BM25 + RRF) order rather than failing.

`semantic` options: `QUERY` (argument), `--db`, `--limit 20`, `--threshold 0.3`,
`--json`, `--include-callers`. Rerank runs when enabled (auto after
`download-reranker`, or `CAIRN_RERANK=1`).

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
| `cairn verify CONCEPT` | Run the deterministic critic on a single compass/wiki/memory concept (concept id relative to `.knowledge/`, no `.md` suffix). Prints the verdict — passed/errors/warnings + quality score — read-only; the single-concept front to the critic gate behind the verification contract's promise #2. |
| `cairn import-scip SCIP_FILE` | Import compiler-grade symbol bindings from a SCIP index file. |

`import-scip` options: `SCIP_FILE` (argument), `--db`, `--repo default`,
`--format proto|json` (`proto` is the default and what real indexers emit;
`json` is the legacy shape). See [scip.md](./scip.md) for generating indexes
and wiring them into `cairn.json` for build-time hybrid indexing.

### System, metrics, and eval

| Command | Description |
|---------|-------------|
| `cairn metrics` | Report MCP tool invocation metrics (calls, avg latency, error rate), and — with a flag — telemetry trends from `build_runs`/`events`. |
| `cairn doctor` | Run 8 system health checks (PASS/WARN/FAIL each); exit 0 or 1 so agents can gate on it. Read-only. |
| `cairn report` | Print a redacted diagnostic bundle (versions, doctor, recent errors, config) for bug reports / GitHub issues. Never uploads. |
| `cairn status` | System status and health across all layers. |
| `cairn eval` | Run retrieval evaluation harness across L1/L5 corpora (`--corpus`, `--json`; `--sweep`/`--kfold` for lever sweeps). |
| `cairn bench` | Run performance, scalability, or agent-effort benchmarks. |
| `cairn dashboard` | Launch the local read-only web dashboard (127.0.0.1:8765): projects, interactive graph (symbol typeahead search, node expansion, layouts), tool-call history with filters, per-tool token estimates with truncation stats, session chains, health/memory/task panels, machine-wide `/workspaces` launcher with restart-free `?store=` switching, and CSV/JSON export of the filtered history/tokens views. Loopback-only; every connection is read-only — retention ages rows in the recording pipeline, never here. |

`dashboard` options: `--db PATH` (serve a specific store; default: the
central store for this workspace), `--host` (loopback only — non-loopback
binds are refused), `--port` (default 8765). Heavy probes (reranker/ANN)
prewarm in a background thread at startup, so the first `/health` render
doesn't pay the import. See [configuration.md](configuration.md) for the
retention policy env vars surfaced on the health panel.
`metrics` options: `--db`, `--tool NAME` (default aggregation only), `--json`,
plus the telemetry-trend flags `--builds`, `--quality`, `--contention`, `--tasks`.
`doctor` options: `--db`, `--json`.
`report` options: `--db`, `--json`, `--out PATH`.
`status` options: `--db`, `--knowledge`.
`eval` options: `--db`, `--knowledge`, `--corpus L1|L5|all`, `--queries PATH`
(a queries.yaml file, or a ground-truth directory holding `queries.jsonl` +
`expectations.tsv`), `--json`, plus the lever-sweep flags: `--sweep` (a JSON
file or inline JSON list of `{name, params}` combos — `RetrievalParams`
fields, `null`/omitted = today's default; evaluates the TUNE split only,
held-out ids guarded; requires the ground-truth-directory form of
`--queries`), `--out` (with `--sweep`: write the canonical sweep document —
the harness itself never writes), `--kfold` (with `--sweep`: run the sweep
once per fold of the seeded k-fold rotation), `--folds 5` (fold count; the
harness refuses fewer than 5).
`bench` options: `--suite perf|scaling|agent`, `--workspace`, `--sizes`,
`--n-files`, `--complexity`, `--embed-backend`, `--json`, `--save`,
`--compare`, `--baseline`, `--threshold`, `--repeats`, `--runs`.

`--baseline DS-v1` compares against the committed, stamped baseline under
`benchmarks/baselines/<DS-version>/<suite>.json` (mutually exclusive with
`--compare`) and prints a provenance header — dataset version + tree hash,
cairn version, runner class — before the comparison table. A machine-profile
class mismatch between the baseline and the current run (macOS vs Linux,
arm64 vs x86_64, different CPU counts) only warns: timings stay advisory,
never normalized. `--runs` sets the agent suite's measured runs per task
(medians reported).

#### `cairn doctor` — the 8 health checks

`cairn doctor` runs eight read-only checks and prints one PASS/WARN/FAIL row
per check (color-coded `✓`/`!`/`✗`, plus a remediation `hint` where useful).
It **never writes** to the store — a missing `--db` path is reported as a
`schema` FAIL ("store not found"), never silently created. The **exit code is
0 when every check is PASS or WARN, and 1 the moment any check is FAIL**, so
an agent or CI step can gate on it (`cairn doctor --json && …`). Absence of an
optional backend (`sentence-transformers`, `sqlite-vec`) degrades to WARN
(functional-but-slower), never FAIL — FAIL is reserved for "broken": a
corrupt store, one that can't be opened, or no store at all.

| # | Check | FAIL / WARN triggers |
|---|-------|----------------------|
| 1 | `schema` | FAIL on a `PRAGMA quick_check` integrity error (corrupt/not-a-database) or when the store path doesn't exist. |
| 2 | `embeddings` | WARN when the dep-free hash backend is silently active (retrieval degraded). An explicit `CAIRN_EMBED_BACKEND=hash` stays PASS. |
| 3 | `ann` | WARN when `sqlite-vec` is *expected* (env unset/`=sqlite-vec`) but unavailable or failed to load; surfaces the latest `ann_fallback` reason. An explicit `CAIRN_ANN_BACKEND=off` stays PASS. |
| 4 | `freshness` | WARN on unindexed `pending_sync` edits, a stale repo-rebuild crash marker (an interrupted `cairn build --repo` left the repo partial — re-run it), or a `build_runs` row older than 7d (or symbols indexed with no recorded build). |
| 5 | `parse_errors` | WARN when `parse_errors` has rows (a file was skipped during indexing); shows the newest 5. |
| 6 | `concurrency` | WARN on any `lock_contention` event in the last 7d (genuinely lock-shaped errors only — schema/FTS failures don't count as contention). `stray_swept` totals are reported but never WARN (sweeping is the stdio-leak fix *working*). |
| 7 | `tool_health` | WARN when any MCP tool's 7-day error rate exceeds 10% or its p95 latency exceeds 5s. |
| 8 | `config` | Always PASS — a transparency echo of the effective `CAIRN_*` knobs (workers, read-only, fusion, ann/embed backend, telemetry, log level). |

`--json` emits the checks as a list of `{name, status, detail, hint}` objects.

#### `cairn metrics` telemetry-trend flags

With **no flag**, `cairn metrics` is unchanged: it aggregates `tool_metrics`
(calls / avg ms / errors / err%) for each MCP tool. The three extension flags
render from the telemetry tables (`build_runs`, `events`) and all accept
`--json` (a single flag prints the bare value; multiple flags print one object
keyed by section name):

- `--builds` — recent `build_runs` rows with the resolution mix
  (`exact`/`ambiguous`/`unresolved`), so resolver precision becomes a trend,
  not a forgotten panel. History accumulates across full rebuilds and staged
  builds (the analytics tables are carried over the whole-file swap).
- `--quality` — retrieval-quality aggregates: the empty-result rate (scoped to
  `semantic_search` — the only query kind with a recorded at-risk denominator —
  plus an `empty by kind` breakdown across semantic/explore/search_symbols),
  truncation count, and the semantic-backend mix (`ann`/`brute`/`hash`, fusion,
  rerank).
- `--contention` — `lock_contention` events grouped by site, so repeated
  cross-process lock waits are diagnosable.
- `--tasks` — task-queue lifecycle history: `task_lifecycle` events counted
  by transition (claimed/completed/dropped/revised) and by `task_kind`.

`--tool NAME` filters the default aggregation only; it has no effect on the
three trend flags.

#### `cairn report` — redacted diagnostic bundle

`cairn report` prints one self-describing bundle for pasting into a bug report
or GitHub issue. It is read-only and **never uploads** anything — the output
goes to stdout (and optionally a file). The bundle has four sections:

- **Versions** — cairn `__version__`, Python version, platform/OS, the SQLite
  library version, and a best-effort `PRAGMA user_version` probe (cairn applies
  `CREATE TABLE IF NOT EXISTS` DDL and tracks no numeric schema version, so this
  is typically `0`; `null` when the store is unreadable).
- **Doctor** — the same 8 checks `cairn doctor` runs (reused verbatim), rendered
  as PASS/WARN/FAIL rows.
- **Recent errors** — a bounded set (last 20) of error-ish `events` rows
  (`ann_fallback`, `hash_fallback`, `lock_contention`) plus the last 20
  `tool_metrics` rows with `status='error'`, newest first.
- **Config** — the effective `CAIRN_*` knobs (the same list `doctor` echoes).

**Privacy gate (observability-telemetry spec §7):** every string field is
passed through `memory.privacy.strip_private_data` (known secret shapes — API
keys, bearer tokens, JWTs, … — and `<private>` tags become
`[REDACTED_SECRET]` / `[REDACTED]`) and then through path redaction: absolute
local filesystem paths (POSIX, `~/…`, Windows drive letters) collapse to
`[PATH]/<basename>`, keeping the failing file's name for debuggability while
hiding your directory structure. Workspace-relative paths (`src/main.py`) and
URL path portions survive — they're the useful, non-identifying signals.
Best-effort throughout: a missing, read-only, or corrupt store degrades to
empty sections and a `schema` FAIL (mirroring `cairn doctor`) and never
raises.

`--json` emits the bundle as a JSON object; `--out PATH` additionally writes
the bundle to a file (JSON with `--json`, otherwise the same human-readable
text) and prints a short confirmation to stderr so it can't corrupt JSON on
stdout.

### Agent integration and lifecycle

| Command | Description |
|---------|-------------|
| `cairn install-agents` | Wire cairn into detected AI coding clients (Claude Code/Claude Desktop/Cursor/Droid/ZCode/opencode/agy/kilo/omp). |
| `cairn uninstall-agents` | Remove cairn entries from AI client configs (idempotent). |
| `cairn uninstall` | Full teardown: agent wiring, hooks, graph store, and the `cairn` binary. |
| `cairn version` | Print the installed cairn version. |
| `cairn upgrade` | Upgrade cairn in place (detects install method). |

`install-agents` options: `--client` (repeatable:
`claude|claude-desktop|cursor|droid|zcode|agy|opencode|kilo|omp|all`), `--workspace`,
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
current interpreter path). The reinstall runs behind a single live progress
line — the installer's own output (venv creation, dependency resolution,
per-package downloads) is captured and shown only if the upgrade fails,
followed by the exact manual retry command; a failed upgrade exits 1.
`--check` prints both versions without changing anything. If PyPI is
unreachable, it prints the manual command instead
(`pip install --upgrade cairn-intel`).

`cairn version` prints the installed version from package metadata, falling
back to the source-tree version (with a `(source checkout)` marker) for
editable installs.

---

## Where to look next

- For the **MCP server** tool surface that AI agents consume, see
  [mcp-tools.md](./mcp-tools.md) (27 tools across 4 layers; `explore` is the
  recommended first call).
- For the explore-first workflow, precise-vs-fuzzy rules, and per-tool quirks,
  see `AGENTS.md` in the workspace root.
