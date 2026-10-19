---
type: Tribal-decision
title: AGY remote MCP entries use serverUrl only
description: AGY remote MCP entries use serverUrl only
resource: src/cairn/agent_install/clients/agy.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:50:29Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.636
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.97
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T17:50:29Z'
  action: captured
  score: 0.97
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:50:29Z'
---

AGY remote MCP entries use serverUrl without a type field; legacy url and httpUrl fields are not accepted.
Why: AGY infers transport from the present field, so shared Claude-style remote shapes do not validate.
How to apply: Keep AGY generation isolated from the shared mcpServers remote schema.