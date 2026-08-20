# Survey: cli-usage-recording

**Created**: 2026-08-20 | **Baseline**: cairn-intel 0.12.1 @ `d781383`
Phase-A output — the single source of truth for code state. Every citation
in the other four docs must trace to a line here. Evidence is pasted
verbatim from grep/read output in the session that wrote it.

## Items

```
item Q1: "tool_metrics has exactly one writer — the MCP instrument decorator"
  evidence: grep -rn "INSERT INTO tool_metrics" src/cairn --include="*.py" →
    src/cairn/mcp_server/metric_buffering.py:169 only (plus a comment at :238)
  evidence: src/cairn/mcp_server/metric_buffering.py:287 — "def instrument(fn):
    \"\"\"Decorator: wraps an MCP tool with timing, payload-size capture, error
    capture, and metric logging.\"\"\""
  status: TODO
  verify: grep -rn "INSERT INTO tool_metrics" src/cairn --include="*.py"
  gap: no CLI-side recording exists anywhere (FR-001's delta)

item Q2: "The shared buffered sink is the flush/drain machinery to reuse"
  evidence: src/cairn/telemetry/sink.py:53-54 — "_FLUSHER_STARTED = False
    _FLUSH_INTERVAL = 30.0  # seconds"
  evidence: src/cairn/telemetry/sink.py:289-291 — "t = threading.Thread(
    target=_loop, name=\"cairn-telemetry-flusher\", daemon=True)
    t.start() atexit.register(_flush_all)"
  evidence: src/cairn/mcp_server/metric_buffering.py:199-221 —
    _start_metric_flusher registers _flush_metrics with the shared sink
  status: DONE
  verify: grep -n "atexit.register\|register_flusher" src/cairn/telemetry/sink.py src/cairn/mcp_server/metric_buffering.py
  gap: None — registering a CLI flusher alongside _flush_metrics is the
    designed extension point (sink docstring: "future counters ... share
    ONE daemon flush thread")

item Q3: "Redaction + truncation chokepoints to apply verbatim"
  evidence: src/cairn/mcp_server/metric_buffering.py:263-270 — "if
    error_message: from cairn.memory.privacy import strip_private_data ...
    if args_summary: from cairn.memory.privacy import strip_private_data ...
    args_summary = strip_private_data(args_summary)"
  evidence: src/cairn/mcp_server/metric_buffering.py:280 —
    "args_summary[:MAX_ARGS_SUMMARY_CHARS] if args_summary else None,"
    (MAX_ARGS_SUMMARY_CHARS = 200, line 43)
  status: DONE
  verify: grep -n "strip_private_data\|MAX_ARGS_SUMMARY_CHARS" src/cairn/mcp_server/metric_buffering.py
  gap: None — same protections must wrap CLI args (FR-001's "same fields
    and protections")

item Q4: "Session identity today: CAIRN_SESSION env, per-boot uuid, 'unknown' default"
  evidence: src/cairn/mcp_server/metric_buffering.py:273 —
    "os.environ.get(\"CAIRN_SESSION\", \"unknown\"),"
  evidence: src/cairn/mcp_server/server.py:187-189 — "# with CAIRN_SESSION
    (default \"unknown\"); setdefault keeps an externally
    os.environ.setdefault(\"CAIRN_SESSION\", uuid4().hex[:12])"
  status: DONE
  verify: grep -rn "CAIRN_SESSION" src/cairn --include="*.py" | grep -v test
  gap: CLI invocations run without CAIRN_SESSION set → would land in the
    giant legacy 'unknown' session (spec's explicit risk); FR-006 must
    stamp an identity

item Q5: "Opt-out master switch exists and gates tool_metrics"
  evidence: src/cairn/mcp_server/metric_buffering.py:252-253 — "from
    cairn.telemetry.sink import is_telemetry_off if is_telemetry_off():
    return"
  evidence: src/cairn/telemetry/sink.py:90 — "return os.environ.get(
    \"CAIRN_TELEMETRY\", \"on\").strip().lower() == \"off\""
  status: DONE
  verify: grep -n "is_telemetry_off" src/cairn/mcp_server/metric_buffering.py
  gap: None mechanically; CLI recording must ride the same gate (FR-004)

item Q6: "Click entry point — the single interception surface for every command"
  evidence: src/cairn/cli/main.py — "@click.group() def main(...)" (verified
    present); pyproject.toml project.scripts maps cairn = "cairn.cli:main"
  status: DONE
  verify: uv run cairn --help
  gap: a Group-level invoke wrapper records every subcommand in one place,
    including future ones (FR-001's "every CLI command invocation")

item Q7: "tool_metrics schema: additive-column migration precedent for a source field"
  evidence: src/cairn/graph/schema.py:397-399 — TOOL_METRICS_REQ_CHARS/
    RESP_CHARS/ARGS_SUMMARY MIGRATIONS via ALTER TABLE ADD COLUMN
  status: DONE
  verify: grep -n "TOOL_METRICS.*MIGRATION" src/cairn/graph/schema.py
  gap: a `source` column (cli|mcp) rides the same pattern (FR-002)

item Q8: "CLI reads tool_metrics today (stats/report) — consumers of a source column"
  evidence: src/cairn/cli/system.py:112 — "FROM tool_metrics {where}" (the
    metrics aggregation) plus :1138,1153 error/health queries
  status: PARTIAL
  verify: grep -n "FROM tool_metrics" src/cairn/cli/system.py
  gap: existing CLI aggregations must keep working unchanged (FR-005's
    regression guard); new source filter is additive
```

## Supporting evidence

```
Existing metric suites guarding the buffer (verified present):
- tests/test_metrics.py, tests/test_metrics_extensions.py — conventions:
  direct _flush calls, no sleeps, autouse state reset between tests
- tests/test_telemetry.py — the shared sink's own suite

Duration/status shape parity (verified this session):
- MCP rows: (tool_name, session_id, invoked_at=time.time(),
  duration_ms=(t1-t0)*1000, status ok|error, error_message[:500],
  req_chars, resp_chars, args_summary[:200])
- CLI rows map onto the same columns with the command path as tool_name
  and argv summary as args_summary — no schema divergence beyond `source`
```

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.
