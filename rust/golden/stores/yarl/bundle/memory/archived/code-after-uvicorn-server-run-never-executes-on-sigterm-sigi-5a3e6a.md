---
type: Tribal-mistake
title: Code after uvicorn Server.run() never executes on SIGTERM/SIGINT
description: Code after uvicorn Server.run() never executes on SIGTERM/SIGINT
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-10-05T10:11:24Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.632
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.95
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: archived
tier: asserted
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-10-05T10:11:24Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
memory_triage: session-bookkeeping
valid_from: '2026-10-05T10:11:24Z'
---

uvicorn's Server.capture_signals() saves handlers, gracefully shuts down on SIGTERM/SIGINT, then RE-RAISES the captured signal with the restored default handler — so the process dies inside Server.run() and any statement after it (e.g. a shutdown milestone print in cairn's http branch) never executes on signal-driven stops.
Why: discovered landing http-mcp-serving T007 — a "MCP server stopped" print after server.run() was unreachable; the process exited by signal (exit code reflects the signal, not a normal return).
How to apply: never rely on post-Server.run() code for shutdown observability; shutdown milestones must come from signal handlers installed before uvicorn runs, the ASGI app's lifespan shutdown, or the surrounding finally (watcher stop).