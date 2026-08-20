# Survey: ui-dashboard-traffic-scale

**Created**: 2026-08-20 | **Baseline**: cairn-intel 0.12.1 @ `d781383`
Phase-A output — the single source of truth for code state. Every citation
in the other four docs must trace to a line here. Evidence is pasted
verbatim from grep/read output in the session that wrote it.

## Items

```
item Q1: "History query has no LIMIT and no pagination parameters"
  evidence: src/cairn/dashboard/data.py:302-310 — "rows = conn.execute(
    f\"\"\" SELECT id, tool_name, session_id, invoked_at, duration_ms,
    status, error_message, req_chars, resp_chars, args_summary
    FROM tool_metrics{where} ORDER BY invoked_at DESC \"\"\", params,).fetchall()"
  evidence: src/cairn/dashboard/app.py:202-207 — history() reads only
    tool/session query params; no page/limit/cursor params exist
  status: TODO
  verify: grep -n "LIMIT" src/cairn/dashboard/data.py
  gap: no bound of any kind on rows rendered (FR-001)

item Q2: "Token aggregates are all-time only — no time predicate anywhere"
  evidence: src/cairn/dashboard/data.py:345-354 — "rows = conn.execute(
    \"\"\" SELECT tool_name, COUNT(*) AS calls, SUM(req_chars) AS total_req_chars,
    SUM(resp_chars) AS total_resp_chars FROM tool_metrics GROUP BY tool_name
    \"\"\").fetchall()"
  status: TODO
  verify: grep -n "invoked_at >=" src/cairn/dashboard/data.py
  gap: no window filter input on the route or the query (FR-002, FR-003)

item Q3: "Chains loads the whole table into Python and returns every chain"
  evidence: src/cairn/dashboard/data.py:391-397 — "rows = conn.execute(
    \"\"\" SELECT id, tool_name, session_id, invoked_at, duration_ms, status
    FROM tool_metrics ORDER BY session_id, invoked_at \"\"\").fetchall()"
  followed by in-Python grouping into sessions/chains with no bound
  status: TODO
  verify: grep -n "LIMIT\|max_chains" src/cairn/dashboard/data.py
  gap: no cap on chains or calls per chain rendered (FR-004)

item Q4: "tool_metrics indexes: tool_name and session_id only — invoked_at unindexed"
  evidence: src/cairn/graph/schema.py:285-286 — "CREATE INDEX IF NOT EXISTS
    idx_tool_metrics_tool ON tool_metrics(tool_name);
    CREATE INDEX IF NOT EXISTS idx_tool_metrics_session ON tool_metrics(session_id);"
  status: TODO
  verify: grep -n "idx_tool_metrics" src/cairn/graph/schema.py
  gap: window filters (invoked_at >= ?) and keyset pagination (invoked_at, id)
    have no supporting index (spec's confirmed risk)

item Q5: "Additive schema-migration precedent for index/column rides"
  evidence: src/cairn/graph/schema.py:291-293 — "-- next connect -- the same
    pattern tool_metrics used. CREATE TABLE IF NOT EXISTS build_runs ("
  evidence: src/cairn/graph/schema.py:397-399 — "TOOL_METRICS_REQ_CHARS_MIGRATION
    = \"ALTER TABLE tool_metrics ADD COLUMN req_chars INTEGER\"" (+resp, args)
  status: DONE
  verify: grep -n "MIGRATIONS\|CREATE INDEX IF NOT EXISTS" src/cairn/graph/schema.py | head -8
  gap: None — idempotent executescript + MIGRATIONS list is the established
    seam for adding an index on invoked_at

item Q6: "Monotonic (invoked_at, id) composite exists — keyset pagination is expressible"
  evidence: src/cairn/graph/schema.py:273-274 — id INTEGER PRIMARY KEY AUTOINCREMENT
    (id strictly increases with insertion order)
  status: DONE
  verify: sqlite3 <store> "SELECT COUNT(*) FROM (SELECT id FROM tool_metrics ORDER BY id DESC LIMIT 5)"
  gap: None as a key; ordering today is invoked_at DESC with no tie-break —
    keyset needs ORDER BY invoked_at DESC, id DESC + WHERE (invoked_at, id) < cursor

item Q7: "Store volumes today — the honest baseline behind FR-005's 10k budget"
  evidence: dev store query this session — tool_metrics rows: 251 (cairn
    workspace store), 98 (be workspace store); largest session 'unknown' = 251 rows
  status: PARTIAL
  verify: sqlite3 ~/.cairn/71e4dcfee8d29b5a/.kg "SELECT COUNT(*) FROM tool_metrics"
  gap: no 10k-row store exists locally — FR-005's budget must be proven on a
    synthesized store in tests (spec says so explicitly)

item Q8: "Read-only guard test exists to extend for windowed/paginated routes"
  evidence: tests/test_dashboard_readonly.py — guards the dashboard's
    read-only discipline (spec FR context; verified present in tests/)
  status: DONE
  verify: uv run pytest tests/test_dashboard_readonly.py -q
  gap: None — the guard suite is the place pagination/window tests join
```

## Supporting evidence

```
Legacy session shape (verified this session):
- all 251 rows carry session_id 'unknown' — the MCP server began stamping
  per-boot CAIRN_SESSION only recently (src/cairn/mcp_server/server.py:187-189);
  FR-004's bound must hold for a single session holding the entire table

Related route surfaces (verified this session):
- /chains and /tokens take no query params today; /history takes tool+session only
- cross-links (other spec) adds /chains?session= — window and session filters
  must compose in SQL (both are WHERE clauses on the same query)
```

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.
