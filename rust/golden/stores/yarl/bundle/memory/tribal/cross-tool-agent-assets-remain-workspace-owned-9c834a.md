---
type: Tribal-pattern
title: Cross-tool agent assets remain workspace-owned
description: Cross-tool agent assets remain workspace-owned
resource: src/cairn/agent_install/__init__.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:50:03Z'
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
memory_tier: tribal
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T17:50:03Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:50:03Z'
---

The shared .agents skill, command, and agent copies are always written and removed relative to the workspace.
Why: They are a compatibility fallback shared by multiple clients, not per-client global configuration.
How to apply: Keep cross-tool assets outside client-specific global scope handling and remove generated content only when it matches installer output.