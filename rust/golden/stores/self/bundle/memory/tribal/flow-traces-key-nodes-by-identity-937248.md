---
type: Tribal-pattern
title: Flow traces key nodes by identity
description: Flow traces key nodes by identity
resource: src/cairn/graph/traversal.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:19:07Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.617
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.88
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T15:19:07Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:19:07Z'
---

Downward flow traversal records ordered chains with each node walked once and cycle back-edges retained. Why: call execution order and cycles are lost in a flat caller set. How to apply: key visits by node identity and surface branches, leaves, and cycles separately.