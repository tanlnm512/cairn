# Spec: Observability & Telemetry

- **Status:** draft
- **Branch:** `feat/observability-telemetry`
- **Date:** 2026-08-13
- **Target milestone:** P0 → 0.9.x patch; P1 → 0.10.0
- **Research basis:** deep-research pass 2026-08-13 (OTel signal model, July 2026
  MCP spec RC adopting OTel / deprecating protocol-level logging, Go
  transparent-telemetry norms) + full codebase audit (below)

---

## 1. Problem statement

Cairn's failure modes are almost never crashes — they are **silent quality
degradations**: semantic search permanently degrades to a brute-force scan with
no trace; resolver precision drifts per build but no trend is ever kept; parse
errors are written to a table no command reads; results get truncated with no
count. The v0.9.x lock-contention and stdio-leak bugs were fixed essentially
blind, and nothing today can verify those fixes hold in the field.

Meanwhile the product's differentiators — resolution-aware querying, the
explore-first escalation ladder, provenance labels — are *quality claims* that
nothing measures over time. The hand-maintained "Tool Quirks" table in root
`AGENTS.md` is manual telemetry; this spec automates its collection.

Two external facts make this well-timed:

1. The MCP spec release candidate (July 2026) **deprecated protocol-level
   logging** (`notifications/message`) in favor of servers emitting
   OpenTelemetry-shaped data. Aligning now is cheap; retrofitting later is a
   redesign.
2. Cairn crossed the complexity threshold (27 tools × 5 layers, stdio + SSE
   daemon, task queue, multi-process SQLite) where behavior is no longer fully
   predictable by reading code.

## 2. Goals

- **G1 — Make silent degradation loud.** Every fallback/degradation path
  (ANN→brute-force, hash-embed, lock-contention swallow, truncation) emits at
  least one durable signal.
- **G2 — Build history.** Indexing runs persist a `build_runs` row; resolver
  precision becomes a trend, not a forgotten panel.
- **G3 — One consumable health answer.** `cairn doctor` reports system health
  with an exit code agents can gate on; the `cairn://status` MCP resource
  carries the same health block.
- **G4 — Agent-consumable quality signals.** Empty-result rate, backend
  provenance mix, and truncation rate are measurable per tool and per session
  (the seed of automated "Tool Quirks").
- **G5 — Workflow integration.** Doctor/status wired into the agent workflow
  (AGENTS.md after-task), the review checklist, and the scope-audit checklist.

## 3. Non-goals

- **No network telemetry.** Nothing phones home, ever, by any default. Remote
  sharing (Go-style explicit opt-in) is explicitly out of scope for all phases.
- **No payload capture.** No code content, query text, tool arguments, or tool
  results are recorded. Metadata only (names, durations, counts, enum tags,
  bounded cardinality).
- **No hard OpenTelemetry dependency.** The OTel SDK is never a required
  install. Signals are OTel-*shaped* (name + attributes); an optional OTLP
  exporter (P2) lazy-imports the SDK only when explicitly configured.
- **No new MCP tool.** Health travels through the existing `cairn://status`
  resource and the CLI, preserving the 27-tool contract
  (`_EXPECTED_TOOL_COUNT` in `mcp_server/server.py`) and the four synced
  guidance surfaces.
- **No distributed tracing infrastructure.** "Session as trace" (events
  grouped by `session_id`, ordered by time) is the P1 tracing model.

## 4. Current-state audit (verified 2026-08-13)

### 4.1 What exists and works

| Asset | Location | State |
|---|---|---|
| Per-tool-call metrics | `tool_metrics` table (`graph/schema.py:233`) + `@instrument` decorator (`mcp_server/metric_buffering.py:162`) | Times all 27 tools; ok/error + truncated error message; buffered 30s flush; read-only-mode skip; `atexit` flush |
| Buffered async sink pattern | `mcp_server/metric_buffering.py`, `mcp_server/embed_buffering.py` | Proven twice; deque(maxlen=2000) + daemon thread + best-effort commit |
| Parse-error capture | `parse_errors` table (`graph/schema.py:124`), written by `graph/builder.py:901` and `graph/incremental.py:156` | Written; **read by zero CLI/MCP commands** |
| Skip audit | `skipped_files` → `stats["skipped_by_reason"]` → `cairn stats`/`status` | Surfaced |
| Staleness detection | `pending_sync` → staleness banner (`mcp_server/_server_core.py:131`) + `cairn status` + `cairn://status` resource (`:177`) | Surfaced (proto agent-facing observability) |
| Hash-embed fallback warning | `warn_hash_fallback_once` (`graph/embeddings.py:263`), process-global guard | The one-time-warning pattern to replicate |
| Metrics viewer | `cairn metrics` (`cli/system.py:15`) | Aggregates only: calls / avg ms / errors / err% |
| Bench harness | `bench/timing.py`, `bench/perf_suite.py`, `bench/scaling_suite.py` | Offline regression tracking, not runtime |
| Session labeling | `CAIRN_SESSION` env stamps `tool_metrics.session_id` (`metric_buffering.py:151`) | Correlation-ID seed |

