---
type: Tribal-decision
title: The MCP exit watchdog never reads stdin
description: The MCP exit watchdog never reads stdin
resource: src/cairn/mcp_server/server.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:35:53Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.621
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.9
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T15:35:53Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:35:53Z'
---

The watchdog uses signals plus parent-pid polling, never stdin reads.
Why: the MCP SDK anyio transport is the sole reader of the stdin fd; reading it from the watchdog steals bytes from JSON-RPC messages.
How to apply: detect client death via os.getppid() changes (stdio) or SIGTERM (SSE), not by watching stdin.