---
type: Tribal-pattern
title: Reranker loads serialize cache access
description: Reranker loads serialize cache access
resource: src/cairn/graph/reranker.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:18:47Z'
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
- date: '2026-10-07T15:18:47Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:18:47Z'
---

CrossEncoder construction and cache eviction happen under the reranker lock. Why: concurrent searches can double-load weights or evict a winner while another thread reads it. How to apply: guard lazy model loading and cache mutation with one lock.