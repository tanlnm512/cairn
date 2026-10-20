---
type: Tribal-pattern
title: Derived maintenance seeds from repaired graph deltas
description: Derived maintenance seeds from repaired graph deltas
resource: src/cairn/graph/incremental.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:04:46Z'
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
- date: '2026-10-07T15:04:46Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:04:46Z'
---

Closure sources combine old and new symbols, repair and name-repair sources, and captured ancestors; dataflow names add targets reachable through changed and repaired edges.
Why: both persisted row identity and resolver retargeting change derived reachability.
How to apply: keep row-id seeds for repaired edges and name seeds for dataflow identity.