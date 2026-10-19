---
type: Tribal-decision
title: Rerank failures preserve fused order
description: Rerank failures preserve fused order
resource: src/cairn/graph/reranker.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:18:51Z'
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
- date: '2026-10-07T15:18:51Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:18:51Z'
---

Any rerank disablement, missing cache, or prediction failure returns the hybrid candidates unchanged and reports reranked false. Why: reranking is an optional precision stage and search must remain available. How to apply: fail closed to the existing ranking and never block on downloads.