---
type: Tribal-pattern
title: Session chains never split on unknown timestamps
description: Session chains never split on unknown timestamps
resource: src/cairn/dashboard/data.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:57:40Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.625
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.92
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T16:57:40Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:57:40Z'
---

A chain splits only when consecutive known timestamps exceed the session gap; NULL timestamps stay in the current chain, and window filters run before grouping.
Why: unknown distance is not evidence of either continuity or a gap, and NULL rows cannot match a window predicate.
How to apply: retain numeric invoked_at handling and keep chain caps separate from full-chain count and endpoint truth.