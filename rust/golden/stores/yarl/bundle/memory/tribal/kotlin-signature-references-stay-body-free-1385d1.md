---
type: Tribal-decision
title: Kotlin signature references stay body-free
description: Kotlin signature references stay body-free
resource: src/cairn/parsers/kotlin.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:15:10Z'
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
- date: '2026-10-07T17:15:10Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:15:10Z'
---

Kotlin references edges cover user_type nodes on parameters, return types, properties, and constructor parameters, while excluding declaration bodies.

Why: signatures define the callable API surface; local-variable annotations add noise and can overwhelm the relationship signal.
How to apply: extend type-reference collection only to signature-bearing nodes and keep function, class, and enum bodies excluded.