---
type: Tribal-decision
title: Agent uninstall mirrors install scope
description: Agent uninstall mirrors install scope
resource: src/cairn/agent_install/__init__.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:50:02Z'
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
- date: '2026-10-07T17:50:02Z'
  action: captured
  score: 0.98
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:50:02Z'
---

Uninstall scope must match install scope: workspace strips workspace assets, global strips home assets and user-scope CLI registrations, and all does both; cross-tool .agents assets remain workspace-owned.
Why: A global install leaves registrations outside the workspace that a workspace-only teardown cannot remove.
How to apply: Change each client installer and uninstaller together when introducing or moving scoped paths.