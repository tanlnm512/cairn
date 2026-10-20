---
type: Tribal-pattern
title: Module symbols defer to same-stem code symbols
description: Module symbols defer to same-stem code symbols
resource: src/cairn/graph/builder.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:04:18Z'
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
- date: '2026-10-07T15:04:18Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:04:18Z'
---

The generated module symbol is appended after declared symbols and owns module-level code plus import edges.
Why: first-wins edge ownership must select a real same-stem code symbol over the synthetic module.
How to apply: preserve declaration order when changing symbol insertion.