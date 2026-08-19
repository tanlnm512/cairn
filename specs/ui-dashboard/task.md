# Tasks: ui-dashboard

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
Status reflects code state per [survey.md](survey.md), not intent.
**Created**: 2026-08-20

Waves (plan § Parallelization / § Delivery): wave 1 = Phase 1 ∥ Phase 2
(file sets fully disjoint); wave 2 = Phase 3 ∥ Phase 4 — EXCEPT the shared
hot files `src/cairn/dashboard/data.py` (tech-spec: all SQL lives there),
`src/cairn/dashboard/app.py` (all routes) and `tests/test_dashboard_data.py`
(view tests): plan risk R1's condition fired (centralized layout), so
cross-area tasks on those files carry explicit `(after T###)` chains instead
of `[P]`. Each task's file list is exact and complete — file-set disjointness
between same-wave `[P]` tasks is the dispatch safety condition.

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| 1     | 5     | 5    |
| 2     | 3     | 3    |
| 3     | 4     | 4    |
| 4     | 5     | 5    |
| **Σ** | 17    | 17   |

## Phase 1: Tool-call recording (FR-004, FR-011)
<!-- Checkpoint (plan): an instrumented tool call records a row with non-null
     request/response sizes; buffer/flush semantics and the existing
     tool_metrics row shape are unchanged; clean shutdown drains the buffer.
     Verify: `uv run pytest tests/test_metrics.py
     tests/test_workflow_audit_fixes.py -q` plus the phase's new
     size/durability tests; `sqlite3 ~/.cairn/store/cairn.db ".schema
     tool_metrics"` shows the size columns on a migrated DB. Wave 1, Area A. -->
