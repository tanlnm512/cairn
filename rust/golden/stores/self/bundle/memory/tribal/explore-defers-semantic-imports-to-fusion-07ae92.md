---
type: Tribal-pattern
title: Explore defers semantic imports to fusion
description: Explore defers semantic imports to fusion
resource: src/cairn/graph/explore.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:18:33Z'
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
- date: '2026-10-07T15:18:33Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:18:33Z'
---

Explore imports semantic search only on the fusion path. Why: structural exploration must not load the embeddings and reranking stack. How to apply: keep heavy search dependencies behind the branch that needs them.