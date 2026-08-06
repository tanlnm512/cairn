# Changelog

All notable changes to **cairn-intel** (the `cairn` local codebase intelligence
system) are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> Prior to `0.4.0`, cairn was developed without a public changelog. The
> `0.4.0` entry below is the inaugural documented release; entries from future
> releases will be appended here incrementally.

## [Unreleased]

### Added
- **Hybrid SCIP / tree-sitter indexing.** `cairn build` can now consume
  pre-built SCIP (Sourcegraph Code Intelligence Protocol) indexes for
  languages where compiler-grade symbol bindings beat tree-sitter's
  heuristic resolver (Kotlin, Java, TypeScript, ...). Declare each language's
  index in `cairn.json` under `scip` (e.g. `{"scip": {"kotlin":
  "build/scip/kotlin.scip"}}`); at build time cairn skips tree-sitter for
  languages whose index exists and imports the SCIP data instead, producing
  **exact** cross-file call edges. A missing or undeclared index falls back
  to tree-sitter for that language — both can coexist in one workspace.
  Cairn is a consumer of SCIP indexes, never a producer (generate them
  out-of-band with `scip-kotlin`/`scip-typescript`/etc.). See `docs/scip.md`.
  - New optional `[scip]` extra: `pip install cairn-intel[scip]` (depends on
    the real `protobuf` runtime; no PyPI package ships SCIP bindings, so a
    vendored generated stub is checked in at
    `src/cairn/parsers/_scip_pb2.py` — regenerate via
    `scripts/regen_scip_pb2.sh`).
  - `cairn config` now echoes the resolved SCIP config + whether each index
    file exists.
  - `cairn import-scip` gains a `--format` flag (`proto` default, `json`
    legacy).
  - `symbols.source` column (`'tree_sitter'` or `'scip'`) records provenance;
    NULL on legacy rows is treated as `tree_sitter`. Run `cairn build` once
    after upgrading to populate it.
  - Incremental updates (`cairn update`, file watcher, MCP catch-up) fall
    back to tree-sitter for an edited SCIP-covered file (bounded, self-healing
    staleness — the next full build restores `source='scip'` once the index
    is regenerated).
- **Portable `.kg` database.** The code graph now stores file paths
  **repo-relative** (`files.path`, `parse_errors.file_path`,
  `skipped_files.path`, `pending_sync.path`) and `repos.path` **workspace-
  relative**, so the `.kg` SQLite file is shareable across machines — copy it
  into the recipient's `~/.cairn/<key>/` store (or point `CAIRN_DB` at it) and
  queries resolve against the local workspace. Paths are reconstructed to
  absolute only at disk-I/O time (source reads, freshness checks) via the new
  `scanner.resolve_file_path` chokepoint. **Run `cairn build` once after
  upgrading** to convert an existing absolute-path DB to relative form;
  un-rebuilt DBs keep working until then (read paths tolerate both forms).

### Changed
- _Nothing yet._

### Fixed
- _Nothing yet._

### Removed
- _Nothing yet._

## [0.6.0] - 2026-08-05

> **Focus:** `install-agents` gets an interactive checkbox picker, and
> `scripts/install.sh` no longer silently mis-installs semantic-search deps
> or wires up AI-client hooks that fail on every invocation.

### Added
- **Interactive checkbox picker for `cairn install-agents`.** Replaces the
  old comma-separated free-text prompt with an arrow-key navigable,
  spacebar-toggle checklist (via `questionary`) of detected AI clients,
  pre-checked for the ones not yet wired up. Styled with cairn's own CLI
  color theme (cyan question, green selected, yellow pointer, dim
  instructions). Both Ctrl+C and Ctrl+D abort cleanly with a single
  consistent message; `--yes`/`--client`/non-TTY paths are unchanged.

### Changed
- **`scripts/install.sh` no longer wires AI coding clients.** Installing
  cairn and wiring agents are now separate steps -- run `cairn
  install-agents` afterward (interactively, or with `--client`/`--scope`).
  The install.sh flags `--no-agents`, `--agents`, and `--scope` are removed
  as a result.
