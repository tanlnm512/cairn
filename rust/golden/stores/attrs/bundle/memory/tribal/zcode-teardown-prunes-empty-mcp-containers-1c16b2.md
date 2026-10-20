---
type: Tribal-pattern
title: ZCode teardown prunes empty MCP containers
description: ZCode teardown prunes empty MCP containers
resource: src/cairn/agent_install/clients/zcode.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:50:31Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.623
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.91
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T17:50:31Z'
  action: captured
  score: 0.91
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:50:31Z'
---

ZCode MCP removal deletes empty mcp and servers containers after removing cairn.
Why: Leaving empty schema-only objects makes a clean uninstall indistinguishable from residual client configuration.
How to apply: Prune only containers made empty by cairn removal and leave unrelated servers untouched.