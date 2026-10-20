---
type: Tribal-decision
title: ANN query failures mean caller fallback
description: ANN query failures mean caller fallback
resource: src/cairn/graph/ann_index.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:18:21Z'
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
- date: '2026-10-07T15:18:21Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:18:21Z'
---

ann_query returns None rather than zero results when the extension or index is unavailable, and multi-vector hits may repeat a symbol. Why: callers must preserve brute-force correctness and deduplicate by best score. How to apply: treat None as fallback and consolidate repeated symbol ids at their maximum score.