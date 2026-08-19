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
- `cairn config --db` — prints only the resolved graph DB path (machine-readable,
  for scripting). The README self-demo already piped this into `sqlite3`; the
  flag now exists to match.

### Changed
- Code-vs-docs drift fixes from a full docs audit. `docs/mcp-tools.md`: the
  freshness section now describes the live file watcher (`[watch]` extra, ~2s
  debounce, `pending_sync` staleness banners) instead of the pre-watcher
  boot-only model, and `semantic_search` documents its `rerank` parameter and
  the `CAIRN_RERANK_MIN_MARGIN` auto gate. `docs/cli-reference.md`: 15 (not 14)
  memory subcommands with `cairn memory embed` documented, `update
  --knowledge`, `config --db`, and the `build --staging`/`--repo` mutual
  exclusion. `docs/configuration.md`: `CAIRN_WORKERS` is clamped to [1, 256]
  (not uncapped); `CAIRN_LLM_BACKEND` documents `droid`/`opencode`/`claude`;
  `CAIRN_CHUNK_VARIANT` documents all seven variants; the `openai` embed
  backend's `OPENAI_API_KEY` requirement is stated; `CAIRN_CONN_POOL`,
  `CAIRN_WARM_MODELS`, and `CAIRN_RERANK_MIN_MARGIN` are documented.
  `docs/contribution-workflow.md`: counts all nine CI jobs (the test matrix
  expands to five per-version checks) and adds the DS-v2-seal and advisory
  Bench rows to the gate-failure decision table.

## [0.12.1] - 2026-08-18

> **Focus:** documentation as a product surface — no behavior changes to the
> indexer, query path, or MCP tools. The README and the `docs/` set are
> restructured for human navigation, the completed spec campaigns are audited
> and archived, and the release ships one dev-tooling fix.

### Added
- `docs/README.md` — a docs index grouping the doc set by reader need (start
  here / how it works / evidence / operate / contribute); every `docs/` page
  now opens with a scannable orientation block (what/when summary + Contents
  table on long pages) and a back-link to the index.
- `benchmarks/README.md` — the artifact-by-artifact inventory: all 30
  committed measurement JSONs keyed by path, each named by its human-readable
  companion (one-command drift detector included).
- `scripts/check_doc_links.py` — relative-link checker over `docs/` +
  `README.md` + `benchmarks/README.md` with a resolution-based back-link
  advisory (exit 0/1).

### Changed
- README revamped after the codegraph presentation pattern: linked TOC,
  numbered Get Started (install → wire agents → build → stay fresh →
  uninstall), a Language Support table (14 languages, extensions, per-language
  SCIP/JSX/header-sniffing detail), Why cairn with honest trade-offs, an
  architecture diagram, and a Measured Results section fed by committed
  benchmark artifacts only.
- Completed specs (benchmark-datasource, retrieval-quality,
  retrieval-quality-v2) re-audited at `8dbf2ca` — every FR verified DONE with
  verify commands re-run green — and archived under `specs/archive/`;
  superseded `docs/phases/` sets removed.
- Test-suite comments trimmed to general-purpose (assert-narration, stale
  fossils, fixture-relocation history; 16 lines, zero behavior change).

### Fixed
- `scripts/ci-local.sh`: the bench job died at its install step
  (`pip install -e ".[]"` is not a valid requirement); extras-free installs
  now use plain `-e .`, matching CI's bench job.

## [0.12.0] - 2026-08-17

> **Focus:** the second retrieval-quality campaign — an evidence-base upgrade,
> not a default change. `cairn eval` gains k-fold cross-validation (>=5 seeded
> folds, pooled per-query paired bootstrap); DS-v2 brings a cross-corpus ground
> truth (198 queries over yarl + attrs-26.1.0, sealed by a new CI job); and
> three new levers are measured for the first time — IDF-aware enrichment,
> RM3 pseudo-relevance feedback, and multi-vector-per-symbol embeddings
> (`cairn embed --multivector`). Multivector reached both SC-1 targets on
> DS-v1 (recall@10 0.5588, MRR 0.3395 — the first configuration to do so) but
> was refuted zero-shot on DS-v2, so nothing ships; the honest verdict lives
> in the unified ablation record, and four scope-audit defect fixes (seal
> re-mint, k-fold embedding-state isolation, the vecmv purge trap, mv row
> metadata) round out the release.

### Added
- **Retrieval-quality evidence base, second campaign (no default changes)**:
  k-fold cross-validation in `cairn eval` (`--kfold`, >=5 seeded folds, pooled
  per-query paired bootstrap — per-fold spread descriptive only); a DS-v2
  cross-corpus ground truth (198 queries over yarl + attrs-26.1.0, per-corpus
  rows + macro-average); IDF-aware query enrichment (`enrich_idf`, cutoff 0.90
  via the persisted `term_df` table); RM3-style pseudo-relevance feedback
  (`prf` + `prf_docs`/`prf_terms`/`prf_lambda`, replaces-not-stacks rerank);
  and multi-vector-per-symbol embeddings (`cairn embed --multivector`,
  `multivector` query flag; name/docstring vectors, max-score dedup,
  vecmv_ ANN index). All levers flag-off by default. The confirmation ladder
  cleared three candidates on the DS-v1 k-fold guard — multivector reached
  both SC-1 targets there (0.5588/0.3395) — but zero-shot DS-v2 refuted
  transfer for all of them, so nothing ships and the shortfall + next
  binding constraint (lever generalization) are documented in
  `benchmarks/quality/ablation.md`.

### Changed
- **CI bench: rolling same-class baseline**: the advisory comparison now
  targets a rolling baseline minted on every `main` push (same hosted
  runner class) instead of the reference-local committed artifact, with a
  SHA sidecar naming the commit it was minted on (the attribution fix for
  the old rolling design); the committed DS-v1 artifact stays as the
  cold-start fallback, the CLI's machine-profile check now buckets
  same-pool `runner_class`/`os` stamps at class level (CI-vs-CI renders
  clean; cross-class pairs still warn), and the advisory PR comment names
  which baseline the numbers are against.
- **Unified ablation record**: the two retrieval-quality campaign records
  (`ablation.{json,md}` v1 + `ablation-v2.{json,md}` v2, artifacts of the two
  campaign PRs) merged into one `benchmarks/quality/ablation.{json,md}`
  (schema `cairn-quality-ablation/2`); the first campaign's `/1` record is
  embedded verbatim under `campaigns.retrieval-quality-v1` (original blob
  hashes recorded; git history keeps the standalone originals), and the two
  guard test files merged into one `tests/test_ablation_artifact.py`.
  Dataset labels (`DS-v1`/`DS-v2`) and measurement families are untouched —
  they name datasets and protocols, not campaign versions.
