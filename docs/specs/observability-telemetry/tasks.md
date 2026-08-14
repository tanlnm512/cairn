# Tasks: Observability & Telemetry

Tracking file for [spec.md](spec.md) / [plan.md](plan.md). Check boxes as
work lands; each PR references the task IDs it completes.

## Index

| ID | Task | Phase | Priority | Depends on |
|----|------|-------|----------|------------|
| T01 | Central logging config (`CAIRN_LOG_LEVEL`, `-v`) | P0 | high | — |
| T02 | ANN fallback one-time warning | P0 | high | — |
| T03 | Lock-contention log lines + `note_contention()` helper | P0 | high | — |
| T04 | Surface `parse_errors` in `cairn status` | P0 | high | — |
| T05 | `tests/test_metrics.py` for instrument/flush path | P0 | high | — |
| T06 | P0 docs (configuration.md, CHANGELOG) | P0 | medium | T01-T04 |
| T07 | `telemetry/` module (sink + events) | P1 | high | T05 |
| T08 | `build_runs` + `events` schema | P1 | high | T07 |
| T09 | Builder/build-run instrumentation (build/sync/embed/incremental) | P1 | high | T08 |
| T10 | Semantic-path events (`semantic_backend`, `empty_result`) | P1 | high | T08 |
| T11 | Truncation + task-lifecycle + stray-sweeper events | P1 | medium | T08 |
| T12 | `cairn doctor` command | P1 | high | T08-T11 |
| T13 | `cairn metrics --builds/--quality/--contention` | P1 | medium | T08 |
| T14 | `cairn://status` health block | P1 | medium | T12 |
| T15 | P1 tests (`test_telemetry.py`, doctor fixtures, cardinality guard) | P1 | high | T07-T12 |
| T16 | P1 docs + 0.10.0 release prep | P1 | medium | T12-T15 |
| T17 | Workflow wiring (AGENTS.md, review/scope checklists, 4-surface mirror) | P2 | high | T12 |
| T18 | CI bench artifacts + baseline comparison | P2 | medium | — |
| T19 | Optional OTLP export (`CAIRN_OTEL_ENDPOINT`, lazy) | P2 | low | T07 |
| T20 | `cairn report` redacted diagnostic bundle | P2 | low | T12 |

---

## Phase 0 — close today's worst holes (no schema change)

- [x] **T01 — Central logging config**
  - [x] `cli/main.py`: configure `cairn` namespace in `main` callback; `CAIRN_LOG_LEVEL` (default WARNING); `-v/--verbose` → DEBUG; never root logger
  - [x] `mcp_server/server.py::run()`: same env read for the server path
  - [x] Verify: stdio stdout stays pure JSON-RPC (existing transport tests)
  - Acceptance: `CAIRN_LOG_LEVEL=DEBUG cairn build` shows debug lines; `cairn -v build` shows debug lines (the `-v` group flag must precede the subcommand — click rejects `cairn build -v`; `CAIRN_LOG_LEVEL=DEBUG` is the position-independent form); default output unchanged.

- [x] **T02 — ANN fallback one-time warning**
  - [x] Add `warn_ann_fallback_once` mirroring `graph/embeddings.py:263` pattern
  - [x] Call from `ann_index.try_load` failure + `semantic.py` brute-force branch; include reason class + `CAIRN_ANN_BACKEND` hint
  - Acceptance: with sqlite-vec unavailable, first semantic query logs one warning; subsequent queries silent; `CAIRN_ANN_BACKEND=off` (explicit choice) does not warn.

- [x] **T03 — Lock-contention visibility**
  - [x] `graph/schema.py::note_contention(site)` helper (P0 body: rate-limited `logger.warning`)
  - [x] Wire at the 13 sites (spec §4.2 gap 3)
  - Follow-up fix: gated `note_contention("schema.migration")` to the genuine-error branch only — the idempotent "duplicate column" path (fired by the retained `transitive_edges.target_id` migration on every fresh DB) is not contention and was producing a false-positive WARNING on first-run init. Regression: `test_fresh_db_init_emits_no_contention_warning`.
  - Acceptance: simulated `database is locked` produces ≤1 warning per site per process; swallow semantics unchanged (tests still pass).

- [x] **T04 — Surface `parse_errors`**
  - [x] `cairn status`: count + newest 5 (paths shortened via `_shorten`); silent when empty
  - Acceptance: fixture DB with parse errors shows the block; clean DB output identical to today.

