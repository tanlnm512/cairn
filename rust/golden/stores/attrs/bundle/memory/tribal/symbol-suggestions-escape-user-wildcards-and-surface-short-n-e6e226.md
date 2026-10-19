---
type: Tribal-pattern
title: Symbol suggestions escape user wildcards and surface short names first
description: Symbol suggestions escape user wildcards and surface short names first
resource: src/cairn/dashboard/data.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:57:32Z'
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
- date: '2026-10-07T16:57:32Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:57:32Z'
---

Prefix suggestions bind the typed value as a parameter, escape LIKE wildcards, order shortest names first, and over-fetch by one to report truncation.
Why: suggestion text is user data, and short names are the most useful completions while typing.
How to apply: complement symbol_candidates with symbol_suggest instead of issuing unescaped LIKE queries.