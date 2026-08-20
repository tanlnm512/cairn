# Survey: ui-dashboard-polish

**Created**: 2026-08-20 | **Baseline**: cairn-intel 0.12.1 @ `88d8de5`
Phase-A output — the single source of truth for code state. Every citation
in the other five docs must trace to a line here. Evidence is pasted
verbatim from grep/read output in the session that wrote it.
RE-SURVEY of the `d781383` survey: five feature lands in between
(graph-nav cd38065, cross-links ae387ef, live-updates 64f0bcc,
traffic-scale 5e26d88, workspace-launcher 5dd8158, cli-usage-recording
88d8de5) — every line number and count below re-verified at HEAD.

## Items

```
item Q1: "Health's first request can pay a multi-second import — the warm-up mechanism"
  evidence: src/cairn/dashboard/data.py:20 — "from cairn.graph.reranker import
    reranker_available"; data.py:280 — "\"reranker_available\":
    reranker_available()," inside get_health
  evidence: src/cairn/graph/reranker.py:126 — "from sentence_transformers
    import CrossEncoder  # noqa: F401" (first call of the probe, inside
    reranker_available(), reranker.py:119-128)
  evidence: grep -rn "prewarm|warmup|warm_up" src/cairn --include="*.py" —
    only bench/perf_suite.py hits (timing harness, unrelated); no dashboard
    prewarming exists
  status: TODO
  verify: grep -n "reranker_available" src/cairn/dashboard/data.py src/cairn/graph/reranker.py
  gap: unchanged from d781383 — the sentence_transformers import still lands
    inside the first /health request; no prewarming path (FR-001's delta)

item Q2: "Token estimates are chars//CHARS_PER_TOKEN with CHARS_PER_TOKEN = 4"
  evidence: src/cairn/bench/agent_suite.py:58 — "CHARS_PER_TOKEN = 4";
    src/cairn/dashboard/data.py:17 — "from cairn.bench.agent_suite import
    CHARS_PER_TOKEN"
  evidence: src/cairn/dashboard/data.py:444-448 — "\"est_req_tokens\": (
    None if row[\"req_chars\"] is None else row[\"req_chars\"] // CHARS_PER_TOKEN),"
    (+ est_resp_tokens same shape) in list_history's row dict
  evidence: src/cairn/dashboard/data.py:481 — "def get_tool_tokens(conn,
    since: Optional[float] = None)" — NEW at HEAD: `since` window param
    (traffic-scale); aggregates at data.py:512-513 — "est_req =
    (row[\"total_req_chars\"] or 0) // CHARS_PER_TOKEN" — same constant
  evidence: src/cairn/dashboard/templates/tokens.html:19-22 — headers
    "Est. req tokens / Est. resp tokens / Total est. tokens / Mean est.
    tokens", rendered "~{{ t.est_req_tokens }}" (tokens.html:30) — no mode
    label anywhere
  status: DONE
  verify: grep -n "CHARS_PER_TOKEN" src/cairn/bench/agent_suite.py src/cairn/dashboard/data.py
    (+ .venv/bin/python -m pytest tests/test_dashboard_data.py -q → passed,
    part of 73-passed run below)
  gap: estimate path itself unchanged by the lands; still no tokenizer mode,
    no active-mode label (FR-002's delta); constant still shared with the
    bench suite (comparability constraint); get_tool_tokens now takes since=

item Q3: "Truncation IS recorded — as a coarse bucketed event, emitted at the chokepoint"
  evidence: src/cairn/telemetry/events.py:44 — "TRUNCATE_RESULT =
    \"truncate_result\""
  evidence: src/cairn/mcp_server/metric_buffering.py:63-77 — "_truncate_result"
    emits at the actual truncation branch (metric_buffering.py:310 inside
    instrument): "_emit(TRUNCATE_RESULT, tool=name, chars_bucket=
    _chars_bucket(len(result)))" — buckets (metric_buffering.py:52-61):
    "<=500" / "500-2k" / "2k-10k" / ">10k"
  evidence: live store census (this session, ~/.cairn/acc48fd52e4c6e8d/.kg):
    events table has truncate_result = 4074 of exactly 5000 rows — the
    bucketed event now dominates the capped table (old survey's mix
    "semantic_backend 3611 + empty_result 1384" no longer holds)
  status: PARTIAL
  verify: grep -rn "truncate_result\|TRUNCATE_RESULT" src/cairn --include="*.py"
  gap: still a coarse bucket, exact magnitude not recorded, no durable
    per-tool_metrics attribute, and no dashboard view surfaces it (FR-003's
    real delta = extend + surface). Note: events table column is `name`
    (schema: id/ts/name/session_id/attrs — pasted from sqlite_master), not
    `kind`; see Q9 for the existing per-tool count pattern

item Q4: "Retention exists for events/build_runs but NOT tool_metrics"
  evidence: src/cairn/telemetry/sink.py:79-80 — "_MAX_EVENTS_ROWS = 5000
    _MAX_BUILD_RUNS_ROWS = 500" (still hardcoded constants)
  evidence: src/cairn/telemetry/sink.py:149-184 — _prune DELETEs events then
    build_runs (time-ordered, inside the flush transaction); no other tables
  evidence: grep -rn "DELETE FROM tool_metrics" src/cairn --include="*.py"
    → no matches (exit 1)
  evidence: live store census (this session): tool_metrics = 6547 rows
    unbounded (oldest invoked_at 1786808248.58 → newest 1787236456.68),
    source split cli=100 / mcp=6447, top tool _bench_tool=5634
  evidence: NEW at HEAD — src/cairn/telemetry/cli_metrics.py:210-211 —
    "register_flusher(_flush_cli_metrics)" + "start_flusher()": the CLI
    buffered sink rides sink.py's shared flush thread + atexit drain
    (sink.py:70 _FLUSHERS, sink.py:123 register_flusher), so two extension
    points exist for a tool_metrics prune: sink._prune or a registered
    flusher; cli_metrics itself prunes nothing (no _MAX/DELETE in it)
  status: TODO
  verify: grep -rn "DELETE FROM tool_metrics" src/cairn --include="*.py"
    (+ grep -n "_MAX\|DELETE" src/cairn/telemetry/cli_metrics.py → buffer
    maxlen only)
  gap: tool_metrics rows grow unboundedly; caps hardcoded, not configurable
    (FR-004's delta — extend the prune seam; rows from BOTH sources and
    bench traffic must count against the bound, see Q10)

item Q5: "No export of any view — CSV/JSON surfaces absent"
  evidence: src/cairn/dashboard/app.py:446-457 — full route table at HEAD:
    "/", "/workspaces", "/projects", "/graph", "/graph/candidates",
    "/graph/neighbors", "/history", "/tokens", "/chains", "/health",
    "/memory", "/tasks" — 12 routes, none export
  evidence: grep -n "csv\|export\|json\|JSON\|CSV" src/cairn/dashboard/app.py
    → only the two graph-nav JSONResponse endpoints (app.py:279, 305) and a
    module-docstring mention (app.py:4); zero export/csv code
  status: TODO
  verify: grep -n "csv\|export\|json" src/cairn/dashboard/app.py
  gap: FR-005 adds filtered export routes over the existing data functions;
    "current filtered contents" now also means the window param
    (_resolve_window) and, for history, tool/session/source filters — see Q11

item Q6: "Light theme only — CSS custom properties exist (theme seam ready)"
  evidence: src/cairn/dashboard/static/app.css:1-8 — ":root { --bg:
    #f6f7f9; --surface: #ffffff; --text: #1f2430; --muted: #6b7280;
    --border: #d9dde3; --accent: #2563eb; }" (block unchanged by the lands)
  evidence: grep -c "prefers-color-scheme\|data-theme\|dark"
    src/cairn/dashboard/static/app.css → 0
  status: TODO
  verify: head -10 src/cairn/dashboard/static/app.css
  gap: no dark palette, no prefers-color-scheme, no persistence (FR-006);
    base.html/app.js grew (nav, polling banner, window control) but no
    theme machinery joined them

item Q7: "Health panel shape — where retention policy surfaces (FR-004)"
  evidence: src/cairn/dashboard/data.py:216 — "def get_health(conn:
    sqlite3.Connection, db_path: Optional[str] = None) -> Dict"; return
    dict data.py:273-289 keys: db_size_bytes, last_build_at, last_build_age,
    embed_backend, hash_fallback, ann_configured, ann_backend_enabled,
    ann_model, ann_embedding_rows, ann_index_exists, ann_index_rows,
    reranker_available (12 keys)
  evidence: NEW at HEAD — the handler resolves the store per-request:
    app.py:308-314 — "selected_db, _, store_key = resolve_selection(request,
    db_path, knowledge_dir)" then "health_data = get_health(conn,
    selected_db)"; template context carries store_key
  status: DONE
  verify: sed -n 216,300p src/cairn/dashboard/data.py
    (+ .venv/bin/python -m pytest tests/test_dashboard_data.py -q → passed,
    part of 73-passed run below)
  gap: None structurally — new key(s) join the same dict/template; note the
    handler now goes through resolve_selection (Q11) and passes selected_db

item Q8: "Optional-dependency precedent for heavy ML extras"
  evidence: src/cairn/graph/reranker.py:133-138 — install_hint: "Install it
    with: pip install 'cairn-intel[semantic]', then set CAIRN_RERANK=1."
  evidence: pyproject.toml:104-107 — "semantic = [
    \"sentence-transformers>=3.0\", \"numpy>=1.24\", ]" under
    [project.optional-dependencies] (pyproject.toml:66)
  evidence: src/cairn/paths.py:35-41 — shared-lib comment block +
    "SHARED_LIB = CAIRN_HOME / \"lib\"" (~/.cairn/lib, pip install --target)
  status: DONE
  verify: grep -n "semantic" pyproject.toml | head -3
  gap: an exact-tokenizer mode follows the same optional-dependency
    discipline (spec assumption); zero new required deps

item Q9: "Per-tool truncation counts ALREADY exist — in the CLI, not the dashboard (NEW at HEAD)"
  evidence: src/cairn/cli/system.py:215 — "truncate_total = _count_events(
    conn, \"truncate_result\")"; system.py:225 — "\"truncations_by_tool\":
    _attr_counts(conn, \"truncate_result\", \"tool\")," — the exact query
    pattern FR-003's tokens view needs, already built over the events table
  evidence: same fn returns "truncations": truncate_total (system.py:224) —
    surfaced in `cairn system` quality output
  status: DONE
  verify: grep -n "truncations_by_tool\|truncate_result" src/cairn/cli/system.py
  gap: fact-recording item: the pattern exists to reuse; the dashboard
    tokens view still surfaces nothing (that gap stays under Q3/FR-003);
    counts derive from capped events rows, so they under-count by
    construction — the durable-home concern in FR-003 remains

item Q10: "tool_metrics gained a `source` column with two writers (NEW at HEAD)"
  evidence: src/cairn/graph/schema.py:284 — "source TEXT NOT NULL DEFAULT
    'mcp'  -- 'mcp' | 'cli' (spec cli-usage-recording FR-002)"
  evidence: src/cairn/telemetry/cli_metrics.py:80-85 — INSERT names
    "req_chars, resp_chars, args_summary, source)"; cli_metrics.py:164 —
    "\"cli\",  # source (FR-002): explicit here; MCP rows ride DEFAULT 'mcp'"
  evidence: history view already filters by it: app.py reads the `source`
    query param; data.py:350 list_history(..., source=None, ...);
    data.py:394-398 — "if source is not None: ...
    filter_clauses.append(\"source = ?\")"; row dict carries it
    (data.py:437 — "\"source\": row[\"source\"]")
  evidence: live store census (this session): by source [('cli', 100),
    ('mcp', 6447)] — both writers active in the real store
  evidence: src/cairn/graph/schema.py:409 —
    "TOOL_METRICS_SOURCE_MIGRATION = \"ALTER TABLE tool_metrics ADD COLUMN
    source TEXT NOT NULL DEFAULT 'mcp'\"" (migration for pre-existing stores)
  status: DONE
  verify: grep -n "DEFAULT 'mcp'" src/cairn/graph/schema.py
  gap: load-bearing constraints only: FR-004's retention bound must cover
    rows from both sources; FR-005's export of history rows should carry
    the column the view already filters on

item Q11: "Per-request store selection + time-window filtering now wrap every view (NEW at HEAD)"
  evidence: src/cairn/dashboard/app.py:163-193 — resolve_selection(request,
    db_path, knowledge_dir): "?store=" must name a registry key from
    enumerate_stores, "the param is a registry key, never a raw path";
    unknown/missing key raises MissingDatabaseError; no param = launch
    store, byte-identical to old behavior
  evidence: every data-bearing handler calls it (health app.py:308, tokens
    app.py:367, chains app.py:384, history, graph, projects) and opens
    get_read_only_db (data.py:674) — e.g. app.py:314-316
  evidence: src/cairn/dashboard/app.py:105-113 — _resolve_window(window)
    returns (preset, since-epoch); tokens/chains/history pass since= into
    the data functions (get_tool_tokens since param at data.py:481)
  status: DONE
  verify: sed -n 163,193p src/cairn/dashboard/app.py
    (+ .venv/bin/python -m pytest tests/test_dashboard_workspaces.py
    tests/test_dashboard_app.py -q → 69 passed)
  gap: fact-recording item: any new route FR-005 adds (export) or FR-004's
    health additions must ride resolve_selection + the window/filter params
    to see the same "current filtered contents" the page renders
```