### 4.2 Gaps (each verified in code)

| # | Gap | Evidence |
|---|---|---|
| 1 | **No central logging config** — no `basicConfig`/handler anywhere; 16 module loggers hit an unconfigured root; no `--verbose`; stdio server "log" is hand-stamped `print(..., file=sys.stderr)` | `mcp_server/server.py` print rail; FastMCP pins `log_level="WARNING"` (`_server_core.py:75`) to avoid clobbering root |
| 2 | **ANN fallback completely silent** — `try_load()` returns False on *any* exception with no log; `ann_query()` returns None with no log; `semantic.py` falls through to brute-force | `graph/ann_index.py:58-75,122-148` (module has no logger at all); `graph/semantic.py:132-139` |
| 3 | **Lock contention swallowed silently at 13 sites** | `ann_index.py:146`; `memory/promotion.py:115,144`; `graph/builder.py:957`; `graph/embeddings.py:698`; `graph/stats.py:53`; `graph/lexical.py:161`; `graph/schema.py:346,398`; `graph/incremental.py:153,172,209`; `compass/router.py:186` |
| 4 | **Build history discarded** — summary dict (repos/files/symbols/edges/skipped/resolution/scip) assembled, logged, returned, printed, forgotten | `graph/builder.py:654-666` |
| 5 | **No engine/CLI-side metrics** — only MCP tool calls are timed; `cairn build/sync/embed` record nothing | ad-hoc `t0 = time.time()` display-only in `cli/core.py`, `cli/embed.py` |
| 6 | **No doctor** — health smeared across `status`/`stats`/`metrics`; nothing detects degradation (ANN loaded? hash backend active?) | `cli/system.py` |
| 7 | **Semantic provenance evaporates** — `semantic` / `bm25` / `fused(...)` / `(hash backend)` strings + `reranked` bool returned per result, counted nowhere | `graph/semantic.py:121-122,174,214-229` |
| 8 | **Truncation untracked** — `_truncate_result` runs centrally, never counted | `mcp_server/metric_buffering.py:37-51` |
| 9 | **Task queue has no lifecycle history** — claim/complete/revise/drop states live in OKF docs; no aggregate | `llm/tasks.py:107,185` (`MAX_REVISE_CYCLES=3`, `CLAIM_STALE_SECONDS=3600`) |
| 10 | **Stray-sweeper kills uncounted** — the stdio-leak remediation works invisibly | `mcp_server/server.py:249,266` |
| 11 | **No telemetry on/off switch or docs** — ~30 `CAIRN_*` env vars, none govern telemetry | `docs/configuration.md` |

### 4.3 Instrumentation seams (where data funnels)

1. `instrument` decorator (`mcp_server/metric_buffering.py:162`) — all 27 MCP tools
2. `builder.build_graph` summary + `on_progress` phase callbacks (`cli/core.py:262`) — phase timing nearly free
3. `schema.get_db` (`graph/schema.py:410`) / `build_lock` (`:507`) — every connection; chokepoint for a contention counter
4. `semantic.semantic_search` (`graph/semantic.py`) — where ANN / fusion / rerank / hash branch
5. `llm/tasks.py` claim/complete — queue lifecycle
6. `cli/display.py` — single rendering layer for human-facing output

## 5. Design principles (invariants)

1. **Local-only by default; zero network.** Master switch `CAIRN_TELEMETRY=off`
   disables all recording. Default is on-but-local (the Go transparent-telemetry
   posture).
2. **OTel-shaped, not OTel-dependent.** A signal is `name + attributes`;
   attributes have bounded cardinality (enums, bucketed numbers, short site
   tags — never file paths in counters).
3. **Metadata, never payloads.** If a future need justifies payloads, they must
   pass through `memory/privacy.strip_private_data` first (Tier-1 redaction
   invariant) and be gated on an explicit opt-in.
