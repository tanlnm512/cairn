---
type: Tribal-pattern
title: Claude Desktop config is global and per-OS
description: Claude Desktop config is global and per-OS
resource: src/cairn/agent_install/detect.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:50:12Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.634
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.96
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T17:50:12Z'
  action: captured
  score: 0.96
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:50:12Z'
---

Claude Desktop detection uses the single per-OS global claude_desktop_config.json location rather than workspace files.
Why: The desktop app has one global MCP config and no workspace-scoped config file.
How to apply: Keep path resolution and detection aligned when platform paths change.