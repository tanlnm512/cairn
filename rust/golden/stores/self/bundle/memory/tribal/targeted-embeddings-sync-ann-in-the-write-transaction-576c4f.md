---
type: Tribal-pattern
title: Targeted embeddings sync ANN in the write transaction
description: Targeted embeddings sync ANN in the write transaction
resource: src/cairn/graph/embeddings.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:52:43Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T14:52:43Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:52:43Z'
---

Targeted symbol embedding batches one model call, skips fresh and unknown ids, upserts each selected symbol, and synchronizes its vec0 delete-plus-insert inside the same transaction.
Why: Why: single-symbol write paths must be immediately visible to ANN queries without waiting for the next wholesale rebuild.
How to apply: How to apply: route incremental symbol writes through embed_symbols rather than copying its upsert without index sync.