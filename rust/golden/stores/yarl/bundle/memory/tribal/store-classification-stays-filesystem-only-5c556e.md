---
type: Tribal-pattern
title: Store classification stays filesystem-only
description: Store classification stays filesystem-only
resource: src/cairn/graph/federation.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:18:37Z'
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
- date: '2026-10-07T15:18:37Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:18:37Z'
---

Federated stores classify missing and unindexed homes without opening databases, while locked databases classify during query. Why: classification itself must not contend for locks. How to apply: reserve query-time classification for databases that exist but cannot be read.