- **Docs synced with the retrieval stack**: architecture docs (html +
  overview + query-flow + diagrams) now cover the `embeddings_mv`/`vecmv_`
  dual index, the `term_df` table, and the flag-off PRF/multivector/IDF
  levers; `docs/benchmarks.md` documents the eval sweep/k-fold harness,
  DS-v2, and the campaign verdict; `docs/cli-reference.md` gains
  `--multivector`, `--sweep/--kfold/--folds`, and the bench baseline flags;
  README adds a `cairn eval` row. Also corrected pre-existing stale claims
  (27 MCP tools, 8 memory tools, 5 resolver tiers, incremental `cairn update`
  rebuilding derived indexes).

### Fixed
- **Scope-audit defects from the retrieval-quality-v2 campaign** (audit
  2026-08-17; none affect the campaign's conclusions — its sweeps used no
  variant combos and no ship happened): `tree_hash` now excludes build-noise
  directories (`__pycache__`, `.ruff_cache`, `.mypy_cache`, `.pytest_cache`)
  — hash-neutral for clean trees — and the DS-v2 attrs corpus seal is
  re-minted over the committed content (the old pin was minted over an
  authoring tree with untracked noise, so a fresh clone failed the seal);
  the seal now also runs as a CI job (`Verify DS-v2 seal`). `run_sweep` /
  `run_sweep_kfold` measure every combo under its own declared embedding
  state (a state machine snapshots the session baseline and restores it
  before any non-variant combo that follows a variant one — previously
  folds >= 1 and later combos inherited the last variant's embeddings).
  `purge_stale_models` no longer drops the active `vecmv_<model>` index
  (`LIKE 'vec_%'` matched `vecmv_` too because `_` is a SQL wildcard; both
  index families are now kept/dropped by exact model membership). The DS-v2
  zero-shot runner derives the `mv` row marker from the combo definition
  instead of hardcoding `false`, and the committed `ablation.json` /
  `rows-ds2.json` multivector rows are corrected to `mv: true` (the DS-v1
  record is embedded verbatim and untouched).

## [0.11.0] - 2026-08-16

> **Focus:** performance across the whole arc an agent feels — query path,
> write path, freshness, and evidence. Impact queries answer from the
> precomputed closure (20.9 ms → 0.1 ms p50); the server pools connections
> (11.7×) and pre-warms its models (first semantic query 9.4 s → 0.3 s);
> rerank is confidence-gated behind a calibrated threshold; `cairn update`
> maintains derived indexes incrementally (single-file update at 1,000 files:
> 377 s → 9.7 s, 2.5% of a build); the ANN index stays in sync per
> upsert/delete with drift surfaced in doctor and status; with the `[watch]`
> extra, `cairn serve` sees edits live (save → queryable in ~2 s); and a new
> agent-effort benchmark publishes the harness cost story (99% fewer tool
> calls, 99.5% fewer tokens vs a grep/read control). numpy became a core
> dependency to keep the fallback scan fast. Full methodology and tables in
> `docs/benchmarks.md`; phase record in `docs/phases/performance-gap/`.

### Added
- **Retrieval-quality tuning infrastructure**: an ablation harness with held-out
  discipline (seeded 50/50 split; selection-stage evaluation of validation ids fails
  loudly; paired-bootstrap accept guard with t cross-check) — `cairn eval --sweep`;
  explicit `RetrievalParams` injection (threshold, RRF k/weights, pool sizes, rerank,
  sparse top-N, enrichment flag) threaded through `semantic_search` with
  None-means-today's-default equivalence; deterministic query enrichment (identifier
  extraction + an OR-terms FTS path fixing the empty-BM25 defect for sentence queries —
  wired, default off on measurement); field-dropout chunk variants with a recipe param;
  structured rerank pairs with pinned 512-token query-priority truncation (default
  reverted to flat on measurement: -10.4pp MRR); warm-time measurement harness +
  artifact (cold 15.5s → warm 232.6ms, 66.6×); DS-v1.1 quality mint with retrieval-state
  stamping; and the committed ablation record (`benchmarks/quality/ablation.{json,md}`,
  22 rows). The SC-1 improvement targets were NOT reached (shipped config unchanged at
  L1 recall@10 0.4174 / MRR 0.2862): five bootstrap-guarded candidates all failed
  significance on the 58-query ground truth (best Δ+0.112 at p=0.118) — the shortfall is
  documented with full evidence, and the ground-truth size is identified as the binding
  constraint (DS-v2 is the unlock).
- **Benchmark datasource (DS-v1)**: a pinned, versioned comparison substrate —
  T1 synthetic corpus content-pinned by manifest hash in CI; T2 vendored yarl
  snapshot (437 KB, Apache-2.0, full provenance) with a hand-verified ground
  truth (82 queries / 234 expectations, 100% verified against a real build);
  committed `benchmarks/baselines/DS-v1/` artifacts stamped with dataset
  version, cairn version, and machine profile; `cairn bench --baseline DS-v1`
  with an advisory profile-mismatch warning; docs/benchmarks.md reference
  tables generated from the baselines between sentinels (hand-edit-guarded in
  CI); T3 scale pins (home-assistant/core, torvalds/linux) with a local
  fetch-by-pin command that verifies HEAD == pin exactly. First real quality
  baseline: L1 recall@10 = 0.4174, MRR = 0.2862.
- **Live file watching**: with the `[watch]` extra installed, `cairn serve`
  now sees source edits made while it runs — debounced (≤2 s) file events
  insert `pending_sync` rows (so concurrent readers get staleness banners)
  and trigger an incremental update under the build lock, with contention
  absorbed and retried on the next batch. `CAIRN_WATCH=0` disables;
  read-only servers never watch. Live smoke: save → new symbol queryable in
  2.1 s.
- **Agent-effort benchmark** (`cairn bench --suite agent`): six task-shaped
  questions answered by scripted cairn tool sequences vs a deterministic
  grep/read control — measured 99.0% fewer tool calls and 99.5% fewer
  context tokens per query (grep wins concept-search wall-time; reported),
  with `--save`/`--compare` baselines and methodology in
  `docs/benchmarks.md`.
- `impact_analysis` index mode: precise, structural, depth ≤ 3 queries are
  answered from the `transitive_edges` closure in one indexed statement
  (~200× faster p50 on the bench corpus; shortest-path depths, DFS fallback
  for fuzzy/service/deep queries, non-exact names, unmaterialised closures,
  and seed cycles so cycle reporting is preserved). `use_index=False` pins
  the classic DFS; the closure is now seeded only with structural edge kinds
  and gains a `target_id` index.
- Golden parity harness (`tests/test_traversal_parity.py`) pinning
  `impact_analysis`/`trace_flow`/`get_dataflow` outputs on a resolved-edge
  corpus, plus index-mode invariant and DFS query-count tests.

