---
type: Tribal-decision
title: Ladder aliases retain the stored corpus stamp
description: Ladder aliases retain the stored corpus stamp
resource: src/cairn/graph/embed_ladder.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:18:29Z'
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
- date: '2026-10-07T15:18:29Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:18:29Z'
---

Adopting a rung pins the stored corpus stamp rather than the candidate-derived stamp. Why: reads and writes must continue against existing embeddings without a re-embed. How to apply: move only the serving model identifier and keep storage stamps stable during adoption.