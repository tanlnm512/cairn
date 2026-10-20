---
type: Tribal-decision
title: Closure impact trades DFS details for bounded coverage
description: Closure impact trades DFS details for bounded coverage
resource: src/cairn/graph/dataflow.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:05:20Z'
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
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T15:05:20Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:05:20Z'
---

Closure impact returns shortest caller depth, deterministic rows, and a precise-DFS superset without per-node fetch caps, but cannot attribute cycles.
Why: one indexed statement answers bounded impact more cheaply while preserving useful minimum-hop semantics.
How to apply: document any deviation from DFS shape and keep truncation exact.