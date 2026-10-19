---
type: Tribal-pattern
title: LIKE fallback escapes only user literals
description: LIKE fallback escapes only user literals
resource: src/cairn/graph/lexical.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:18:39Z'
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
- date: '2026-10-07T15:18:39Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:18:39Z'
---

Lexical LIKE fallback maps user stars to percent while escaping literal percent and underscore segments and deciding wrapping from the raw pattern. Why: user underscores must not become wildcards, and escaping must not suppress substring wrapping. How to apply: split wildcard syntax before escaping literal segments.