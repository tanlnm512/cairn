---
type: Tribal-pattern
title: MCP status surfaces stay independent of CLI imports
description: MCP status surfaces stay independent of CLI imports
resource: src/cairn/mcp_server/_server_core.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:35:25Z'
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
- date: '2026-10-07T15:35:25Z'
  action: captured
  score: 0.85
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:35:25Z'
---

The status resource formats build ages and computes its health block locally instead of importing doctor CLI helpers.
Why: the MCP server surface is separate from the CLI; importing CLI code into the server drags command registration and CLI deps into every serve process.
How to apply: duplicate small formatting helpers in the server rather than importing from cairn.cli.