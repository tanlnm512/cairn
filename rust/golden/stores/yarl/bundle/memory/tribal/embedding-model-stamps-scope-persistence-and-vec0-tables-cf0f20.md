---
type: Tribal-decision
title: Embedding model stamps scope persistence and vec0 tables
description: Embedding model stamps scope persistence and vec0 tables
resource: src/cairn/graph/embeddings.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:52:34Z'
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
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T14:52:34Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:52:34Z'
---

Embedding model identity is stored on every row and folded into model-scoped vec0 table names; server stamps derive from the resolved base URL netloc plus request model, an explicit stamp override is verbatim, and a rung-1 adoption pins the stored stamp while requests use the adopted model.
Why: Why: producer swaps must invalidate, purge, and re-embed stale vectors without a schema change, while an adopted parity-proven producer can serve existing rows.
How to apply: How to apply: treat current_model as a compatibility boundary; changing its derivation requires coordinated staleness, purge, and vec0 table handling.