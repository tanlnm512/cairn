---
type: Tribal-decision
title: Multi-vector embeddings use per-kind staleness
description: Multi-vector embeddings use per-kind staleness
resource: src/cairn/graph/embeddings.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:52:35Z'
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
- date: '2026-10-07T14:52:35Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:52:35Z'
---

Name and docstring multi-vector kinds have their own text producers and content hashes, are upserted by symbol, model, and kind, and a vanished docstring text deletes its stale row.
Why: Why: fields refresh independently and must not inherit base-chunk staleness or violate the identity-floor tests that iterate CHUNK_VARIANTS.
How to apply: How to apply: every new vector kind needs a producer, per-kind hash, upsert key, and stale-row cleanup.