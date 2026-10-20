---
type: Tribal-pattern
title: Tool-count guard runs at server start, not import time
description: Tool-count guard runs at server start, not import time
resource: src/cairn/mcp_server/server.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:35:54Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T15:35:54Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:35:54Z'
---

verify_tool_count() is a callable invoked inside run(), not a module-level assert.
Why: merely importing cairn.mcp_server (which CLI and tests do) must never trip the guard; a registration regression should surface at server start.
How to apply: count tools from the live FastMCP registry and update _EXPECTED_TOOL_COUNT when the tool surface changes.