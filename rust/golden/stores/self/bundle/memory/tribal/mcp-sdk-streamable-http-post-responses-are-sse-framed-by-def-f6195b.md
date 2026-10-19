---
type: Tribal-pattern
title: mcp SDK streamable-HTTP POST responses are SSE-framed by default; sync serve
  entry is uvicorn Server.run()
description: mcp SDK streamable-HTTP POST responses are SSE-framed by default; sync
  serve entry is uvicorn Server.run()
resource: src/cairn/mcp_server/server.py
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-10-05T09:43:08Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.61
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.85
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-05T09:43:08Z'
  action: captured
  score: 0.85
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-05T09:43:08Z'
---

With FastMCP's default json_response=False, POST /mcp responses return as text/event-stream bodies (JSON rides `data:` lines), and POST Accept headers must list both application/json and text/event-stream or the transport 406s; the session id arrives in the `mcp-session-id` RESPONSE header of initialize. Also: cairn's run() http branch drives uvicorn via `uvicorn.Server(uvicorn.Config(app, host, port, log_level)).run()` (stdlib asyncio inside uvicorn) rather than importing anyio — anyio is only a transitive dep of mcp, and a direct import would trip the C-03 dependency gate. Why: the SDK's own run_streamable_http_async is async-only and its sync run() wrapper uses anyio.run; mirroring the Config (D-001) does not require adopting anyio as a direct import. How to apply: when speaking raw JSON-RPC to a cairn HTTP server (TC-001-style checks), parse `data:` lines and send the two-type Accept header; when adding a sync blocking serve path, prefer Server.run() over new direct async-stack imports.