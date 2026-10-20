---
type: Tribal-decision
title: Scoped closure reproduction preserves full ordering
description: Scoped closure reproduction preserves full ordering
resource: src/cairn/graph/dataflow.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:05:17Z'
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
- date: '2026-10-07T15:05:17Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:05:17Z'
---

Closure walks use global kind-and-rowid ordering, resolved before unique-name hops, and scoped rebuilds restrict that same sequence.
Why: deterministic tie-breaking makes restricted maintenance row-for-row equal to the full closure.
How to apply: keep full and scoped paths on the shared level loop and ordering rules.