### Changed
- **Dependencies**: `numpy>=1.24` is now a core dependency (was
  `[semantic]`-only) — it drives the batched cosine scan at ~0.7 µs/row vs
  ~28–39 µs/row for the (also improved) pure-Python fallback, and that scan is
  what `semantic_search` runs whenever no vec0 index exists. pip-audit clean at
  the locked versions.
- **Performance**: `impact_analysis`/`trace_flow` memoise per-name
  caller/callee/definition lookups (one query per distinct name per call
  instead of one per visited symbol); the MCP server pools read connections
  per (thread, db path) with atomic-swap detection and a `CAIRN_CONN_POOL=0`
  kill switch (connection+query path 0.826 → 0.071 ms, 11.7×). The perf
  suite now builds the transitive closure (matching real deployments) and
  adds an `impact_analysis_wide` fan-in benchmark.
- **Performance (semantic hot path)**: the server pre-warms the embedding
  and reranker models in a boot-time background thread when their weights
  are already cached (`CAIRN_WARM_MODELS=0` disables) — first
  `semantic_search` drops from ~9.4 s to ~0.3 s; the optional rerank stage
  is skipped when the fused ranking is already decisive
  (`CAIRN_RERANK_MIN_MARGIN`, default 0.45, plus exact-name corroboration;
  per-call `rerank` override on the tool), with a `rerank_skipped`
  telemetry event; the brute-force cosine fallback is a single batched
  matrix product instead of a per-row loop (2.8–6.4× on the scan).
- **Performance (write path)**: `cairn update` now maintains the derived
  indexes incrementally (affected-source closure re-derivation + per-name
  dataflow refresh) instead of rebuilding them from scratch — a single-file
  update on a 1000-file corpus drops from ~377 s (95% of a full build) to
  ~9.5 s (2.5%), verified row-for-row against full rebuilds by a 50-sequence
  property test. Embedding upserts keep the sqlite-vec ANN index in sync
  transactionally (delete+re-insert — vec0 has no replace idiom), deletion
  paths sync too, and `cairn doctor` reports direction-aware ANN drift
  (unindexed vs stale entries) with the recovery command.

### Fixed
- `semantic_search` no longer silently degrades to the FTS fallback when
  called with a bare `sqlite3` connection (no `Row` factory): retrieval rows
  are normalized at the fetch boundary, and a regression test pins it.

### Removed
- _Nothing yet._

## [0.10.0] - 2026-08-14

> **Focus:** observability & telemetry — the full spec (T01–T20): local-only
> event pipeline, build history, `cairn doctor`, metrics trends, optional
> OTLP export, and workflow wiring; plus a full 9-scope audit (4 P1 / 27 P2
> findings, all fixed same day) and a post-review remediation pass (report
> path redaction, telemetry retention across rebuilds, OTLP failure
> semantics, flush serialization, transcript redaction).

### Added
- **Observability (P0 quick wins, T01–T05).** Central logging config and
  visibility for previously silent degradations. `cairn.utils.logging.configure_logging()`
  is the single config point for the `cairn` namespace logger, wired into both
  the CLI group callback and the MCP server `run()`. It reads
  `CAIRN_LOG_LEVEL` (default `WARNING`; `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`,
  case-insensitive; an invalid value falls back to `WARNING` with one stderr
  notice) and forces `DEBUG` when the `-v`/`--verbose` flag is set. The handler
  is **stderr-only** and never touches the root logger, so stdio stdout stays
  pure JSON-RPC and `caplog` still works in tests. The ANN brute-force fallback
  (when `sqlite-vec` is unavailable) now emits a one-time `WARNING` via
  `warn_ann_fallback_once`; an explicit `CAIRN_ANN_BACKEND=off` stays silent as
  an informed choice rather than a degradation. `cairn status` surfaces
  `parse_errors` (total count plus the newest five, silent when the table is
  empty) so a partially-failed build is no longer invisible. The previously-
  untested metric instrument/flush path is now covered by `tests/test_metrics.py`
  (decorator success/error paths, `_truncate_result` at `CAIRN_MAX_RESULT_CHARS`,
  and `_flush_metrics` drain/retry/read-only-skip).
- **Observability (P1 telemetry pipeline, T07–T11, T15).** A local-only event
  sink (`src/cairn/telemetry/`) generalizes the proven `metric_buffering`
  buffered-writer pattern: a deque + daemon flush (every 30s) + `atexit` +
  backlog cap, with one shared flush thread per process. Every previously-silent
  degradation path now emits at least one durable, low-cardinality signal into
  two additive SQLite tables — `events` and `build_runs` (`CREATE TABLE IF NOT
  EXISTS`; old DBs upgrade on connect, no migration entry needed). The event
  catalog: `ann_fallback`/`hash_fallback` (closes the silent ANN/hash backend
  gaps), `lock_contention` (emitted alongside the P0 one-time warning so the
  v0.9.x lock-wait class is now aggregatable, not just logged), `truncate_result`,
  `empty_result`, `semantic_backend` (backend/fusion/rerank/ms/n-results — so
  retrieval provenance stops evaporating), `task_lifecycle`, and `stray_swept`.
  `build_runs` persists one row per build/sync/embed/incremental pass (repos,
  files, symbols, edges, the exact/ambiguous/unresolved resolution mix, parse
  errors, skipped, phase timings), so resolver precision and build cost become a
  queryable trend rather than a forgotten summary dict. Attributes are enums,
  short fixed tags, or bucketed values only — never paths or free text — and a
  parametrized cardinality-guard test asserts every emitter's attr values stay
  in their declared enum sets (`tests/test_telemetry.py`). The new
  `CAIRN_TELEMETRY` env var (default `on`) is the master kill switch: `off`
  makes `emit`/`warn_once`/`note_contention` near-zero-cost no-ops. Turning it
  off stops **recording** but does not silence the one-time operational WARNING
  logs (lock-contention, ANN fallback, hash fallback) — those are operational
  signals, not telemetry data.
- **Observability (P1 health surfaces, T12–T14).** Three ways to consume the
  new data, all read-only and preserving the 27-tool MCP contract (no new tool):
  `cairn doctor` runs 8 health checks (schema integrity, embeddings backend,
  ANN, freshness, parse errors, lock contention, per-tool error/latency health,
  config echo), each PASS/WARN/FAIL, exiting 0 unless any check FAILs — so an
  agent or CI step can gate on it, and the previously-invisible `parse_errors`
  table is finally read by a command. `cairn metrics` gains `--builds` (build-run
  trend with the resolution mix), `--quality` (empty-result/truncation rate +
  backend mix), and `--contention` (lock events by site); the flagless output is
  unchanged. The `cairn://status` MCP resource appends a `health` block (active
  backend degradations, pending-sync count, last-build age, 24h tool error rate)
  so a single resource read answers "is this store degraded?".
