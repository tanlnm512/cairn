---
type: Tribal-pattern
title: sqlite-vec loading degrades without raising
description: sqlite-vec loading degrades without raising
resource: src/cairn/graph/ann_index.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:18:20Z'
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
- date: '2026-10-07T15:18:20Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:18:20Z'
---

Loading sqlite-vec returns False for missing packages, unsupported extension loading, and shared-library failures. Why: ANN is an optional acceleration and query paths must survive every platform degradation. How to apply: catch load failures at this boundary and let callers use the brute-force path.