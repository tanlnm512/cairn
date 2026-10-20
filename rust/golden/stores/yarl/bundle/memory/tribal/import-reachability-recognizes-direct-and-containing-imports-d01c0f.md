---
type: Tribal-pattern
title: Import reachability recognizes direct and containing imports
description: Import reachability recognizes direct and containing imports
resource: src/cairn/graph/resolver.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:18:55Z'
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
- date: '2026-10-07T15:18:55Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:18:55Z'
---

Import-aware candidates score direct symbol imports and imports containing the candidate type. Why: languages expose methods through both their defining module and an enclosing type import. How to apply: keep highest-score candidates and return ties as ambiguous.