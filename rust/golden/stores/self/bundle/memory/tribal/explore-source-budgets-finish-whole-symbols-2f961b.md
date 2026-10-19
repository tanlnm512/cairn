---
type: Tribal-pattern
title: Explore source budgets finish whole symbols
description: Explore source budgets finish whole symbols
resource: src/cairn/graph/explore.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:18:32Z'
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
- date: '2026-10-07T15:18:32Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:18:32Z'
---

Source-span reads stop between symbols, keep relative stored paths as result keys, and omit unreadable files. Why: truncating one symbol destroys its contract and absolute keys leak machine layout. How to apply: enforce budget at symbol boundaries and return portable paths.