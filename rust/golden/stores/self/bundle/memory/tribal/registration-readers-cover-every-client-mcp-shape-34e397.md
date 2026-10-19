---
type: Tribal-pattern
title: Registration readers cover every client MCP shape
description: Registration readers cover every client MCP shape
resource: src/cairn/agent_install/__init__.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:50:04Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T17:50:04Z'
  action: captured
  score: 0.97
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:50:04Z'
---

Registration readers recognize flat mcpServers.cairn, ZCode mcp.servers.cairn, OpenCode/Kilo mcp.cairn, and top-level cairn shapes while tolerating malformed JSON and non-object containers.
Why: Verification and doctor audits must classify all installed shapes consistently instead of recognizing only the newest schema.
How to apply: Extend the shared reader and detector together for every new client configuration schema.