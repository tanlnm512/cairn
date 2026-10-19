---
type: Tribal-pattern
title: Memory tier moves carry embeddings without re-embedding
description: Memory tier moves carry embeddings without re-embedding
resource: src/cairn/memory/promotion.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:39:31Z'
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
- date: '2026-10-07T14:39:31Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:39:31Z'
---

Promotion, demotion, and decay moves rename an unchanged memory embedding row to its new concept id in place, clear stale memory-tier labels, and best-effort reap orphan vectors after decay.
Why: unchanged content need not be re-embedded, stale labels make canonical docs masquerade as memories, and orphan vectors tax every brute-force memory scan.
How to apply: any tier-move path must update or reap persisted embeddings in the same operation and preserve the public cairn.graph import boundary.