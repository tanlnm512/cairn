---
type: Tribal-decision
title: Droid registration removal ignores file scope
description: Droid registration removal ignores file scope
resource: src/cairn/agent_install/clients/droid.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:50:25Z'
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
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T17:50:25Z'
  action: captured
  score: 0.96
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:50:25Z'
---

Droid MCP removal runs at every uninstall scope when the CLI registration path is available.
Why: The registration lives in Droid user configuration while file wiring is workspace-scoped, so workspace-only removal would leave a live stale server.
How to apply: Treat Droid CLI registration and file fallback as one logical installation with paired cleanup.