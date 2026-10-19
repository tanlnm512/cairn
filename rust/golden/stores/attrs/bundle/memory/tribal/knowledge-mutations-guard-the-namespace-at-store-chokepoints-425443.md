---
type: Tribal-decision
title: Knowledge mutations guard the namespace at store chokepoints
description: Knowledge mutations guard the namespace at store chokepoints
resource: src/cairn/knowledge/store.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:31:15Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.625
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.92
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T17:31:15Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:31:15Z'
---

Document reads may address any bundle concept, but resolution and mutations refuse concepts outside knowledge/; an out-of-namespace target raises while an unresolved id keeps not-found behavior.
Why: direct CLI callers could otherwise modify compass, wiki, or memory concepts through knowledge tools.
How to apply: enforce the shared namespace guard inside store mutation and resolution chokepoints, not only in MCP wrappers.