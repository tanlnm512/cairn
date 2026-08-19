# Plan: ui-dashboard

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-20
Source of truth for code state: [survey.md](survey.md). Statuses below cite
survey items (Q#) or greps run in this planning session (file:line).

## Milestones
<!-- Each milestone = a phase in task.md. -->
| Phase | Milestone | Delivers (demoable) | FRs | Depends on |
|-------|-----------|---------------------|-----|------------|
| 1 | Tool-call recording | Every instrumented MCP tool call appends a buffered `tool_metrics` row that now also carries request/response payload sizes; a clean process shutdown flushes 100% of returned-call rows (no silent drops) | FR-004, FR-011 | — |
| 2 | Dashboard shell | `cairn dashboard` boots a localhost, read-only web app from one CLI command, prints its URL, and serves the page skeleton + vendored no-build assets | FR-001, FR-010 | — |
| 3 | Existing-state views | Projects list (counts, freshness, embedding status), interactive project graph (scope/depth controls), health panel, memory + task-queue panels — all reading tables that already exist | FR-002, FR-003, FR-008, FR-009 | Phase 2 |
| 4 | Tool-traffic views | Newest-first filterable history, per-tool token aggregates ranked by cost, session-grouped call chains with gap-splitting — reading `tool_metrics` incl. Phase-1 size columns | FR-005, FR-006, FR-007 | Phase 2 (FR-006 additionally Phase 1) |

Phase 1 exists to de-risk early: it touches the MCP hot path and a live-DB
migration (the two riskiest changes in the spec), and its recorded data is what
makes Phase 4 demoable with real traffic. Phase 2 is the unlock for all view
work. Phases 3 and 4 are the wide, low-risk read-only layer.

## Dependencies

```
Phase 1 (recording)  ──┐
                       ├──> Phase 4 (traffic views)   [FR-006 only, via new size columns]
Phase 2 (shell) ───────┼──> Phase 3 (state views)
                       └──> Phase 4 (mounting/routes)
```

- **Phase 1 ∥ Phase 2** — no shared files, no shared state (evidence below).
- **Phase 2 → Phases 3, 4** — the shell produces what every view consumes:
  the app object, route mounting point, read-only connection factory, and the
  browser-asset spine. Views cannot be served without it.
- **Phase 1 → FR-006 only** — the token view aggregates over the payload-size
  columns Phase 1 adds. FR-005 (history) and FR-007 (chains) run entirely on
  columns that already exist (`tool_metrics`: `tool_name, session_id,
  invoked_at, duration_ms, status, error_message` — schema.py:273-281, survey
  Q4), so they wait only on Phase 2.
- Because implementation runs in waves (wave 1 = Phases 1+2 parallel; wave 2 =
  Phases 3+4 parallel), the Phase 1 → FR-006 edge is satisfied automatically
  by wave ordering; it never constrains inside-wave parallelism.

## Parallelization map
<!-- Which work areas are independent (different files/subsystems, no shared
     state) and can be developed concurrently, and which are strictly
     sequential. The task-breaker turns this into [P] markers per task. -->

**Area A — Recording** (Phase 1: FR-004, FR-011)
Files: `src/cairn/mcp_server/metric_buffering.py` (size capture in
`instrument()`/`_log_metric`; buffer/flush machinery already present — survey
Q4, Q5), `src/cairn/graph/schema.py` (additive columns on `tool_metrics` via
the proven `ALTER TABLE ... ADD COLUMN` migration pattern; precedent
`FILES_SIZE_MIGRATION = "ALTER TABLE files ADD COLUMN size INTEGER"`,
schema.py:365), `tests/test_metrics.py` (existing suite for this exact buffer;
conventions: direct `_flush_metrics` calls, no sleeps, autouse state reset).
Callers verified this session via `cairn callers _log_metric`: only
metric_buffering itself plus tests — the change is contained.

**Area B — Dashboard shell** (Phase 2: FR-001, FR-010)
Files: new `src/cairn/cli/dashboard.py` (or dashboard package — layout is
tech-spec's call), `src/cairn/cli/__init__.py` (+1 import line, the documented
registration pattern — survey Q10), new vendored asset files (new directory).
Read-only DB via existing `get_db(read_only=True)` URI path (survey Q11);
FastAPI/uvicorn already available transitively (survey Q6).

**Area C — Existing-state views** (Phase 3: FR-002, FR-003, FR-008, FR-009)
Files: new view/route modules + view asset sections. Data sources all exist:
repos/files tables (Q1), embedding model tracking (Q2), viz query layer with 5
scopes (Q3), doctor/health sources (Q7), memory + task tables (Q8). Reuses
`viz/query.py` and health-check logic read-only; if health data assembly needs
extracting from `cli/system.py`, that refactor belongs to this area alone.

**Area D — Tool-traffic views** (Phase 4: FR-005, FR-006, FR-007)
Files: new view/route modules + view asset sections, reading `tool_metrics`
(+ Phase-1 size columns). Token estimation follows the existing
`CHARS_PER_TOKEN = 4` precedent (Q9; final divisor is tech-spec's call).

- Independent: **A ∥ B** (wave 1) — file sets are fully disjoint:
  {mcp_server, graph/schema.py, tests/test_metrics.py} vs {new cli module +
  assets + one `cli/__init__.py` line}. B never touches `schema.py` (FR-010
  forbids it), so the repo's hottest shared file has exactly one writer.
- Independent: **C ∥ D** (wave 2) — disjoint query surfaces (C reads
  repos/files/symbols/edges/embeddings/memory/tasks; D reads `tool_metrics`
  only) and disjoint view modules. CONDITION — see risk R1: this holds only if
  tech-spec gives each area its own route module and asset section; if routes
  or the single-file JS asset are centralized in one file, C and D serialize
  on that file and the task-breaker must drop the [P] for the asset/route
  task.
- Strictly ordered: **B → C and B → D** — B produces the app skeleton, route
  mounting, read-only conn factory, and asset spine that C and D mount into;
  before B lands there is nothing for view code to register against.
- Strictly ordered: **A → FR-006 (within D)** — A's payload-size columns are
  the input FR-006's aggregation consumes; the existing-columns parts of D
  (FR-005 history, FR-007 chains) have no such edge.

## Checkpoints
<!-- Exit condition per phase; verify before starting the next. -->

- **After Phase 1** (covers SC-2, SC-3): an instrumented tool call records a
  row with non-null request/response sizes; buffer/flush semantics and the
  existing `tool_metrics` row shape are unchanged; clean shutdown drains the
  buffer. Verify:
  `uv run pytest tests/test_metrics.py tests/test_workflow_audit_fixes.py -q`
  (existing suites guard the buffer) plus the phase's new size/durability
  tests; schema observable:
  `sqlite3 ~/.cairn/store/cairn.db ".schema tool_metrics"` shows the size
  columns on a migrated DB.
- **After Phase 2**: the command boots, prints a localhost URL, serves the
  skeleton over HTTP, and opens the DB read-only (mode=ro). Verify:
  `uv run cairn dashboard --db <tmp.db> --port 7901 & sleep 1; curl -sf
  http://127.0.0.1:7901/ >/dev/null && echo SHELL-OK` and the phase's
  CliRunner test (pass `--db` explicitly; CAIRN_HOME binds at import).
- **After Phase 3**: endpoints return live data without any dashboard-side
  writes — projects JSON lists every repo with counts + last-indexed time and
  embedding status; graph endpoint returns the viz layer's
  `{nodes, edges, metadata}` for each scope; health, memory, and task panels
  render. Verify: `curl -sf` against each of the routes `/projects`,
  `/graph`, `/health`, `/memory`, `/tasks` plus phase tests under
  `uv run pytest -q`.
- **After Phase 4** (covers SC-1 end-to-end): with recorded tool traffic,
  history is newest-first and filterable by tool/session; token aggregates are
  ranked by total; chains split at inactivity gaps; first page render < 2s on
  a warmed DB (manual browser check). Verify: `curl -sf
  'http://127.0.0.1:7901/history?tool=explore'` returns only matching rows;
  full suite green: `make test` (or `uv run pytest -q`).

## Risks & mitigations
- R1 — Shared asset/route file between Areas C and D: spec mandates single-file
  JS, no build step; if both areas edit the same file concurrently, wave-2
  parallelism breaks → mitigation: plan assumes per-view route modules and
  per-view asset sections (spine owned by Phase 2); task-breaker MUST check
  tech-spec.md's file layout before marking C ∥ D tasks [P]; if centralized,
  the route/asset task serializes after both.
- R2 — Live-DB migration: adding columns to `tool_metrics` on existing
  databases → mitigation: nullable/defaulted additive columns via the proven
  ALTER TABLE migration pattern (schema.py:360-389 precedent); existing rows
  read as "size unknown", views must tolerate nulls.
- R3 — Hot-path latency budget (< 5%, SC-2): size capture runs inside
  `instrument()` → mitigation: chars-length only (O(1) on str), no I/O — the
  existing buffered-deque design (30s shared sink flush, survey Q5) already
  keeps writes off the hot path; Phase 1 checkpoint re-verifies with the
  existing metric suites.
- R4 — Pending tech-spec decisions (framework, estimation divisor, session-gap
  threshold, exact column names): plan deliberately depends only on FR-level
  facts; where a name was needed it is marked as tech-spec's call.
- R5 — Survey Q12 is PARTIAL (baseline runtime unverified in the surveyor's
  session; system python 3.9.6 too old): all verify commands above use
  `uv run` / `make test`, never bare `python3`.

## Assumptions (not evidenced in survey.md)
- Payload sizes land as additive columns on the existing `tool_metrics` table
  (extending, not replacing, the Q4 machinery — consistent with spec's
  "extend, not replace" risk note); if tech-spec chooses a separate table,
  Area A's file set gains a new table in `schema.py` but the disjointness vs
  Area B is unchanged.
- Exact names of new columns/endpoints/assets are tech-spec's; checkpoints
  describe observables, not identifiers.
- FR-007's session grouping uses the existing `session_id` column
  (`CAIRN_SESSION` env — metric_buffering.py:221); gap-splitting threshold is
  tech-spec's call.

## Delivery
Branch `feat/ui-dashboard`; the whole spec lands as ONE PR with one commit for
the entire plan after a batched closing audit (solo developer + AI agents).
Implementation runs as parallel implementer waves exactly as the
parallelization map defines: wave 1 = Areas A ∥ B, wave 2 = Areas C ∥ D.
Each checkpoint above is the wave gate — verify before spawning the next wave.
Post-merge: `cairn update` + `record_memory` per AGENTS.md; run `cairn doctor`
(survey Q12) since this spec touches a performance path (SC-2).
