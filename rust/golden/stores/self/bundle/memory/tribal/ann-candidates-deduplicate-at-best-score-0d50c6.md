---
type: Tribal-decision
title: ANN candidates deduplicate at best score
description: ANN candidates deduplicate at best score
resource: src/cairn/graph/semantic.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:18:59Z'
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
- date: '2026-10-07T15:18:59Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:18:59Z'
---

ANN candidates reapply the similarity threshold and consolidate repeated symbols at their maximum score. Why: vec0 nearest-k has no threshold concept and multi-vector tables can return one symbol per vector. How to apply: threshold best hits and rank each symbol once.