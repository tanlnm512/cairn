# Survey: ui-dashboard-live-updates

**Created**: 2026-08-20 | **Baseline**: cairn-intel 0.12.1 @ `d781383`
Phase-A output — the single source of truth for code state. Every citation
in the other four docs must trace to a line here. Evidence is pasted
verbatim from grep/read output in the session that wrote it.

## Items

```
item Q1: "Client-side JS today is graph-render-only — no fetch, no timers, no refresh machinery"
  evidence: src/cairn/dashboard/static/app.js:1-6 — "/* Graph view: build vis-network DataSets
    from the server-serialized {nodes, edges, metadata} JSON block and render an
    interactive network (drag to pan, wheel to zoom). No CDN — vis-network is
    vendored. */ (function () { \"use strict\"; var block =
    document.getElementById(\"graph-data\");"
  evidence: grep -n "setInterval\|setTimeout\|fetch(" src/cairn/dashboard/static/app.js → no matches
  status: TODO
  verify: grep -n "setInterval\|setTimeout\|fetch(" src/cairn/dashboard/static/app.js
  gap: no polling/refresh code exists at all — every view is a one-shot server render

item Q2: "All dashboard routes are synchronous Jinja TemplateResponses — no fragments, no JSON deltas"
  evidence: src/cairn/dashboard/app.py:156-160 — "return templates.TemplateResponse(
    request, \"projects.html\", {\"projects\": rows},)"
  evidence: src/cairn/dashboard/app.py:210-214 — history handler returns
    TemplateResponse(request, "history.html", {"calls": calls, "tool": ..., "session": ...})
  status: TODO
  verify: grep -n "TemplateResponse" src/cairn/dashboard/app.py | wc -l
  gap: no endpoint serves a refreshable body region; refresh today means full page reload

item Q3: "History rows carry a monotonic id — the idempotency key FR-006 needs"
  evidence: src/cairn/graph/schema.py:273-274 — "CREATE TABLE IF NOT EXISTS tool_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,"
  status: DONE
  verify: sqlite3 <store> ".schema tool_metrics" | head -2
  gap: None — AUTOINCREMENT rowid is monotonic; new-record detection can key on max(id)

item Q4: "History handler round-trips filter inputs through the form GET"
  evidence: src/cairn/dashboard/app.py:202-213 — history() reads tool/session from
    query_params and passes them back into the template context
  status: DONE
  verify: grep -n "\"session\":" src/cairn/dashboard/app.py
  gap: preserved across manual form submits only — a client-side swap must replicate it (FR-003)

item Q5: "No disconnected state anywhere — a stopped server is a browser error page"
  evidence: src/cairn/dashboard/app.py:278-290 — the only error surface is the
    server-side missing_db handler (MissingDatabaseError), which a stopped server
    never reaches (connection refused happens in the browser)
  status: TODO
  verify: grep -rn "disconnect\|offline\|unreachable" src/cairn/dashboard/
  gap: FR-005's disconnected state is entirely new client behavior

item Q6: "Recording lands in batches on a 30s cadence — SC-1's visibility window"
  evidence: src/cairn/telemetry/sink.py:54 — "_FLUSH_INTERVAL = 30.0  # seconds"
  evidence: src/cairn/mcp_server/metric_buffering.py:29 — "_METRIC_BUFFER:
    collections.deque = collections.deque(maxlen=2000)"
  status: DONE
  verify: grep -n "_FLUSH_INTERVAL" src/cairn/telemetry/sink.py
  gap: a call can sit buffered up to 30s before the store sees it — SC-1's "within 2
    refresh cycles" at a 5s interval is flush-bound, not poll-bound

item Q7: "Existing dashboard test suites and harness conventions"
  evidence: tests/test_dashboard_app.py — Starlette TestClient per-route tests over tmp_path DBs
  evidence: tests/test_dashboard_data.py — pure data-layer tests over seeded sqlite
  status: DONE
  verify: uv run pytest tests/test_dashboard_app.py -q
  gap: None — conventions to follow: tmp DBs, no sleeps, no wall-clock dependence
```

## Supporting evidence

```
Render path (verified this session):
- app.py handlers are plain-def on purpose (threadpool note at app.py:148-149) — blocking SQL is fine
- templates/base.html carries the shared nav; history/chains/tokens render server-side tables
- static assets: app.css (light theme only), app.js (graph only), vis-network.min.js (vendored)

Store facts (verified this session):
- current store holds 251 tool_metrics rows, all session 'unknown' (legacy shape)
- tool_metrics indexed on tool_name and session_id only — no invoked_at index
```

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.
