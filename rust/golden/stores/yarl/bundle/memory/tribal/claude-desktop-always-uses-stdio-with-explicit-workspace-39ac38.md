---
type: Tribal-decision
title: Claude Desktop always uses stdio with explicit workspace
description: Claude Desktop always uses stdio with explicit workspace
resource: src/cairn/agent_install/clients/claude_desktop.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:50:23Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.64
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.99
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T17:50:23Z'
  action: captured
  score: 0.99
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:50:23Z'
---

Claude Desktop generation ignores transport selectors and always emits stdio with CAIRN_WORKSPACE plus CAIRN_HOME when non-default.
Why: Claude Desktop supports neither SSE config nor a workspace cwd; API symmetry must not leak an unsupported remote transport.
How to apply: Keep desktop generation outside shared transport branching and pin the workspace explicitly.