---
type: Tribal-pattern
title: Embedding caches serialize first-writer resolution
description: Embedding caches serialize first-writer resolution
resource: src/cairn/graph/embeddings.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:52:36Z'
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
- date: '2026-10-07T14:52:36Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:52:36Z'
---

Model loading and effective-backend resolution use double-checked locks, local references, and first-writer cache stamps; server network probes run outside the lock.
Why: Why: the embed flusher and tool threads can race loads and resolutions, causing double-loaded weights, lost verdicts, or eviction KeyErrors.
How to apply: How to apply: keep check-then-stamp sections under the shared backend lock and retain loaded values in locals across eviction.