4. **Never on the hot path.** All recording goes through the buffered-sink
   pattern (deque + daemon flush + `atexit` + read-only-mode skip +
   backlog cap). A telemetry failure must never fail a tool call, a build, or
   hold a lock.
5. **Instrument the engine layer, not just the MCP wrapper.** CLI and MCP both
   flow through `builder` / `queries` / `semantic`; engine-level signals cover
   both surfaces. The wrapper adds per-tool UX metrics only.
6. **Analytics, not correctness.** Telemetry writes are best-effort and
   skippable (mirrors the `tool_metrics` doctrine in
   `mcp_server/metric_buffering.py:3`).

## 6. Detailed design

### 6.1 Telemetry module (`src/cairn/telemetry/`)

Generalizes the proven `metric_buffering` sink:

```
telemetry/
  __init__.py      # public API: emit(name, **attrs), counter(...), flush hooks
  sink.py          # buffered writer (deque + daemon thread + atexit), env gates
  events.py        # event emission helpers (lock_contention, ann_fallback, ...)
```

- `emit(name, **attrs)` appends `(ts, name, session_id, attrs_json)` to the
  buffer; JSON-serializable attrs only; oversized attr values truncated.
- Gates: `CAIRN_TELEMETRY=off` → no-op module (functions become `lambda *a,
  **k: None`, zero overhead). `CAIRN_READ_ONLY` → skip writes (same rationale
  as `metric_buffering._log_metric`).
- `metric_buffering.py` is refactored to sit on this sink (behavior unchanged;
  `tool_metrics` remains its own table). One flush thread per process, shared.

### 6.2 Schema additions (additive only)

New tables ride `SCHEMA_SQL` (`CREATE TABLE IF NOT EXISTS` — no `MIGRATIONS`
entry needed, matching how `tool_metrics` itself was added):

```sql
-- One row per indexing pass (full build / incremental sync / embed).
CREATE TABLE IF NOT EXISTS build_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,             -- 'build' | 'sync' | 'embed' | 'incremental'
    started_at TIMESTAMP NOT NULL,
    duration_s REAL,
    phase_timings TEXT,             -- JSON {scan,parse,insert,resolve,persist}
    repos INTEGER, files INTEGER, symbols INTEGER, edges INTEGER,
    resolution_exact INTEGER, resolution_ambiguous INTEGER, resolution_unresolved INTEGER,
    parse_errors INTEGER, skipped INTEGER,
    workers INTEGER,
    session_id TEXT
);

-- Generic low-cardinality events.
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TIMESTAMP NOT NULL,
    name TEXT NOT NULL,             -- see 6.4 event catalog
    session_id TEXT,
    attrs TEXT                      -- JSON, bounded values only
);
CREATE INDEX IF NOT EXISTS idx_events_name ON events(name);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
```

Retention: `build_runs` and `events` are pruned opportunistically (keep newest
N rows, N ≈ 5000 events / 500 build runs) inside the flush thread — bounded
growth in the shared DB file.

### 6.3 The one-time-warning pattern (replicated)

`warn_hash_fallback_once` (`graph/embeddings.py:263`) generalizes to
`telemetry/events.py::warn_once(key, logger, message)` — process-global guard
set, keyed so each degradation class warns at most once per process:

- `ann_unavailable` — sqlite-vec missing / load failed (closes Gap 2)
- `hash_backend` — delegates to existing helper
- `read_only_metrics_skip` — first skipped flush on a read-only daemon

### 6.4 Event catalog (initial)

| Event name | attrs | Emitted from |
|---|---|---|
| `ann_fallback` | `reason: load_failed\|not_installed\|no_index\|disabled\|query_error` | `ann_index.try_load` / `ann_query` / `semantic.py` fallback branch |
| `hash_fallback` | (existing warning path) | `embeddings.warn_hash_fallback_once` callers |
| `lock_contention` | `site: <module>.<function>` tag | the 13 `except sqlite3.OperationalError` sites, via a shared `note_contention(site)` helper |
| `truncate_result` | `tool`, `chars` bucket | `metric_buffering._truncate_result` |
| `empty_result` | `query_kind: semantic_search\|explore\|search_symbols` | engine query layer: `semantic.semantic_search` (engine), `explore.explore` (engine; single MCP-tool caller), and the `search_symbols` MCP tool wrapper (`tools_graph.search_symbols_data` — not the shared primitive, to avoid double-counting). The per-tool identity folds into `query_kind`; the per-backend view comes from correlating with `semantic_backend`. |
| `semantic_backend` | `backend: ann\|brute\|hash`, `fusion: 0/1`, `rerank: 0/1`, `ms` bucket, `n_results` bucket | `semantic.semantic_search` return path |
| `task_lifecycle` | `task_kind`, `event: claimed\|completed\|dropped\|revised`, `attempt` | `llm/tasks.py` |
| `stray_swept` | `count` | `server._install_stray_sweeper` |

