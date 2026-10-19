---
type: Tribal-decision
title: Closure impact preserves DFS parity
description: Closure impact preserves DFS parity
resource: src/cairn/graph/traversal.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:19:06Z'
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
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T15:19:06Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:19:06Z'
---

The precomputed closure serves only servable queries and returns matching shortest depths and ordering, while forced use explicitly accepts missing cycle reporting. Why: indexed traversal must not silently change result semantics. How to apply: enforce the precision, depth, structural, and seed-cycle gates before automatic closure use.