- **Observability (P2 workflow integration + optional export, T17/T19/T20).**
  The telemetry surfaces are now part of the agent's own procedure (not just a
  passive dashboard): root `AGENTS.md` (+ the 4 synced guidance surfaces) add an
  after-task `cairn doctor` step whose FAILs feed `record_memory(type="mistake")`,
  and the PR/review/audit checklists gain a "does this change alter a
  fallback/performance path?" gate plus a Tier-1 "silent degradation" audit
  scope. `cairn report` prints a redacted diagnostic bundle (versions, the 8
  doctor checks, recent error events, config echo) for bug reports — every
  string field passes through `memory.privacy.strip_private_data`; never
  auto-uploads (`--json`, `--out`). Optional OTLP export: setting
  `CAIRN_OTEL_ENDPOINT` and installing the new `[otlp]` extra forwards cairn's
  local telemetry events as OpenTelemetry LogRecords — strictly lazy (zero
  module-level SDK imports; the default install stays OTel-free), best-effort
  (never blocks the flush), and fully off by default.

- **`cairn doctor` surfaces interrupted repo rebuilds.** The single-repo
  crash-recovery marker (`repo_build_state`, previously written and cleared
  but read by nothing) now feeds doctor's `freshness` check: a stale
  `building` row WARNs with a "re-run `cairn build --repo <repo>`" hint, so
  a repo left partial by a crashed rebuild is finally observable. The marker
  is also cleared only after the SCIP post-resolve import hook, so a crash
  during SCIP import stays detectable too.
- **`make ci-local` — clean-room CI replication via Apple's `container`.**
  `scripts/ci-local.sh` mirrors `.github/workflows/ci.yml` job-by-job
  (`test`, `test-all` for the 3.10–3.14 matrix, `security`, `typecheck`,
  `precommit`, `build`, `bench`) inside a bare Linux container driven by the
  Virtualization-framework `container` CLI — no Docker required. Host
  PATH/HOME/agent CLIs never leak in, so non-hermetic tests fail locally
  instead of on the runner. Venv/pip/pre-commit caches persist under
  `.cache/ci-local/`; `CI_LOCAL_ARCH=linux/amd64` runs the x86-64 image via
  Rosetta for GitHub-runner parity. (The old `make ci-local` target required
  Docker and ran only the pytest suite.)

### Changed
- **`mcp_server/metric_buffering` now writes through the shared telemetry sink.**
  `tool_metrics` recording is refactored onto `src/cairn/telemetry/` (one flush
  thread per process instead of two); the `tool_metrics` table shape and the
  flagless `cairn metrics` output are byte-for-byte unchanged, and the P0
  `tests/test_metrics.py` suite passes unmodified.

- **`cairn doctor` / `cairn report` no longer create a missing store.** Both
  are read-only diagnostics, but they opened the DB with `get_db`, which
  materializes missing files — so a typo'd `--db` produced an empty
  all-PASS "fresh install" (and a false-green exit code for gating agents)
  instead of an error. Both now stat the path first and degrade to their
  existing `schema` FAIL path with a "store not found" detail.
- **`cairn report` redacts absolute filesystem paths.** The privacy gate now
  collapses absolute local paths (POSIX, `~/…`, Windows) in every string
  field to `[PATH]/<basename>`, after the existing secret scrub — error
  text routinely embeds such paths via `str(exc)`, and the bundle is meant
  to be pasted into public issues. Workspace-relative paths and URL path
  portions survive.

### Fixed
- **Full 9-scope audit remediation (2026-08-14).** All 4 P1 and 25 P2 findings
  from the scope-audit pass, each ground-verified before fixing. Highlights:
  the atomic-swap no longer leaves the old WAL sidecar (silent build loss —
  found independently by two auditors); the build lock can no longer be
  defeated by its own error path (loser-unlink double-acquire); the knowledge
  layer redacts at the store chokepoint and session transcripts are stripped
  before queueing (4th incarnation of the codepath-divergence class, now
  closed at the store layer everywhere); `CAIRN_TELEMETRY=off` now provably
  stops ALL recording (build_runs + tool_metrics included);
  `semantic_backend` reports fusion/rerank execution truth with degraded
  flags; ANN no-index/stale-index states surface in doctor + the status
  health block; `cairn build --staging --repo` is rejected instead of
  destroying other repos; the stdio watchdog drains telemetry/metric/embed
  buffers before exit (was losing ≤30s of data per session end); the stray
  sweeper matches real editor spawn shapes, kills only lsof-verified
  db-holders, and re-verifies pids before SIGKILL; Ruby chained calls, PHP
  namespaced calls, and Kotlin class-body properties are recovered (dropped
  edges/symbols that golden fixtures had baked in); tool_metrics error
  messages are redacted at write time; store-chokepoint namespace guards
  protect CLI twins of guarded MCP ops; URI-embedded credentials are redacted.
  7 new BUGS.md entries record the classes; ~150 new tests across 6 commits.