- [x] T001 Standing foundation: buffered recording + clean-shutdown drain
      machinery (pre-existing, verified — no edit required) —
      `src/cairn/mcp_server/metric_buffering.py` (`_METRIC_BUFFER`
      deque maxlen=2000, `_flush_metrics`), `src/cairn/telemetry/sink.py`
      (30s daemon flush + `atexit.register(_flush_all)`),
      `src/cairn/mcp_server/server.py` (`_drain_buffered_telemetry`
      parent-death-watchdog path) (FR-004, FR-011)
  - done 2026-08-20 (survey baseline; machinery predates this spec) —
    survey Q4 DONE ("MCP server with metric buffering via instrument
    decorator"; verify `grep -r "instrument\|_log_metric" src/cairn/mcp_server --include="*.py" | wc -l`)
    and Q5 DONE (gap "None — Shared telemetry sink with atexit handler and
    daemon flush thread"; verify `grep -r "atexit\|_flush\|flush()" src/cairn/telemetry --include="*.py" | head -10`).
    The durability test later in this phase re-proves drain for the extended row.
- [x] T002 [P] Add additive payload-size and arg-summary columns to
      `tool_metrics` — `src/cairn/graph/schema.py`: extend the
      `CREATE TABLE IF NOT EXISTS tool_metrics` DDL with `req_chars INTEGER`,
      `resp_chars INTEGER`, `args_summary TEXT`; add matching named
      `ALTER TABLE tool_metrics ADD COLUMN ...` entries to the `MIGRATIONS`
      list (idempotent duplicate-column tolerance; precedent
      `FILES_SIZE_MIGRATION`); extend `_TELEMETRY_TABLE_COLUMNS["tool_metrics"]`
      so `copy_telemetry_tables` carries the new columns across whole-file
      rebuild swaps — omitting that map entry silently wipes recorded sizes (FR-004)
  - verify first (tech-spec Area 1): `sqlite3 ~/.cairn/store/cairn.db ".schema tool_metrics"`
    (row-shape baseline); D-005; plan R2 — columns are nullable, views must
    tolerate null sizes on pre-migration rows
  - done 2026-08-20 — uv run pytest tests/test_metrics.py -q green; fresh-DB PRAGMA table_info(tool_metrics) lists req_chars/resp_chars/args_summary (idempotent re-init)
- [x] T003 [P] Write a per-process session id at MCP server boot —
      `src/cairn/mcp_server/server.py`: `os.environ.setdefault("CAIRN_SESSION",
      uuid4().hex[:12])` at the top of `run()` (signature and control flow
      untouched); extend `tests/test_server_robustness.py` with the
      setdefault test (pre-set value wins, unset gets generated) (FR-004, FR-007)
  - grounded this session: `CAIRN_SESSION` has readers but no writer
    (`metric_buffering.py:221`, `telemetry/events.py:74`,
    `graph/builder.py:955` — every row currently lands as session "unknown");
    D-004; TC anchor: TC-009 (session field populated)
  - done 2026-08-20 — uv run pytest tests/test_server_robustness.py -q (12 passed; pre-set wins, unset generates 12-hex)
- [x] T004 Capture payload sizes and arg summary in the instrument wrapper
      (after T002) — `src/cairn/mcp_server/metric_buffering.py`: in
      `instrument()`'s wrapper compute `req_chars = len(json.dumps(kwargs, default=str))`
      and `resp_chars` from the result (the wrapper already branches
      `isinstance(result, str)` for `_truncate_result`); build `args_summary`
      as compact JSON of kwargs, redacted via `strip_private_data` and
      truncated (~200 chars) at the write chokepoint; `_log_metric` gains
      optional trailing kwargs `req_chars=None, resp_chars=None,
      args_summary=None` so existing positional test calls stay valid; the
      buffered row tuple and the `INSERT INTO tool_metrics` in `_flush_metrics`
      gain the three columns in lockstep (a count mismatch shows up as
      permanently buffered rows, not an error — flush swallows exceptions by
      design) (FR-004)
  - serialized after T002: the INSERT consumes T002's columns; D-005; keep
    `functools.wraps` and the never-raise wrapper discipline (27 `@instrument`
    sites + FastMCP schema introspection ride on it); size capture is chars
    only, O(1) on str — keeps SC-2 (plan R3)
  - done 2026-08-20 — uv run pytest tests/test_metrics.py -q green; e2e row proof req/resp/summary round-trip + redaction at chokepoint
- [x] T005 Prove extended-row recording and clean-shutdown drain in tests
      (after T004) — `tests/test_metrics.py` (existing conventions: direct
      `_flush_metrics` calls, no sleeps, autouse state reset): call
      `_log_metric` with the new kwargs, flush, SELECT the new columns;
      positional-call back-compat; `args_summary` redaction + truncation;
      durability — an explicit flush drains 100% of buffered extended rows
      (no silent drops) (FR-004, FR-011)
  - serialized after T004: tests the row shape T004 lands; proof anchors
    TC-009, TC-022; phase gate `uv run pytest tests/test_metrics.py tests/test_workflow_audit_fixes.py -q`
  - done 2026-08-20 — uv run pytest tests/test_metrics.py tests/test_workflow_audit_fixes.py -q (37 passed; K=60 drain 100%)

## Phase 2: Dashboard shell (FR-001, FR-010)
<!-- Checkpoint (plan): the command boots, prints a localhost URL, serves the
     skeleton over HTTP, and opens the DB read-only (mode=ro). Verify:
     `uv run cairn dashboard --db /tmp/dash-shell.db --port 7901 & sleep 1;
     curl -sf http://127.0.0.1:7901/ >/dev/null && echo SHELL-OK` plus the
     CliRunner test (pass --db explicitly; CAIRN_HOME binds at import).
     Wave 1, Area B — fully disjoint from Phase 1's files. -->
- [x] T006 [P] Create the read-only Starlette app package spine — new
      `src/cairn/dashboard/__init__.py`, `src/cairn/dashboard/app.py` (app
      factory, Jinja2Templates + StaticFiles wiring, landing route,
      route-mounting point for Phases 3-4), `src/cairn/dashboard/data.py`
      (read-only connection factory over `get_db(..., read_only=True)` — URI
      `mode=ro`, cannot contend with writers), new
      `src/cairn/dashboard/templates/base.html`,
      `src/cairn/dashboard/templates/index.html`,
      `src/cairn/dashboard/static/app.css` (FR-001, FR-010)
  - verify first (tech-spec Area 2): `.venv/bin/python -c "import importlib.util as u; print(u.find_spec('starlette'), u.find_spec('uvicorn'), u.find_spec('jinja2'))"`
    — all transitive via `mcp` (fastapi is NOT installed; D-001); loopback
    only, port 8765 distinct from the SSE daemon's 9876 (D-006); Starlette /
    uvicorn / jinja2 imported lazily so core CLI paths never load them
  - done 2026-08-20 — uv run pytest tests/test_dashboard_app.py -q spine tests; lazy guard False; RO-OK write-refused
- [x] T007 [P] Vendor the standalone vis-network build — new
      `src/cairn/dashboard/static/vis-network.min.js` (single-file drop, no
      CDN, no bundler; skipif-on-missing-file guard added by the graph-view
      task that wires it) (FR-003)
  - D-002; file-set disjoint from T006 (which owns `app.css`) — parallel in
    wave 1; satisfies US2-AC1 pan/zoom without a build step
  - done 2026-08-20 — sha256 39b9a36f17b4ca2e27d1e4327216860b042077b3307402ae315402ac826f878b vis-network@9.1.13; NOTICE vendored-content entry
- [x] T008 Wire the `cairn dashboard` CLI command (after T006) — new
      `src/cairn/cli/dashboard.py` (Click `@main.command()`: `--db` default
      central store, `--port` default 8765, `--host` default 127.0.0.1 —
      never `0.0.0.0`; `click.echo` the URL; lazy uvicorn run of T006's app
      factory; blocking run — CliRunner tests stop at app construction /
      `--help`), plus one registration import line `from . import dashboard`
      in `src/cairn/cli/__init__.py`, plus a `tests/test_cli_smoke.py`
      extension asserting `runner.invoke(main, ["dashboard", "--help"])`
      succeeds (FR-001)
  - serialized after T006: consumes its app factory; registration pattern per
    survey Q10 (`@main.command()` decorator side effects on import);
    TC anchors TC-001, TC-002; verify first: `uv run python -c "from cairn.cli import main; print([cmd for cmd in main.list_commands(None)])"`
  - done 2026-08-20 — uv run pytest tests/test_cli_smoke.py -q; boot smoke curl 200; cairn dashboard in main.list_commands

## Phase 3: Existing-state views (FR-002, FR-003, FR-008, FR-009)
<!-- Checkpoint (plan): endpoints return live data without any dashboard-side
     writes — projects JSON lists every repo with counts + last-indexed time
     and embedding status; graph endpoint returns the viz layer's node/edge
     data for each scope; health, memory, and task panels render. Verify:
     curl each endpoint on the running dashboard plus phase tests under
     `uv run pytest -q`. Wave 2, Area C — enters after the Phase 2
     checkpoint. -->
- [x] T009 Assemble projects + graph view data (after T006) —
      `src/cairn/dashboard/data.py`: `list_projects()` over
      `repos`/`files`/`symbols`/`edges` (file/symbol/edge counts via
      `files.repo_id` / `symbols.file_id`; freshness from
      `MAX(files.indexed_at)` / `repos.indexed_at`) plus `embeddings`
      coverage (embedded vs not vs partial + `DISTINCT model` where
      recorded); graph scope dispatch calling `src/cairn/viz/query.py` live
      signatures `get_symbol_graph(conn, name, depth=1)`,
      `get_module_graph(conn, module)`,
      `get_impact_graph(conn, name, max_depth=3)`, `get_deps_graph(conn)`,
      `get_repo_graph(conn, repo, max_nodes=30)` — all return
      `{nodes, edges, metadata}`; plus new `tests/test_dashboard_data.py`
      (seeded `fresh_db` rows, counts known by construction) (FR-002, FR-003)
  - verify first (survey Q3): `uv run python -c "from cairn.viz.query import get_symbol_graph, get_module_graph, get_impact_graph, get_deps_graph, get_repo_graph; print('ok')"`;
    `repos.path` is workspace-relative — display, don't resolve; TC anchors
    TC-003, TC-004, TC-005, TC-007
  - done 2026-08-20 — uv run pytest tests/test_dashboard_data.py -q (projects counts/embed statuses/graph dispatch cases)
- [x] T010 Assemble health + memory + task-queue view data (after T009) —
      `src/cairn/dashboard/data.py`: health panel (DB size via `os.stat`,
      index freshness from `build_runs.started_at`, vector-backend probes
      `cairn.graph.embeddings.is_hash_fallback` /
      `cairn.graph.ann_index.ann_backend_enabled` + `index_exists` /
      `index_row_count`, reranker probe against
      `src/cairn/graph/reranker.py` — exact probe function `unknown — verify`
      against that file during implementation); recent memories via
      `OKFBundle.list_concepts` (`src/cairn/okf/bundle.py`); queue via
      `list_tasks` from `src/cairn/llm/tasks.py` (status filter); tests in
      `tests/test_dashboard_data.py` (FR-008, FR-009)
  - serialized after T009: same two files (tech-spec centralizes all SQL in
    `data.py` — plan R1 realized); D-007 (probes import graph-layer helpers,
    never `mcp_server`)
  - survey Q12 PARTIAL gap: "Cannot execute baseline verification due to
    Python version constraint" (system python 3.9.6) — establish the runtime
    baseline with `uv run cairn doctor` before implementing and make the
    panel agree with it (TC-018); verify first (survey Q8): `uv run cairn memory --help && uv run cairn task --help`
  - done 2026-08-20 — uv run pytest tests/test_dashboard_data.py -q (health keys, memories newest-first, tasks status filter; doctor-probe agreement)
- [x] T011 [P] Serve projects + graph views with interactive rendering
      (after T009) — `src/cairn/dashboard/app.py`: routes `/projects` and
      `/graph` (form-GET scope/depth controls, default module scope); new
      `src/cairn/dashboard/templates/projects.html`,
      `src/cairn/dashboard/templates/graph.html`,
      `src/cairn/dashboard/static/app.js` (vis-network DataSets built from
      the serialized `{nodes, edges, metadata}`; pan/zoom; metadata
      node/edge counts surfaced so scope truncation is visible), wiring
      T007's vendored asset into `base.html`'s static spine (FR-002, FR-003)
  - parallel with T010 (disjoint files: `app.py`/templates/`app.js` vs
    T010's `data.py`/test file); consumes T009's functions; TC anchors
    TC-003, TC-006, TC-007, TC-008 (2s budget on 10k+ symbols)
  - done 2026-08-20 — uv run pytest tests/test_dashboard_app.py -q; boot smoke /projects + /graph?scope=module 200; skipif guard for missing vendored asset
- [x] T012 Serve health + memory + task-queue panels (after T010, T011) —
      `src/cairn/dashboard/app.py`: routes `/health`, `/memory`, `/tasks`;
      new `src/cairn/dashboard/templates/health.html`,
      `src/cairn/dashboard/templates/memory.html`,
      `src/cairn/dashboard/templates/tasks.html` (FR-008, FR-009)
  - serialized: shares `app.py` with T011 and consumes T010's functions;
    TC anchors TC-018, TC-019, TC-020
  - done 2026-08-20 — uv run pytest tests/test_dashboard_app.py -q; boot smoke health/memory/tasks 200

## Phase 4: Tool-traffic views (FR-005, FR-006, FR-007)
<!-- Checkpoint (plan; SC-1 end-to-end): with recorded tool traffic, history
     is newest-first and filterable by tool/session; token aggregates are
     ranked by total; chains split at inactivity gaps; first page render
     under 2s on a warmed DB (manual browser check). Verify: `curl -sf
     'http://127.0.0.1:7901/history?tool=explore'` returns only matching
     rows; full suite green: `make test` (or `uv run pytest -q`). Wave 2,
     Area D — FR-005/FR-007 need only Phase 2; FR-006 additionally Phase 1
     (wave ordering satisfies that edge). -->
- [x] T013 Assemble history view data (after T010) —
      `src/cairn/dashboard/data.py`: newest-first
      `SELECT ... FROM tool_metrics ORDER BY invoked_at DESC` with
      `WHERE tool_name = ?` / `session_id = ?` filters from query params;
      per-row estimated request/response tokens (US4-AC2 satisfied at row
      level); `invoked_at` is a raw `time.time()` epoch float — compare
      numerically, never parse as ISO; tolerate null sizes on pre-migration
      rows; empty-state shape for a fresh DB; tests in
      `tests/test_dashboard_data.py` (FR-005)
  - serialized after T010: `data.py` + the shared view-test file are
    Phase-3-owned until T010 lands (plan R1 cross-area chain); TC anchors
    TC-011, TC-012, TC-013, TC-024 (args summaries, never full payloads)
  - done 2026-08-20 — uv run pytest tests/test_dashboard_data.py -q (newest-first, filters, NULL-size tolerance, TC-024 truncation guard)
- [x] T014 [P] Assemble token + chains view data (after T013) —
      `src/cairn/dashboard/data.py`: per-tool aggregates over the Phase-1
      size columns — `SUM(req_chars)` / `SUM(resp_chars)` →
      `est_tokens = chars // CHARS_PER_TOKEN` with `CHARS_PER_TOKEN`
      imported from `src/cairn/bench/agent_suite.py` (D-003), calls + total
      + mean ranked by total desc; chains `GROUP BY session_id` ordered by
      `invoked_at`, split at `SESSION_GAP_S = 1800` inactivity gaps (D-004);
      tests seeding `req_chars=400, resp_chars=800` → 100 + 200 est tokens
      (FR-006, FR-007)
  - serialized after T013 (same files); consumes the wave-1 size columns
    (T002/T004 — the plan's Phase 1 → FR-006 edge); TC anchors TC-014,
    TC-015, TC-016, TC-017
  - done 2026-08-20 — uv run pytest tests/test_dashboard_data.py -q (req400/resp800 -> 300 est tokens; gap-split chains; SESSION_GAP_S=1800)
- [x] T015 [P] Serve the history view (after T013) —
      `src/cairn/dashboard/app.py`: route `/history` with form-GET tool and
      session filters and a no-match empty state; new
      `src/cairn/dashboard/templates/history.html` (columns: tool name,
      timestamp, duration, status, session; per-call token estimates;
      truncated arg summaries only) (FR-005)
  - parallel with T014 (disjoint files: `app.py` + template vs T014's
    `data.py` + tests); consumes T013's query functions; TC anchors TC-011,
    TC-012, TC-013, TC-024
  - done 2026-08-20 — uv run pytest tests/test_dashboard_app.py -q; boot smoke /history + filtered 200
- [x] T016 Serve tokens + chains views (after T014, T015) —
      `src/cairn/dashboard/app.py`: routes `/tokens` and `/chains`; new
      `src/cairn/dashboard/templates/tokens.html` (calls / total / mean,
      ranked) and `src/cairn/dashboard/templates/chains.html`
      (session-grouped timelines, gap-split) (FR-006, FR-007)
  - serialized: shares `app.py` with T015 and consumes T014's aggregates;
    TC anchors TC-014, TC-016, TC-017
  - done 2026-08-20 — uv run pytest tests/test_dashboard_app.py -q; boot smoke /tokens + /chains 200 (ranked, gap-split)
- [x] T017 Add the read-only standing guard and close the Phase-4 gate
      (after T016) — new `tests/test_dashboard_readonly.py`: starlette
      TestClient (`pytest.importorskip("httpx")`) exercises every dashboard
      route against a checksummed populated DB and asserts the file checksum
      and the `tool_metrics` row count are unchanged after the full pass;
      then run the phase checkpoint (`make test`; filter curl against the
      running dashboard) (FR-010)
  - serialized after T016: the guard needs every route on the app; TC
    anchors TC-021, TC-025; SC-1 manual browser timing (TC-023) recorded in
    the PR audit checklist, not automated here
  - done 2026-08-20 — uv run pytest tests/test_dashboard_readonly.py -q (checksum + row-count unchanged over all routes; concurrent-writer all-200)

## Conventions
- `- [ ]` todo · `(in-progress)` claimed · `- [x]` done + proof note line
  in the form `done DATE — the passing command that proves it`
- Dropped: `- [ ] ~~T###~~ dropped DATE (D-###)` — never delete the line;
  dropped tasks stay visible with the decision that killed them
- `[P]` = parallelizable (default): no shared files with any task it could
  run beside; a chained task may still carry `[P]` when it has
  concurrently-runnable siblings — its `(after T###)` note names only its
  true upstreams. Serial runs state their reason (shared file or consumed
  output); parallel runs need none
- Every task cites its FR-###; tasks with no FR are scope creep — fix the
  spec first
- Status derives only from survey.md evidence; TODO is the default for all
  new code (the dashboard package, CLI command, size columns, session
  writer, and dashboard tests do not exist on main — verified this session)
- Verify commands always go through `uv run` / `make test` / `.venv/bin/...`,
  never bare `python3` (plan R5: system python 3.9.6 is too old)
