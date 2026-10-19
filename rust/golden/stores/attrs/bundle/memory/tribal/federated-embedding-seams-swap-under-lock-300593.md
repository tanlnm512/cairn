---
type: Tribal-pattern
title: Federated embedding seams swap under lock
description: Federated embedding seams swap under lock
resource: src/cairn/graph/federation.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:18:36Z'
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
- date: '2026-10-07T15:18:36Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:18:36Z'
---

Shared federated embedding replaces the process query-embed seam with a memoizing wrapper under a lock and always restores it. Why: semantic search reaches its backend through that seam, and a failed swap would poison unrelated queries. How to apply: serialize temporary seam replacement and restore it in finally blocks.