- **Telemetry: fresh-DB init no longer emits a spurious lock-contention warning.**
  `note_contention("schema.migration")` was firing on every first-run `cairn build`,
  because the retained `transitive_edges.target_id` migration raises "duplicate
  column name" on a fresh DB (its `CREATE TABLE` already declares the column) and
  the contention call sat *before* the idempotent-duplicate check. That path is not
  lock contention — it now stays silent; only a genuine error (e.g. "database is
  locked") surfaces the warning. Regression guard:
  `tests/test_contention_visibility.py::test_fresh_db_init_emits_no_contention_warning`.

- **Telemetry history survives full rebuilds and staged builds.** The
  whole-file DB swap silently wiped `build_runs`/`events`/`tool_metrics` on
  every `cairn build`, resetting build trends, contention history, and
  doctor's freshness/tool-health windows. The analytics tables are now
  carried across the swap (fresh ids, time order preserved; `pending_sync`
  is deliberately dropped — a full rebuild recomputed that state).
- **OTLP export no longer silently loses data during a collector outage.**
  `BatchLogRecordProcessor` pops the batch before exporting and swallows
  exporter failures, so a dead endpoint popped every row as "exported".
  Export is now synchronous and failure-observing (a tracking wrapper
  around the OTLP/http exporter behind `SimpleLogRecordProcessor`): failed
  exports retain the rows for the next tick, an outage short-circuits the
  batch after one timeout (5s cap), and the OTLP side buffer is drained on
  normal MCP session end (previously up to 30s of export was lost per
  session).
- **Telemetry flush cycles are serialized.** The 30s daemon tick, the
  parent-death watchdog drain, `flush()` callers, and `atexit` can overlap;
  two concurrent flushes snapshotted the same rows, double-inserted them,
  and dropped never-written rows on the second pop. Both sinks (events,
  OTLP) now hold a flush mutex across snapshot → write → pop.
- **Session transcripts can no longer bypass redaction via the subprocess
  fallback.** With `CAIRN_LLM_BACKEND` set but the agent CLI unavailable,
  `SubprocessBackend.extract` fell back to `FileQueueBackend` carrying the
  raw transcript, persisting it unredacted into the task file — the exact
  codepath-divergence class the redaction audit claimed closed. `create_task`
  now applies the same `memory-*` privacy floor as `complete_task`.
- **`lock_contention` only means lock contention.** `note_contention` fired
  on any `OperationalError`, so FTS5-unavailable and missing-table failures
  (the reasons those `except` clauses exist) emitted phantom contention
  events that doctor's concurrency check and `metrics --contention` counted
  as real. Every swallow site now passes its caught exception and only
  genuinely lock-shaped errors ("database is locked/busy") emit the signal.
- **Telemetry event attrs enforce their policy at the coercion chokepoint.**
  Oversized strings nested in dicts/lists bypassed the truncation cap,
  `default=str` persisted `str(exc)` verbatim (routinely embedding secrets
  and absolute paths) into `events` — and, with OTLP on, onto the network —
  and no whole-blob bound existed. Attrs are now truncated recursively,
  stringified objects are scrubbed, and blobs over 4 KB drop to NULL (the
  event itself always survives).
- **CI flake: `test_to_file_writes_atomically`.** The test recomputed the
  expected content after the write while `to_markdown()` stamped
  `datetime.now()` at call time, racing the wall-clock second boundary
  (~1-in-1000 on any Python). The concept's timestamp is now pinned.

### Removed
- _Nothing yet._

> **Note (audit, post-T16):** an observability spec-conformance pass closed four
> gaps. `empty_result` now emits from the engine query layer named in the spec
> (`explore` and the `search_symbols` MCP-tool wrapper, in addition to
> `semantic_search`) and carries only `query_kind` (the non-spec `backend` attr
> was dropped; the per-backend view comes from correlating with
> `semantic_backend`); `cairn metrics --quality` scopes its empty-result rate to
> the semantic kind and adds an `empty by kind` breakdown. The `lock_contention`
> spec row was amended to match the implementation (`site: <module>.<function>`,
> no `wait_ms`). A `mypy` error in `doctor`'s freshness check was fixed
> (`bool(last_dt)` → `last_dt is not None`), and a stale cardinality-guard
> comment was corrected.

## [0.9.1] - 2026-08-13

> **Focus:** memory semantic recall + MCP lock-contention hardening (PR #23),
> plus a scope-audit pass that closed secret-redaction and dropped-edge gaps.

### Added
- **Memory + MCP server:** semantic recall pipeline and MCP server
  lock-contention hardening (#23). `recall_memory` now falls back to semantic
  similarity when lexical matching comes up empty; remaining MCP server
  lock-contention gaps are closed; orphaned memory embeddings are reaped at
  decay sites; lock behavior is covered by new tests.

### Changed
- **MCP server:** the background memory-embed flusher escalates to a `WARNING`
  log after repeated failures instead of retrying invisibly at `debug` level
  every 15s forever, so a chronically broken embed model is observable.
- **CI:** mypy is now advisory (step-level `continue-on-error`) so the ~60
  known type errors don't block PRs; new type regressions still print in the
  log for visibility.

### Fixed
- **Memory:** `evolve_memory` (the `memory_evolve` MCP tool / `cairn memory`
  evolve CLI) now redacts secrets from the new body before storage, matching
  `capture_memory`'s floor. A secret in an evolved body was previously
  persisted verbatim -- the same two-codepath divergence that once left
  `record_memory` unredacted.
- **Parsers:** constructor calls (`new Foo()`) in Java and PHP, and call edges
  inside class-field initializers in TypeScript and Java (`repo = createRepo()`),
  are now indexed as `calls` edges. They were previously dropped silently --
  the same edge-drop family as the `var-declarator` fix.
- **Agent install:** `cairn agents uninstall` now refuses to delete a directory
  that isn't cairn-scoped (named `cairn`). The `_rm_tree_if_cairn` helper
  promised this guard in its name but never enforced it; current callers were
  safe, but a broader path would have been wiped.

### Removed
- _Nothing yet._

## [0.9.0] - 2026-08-11

> **Focus:** backend language coverage — C#, C, and C++ parsers.

### Added
- **C# language support.** First-class tree-sitter indexing for C#: classes,
  interfaces, structs, records, enums with enum members, methods, constructors,
  properties, fields, call edges (invocation + `new` object creation), and
  `extends` / `implements` edges from base lists. Namespaces scope qualified
  names (`App.Models.User.Greet`). `using` directives captured as imports.
- **C language support.** First-class tree-sitter indexing for C: functions,
  structs (`typedef struct` → class symbol), call edges (including
  `p->method()` field-expression calls), and `#include` imports.
- **C++ language support.** First-class tree-sitter indexing for C++: classes
  with methods, namespaces (scope-qualified FQNs), inheritance edges
  (`base_class_clause`, with access-specifier skipping — `: public Engine`),
  template functions, field-expression calls (`obj->method()`) and
  template-function calls (`max_val<int>()`). A shared `_CFamilyParser`
  traversal drives both C and C++ (tree-sitter-cpp is a superset of
  tree-sitter-c), mirroring the TypeScript/JavaScript pattern.
- Parser modules now cover **14 languages**: Kotlin, Java, Python, Swift,
  TypeScript, JavaScript, Dart, Objective-C, Go, PHP, Ruby, C#, C, C++. The
  extension map gains `.cs/.csx`. C/C++ extensions (`.c/.cpp/.cc/.cxx/.hpp`)
  were already in the scanner; they now have parsers instead of silently
  failing with "No parser for c".

## [0.8.0] - 2026-08-11

> **Focus:** web-language coverage expansion — PHP and Ruby parsers, JSX
> component-reference tracking, and parser hardening from a deep audit.

### Added
- **PHP language support.** First-class tree-sitter indexing for PHP: classes,
  interfaces, traits, enums (PHP 8.1+) with enum cases, functions, methods,
  properties (including constructor property promotion), three call shapes
  (function / member / nullsafe `?->` / scoped `::`), `extends` / `implements`
  edges, and `require` / `include` / `use` imports (single, multi, and grouped
  `use Foo\{A, B};`). Uses the `php_only` grammar for clean declaration nodes.
- **Ruby language support.** First-class tree-sitter indexing for Ruby:
  modules, classes (including `class A::B` scope-resolution syntax), methods,
  singleton methods (`def self.foo`, `def obj.foo`), inheritance edges
  (including qualified superclasses `< ::Base`, `< User::Base`), call edges
  (zero-arg, parenless, safe-navigation `&.`, and block-bearing `each do ...
  end`), and `require` / `require_relative` / `load` imports.
- **JSX component-reference tracking.** The TypeScript / JavaScript parser now
  emits a `references` edge when it sees a capitalized JSX element
  (`<UserCard/>`), linking the enclosing component to the referenced
  component. This closes the largest blind spot in React / React Native
  codebases — previously JSX usage was invisible, so `cairn callers UserCard`
  missed roughly half of real inter-component relationships. Lowercase host
  tags (`<div>`, `<span>`) are skipped; member expressions (`<UI.Card/>`) and
  namespace names (`<foo:Bar/>`) resolve to the property / trailing name. The
  `references` kind resolves through the same tiers as `calls` and is surfaced
  by `get_callers` / `get_callees`, but is excluded from
  `STRUCTURAL_EDGE_KINDS` so `impact_analysis` / `trace_flow` treat a JSX ref
  as a usage, not a transitive call.
- Parser modules now cover **11 languages**: Kotlin, Java, Python, Swift,
  TypeScript, JavaScript, Dart, Objective-C, Go, PHP, Ruby. The extension map
  gains `.php/.phtml/.php3-5` and `.rb/.rbw`.

### Fixed
- **TypeScript / JavaScript variable-declarator initializer edges.** A
  pre-existing bug in `_handle_var_decl` dropped edges when a call,
  new-expression, or JSX element was the direct initializer of a variable
  declarator (`const x = getUser()`, `let r = new Foo()`,
  `const x = <UserCard/>`). The branch walked the value's children instead of
  visiting the value node itself, so the value's own type never dispatched
  through `_visit` and its edge was never emitted. Now fixed; verified no
  double-emission for nested calls. Predates the JSX work and affected calls
  too.
- **Ruby zero-argument and block-bearing calls.** The `argument_list` gate
  was based on a wrong assumption about the tree-sitter-ruby grammar: every
  `call` node is a real call (local-var reads are plain `identifier` nodes).
  `X.new`, `user.name`, `obj&.name`, `items.each do ... end`, and
  `users.map { }` now produce edges.
- **Ruby nested-class inheritance edges** no longer silently dropped. The
  `source_name` now uses the bare class name (matching the builder's same-file
  name lookup) instead of the qualified name.
- **PHP enums, nullsafe calls, and anonymous classes** now handled (previously
  dropped or polluting the enclosing scope). See the PHP parser module
  docstring for the full node-type reference.

## [0.7.1] - 2026-08-10

> **Focus:** a full-codebase audit remediation — 10 verified defects (P1–P10)
> and one architecture cleanup (A1), each with a regression test and a
> `docs/BUGS.md` entry. Plus a query-strategy decision tree for agents and a
> restructured, scannable bug registry.

### Added
- **Query decision-tree skill reference**
  (`agent_integration/skill/references/decision-tree.md`) — a top-down
  "what's your question → which tool" map with the full decision tree, the
  precise/fuzzy axis, and the pre-edit checklist. The `explore`-first
  workflow's escalation triggers (depth-2 blast radius → `impact_analysis`;
  unordered → `trace_flow`; pure L1 → `ask_compass`; token-based →
  `semantic_search`) are mirrored into all three agent-definition surfaces
  (SKILL.md, the AGENTS.md/CLAUDE.md generator, Cursor rules).

### Fixed
- **P1 — `record_memory` persisted secrets verbatim.** The hook auto-capture
  path redacted via `strip_private_data`; the primary MCP write path did not.
  Redaction now happens at the shared `capture_memory` chokepoint, so every
  caller (MCP, CLI, hook) gets it for free.
- **P2 — inverted parse-error telemetry.** `builder.py` counted the
  successful-parse payload slot (`r[5]`) instead of the error slot (`r[6]`),
  so a clean build reported ~100% errors. Off-by-one into the wrong tuple
  position.
- **P3 — `semantic_search` RRF fusion silently never ran.** The fusion path
  called `.get("id")` on a `sqlite3.Row` (no `.get()`), and the resulting
  `AttributeError` was swallowed by a bare `except Exception: pass` logged at
  `debug`. BM25 rows are now converted to `dict` at the boundary, and the
  degrade log moved to `warning` so a future regression is visible.
- **P4 — `_clear_repo` left `resolution='exact'` on orphaned edges.** After a
  single-repo rebuild, precise-mode queries (`get_callers`,
  `impact_analysis`) treated dangling, `target_id=NULL` edges as resolved.
  Now resets `resolution='unresolved'` in the same UPDATE, mirroring the
  incremental path.
- **P5 — failed SCIP import left partial writes uncommitted-but-persisted.**
  The builder caught the exception but never rolled back the shared
  connection; a later unrelated commit persisted the half-imported index.
  The except block now calls `conn.rollback()`.
- **P6 — schema "initialized" flag set before migration applied.** A
  mid-migration failure (disk full, locked file) permanently marked the path
  initialized, so every later `get_db()` skipped schema setup. The flag now
  sets after migration+commit succeed.
- **P7 — raw memory tier `concept_id` collision.** Raw captures used only
  `date + slug`, unlike every other tier which appends a uuid suffix. Two
  same-day captures with the same title overwrote each other. Now uses the
  same uuid-suffix scheme, keeping the date prefix for decay.
- **P8 — `knowledge_status` missing the scope guard `knowledge_delete` has.**
  `knowledge_status` could archive a compass/wiki/memory concept via a crafted
  `doc_id`. Now applies the same `knowledge/` namespace guard.
- **P9 — dead `depends_on` key in `knowledge_search`.** The cross-repo
  enrichment line read `deps.get("depends_on")`, but `cross_repo_deps`
  returns `dependencies` — so the documented "cross-repo bridge" line never
  printed, in both the MCP tool and the CLI. Fixed the key and value
  extraction (list of dicts, not strings) in both.
- **P10 — Swift modifier extraction polluted by attribute text.** The nested
  `modifiers`-node path appended any child text unconditionally; the
  direct-child path filtered through `SWIFT_MODIFIERS`. The tree-sitter Swift
  grammar nests `@available` attributes inside `modifiers`, so attribute text
  leaked into the modifier list. Both paths now filter through
  `SWIFT_MODIFIERS`, matching Java/Kotlin.
- **`IncompleteFieldDefinitionWarning` import hardened.** The 0.7.0 warning
  suppression imported the class unconditionally, but `pydantic_settings`
  2.14.x doesn't define it. The import is now defensive so the module loads
  on both old and new versions.

### Changed
- **A1 — removed dead `AppContext` lifespan scaffolding.** `AppContext` /
  `app_lifespan` were wired to thread config through requests, but no
  `@mcp.tool()` consumed the lifespan context — a half-finished refactor
  implying a contract that didn't hold. Removed the dataclass; kept
  `app_lifespan` as a minimal no-op (FastMCP requires it); documented
  `_conn()`/`_store()` as the single config source of truth. The
  more-correct fix (wire `ctx` through all 28 tools) is deferred to a
  separate spec.
- **`docs/BUGS.md` restructured** for scannability: an index table (date,
  slug, area, one-line symptom) as the navigation layer; a TL;DR line under
  each entry heading as a scan-anchor; entries stay chronological and
  append-only.

## [0.7.0] - 2026-08-10

> **Focus:** the verification contract becomes the product — a machine-checked
> promise that every `exact` edge is resolved, every doc symbol is
> graph-verified, and the LLM is never in the query path. Plus a memory moat
> (stale-flag + build hints) and a one-command rerank setup.

### Added
- **`cairn verify <doc-id>`** — run the deterministic critic on any single
  compass/wiki/memory concept and print the verdict (passed, errors, warnings,
  quality). Read-only; exit 1 on blocking errors, 2 on a missing doc, 0 on
  pass. The user-facing front to the critic gate that promise #2 rests on.
- **Structured critic verdict in MCP `generate_flow`** — `generate_flow` now
  appends a fenced `cairn-critic` JSON block (`passed` / `quality_score` /
  `errors` / `warnings`) after its prose, so agents can parse the verdict
  without regex. Additive: the existing response string is unchanged.
  `ask_compass`'s file-path-aware mode also surfaces the verdict when it loads
  a compass concept.
- **`cairn download-reranker`** — a dedicated command to pre-fetch the
  CrossEncoder reranker weights into the local HuggingFace cache, so the first
  reranked query isn't blocked on a download. `--model` overrides; default is
  `BAAI/bge-reranker-base`.
- **Memory-recall STALE flag.** `recall_memory` now derives a discrete `[STALE]`
  verdict from the `refs-verified` fraction it already computed: a memory is
  flagged when any cited backtick file/symbol no longer exists in the graph.
  Prose-only memories (no backtick refs) surface as `refs-verified=n/a (0 refs)`
  rather than a misleading `1.0`, so "nothing was checked" is no longer
  indistinguishable from "all refs passed." This is the recall-side analog of
  the critic gate — silent drift surfaced loudly.
- **Memory-triggered build hints.** `cairn update` now warns (advisory,
  non-blocking) when a reindex invalidates a memory — naming each stale memory
  by its full concept path so a user can open and review it. The warning uses
  the existing `display.warning` channel; `update` still exits 0. `update`
  gains a `--knowledge` flag so the bundle path is overridable.
- **"cairn on cairn" self-demo**, CI-gated under `-m core`
  (`tests/test_self_demo.py`): builds cairn's own source tree in an isolated
  temp DB and asserts `def`/`impact` return correct results for known symbols
  AND the resolution invariant (`exact ⟹ target_id IS NOT NULL`) holds on
  cairn's own code. The strongest dogfood — the verification contract
  demonstrated on the verifier.
- **False-positive-rate methodology post**
  (`docs/methodology-precise-vs-fuzzy.md`) — a standalone, install-free writeup
  of the precise-vs-fuzzy tradeoff with measured Python-corpus numbers: **82%
  of fuzzy results for common names are name-collision noise** that precise
  mode excludes. Linked from the README and `benchmarks.md`.

### Changed
- **Narrative repositioned to the verification contract.** README and
  `architecture.md` now lead with the 3-promise contract (every `exact` edge
  resolved; every doc symbol graph-verified by a deterministic critic; LLM
  never in the query path). Resolution-labeled edges are demoted to *evidence
  for promise #1*, not the headline — reflecting that the labeling scheme
  itself is commoditized, while the verified 5-layer stack + critic is owned
  white space.
- **Default rerank model is now `BAAI/bge-reranker-base`** (was
  `cross-encoder/ms-marco-MiniLM-L-6-v2`). The natural pair for the `bge-m3`
  embedder — same BAAI family. The change applies consistently to both
  download and reranking, so they can't split-brain.
- **Reranking auto-enables after `cairn download-reranker`.** A CLI process
  can't export an env var to its parent shell, so a successful download writes
  a persistent `~/.cairn/rerank_enabled` marker, and `rerank_enabled()` honors
  it as if `CAIRN_RERANK=1` were set. `CAIRN_RERANK=0` remains a hard kill
  switch (always wins, even with the marker). Before attempting a rerank, the
  configured model is checked for local cache presence; if missing/evicted,
  the query falls back to the hybrid (vector + BM25 + RRF) order rather than
  blocking on a download or crashing. `[semantic]` (sentence-transformers) is
  unchanged as the dependency.
- **SCIP importer BUGS entry corrected.** `docs/BUGS.md#scip-importer-fake-resolution`
  was doubly stale — its `Fix:` said "not yet implemented" and its
  `Prevention:` called the invariant test "future," when both the fix
  (`scip_importer.py:540-542`) and the tests existed. Rewritten to retract
  both claims and cite the guards. Added a "How the importer resolves edges"
  section to `docs/scip.md`.

### Fixed
- **Critic test coverage gap closed.** The critic's asymmetric contract (file
  refs block, symbol refs warn) was only tested for the file-rejects and
  real-refs-pass cases; the symbol-ref-warns path was missing. Added
  `test_unknown_symbol_ref_warns_not_blocks`. Added a partial-stale test
  (`0 < fraction < 1`) and an exception-path test for the STALE flag's
  `isinstance` guard, plus an invariant test that runs the `exact ⟹ target_id`
  SQL over importer-populated data (the hand-seeded invariant's own docstring
  admitted it couldn't catch the importer path).
- **CI: reranker success-path test was environment-dependent.**
  `test_rerank_resorts_by_fake_model_score` passed locally (a real model was
  cached) but failed in CI (no model) because the proactive cache guard
  short-circuited the fake-model path. Fixed by stubbing the cache check so
  the test is cache-independent.

### Removed
- _Nothing yet._

## [0.6.1] - 2026-08-07

> **Focus:** post-audit hardening — incremental reindex correctness repairs
> and a full code↔docs reconciliation. No user-facing API changes.

### Added
- **Vertical-rail flow UI for `cairn init` and `cairn build`.** Both commands
  now render a clack-style vertical rail — `┌` open, `│` spacers between
  groups, green `◆` step markers, animated sub-steps (`Scanning files`,
  `Parsing code`, `Resolving refs`, `Persisting graph`) that settle in place,
  and a guaranteed `└` close. Counts and durations in value positions are
  highlighted (bold blue), while paths and identifiers are left unstyled. The
  renderer is encoding-safe (ASCII fallback for cp1252/non-UTF-8 terminals),
  no-op-robust (reordered/dropped progress events can never raise from the
  renderer), and closes cleanly on every exit path including exceptions
  (`✗ … — failed`, `└ Failed`). `cairn build` keeps its existing summary
  panel; the rail replaces only its progress rendering. `cairn init` no
  longer passes `verbose=True` to `build_graph`, so the per-file `print()`
  noise the rail replaces is gone. `cairn build -v` renders the rail as plain
  sequential lines (no live region) so verbose output can't corrupt it.
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
- **Automatic SCIP index generation (bounded, opt-in).** When `cairn.json`
  declares a SCIP index for a language but the file is *missing*, and a known
  indexer is on `PATH`, `cairn build` runs the indexer once to produce it
  before importing — the one bounded exception to "cairn never generates
  indexes". An existing index is never rebuilt; a missing/failing/timeout
  indexer logs (under `-v`) and falls back to tree-sitter for that language, so
  generation never breaks the build. Known indexers: `scip-swift` (Swift),
  `scip-java` (Java **and** Kotlin — scip-kotlin is merged in and deprecated),
  `scip-typescript` (TypeScript/JS), `scip-python` (Python, npm pkg),
  `scip-go` (Go), `rust-analyzer scip` (Rust). Dart/PHP indexers exist but lack
  an `--output` flag, so they're generate-out-of-band only. See `docs/scip.md`
  § "Automatic generation".
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
- **SCIP / tree-sitter coexistence (replaces hybrid skip).** `cairn build` no
  longer skips tree-sitter for SCIP-covered languages. Both sources now run:
  tree-sitter parses every file (providing modifiers, body, inheritance edges,
  parent_scope that SCIP can't emit), then SCIP's exact-resolution edges and
  richer qualified_name are merged onto the tree-sitter symbol rows. The result
  is one row per symbol (`source='merged'`) carrying the strengths of both —
  no query-layer dedup needed. Tree-sitter's `implements`/`extends` edges
  survive the merge (SCIP has no inheritance role); its fuzzy `calls` edges are
  replaced by SCIP's exact ones. Matching by `(file_id, name, line_start)` with
  name normalization (e.g. `greet()` → `greet`).
  - **Per-language fallback:** when an indexer's symbol names don't match
    tree-sitter's (merge rate ~0, e.g. scip-swift's opaque USRs), coexistence
    duplicates are harmful — two disconnected graphs for the same logical
    symbol break `get_callers` for both name forms. The build detects the zero
    merge rate and reverts that language to pure-SCIP (removes tree-sitter
    symbols, keeps SCIP intact). Languages whose indexers have human-readable
    descriptors (scip-java, scip-typescript, scip-python, scip-go) keep the
    coexistence merge. Validated end-to-end against real scip-java 0.10.4
    (4/7 symbols merged) and scip-swift 0.1.2 (reverted to pure-SCIP).
- **`cairn upgrade` now uses PEP 440 version comparison and themed output.**
  The install/update flow no longer relies on naive string equality, so
  pre-release (`rc`), post-release (`.post1`), and local-segment (`+local`)
  versions compare correctly. Output switched from raw `click.echo` to the
  shared `display.*` helpers (`✓`/`⠿`/`!` glyphs) for consistency with the
  rest of the CLI. Install-method detection (uv/pipx/pip) and the `--check`
  flag are unchanged.
- **`packaging` is now a declared direct dependency** (was transitive via
  pip/setuptools). Required for the PEP 440 comparison in `cairn upgrade`.
- **README rewritten to lead with PyPI install.** The from-source build/wheel
  and bootstrap-script framing is retired from the user path; a new
  **Upgrading** section documents `cairn upgrade` / `--check` / `cairn version`.

### Fixed
- **SCIP `project_root` now handles `file://` URLs.** scip-swift writes
  `Metadata.project_root` as `URL(fileURLWithPath:).absoluteString`
  (`file:///abs/path`); the importer treated it as a relative path (joined it
  verbatim onto the workspace root) and silently mis-attributed every Swift
  document to the wrong repo id. The value is now scheme-stripped before
  resolution, so absolute paths, workspace-relative paths, and `file://` URLs
  (including the `file://localhost/` form) all resolve correctly.
  (Found by validating against real scip-swift output.)
- **SCIP importer no longer crashes on file-level occurrences.** A reference
  with no enclosing definition (top-level code in a Swift `main.swift`, a
  reference before any definition) used to insert `edges.source_id = NULL`,
  violating the NOT NULL FK and crashing the whole import. Now skipped,
  matching the tree-sitter path's "file-level call with no owning symbol"
  handling. (Found against real scip-swift output.)
- **SCIP `Document.language` now falls back to the file extension.** scip-java
  0.10.4 emits an empty `language` field for both Java and Kotlin documents;
  the importer stored `'scip'`, breaking the hybrid skip logic (which keys off
  `files.language`). The language is now derived from the extension when the
  document omits it, and normalized to lowercase (scip-swift emits `"Swift"`).
  (Found against real scip-java output.)
- **README documented `scripts/install.sh` flags that the script does not
  support.** The README listed `--agents`, `--scope`, and `--no-agents`;
  the script only supports `--semantic` and `--venv`. The from-source
  bootstrap block has been removed from the user-facing README.

### Removed
- _Nothing yet._

### Audit findings fixed (code↔docs reconciliation)
- **README/quickstart recommended `cairn update` as the first command**, but it
  parses nothing on a fresh clone (`git diff HEAD` is empty). Switched to
  `cairn build` for the first run.
- **README's `--client claude,cursor` install example failed** under Click
  `multiple=True`; fixed to repeated `--client`.
- **`opencode` was installable but not removable via `--client`** — added to
  both uninstall `click.Choice` lists.
- **Skill `cli-fallback.md` documented a non-existent `compass flow-gaps
  --as-workflow` flag** — removed.
- **Stale `_INSTRUCTIONS_BODY` install template** regenerated three wrong
  claims into every fresh AGENTS.md/CLAUDE.md (ANN backend "opt-in" vs the
  on-by-default code, `recall_memory` "substring-only" vs multi-token + semantic
  fallback, non-canonical layer breakdown). Synced with the committed AGENTS.md.
- **"wiki generate is critic-gated" was false** — it runs the critic but still
  writes. Scoped the safety guarantee to `compass generate` / `compass flow`.
- **MCP `get_callers`/`get_callees` documented with `kind=` filter the wrappers
  don't expose** — scoped to library/CLI layer.
- **"cairn update does not rebuild derived indexes" was false** (already fixed
  in code above) — docs updated.
- **`configuration.md` SCIP row claimed tree-sitter is "skipped"** — rewritten
  to the actual coexistence-then-merge model; stale `scip-kotlin` reference
  corrected to `scip-java`.
- **`architecture.md` mislabeled the 9th graph tool as "a blast-radius helper"**
  — it is `visualize_graph`. Also documented the C/C++ scanner gap.
- **Version drift**: README Status `v0.5.3` and SECURITY supported-versions
  `0.5.x` refreshed to `0.6.x`; `architecture.html` "26 tools" → "27 tools".

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
