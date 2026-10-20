---
type: Tribal-decision
title: Claude global MCP registrations use user scope
description: Claude global MCP registrations use user scope
resource: src/cairn/agent_install/clients/claude.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:50:21Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.638
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.98
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T17:50:21Z'
  action: captured
  score: 0.98
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:50:21Z'
---

Claude workspace scope writes .mcp.json, while global registration uses claude mcp add --scope user because Claude does not read a global ~/.mcp.json.
Why: Writing a global file that the client ignores would falsely report a working global integration.
How to apply: Keep workspace file and user-scope CLI registration on separate scope branches and treat a missing CLI as best-effort.