## Supporting evidence

```
Live-store census (re-counted this session; the d781383 survey's numbers
were claims and no longer hold):
- main telemetry store ~/.cairn/acc48fd52e4c6e8d/.kg (45,309,952 bytes)
- events = exactly 5000 (at the _MAX_EVENTS_ROWS cap), by name:
  truncate_result 4074 · semantic_backend 687 · empty_result 225 ·
  task_lifecycle 12 · ann_fallback 2 — truncate_result now dominates the
  capped table (81%), sharpening FR-003's "durably (not subject to the
  events table's row cap)" clause
- tool_metrics = 6547 rows, unbounded (no prune exists), oldest→newest
  invoked_at 1786808248.58 → 1787236456.68; by source: cli 100, mcp 6447;
  top tools: _bench_tool 5634, impact_analysis 138, generate_flow 120 —
  bench traffic is the bulk any FR-004 bound will prune
- events table schema (from sqlite_master, verbatim shape): columns are
  id / ts / name / session_id / attrs — queries must filter on `name`, not
  `kind`
- only 2 of ~180 stores under ~/.cairn hold any telemetry rows; the rest
  are test-fixture stores from the tmp workspace registry

Flush/prune seam (verified this session):
- sink.py:70 _FLUSHERS registry + sink.py:123 register_flusher +
  sink.py:268 start_flusher — single shared flush thread + atexit drain
- cli_metrics.py:210-211 registers _flush_cli_metrics there (shares the
  30s cadence, prunes nothing); _prune (sink.py:149) runs inside
  _flush_events' transaction — either is the transactional seam FR-004
  extends; _prune is time-ordered (ts/started_at) not id-ordered, and
  best-effort guarded (missing table / read-only conn never raises)

Dashboard read-only discipline (verified this session):
- get_read_only_db defined at data.py:674; every handler opens it
  (app.py:222, 249, 274, 300, 314, ...) and closes in a finally — aging
  performed by the recording side keeps FR-007's split by construction
- tests/test_dashboard_readonly.py passes at HEAD (part of 73-passed run)

Route table (app.py:446-457, re-counted): 12 routes — /, /workspaces,
/projects, /graph, /graph/candidates, /graph/neighbors, /history,
/tokens, /chains, /health, /memory, /tasks (+ MissingDatabaseError
handler at app.py:465). The only JSON producers are the two graph-nav
endpoints (app.py:279, 305) — no export surface.

Tests actually run at HEAD this session (venv: .venv/bin/python):
- tests/test_dashboard_data.py + tests/test_cli_metrics.py +
  tests/test_dashboard_readonly.py → 73 passed, 1 warning
- tests/test_dashboard_workspaces.py + tests/test_dashboard_app.py →
  69 passed, 1 warning
```

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.
