---
type: Tribal-decision
title: Resolver excludes module symbols
description: Resolver excludes module symbols
resource: src/cairn/graph/resolver.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:18:52Z'
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
- date: '2026-10-07T15:18:52Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:18:52Z'
---

Bare-name resolution excludes module symbols. Why: modules are structural endpoints, and a file named after its class would make every same-name reference ambiguous. How to apply: keep module nodes in graph relationships but outside name-resolution targets.