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

- [ ] **T01 — Central logging config**
  - [ ] `cli/main.py`: configure `cairn` namespace in `main` callback; `CAIRN_LOG_LEVEL` (default WARNING); `-v/--verbose` → DEBUG; never root logger
  - [ ] `mcp_server/server.py::run()`: same env read for the server path
  - [ ] Verify: stdio stdout stays pure JSON-RPC (existing transport tests)
  - Acceptance: `CAIRN_LOG_LEVEL=DEBUG cairn build` shows debug lines; `cairn build -v` shows debug lines; default output unchanged.

- [ ] **T02 — ANN fallback one-time warning**
  - [ ] Add `warn_ann_fallback_once` mirroring `graph/embeddings.py:263` pattern
  - [ ] Call from `ann_index.try_load` failure + `semantic.py` brute-force branch; include reason class + `CAIRN_ANN_BACKEND` hint
  - Acceptance: with sqlite-vec unavailable, first semantic query logs one warning; subsequent queries silent; `CAIRN_ANN_BACKEND=off` (explicit choice) does not warn.

- [ ] **T03 — Lock-contention visibility**
  - [ ] `graph/schema.py::note_contention(site)` helper (P0 body: rate-limited `logger.warning`)
  - [ ] Wire at the 13 sites (spec §4.2 gap 3)
  - Acceptance: simulated `database is locked` produces ≤1 warning per site per process; swallow semantics unchanged (tests still pass).

- [ ] **T04 — Surface `parse_errors`**
  - [ ] `cairn status`: count + newest 5 (paths shortened via `_shorten`); silent when empty
  - Acceptance: fixture DB with parse errors shows the block; clean DB output identical to today.

- [ ] **T05 — `tests/test_metrics.py`** (new; the path is untested today)
  - [ ] `instrument`: ok path writes row; error path records status/error + re-raises; truncation applies at `CAIRN_MAX_RESULT_CHARS`
  - [ ] `_flush_metrics`: success drains; failure retains buffer; read-only skip
  - Acceptance: `pytest tests/test_metrics.py` green in core-marker runtime.

- [ ] **T06 — P0 docs**
  - [ ] `docs/configuration.md` "Server and runtime": `CAIRN_LOG_LEVEL`
  - [ ] `CHANGELOG.md` entry
  - Acceptance: doc drift sweep clean (memory: version refs across ~10 surfaces).

## Phase 1 — event pipeline, build history, doctor (0.10.0)

- [ ] **T07 — `src/cairn/telemetry/` module**
  - [ ] `sink.py`: buffered writer — deque + daemon flush (30s) + `atexit` + `CAIRN_TELEMETRY`/`CAIRN_READ_ONLY` gates + retention pruning (5000 events / 500 runs)
  - [ ] `events.py`: `emit(name, **attrs)`, `warn_once(key, logger, msg)`, `note_contention(site)`, catalog constants
  - [ ] Refactor `metric_buffering.py` onto the shared sink; T05 tests pass unmodified
  - Acceptance: `CAIRN_TELEMETRY=off` makes `emit` a no-op; sink failure never raises into callers.

- [ ] **T08 — Schema: `build_runs` + `events`**
  - [ ] `SCHEMA_SQL` additions per spec §6.2; indexes on `events(name, ts)`
  - [ ] Extend `tests/test_schema_versioning.py`: old DB upgrades in place
  - Acceptance: fresh + migrated DBs both pass `_apply_schema` idempotently.

- [ ] **T09 — Build-run instrumentation**
  - [ ] `builder.build_graph` summary → `build_runs` row; phase timings via `on_progress`
  - [ ] `cli/embed.py`, `sync`, `incremental` emit their `kind` rows
  - Acceptance: two builds → `cairn metrics --builds` shows both + resolution mix.

- [ ] **T10 — Semantic-path events**
  - [ ] `semantic_backend` (backend/fusion/rerank/ms-bucket/n-results-bucket) on return path; `empty_result` when 0
  - Acceptance: hash-backend + `CAIRN_ANN_BACKEND=off` fixture run produces correctly-tagged events.

- [ ] **T11 — Remaining emitters**
  - [ ] `_truncate_result` → `truncate_result`; `llm/tasks.py` → `task_lifecycle`; stray sweeper → `stray_swept`
  - Acceptance: each emitter covered by one focused test.

- [ ] **T12 — `cairn doctor`**
  - [ ] 8 checks per spec §6.5; PASS/WARN/FAIL; exit 0/1; `--json`
  - [ ] Fixtures: hash backend, ANN off, stale `pending_sync`, parse errors, contention events
  - Acceptance: each FAIL condition independently provable in tests; clean fixture exits 0.

- [ ] **T13 — `cairn metrics` extensions** — `--builds`, `--quality`, `--contention`; default output unchanged; `--json` for all three.
  - Acceptance: flags render from real tables; empty tables don't crash.

- [ ] **T14 — `cairn://status` health block** — degradations, pending-sync, last-build age, 24h error rate. No new MCP tool.
  - Acceptance: resource snapshot test includes health block; tool count still 27.

- [ ] **T15 — P1 tests** — `tests/test_telemetry.py`: sink flush/retry/cap/gates; emission points; doctor checks; **cardinality guard** (attr values ∈ enum sets, parametrized).
  - Acceptance: full suite green; `pytest -m core` runtime budget unchanged.

- [ ] **T16 — P1 docs + release** — `docs/configuration.md` (`CAIRN_TELEMETRY`), `docs/cli-reference.md` (doctor, metrics flags), CHANGELOG, version 0.10.0, release-checklist sweep (version-drift surfaces list from memory).

## Phase 2 — workflow integration & optional export

- [ ] **T17 — Workflow wiring** — AGENTS.md after-task doctor step + FAIL→`record_memory(mistake)`; review-checklist.md + PR template fallback-path checkbox; audit-checklist.md scope #9 "silent degradation" (Tier 1); mirror all guidance edits across the 4 surfaces (SKILL.md, `_common.py`, `cursor.mdc`, root AGENTS.md).
- [ ] **T18 — CI bench artifacts** — upload `cairn bench` results; advisory baseline comparison in PR comment.
- [ ] **T19 — Optional OTLP export** — `CAIRN_OTEL_ENDPOINT`; lazy `opentelemetry-sdk` import; `warn_once` when unset SDK; optional extra in pyproject; dependency-review clean.
- [ ] **T20 — `cairn report`** — redacted bundle (versions, doctor, recent errors, config echo via `strip_private_data`); never auto-uploads.

---

## Definition of done (feature-level)

1. Every degradation path in spec §4.2 emits ≥1 durable signal.
2. `cairn doctor` gates (exit code) and its checks are test-proven.
3. Build history and quality metrics queryable via `cairn metrics`.
4. `CAIRN_TELEMETRY=off` provably silences everything (test).
5. No measurable tool-latency regression (bench, telemetry on vs off).
6. Docs current across all version-drift surfaces; workflow docs wired; memories recorded (`record_memory` for design decisions).