- **Progress bars share one implementation across TTY and non-TTY output.**
  `display.progress_bar()`'s rich-backed path previously patched ad-hoc
  attributes onto a `Progress` instance at runtime; the non-TTY path
  implemented an unrelated class from scratch. Both now expose the same
  small, explicit interface, and `cairn embed --install-deps`'s
  install-progress indicator goes through this shared mechanism too instead
  of its own hand-rolled status line.

### Fixed
- **`scripts/install.sh --semantic` installed semantic-search deps into the
  wrong location** (the tool's own venv, or system Python) instead of the
  shared lib dir (`~/.cairn/lib`) cairn's runtime actually resolves deps
  from -- the deps it installed were invisible to `cairn embed`. It now
  delegates to `cairn embed --install-deps`, the same code path cairn uses
  internally, honoring `CAIRN_LIB`/`CAIRN_HOME` overrides.
- **Non-TTY output (CI logs, `cairn build | tee`) could get corrupted with
  raw carriage-return bytes** during the semantic-deps install step -- it
  wrote `\r`-updated status lines unconditionally instead of checking
  whether stdout was a terminal.
- **`install-agents` wrote AI-client hook commands pointing at a
  nonexistent Python module** (`src.hooks.claude_hooks` instead of
  `cairn.hooks.claude_hooks`), so every PostToolUse/Stop hook it wired up
  silently failed with `ModuleNotFoundError`. `uninstall-agents` still
  recognizes and removes hooks written by the old, broken path.

## [0.5.3] - 2026-08-03

> **Focus:** fixes `cairn serve` boot hang that prevented Claude Desktop (and
> other MCP clients) from connecting to cairn. The boot catch-up was
> re-discovering every file as "changed" on every startup, blocking the MCP
> stdio loop past the client's connect timeout.

### Fixed

- **`cairn serve` hung for 20+ seconds on boot, causing MCP clients (Claude
  Desktop) to time out and drop the connection.** The boot catch-up
  (`ensure_fresh_force` → `_detect_changed`) queried the `files` table with
  `WHERE repo_id = '<repo_name>'`, but `cairn build` stores `repo_id=''` for
  single-repo workspaces. The query returned nothing, so every file looked
  "new" and the watcher re-indexed the entire workspace on every startup
  (20s+ on a 71k-symbol workspace). The MCP stdio loop didn't start until the
  catch-up finished, so the client's `initialize` request sat unread past its
  connect timeout. `_detect_changed` now falls back to a repo_id-agnostic
  lookup when the named query returns nothing (same fix pattern as the
  `cairn update` repo_id mismatch in 0.5.1), and handles both relative and
  absolute stored paths. Boot catch-up now finds only genuine deltas (0 files
  when the graph is up to date) instead of re-discovering all files.

## [0.5.2] - 2026-08-03

> **Focus:** semantic dependency persistence + embed-backend visibility.
> `cairn embed` no longer silently falls back to the hash embedder when
> `sentence-transformers` isn't installed — it now warns, and the one-time
> `cairn embed --install-deps` installs into a shared lib dir that survives
> tool reinstalls.

### Added

- **Shared semantic-deps directory (`~/.cairn/lib`).**
  `cairn embed --install-deps` now installs torch + sentence-transformers + numpy
  into `~/.cairn/lib/` using `pip install --target`, rather than into the
  tool's isolated venv. The shared lib is prepended to `sys.path` at import
  time (`paths.py`), so every `cairn embed` / `cairn serve` / `cairn semantic` finds
  the deps there regardless of which venv `cairn` runs in. This means a single
  one-time download persists across `uv tool install --force` reinstalls,
  upgrades, and venv resets — the deps are never silently lost.

- **Embed-backend visibility in `cairn config`.**
  `cairn config` now shows the resolved embedding backend and model, so a user
  can see — right after install, before any embed — whether they're getting
  real embeddings (`BAAI/bge-m3  (backend: local)`) or the dep-free hash
  fallback (`hash-256-v1  ⚠ fallback (sentence-transformers not installed)`).

- **Fallback warning in `cairn embed`.**
  When `sentence-transformers` isn't installed and the backend silently falls
  back to `hash`, `cairn embed` now prints a prominent warning with the one-time
  fix (`cairn embed --install-deps`). Previously the only signal was the model
  name (`hash-256-v1`) in the progress bar.

## [0.5.1] - 2026-08-03

> **Focus:** fixes `cairn update` — incremental re-indexing was broken in five
> coupled ways, affecting every workspace (not just edge cases). With these
> fixes, `cairn update` → `cairn embed` correctly detects code changes, re-indexes
> the changed files, and re-embeds them.

### Fixed

- **`cairn update` detected zero changed files on non-git / no-commit repos.**
  `_changed_source_files` ran `git diff --name-only HEAD`, which exits
  non-zero (and is swallowed as "no changes") when the workspace isn't a git
  repo or has no commits yet. Added a size/mtime fallback against the `files`
  table — the same signal `cairn sync` uses — so detection works without git.
- **Repo-name mismatch between `cairn build` and `cairn update`.** `build_graph`
  stores `repo_id=''` for single-repo workspaces (`workspace='.'` has no
  relative-path component), but `reindex_paths` looked up files by the
  inferred repo name (`'cairn'`). The lookup missed the row, skipped the
  delete of stale symbols, and the re-insert then FK-violated. The lookup now
  keys on file path (the stable identity) and uses the stored repo_id for
  downstream inserts.
- **Path-form mismatch (relative vs absolute).** Build stores repo-relative
  paths (`service.py`); reindex received absolute paths. The lookup now tries
  the absolute path, the repo-relative form, and the basename.
- **`DELETE FROM symbols` failed when embeddings existed.**
  `embeddings.symbol_id REFERENCES symbols(id)` — embeddings weren't cleared
  before the symbol delete, so any previously-embedded repo (the common case)
  hit `FOREIGN KEY constraint failed` on every `cairn update`. Embeddings for the
  file's symbols are now deleted first; `cairn embed` repopulates them.
- **`insert_parse_error` FK-violated when the repo wasn't in `repos`.** The
  error-recording path (which runs when a re-indexed file fails to parse)
  inserted into `parse_errors.repo_id`, FK-referencing `repos.id` — but the
  repo row might not exist during incremental reindex. `insert_parse_error`
  now ensures a repos row exists (idempotent `ON CONFLICT DO NOTHING`) before
  the insert, so a parse error is recorded rather than masked by a secondary
  FK failure.

## [0.5.0] - 2026-08-03

> **Focus:** broader language coverage (Go), richer edge semantics
> (service-topology), and making cairn's defining differentiators
> (resolution labels, the deterministic critic, memory refs-verification)
> the headline. Verified end-to-end on a 930-file / 9,504-symbol Android
> (Kotlin) repo and a fresh-install + MCP connection smoke test.

### Added
- **Go parser** (`tree-sitter-go`): structs, interfaces, type aliases,
  functions, methods (with receiver type as `parent_scope`), call expressions,
  and imports. Brings supported languages to nine.
- **Service/topology edges** (`http_call`, `service_call`): a post-parse pass
  detects HTTP client calls (`fetch`/`axios`/`http.Get`/OkHttp/Retrofit) and
  emits edges to the external target. `get_callers`/`get_callees` gain a `kind=`
  filter; `impact_analysis`/`trace_flow` follow structural edges by default
  (`calls`/`extends`/`implements`) and exclude service edges unless opted in via
  `include_service_edges=True` — preserving the precise-by-default identity.
- Configurable cross-repo namespace map: `repo_namespaces` key in
  `cairn.json` (and `CAIRN_REPO_NAMESPACES` env override) drive
  `cross_repo_deps`. Falls back to the built-in default map; a malformed value
  never breaks the build. `cairn config` now prints the resolved map and its source.
- `cairn eval --queries PATH` flag to point the retrieval harness at a custom
  ground-truth set.
- `cairn compass generate --dry-run` / `--show-rejections` and
  `cairn wiki generate --dry-run` / `--show-rejections`: run generation + the
  deterministic critic and print the verdict (errors/warnings/quality) without
  writing. The wiki generator is now critic-gated too. MCP `generate_flow`
  surfaces rejection reasons.
- `memory_digest` (MCP) and `cairn memory digest` / `search` / `list` (CLI) now
  show a live `refs-verified` fraction per memory (recomputed against the graph
  each call). `recall_memory` refactored to a single shared connection.
- `docs/benchmarks.md` — methodology for retrieval quality (`cairn eval`) and
  performance/scaling (`cairn bench`), including the precise-vs-fuzzy
  false-positive methodology.
- `docs/examples/resolution-walkthrough.md` — a concrete worked example of
  precise vs fuzzy querying and ambiguous-dispatch hops.
- `docs/architecture-overview.md` + `architecture.html` — big-picture system
  architecture with 11 rendered flow/sequence/ER diagrams.
- Resolution-flow diagram in `docs/architecture.md § Resolution model`.

### Changed
- **Positioning:** the README now leads with "Resolution-labeled edges" — the
  `exact`/`ambiguous`/`unresolved` taxonomy and precise-by-default rule move
  from a buried architecture note to the headline value prop.
- Reconciled the MCP tool count to **26** consistently across README, AGENTS.md,
  and all docs (was "24" in several places); fixed `explore` being misclassified
  out of the graph layer. Canonical grouping: graph (9), knowledge base +
  compass (5), memory (7), knowledge (5).
- `docs/configuration.md` updated to document the `cairn.json` workspace
  config file (previously claimed "no config file").

### Fixed
- `DEFAULT_QUERIES_PATH` in `eval.py` resolved to `src/tests/eval/queries.yaml`
  (nonexistent). Now resolves to the repo-root `tests/eval/queries.yaml`
  (development) or the in-package layout (sdist). `cairn eval` no longer silently
  reports 0 samples.

## [0.4.0] - 2026-08-02

> **This is the first release with a changelog.** Items below describe the state
> of cairn at `0.4.0`, reconstructed from project history; future releases
> will append entries incrementally.

### Added

- **MCP server** exposing **24 tools** across 5 layers, transportable over stdio
  (default) or SSE/streamable-http:
  - **Graph layer (9 tools):** `find_definition`, `get_callers`, `get_callees`,
    `impact_analysis`, `explore`, `semantic_search`, `search_symbols`,
    `cross_repo_deps`, `visualize_graph`.
    `explore` is the recommended first call — it aggregates the graph layer into
    one response (matching symbols' verbatim source, inter-symbol call paths
    including ambiguous dispatch hops, and a blast-radius summary).
  - **Compass + knowledge base (2 tools):** `ask_compass`, `get_compass`.
  - **Memory (7 tools):** including `record_memory`, `recall_memory`.
  - **Knowledge (5 tools):** including `search_knowledge`.
  - **Router (1 tool).**
- **Structural code graph** built on **tree-sitter**, with parsers for
  **8 languages:** Kotlin, Java, Python, Swift, TypeScript, JavaScript, Dart,
  and Objective-C.
- **Resolution-aware querying:** `get_callers`, `get_callees`, and
  `impact_analysis` support a precise-vs-fuzzy mode toggle (`fuzzy=True`).
  Precise mode follows only edges the resolver pinned to exactly one definition
  (ground truth for blast radius); fuzzy mode returns a candidate list for
  auditing and dead-code hunting. Every result carries a `resolution` label:
  `exact` (trusted), `ambiguous` (multiple candidates, resolver declined to
  guess), or `unresolved` (external/stdlib).
- **Optional semantic search** via sentence-transformers, installable through
  the **`[semantic]`** extra (`pip install cairn-intel[semantic]`). Pulls
  torch/sentence-transformers and numpy; the default install remains
  zero-network and torch-free. `semantic_search` defaults to RRF fusion
  (BM25 + vector) and also powers the optional rerank stage
  (`CAIRN_RERANK=1`).
- **Native ANN index** via the sqlite-vec loadable SQLite extension, opt-in
  through the **`[ann]`** extra and `CAIRN_ANN_BACKEND=sqlite-vec`. Separate
  from `[semantic]` since it only matters at real corpus scale; the brute-force
  cosine scan remains the default for small/medium repos.
- **LLM task queue** for agent-decoupled compass/wiki synthesis. Cairn never
  calls an LLM directly; instead it queues synthesis tasks
  (`cairn task list` / `show` / `claim` / `complete`) that an external agent fulfils.
  A deterministic critic fact-checks every result — only graph-verified
  files/symbols are allowed.
- **CLI** (`cairn`) with a full fallback surface mirroring the MCP tools, plus
  `cairn install-agents` and `cairn uninstall-agents` for wiring cairn into local
  AI coding clients (MCP configs, skills, commands, and optional git hooks).
- **OKF knowledge files** stored under `.knowledge/`: `compass/` (module
  navigation guides), `wiki/` (architectural documentation),
  `memory/tribal/` (decisions, patterns, mistakes), and ephemeral
  `memory/raw/` + `memory/drafts/` scratch areas.
- File-watching live updates via the **`[watch]`** extra (`watchdog`).
- **First-class OKF v0.2 frontmatter families** parsed on read: `sources`,
  `verified`, `status`, `stale_after`. These are now named `OKFConcept` fields
  rather than silently landing in `extensions`, and a bare `verified` mapping is
  normalized to a one-element list per spec §11. (cairn does not yet
  *produce* `Attested Computation` concepts, but the parser tolerates the type
  per spec §4.1.)

### Changed

- **OKF output upgraded from v0.1 to v0.2** (Google's Open Knowledge Format,
  `GoogleCloudPlatform/knowledge-catalog/okf/SPEC.md`). A concept's last
  content change is now emitted as `generated: { by, at }` rather than the v0.1
  bare `timestamp`; the actor follows the spec §7 `<producer>/<version>` form
  (e.g. `cairn/0.4.0`). Legacy v0.1 files with a bare `timestamp:` are
  still read on input via the spec §13.1 fallback, so existing `.knowledge/`
  trees migrate lazily to v0.2 the next time cairn rewrites them.
- **`mcp>=0.9` is now a core dependency.** The SSE/streamable-http transport
  plus its `uvicorn`/`starlette` runtime are bundled in `mcp>=0.9`, so they ship
  with the default install — the legacy `[sse]` extra that older `mcp` versions
  required was removed upstream and is no longer used here.

### Fixed

- **Kotlin `operator fun invoke` call edges.** Bare calls of the standard
  Android UseCase idiom (`someUseCase(params)` against a DI-injected property)
  previously resolved to the *local property* in the calling file rather than
  the invoked class, returning 0 callers regardless of real usage. The parser
  now **retargets these bare-call edges to the callee's declared type.**
  (A narrower gap remains for `this.someUseCase(params)` with an explicit
  receiver — cross-check with `fuzzy=True` or a grep if that shape looks
  under-reported.)
- **`search_symbols` substring / camelCase matching.** FTS5's `*` wildcard is
  prefix-only and the `unicode61` tokenizer does not split camelCase, so
  patterns like `*UseCase*` — or even a bare `UseCase` — previously matched only
  names literally starting with `UseCase`. The tool now **unions in a
  `LIKE`-based substring pass for non-prefix patterns**, so wildcards and
  substring queries work on both underscored and camelCase names.