Cardinality rules: attrs are enums, short fixed tags, or bucketed values
(`0-10ms`, `10-100ms`, …). No paths, no free text from user input.

### 6.5 Surfaces

**`cairn doctor`** (new CLI command, `cli/system.py`) — checks, each PASS/WARN/FAIL:

1. Schema: version, pending migrations, `PRAGMA quick_check` (bounded)
2. Embeddings backend: real vs hash (with remediation hint)
3. ANN: loaded / degraded + reason (from the `ann_fallback` event + live probe)
4. Freshness: `pending_sync` count + oldest edit age; last `build_runs` row age
5. Parse errors: count from `parse_errors` (newest 5 shown) — closes the
   invisible-feature gap
6. Concurrency: `lock_contention` events in last 7d; stray-sweep frequency
7. Tool health: per-tool error rate + p95 from `tool_metrics`
8. Config echo: the CAIRN_* knobs that alter behavior (workers, read-only,
   fusion, ann, embed backend, telemetry)

Exit code 0 (all PASS/WARN) or 1 (any FAIL) — agents can gate on it. `--json`
supported like sibling commands.

**`cairn metrics` extensions** — `--builds` (build-run trend incl. resolution
mix), `--quality` (empty-result rate, truncation rate, backend mix),
`--contention` (lock events by site). Default output unchanged.

**`cairn://status` resource extension** — append a `health` block (backend
degradations, pending-sync, last build age, 24h error rate). No new tool.

**Logging hygiene** — one central config point in `cli/main.py` group callback
+ `mcp_server/server.run()`: `CAIRN_LOG_LEVEL` env (default WARNING) and `-v`
flag → DEBUG on the cairn namespace only (never root, preserving the stdio
stdout-is-sacred rule and the FastMCP `log_level="WARNING"` pin rationale).

### 6.6 Config surface (new env vars)

| Var | Default | Meaning |
|---|---|---|
| `CAIRN_TELEMETRY` | `on` | `off` disables all recording (no-op module) |
| `CAIRN_LOG_LEVEL` | `WARNING` | Logging threshold for the `cairn` namespace |

Both documented in `docs/configuration.md` → "Server and runtime".

## 7. Privacy & security invariants

- No secrets/PII/code content in any signal (mirrors the MCP spec's logging
  security requirements and `memory/privacy.py`'s posture).
- Error messages stored in `tool_metrics` today are already truncated to 500
  chars; new signals carry enum attrs only.
- Everything stays in the local SQLite store alongside the graph.
- Bandit/layers gates in CI continue to apply; telemetry code must not import
  network libraries (the P2 OTLP exporter lazy-imports and is skipped when the
  SDK is absent).

## 8. Success metrics

- Every fallback branch enumerated in §4.2 emits a signal (auditable by grep:
  no silent `return False/None` degradation paths left).
- After any build, `cairn metrics --builds` shows the run and its resolution
  mix; two builds show a trend.
- `cairn doctor` exits non-zero on: hash backend active, ANN degraded,
  stale graph, parse errors present (each independently toggleable in tests).
- `tests/test_metrics.py` + `tests/test_telemetry.py` cover the sink
  (flush/retry/backlog-cap/read-only-skip), the doctor checks, and the
  instrumentation points.
- Zero measurable tool-latency regression with telemetry on (buffered sink;
  assert in bench suite).

## 9. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Telemetry writes contend with writers (the v0.9.x bug class) | Buffered sink, one flush/30s, skip-on-readonly, backlog cap; never in a transaction with user data |
| `events` table growth | opportunistic retention pruning (§6.2) |
| Cardinality explosion | enum/bucket-only attrs (§6.4); enforced by test asserting attr value sets |
| Scope creep toward an APM product | Non-goals §3; OTLP export is P2 and optional |
| Doc drift across the 4 guidance surfaces | Mirror rule applied in plan (SKILL.md, `_common.py`, `cursor.mdc`, root AGENTS.md) |