- [x] **T05 — `tests/test_metrics.py`** (new; the path is untested today)
  - [x] `instrument`: ok path writes row; error path records status/error + re-raises; truncation applies at `CAIRN_MAX_RESULT_CHARS`
  - [x] `_flush_metrics`: success drains; failure retains buffer; read-only skip
  - Acceptance: `pytest tests/test_metrics.py` green in core-marker runtime.

- [x] **T06 — P0 docs**
  - [x] `docs/configuration.md` "Server and runtime": `CAIRN_LOG_LEVEL`
  - [x] `CHANGELOG.md` entry
  - Acceptance: doc drift sweep clean (memory: version refs across ~10 surfaces).

## Phase 1 — event pipeline, build history, doctor (0.10.0)

- [x] **T07 — `src/cairn/telemetry/` module**
  - [x] `sink.py`: buffered writer — deque + daemon flush (30s) + `atexit` + `CAIRN_TELEMETRY`/`CAIRN_READ_ONLY` gates + retention pruning (5000 events / 500 runs)
  - [x] `events.py`: `emit(name, **attrs)`, `warn_once(key, logger, msg)`, `note_contention(site)`, catalog constants
  - [x] Refactor `metric_buffering.py` onto the shared sink; T05 tests pass unmodified
  - Acceptance: `CAIRN_TELEMETRY=off` makes `emit` a no-op; sink failure never raises into callers.

- [x] **T08 — Schema: `build_runs` + `events`**
  - [x] `SCHEMA_SQL` additions per spec §6.2; indexes on `events(name, ts)`
  - [x] Extend `tests/test_schema_versioning.py`: old DB upgrades in place
  - Acceptance: fresh + migrated DBs both pass `_apply_schema` idempotently.

- [x] **T09 — Build-run instrumentation**
  - [x] `builder.build_graph` summary → `build_runs` row; phase timings via `on_progress`
  - [x] `cli/embed.py`, `sync`, `incremental` emit their `kind` rows
  - Acceptance: two builds → `cairn metrics --builds` shows both + resolution mix.

- [x] **T10 — Semantic-path events**
  - [x] `semantic_backend` (backend/fusion/rerank/ms-bucket/n-results-bucket) on return path; `empty_result` when 0
  - Acceptance: hash-backend + `CAIRN_ANN_BACKEND=off` fixture run produces correctly-tagged events.

- [x] **T11 — Remaining emitters**
  - [x] `_truncate_result` → `truncate_result`; `llm/tasks.py` → `task_lifecycle`; stray sweeper → `stray_swept`
  - Gap closed: the `ann_fallback`/`hash_fallback` emit paths were also wired into the existing `warn_ann_fallback_once` / `warn_hash_fallback_once` helpers (P1.3 gap), and `lock_contention` is emitted from `note_contention` alongside the unconditional WARNING.
  - Acceptance: each emitter covered by one focused test.

- [x] **T12 — `cairn doctor`**
  - [x] 8 checks per spec §6.5; PASS/WARN/FAIL; exit 0/1; `--json`
  - [x] Fixtures: hash backend, ANN off, stale `pending_sync`, parse errors, contention events
  - Acceptance: each FAIL condition independently provable in tests; clean fixture exits 0.

- [x] **T13 — `cairn metrics` extensions** — `--builds`, `--quality`, `--contention`; default output unchanged; `--json` for all three.
  - Acceptance: flags render from real tables; empty tables don't crash.

- [x] **T14 — `cairn://status` health block** — degradations, pending-sync, last-build age, 24h error rate. No new MCP tool.
  - Acceptance: resource snapshot test includes health block; tool count still 27.

- [x] **T15 — P1 tests** — `tests/test_telemetry.py`: sink flush/retry/cap/gates; emission points; doctor checks; **cardinality guard** (attr values ∈ enum sets, parametrized).
  - The cardinality guard asserts every emitter's attr values stay in their declared enum sets; the `ann_fallback`/`hash_fallback`/`lock_contention` emit gaps it pins were the P1.3 close.
  - Acceptance: full suite green; `pytest -m core` runtime budget unchanged.

