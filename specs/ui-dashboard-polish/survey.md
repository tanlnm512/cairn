# Survey: ui-dashboard-polish

**Created**: 2026-08-20 | **Baseline**: cairn-intel 0.12.1 @ `d781383`
Phase-A output — the single source of truth for code state. Every citation
in the other four docs must trace to a line here. Evidence is pasted
verbatim from grep/read output in the session that wrote it.

## Items

```
item Q1: "Health's first request can pay a multi-second import — the warm-up mechanism"
  evidence: src/cairn/dashboard/data.py:229 — "\"reranker_available\":
    reranker_available()," inside get_health
  evidence: src/cairn/graph/reranker.py:125-126 — "try:
    from sentence_transformers import CrossEncoder  # noqa: F401
    return True"
  status: TODO
  verify: grep -n "reranker_available" src/cairn/dashboard/data.py src/cairn/graph/reranker.py
  gap: no prewarming exists — the import lands inside the first /health
    request (FR-001's delta)

item Q2: "Token estimates are chars//CHARS_PER_TOKEN with CHARS_PER_TOKEN = 4"
  evidence: src/cairn/bench/agent_suite.py:58 — "CHARS_PER_TOKEN = 4"
  evidence: src/cairn/dashboard/data.py:323-328 — "\"est_req_tokens\": (
    None if row[\"req_chars\"] is None else row[\"req_chars\"] // CHARS_PER_TOKEN),"
    (+ the same for resp; get_tool_tokens aggregates the same way)
  status: DONE
  verify: grep -rn "CHARS_PER_TOKEN" src/cairn/bench/agent_suite.py src/cairn/dashboard/data.py
  gap: no tokenizer mode, no active-mode label anywhere (FR-002's delta);
    the constant is shared with the bench suite (comparability constraint)

item Q3: "Truncation IS recorded — as a coarse bucketed event, emitted at the chokepoint"
  evidence: src/cairn/telemetry/events.py:44 — "TRUNCATE_RESULT = \"truncate_result\""
  evidence: src/cairn/mcp_server/metric_buffering.py:73-77 — "try:
    from cairn.telemetry import TRUNCATE_RESULT, emit as _emit
    _emit(TRUNCATE_RESULT, tool=name, chars_bucket=_chars_bucket(len(result)))"
    with _chars_bucket buckets <=500 / 500-2k / 2k-10k / >10k
  status: PARTIAL
  verify: grep -rn "truncate_result\|TRUNCATE_RESULT" src/cairn --include="*.py"
  gap: exact magnitude not recorded, no per-row tool_metrics attribute, and
    no view surfaces it (FR-003's real delta = extend + surface)

item Q4: "Retention exists for events/build_runs but NOT tool_metrics"
  evidence: src/cairn/telemetry/sink.py:79-80 — "_MAX_EVENTS_ROWS = 5000
    _MAX_BUILD_RUNS_ROWS = 500"
  evidence: src/cairn/telemetry/sink.py:168-183 — _prune DELETEs events and
    build_runs past the caps inside the flush transaction
  evidence: grep -n "DELETE FROM tool_metrics" src/cairn → no matches
  status: TODO
  verify: grep -rn "DELETE FROM tool_metrics" src/cairn --include="*.py"
  gap: usage rows grow unboundedly; caps are hardcoded, not configurable
    (FR-004's delta — extend the prune seam)

item Q5: "No export of any view — CSV/JSON surfaces absent"
  evidence: src/cairn/dashboard/app.py:261-276 — routes list ends with the
    static mount; no .csv/.json export routes
  status: TODO
  verify: grep -n "csv\|export\|json" src/cairn/dashboard/app.py
  gap: FR-005 adds filtered export routes over the existing data functions

item Q6: "Light theme only — CSS custom properties exist (theme seam ready)"
  evidence: src/cairn/dashboard/static/app.css:1-8 — ":root { --bg:
    #f6f7f9; --surface: #ffffff; --text: #1f2430; --muted: #6b7280;
    --border: #d9dde3; --accent: #2563eb; }"
  status: TODO
  verify: head -10 src/cairn/dashboard/static/app.css
  gap: no dark palette, no prefers-color-scheme, no persistence (FR-006)

item Q7: "Health panel shape — where retention policy surfaces (FR-004)"
  evidence: src/cairn/dashboard/data.py:211-230 — get_health returns
    db_size_bytes, last_build_at/age, embed backend, ann status, reranker
  status: DONE
  verify: sed -n 165,231p src/cairn/dashboard/data.py
  gap: None structurally — new key(s) join the same dict/template

item Q8: "Optional-dependency precedent for heavy ML extras"
  evidence: src/cairn/graph/reranker.py:133-137 — install_hint references
    "pip install 'cairn-intel[semantic]'" (the optional-extra pattern) and
    ~/.cairn/lib shared-deps dir (src/cairn/paths.py:34-40)
  status: DONE
  verify: grep -n "semantic" pyproject.toml | head -3
  gap: an exact-tokenizer mode follows the same optional-dependency
    discipline (spec assumption); zero new required deps
```

## Supporting evidence

```
Events-table retention interplay (verified this session):
- events rows compete for the 5000-row cap; current store's events show
  semantic_backend 3611 + empty_result 1384 — truncate_result occurrences
  are pruned alongside; per-call truncation magnitude therefore needs a
  durable home outside the events cap (FR-003's "durably" clause)

Sink flush transaction (verified this session):
- _prune runs inside _flush_events' commit — extending it to tool_metrics
  with a configurable cap is the same transactional seam (FR-004 + FR-007)

Dashboard read-only discipline (verified this session):
- every handler opens get_read_only_db; aging performed by the recording
  side keeps FR-007's split by construction
```

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.
