---
type: Tribal-decision
title: Explore marks hash-vector weakness
description: Explore marks hash-vector weakness
resource: src/cairn/graph/explore.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:18:34Z'
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
- date: '2026-10-07T15:18:34Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:18:34Z'
---

Explore warns once when semantic expansion runs on hash token-overlap vectors. Why: callers need to know that an apparent semantic expansion carries no semantic meaning. How to apply: detect explicit and fallback hash embeddings before treating expansion as quality.