# Plan: Observability & Telemetry

Companion to [spec.md](spec.md) — implementation phases, sequencing, and
file-level changes. Track progress in [tasks.md](tasks.md).

Phases are independently shippable: P0 is log-lines and surfacing only (no
schema change); P1 adds the event pipeline and doctor; P2 is optional
integration. Each phase lands as its own PR through the standard contribution
workflow (branch → pre-commit → conventional commit → PR with audit checklist
→ CI → merge → `cairn update` + `record_memory`).

---

## Phase 0 — Close today's worst holes (no schema change)

**Goal:** every known silent degradation emits at least a log line; hidden
data becomes visible. Days of work, zero risk to storage.

1. **Central logging config**
   - `cli/main.py`: configure the `cairn` logging namespace in the `main`
     group callback — `CAIRN_LOG_LEVEL` env (default `WARNING`), `-v/--verbose`
     → `DEBUG`. Never touch the root logger (stdio stdout discipline;
     FastMCP already pins its own level at `mcp_server/_server_core.py:75`).
   - `mcp_server/server.py::run()`: same env-var read for the server path.
2. **ANN fallback warning** — add `warn_ann_fallback_once` (mirror
   `warn_hash_fallback_once`, `graph/embeddings.py:263`) and call it from:
   - `graph/ann_index.py::try_load` failure branch
   - `graph/semantic.py:139` brute-force fallback branch
   - Message includes the reason class (not installed / load failed) and the
     `CAIRN_ANN_BACKEND=off` escape hatch.
3. **Lock-contention log lines** — add `logger.warning` (rate-limited via
   `warn_once`-style guard) at the 13 `except sqlite3.OperationalError` sites
   listed in spec §4.2. Keep the swallow semantics; make it non-silent.
   Introduce the shared helper `graph/schema.py::note_contention(site)` so P1
   can swap the body for a counter without touching call sites again.
4. **Surface `parse_errors`** — add a block to `cairn status`
   (`cli/system.py`): count + newest 5 messages; keep the output calm when
   empty.
5. **Tests** — create `tests/test_metrics.py` (currently missing): cover
   `instrument` (ok/error/truncation), `_flush_metrics` retry-on-lock
   behavior, read-only skip. Existing `tests/conftest.py::fresh_db` is the
   fixture.
6. **Docs** — `docs/configuration.md` "Server and runtime": `CAIRN_LOG_LEVEL`.
   `CHANGELOG.md` entry.

## Phase 1 — Event pipeline, build history, doctor

**Goal:** the core of spec §6. Ships as 0.10.0.

1. **`src/cairn/telemetry/` module** (spec §6.1)
   - `sink.py`: generalized buffered writer (deque + daemon flush + `atexit`
     + `CAIRN_TELEMETRY`/`CAIRN_READ_ONLY` gates + retention pruning).
   - `events.py`: `emit()`, `warn_once()`, `note_contention()`,
     event-name constants (spec §6.4 catalog).
   - Refactor `mcp_server/metric_buffering.py` onto the shared sink; behavior
     and `tool_metrics` shape unchanged (its tests from P0 must still pass
     unmodified).
2. **Schema** — `build_runs` + `events` tables in `SCHEMA_SQL`
   (`graph/schema.py`), `CREATE TABLE IF NOT EXISTS` (no `MIGRATIONS` entry —
   same pattern as `tool_metrics`). Additive; old DBs upgrade on connect.
3. **Instrumentation**
   - `graph/builder.py`: persist a `build_runs` row from the summary dict
     (`:654-666`); capture phase timings from the existing `on_progress`
     callbacks (`cli/core.py:262`). `cli/embed.py` / `cli/system.py::sync`
     / `graph/incremental.py` emit `kind='embed'|'sync'|'incremental'` rows.
   - `graph/semantic.py`: emit `semantic_backend` (+ `empty_result` when 0
     results) on the return path.
   - `metric_buffering._truncate_result`: emit `truncate_result`.
   - `llm/tasks.py`: emit `task_lifecycle` on claim/complete/drop/revise.
   - `mcp_server/server.py::_install_stray_sweeper`: emit `stray_swept`.
   - Swap P0's contention log-lines to `note_contention()` → event + log.
