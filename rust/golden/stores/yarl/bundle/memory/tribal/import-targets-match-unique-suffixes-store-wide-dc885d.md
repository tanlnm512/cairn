---
type: Tribal-decision
title: Import targets match unique suffixes store-wide
description: Import targets match unique suffixes store-wide
resource: src/cairn/graph/builder.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:04:21Z'
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
- date: '2026-10-07T15:04:21Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:04:21Z'
---

Normalized import bases match indexed dotted suffixes longest-first across the store, but only unique candidates create edges.
Why: source scope must not hide cross-repo targets, while ambiguous or same-named candidates must not guess.
How to apply: delete affected source edges first and keep the candidate universe store-wide for scoped rebuilds.