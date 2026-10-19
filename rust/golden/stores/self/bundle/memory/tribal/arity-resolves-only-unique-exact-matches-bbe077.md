---
type: Tribal-decision
title: Arity resolves only unique exact matches
description: Arity resolves only unique exact matches
resource: src/cairn/graph/resolver.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:18:54Z'
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
- date: '2026-10-07T15:18:54Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:18:54Z'
---

Arity breaks an otherwise ambiguous tier only when exactly one candidate has that recorded count. Why: missing call arity, unknown candidate arity, or multiple matches must preserve precision over recall. How to apply: use arity as a tiebreaker, never as a fuzzy match.