4. **`cairn doctor`** (`cli/system.py`, spec §6.5) — 8 checks, PASS/WARN/FAIL,
   exit code 0/1, `--json`. Unit-test each check against fixture DBs (hash
   backend forced via `hash_backend` fixture; stale graph via unflushed
   `pending_sync` row; ANN degraded via `CAIRN_ANN_BACKEND=off`).
5. **`cairn metrics` extensions** — `--builds`, `--quality`, `--contention`
   flags (default aggregation output unchanged).
6. **`cairn://status` health block** (`mcp_server/_server_core.py:177`).
7. **Tests** — `tests/test_telemetry.py`: sink flush/retry/cap/gates;
   build-run persistence; event emission points; doctor checks; cardinality
   guard (attr values ∈ enum sets).
8. **Docs** — `docs/configuration.md` (`CAIRN_TELEMETRY`);
   `docs/cli-reference.md` (doctor + metrics flags); `CHANGELOG.md`;
   version bump to 0.10.0 per release checklist.

## Phase 2 — Workflow integration & optional export

**Goal:** telemetry feeds the agent workflow (spec G5); power users can
export.

1. **Workflow wiring**
   - Root `AGENTS.md` after-task section: run `cairn doctor` after tasks that
     touch perf/fallback paths; doctor FAILs feed `record_memory(type="mistake")`.
   - `docs/review-checklist.md` + `.github/PULL_REQUEST_TEMPLATE.md`: new
     checkbox — "does this change alter a fallback/performance path? what
     signal exposes the degradation?"
   - `docs/audit-checklist.md`: scope #9 "silent degradation" (Tier 1) —
     enumerate fallback paths in the audited area, verify each emits.
   - **Mirror rule:** any guidance edit propagates to all four surfaces
     (SKILL.md, `agent_install/_common.py` template, `cursor.mdc`, root
     AGENTS.md).
2. **CI** — persist `cairn bench` results as PR artifacts; compare against a
   rolling baseline (advisory first, gate later if stable).
3. **Optional OTLP export** (lazy, off by default): `CAIRN_OTEL_ENDPOINT` →
   lazy-import `opentelemetry-sdk`; absent SDK = skip with `warn_once`. Never
   a hard dependency; pip-audit/dependency-review still apply to the optional
   extra.
4. **`cairn report`** — redacted diagnostic bundle for GitHub issues:
   versions, doctor output, recent error events, config echo with paths
   stripped via `memory/privacy.strip_private_data`. Explicit user action
   only — nothing auto-uploads.

## Sequencing & dependencies

```
P0.1 logging ─┐
P0.2 ann warn ─┼─ independent of each other; P0.3 helper is reused by P1.3
P0.3 locks    ─┘
P0.4 parse_errors ─ independent
P0.5 tests    ─ before P1 touches metric_buffering

P1.1 telemetry module ─→ P1.2 schema ─→ P1.3 instrumentation ─→ P1.4 doctor ─→ P1.5/6 surfaces
P1.7 tests run throughout

P2.* — any order after P1; P2.3 (OTLP) last.
```

## Testing strategy

- **Sink correctness:** flush-success drops buffer; flush-failure retains;
  `CAIRN_TELEMETRY=off` no-ops; read-only skips; retention prunes.
- **Emission points:** each instrumented site emits the expected event with
  enum-valid attrs (parametrized over the catalog).
- **Doctor:** fixture-driven PASS/WARN/FAIL per check; exit codes.
- **No-hot-path regression:** bench suite compares tool latency with
  telemetry on vs off (within noise).
- **Migration safety:** old-shape DB (pre-tables) upgrades in place;
  `test_schema_versioning.py` pattern extended.

## Rollout & compat

- New tables are additive; no data migration; old binaries reading a new DB
  ignore the tables (forward-safe: unknown tables don't affect queries).
- `CAIRN_TELEMETRY=off` fully disables writes for users who object.
- SSE read-only daemons never write telemetry (gate mirrors
  `metric_buffering`).