- [x] **T16 — P1 docs + release** — `docs/configuration.md` (`CAIRN_TELEMETRY`), `docs/cli-reference.md` (doctor, metrics flags), CHANGELOG, version 0.10.0, release-checklist sweep (version-drift surfaces list from memory).
  - Docs done: `CAIRN_TELEMETRY` added to configuration.md, `cairn doctor` (8 checks) + `metrics --builds/--quality/--contention` documented in cli-reference.md, P1 consolidated under `[Unreleased]`. The **0.10.0 version bump + `make release`/`cz bump` + release-checklist sweep is deferred** to a separate release session (out of this docs-only task's scope); version-drift surfaces reported there: `pyproject.toml:version` (×2: `[project]` + `[tool.commitizen]`), `src/cairn/__init__.py:__version__`, plus hand-maintained `README.md` (v0.9.1) and `SECURITY.md` (`0.9.x` line).

## Phase 2 — workflow integration & optional export

- [x] **T17 — Workflow wiring** — AGENTS.md after-task doctor step + FAIL→`record_memory(mistake)`; review-checklist.md + PR template fallback-path checkbox; audit-checklist.md scope #9 "silent degradation" (Tier 1); mirror all guidance edits across the 4 surfaces (SKILL.md, `_common.py`, `cursor.mdc`, root AGENTS.md).
  - Done 2026-08-14: doctor step is after-task step #2 (between `cairn update` and `record_memory`, since a FAIL feeds the mistake memory); scope #9 landed as Tier 1 (Tier-1 note + "Before a release" trigger updated); the doctor step is byte-identical across AGENTS.md / `_common.py` / `cairn.mdc` and condensed to SKILL.md's terse idiom.
- [x] **T18 — CI bench artifacts** — upload `cairn bench` results; advisory baseline comparison in PR comment.
  - Done 2026-08-14: new independent `bench` job in ci.yml (parallel to test→build, every bench step `continue-on-error` so it can never redden CI). `cairn bench --json` already existed; a UTC `timestamp` was added to the payload (minimal, additive; human table unchanged). Rolling baseline via actions/cache restore-keys + `.github/scripts/bench_compare.py` (reuses `compare_reports`, 25% advisory threshold for shared-runner noise) writing the step summary + a find-or-update PR comment on a hidden marker; results uploaded as the `bench-result` artifact.
- [x] **T19 — Optional OTLP export** — `CAIRN_OTEL_ENDPOINT`; lazy `opentelemetry-sdk` import; `warn_once` when unset SDK; optional extra in pyproject; dependency-review clean.
  - Done 2026-08-14: `telemetry/otel.py` — emit-time tap into an otel-owned side deque drained by a flusher registered with the shared sink (`register_flusher`), so OTLP never steals rows from the SQLite flush (DB stays source of truth) and inherits `_flush_all`'s exception isolation. ALL `opentelemetry` imports live inside the first-use function behind the env gate (grep-verified: zero module-level OTel imports in src/); missing SDK → one `warn_once` + one-way disable. Events map to OTel LogRecords (body=name, attributes=attrs + session_id, Resource service.name=cairn). `[otlp]` optional extra in pyproject (versions untouched); `CAIRN_OTEL_ENDPOINT` documented in configuration.md; 12 tests, zero real network.
  - Remediated 2026-08-14 (post-review): export switched from `BatchLogRecordProcessor` (which pops the batch before exporting and swallows exporter failures — a dead collector silently lost every row) to synchronous export via `SimpleLogRecordProcessor` + a result-tracking exporter wrapper; failed exports now retain the batch, an outage short-circuits after one 5s timeout, and the buffer is drained on session end. Flush cycles are serialized (`_FLUSH_LOCK`) in both sinks.
- [x] **T20 — `cairn report`** — redacted bundle (versions, doctor, recent errors, config echo via `strip_private_data`); never auto-uploads.
  - Done 2026-08-14: `report --json/--out` reuses `_run_doctor` + `_check_config`'s knob list; purely additive to `cli/system.py` (+286, 0 deletions). Known limitation (documented in the command docstring + cli-reference): `strip_private_data` redacts secret shapes and `<private>` tags but does NOT scrub file paths (e.g. shortened paths inside doctor's parse-errors detail) — users should still review before pasting publicly. Integration hardening: `json.loads(result.output)` → `result.stdout` across test_doctor/test_metrics_extensions/test_report (click's `Result.output` interleaves stderr; a leaked DEBUG log line broke JSON parsing in full-suite order while real-world stdout stays pure).
  - Remediated 2026-08-14 (post-review): the path limitation is FIXED — the privacy gate now routes every string through `_redact_paths` (absolute POSIX/`~`/Windows paths → `[PATH]/<basename>`, relative + URL paths survive), so the "review before pasting" caveat is gone.

---

## Definition of done (feature-level)

1. Every degradation path in spec §4.2 emits ≥1 durable signal.
2. `cairn doctor` gates (exit code) and its checks are test-proven.
3. Build history and quality metrics queryable via `cairn metrics`.
4. `CAIRN_TELEMETRY=off` provably silences everything (test).
5. No measurable tool-latency regression (bench, telemetry on vs off).
6. Docs current across all version-drift surfaces; workflow docs wired; memories recorded (`record_memory` for design decisions).
