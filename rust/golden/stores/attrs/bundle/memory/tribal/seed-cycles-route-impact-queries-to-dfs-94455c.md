---
type: Tribal-decision
title: Seed cycles route impact queries to DFS
description: Seed cycles route impact queries to DFS
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

Closure readers detect reachability among seed ids and switch cycle-reporting queries to DFS.
Why: the closure proves a seed cycle exists but cannot recover its back-edge symbol and depth pairs.
How to apply: keep the cycle gate before answering from transitive edges.