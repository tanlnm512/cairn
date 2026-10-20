---
type: Tribal-decision
title: Lexical search unions FTS prefixes with LIKE substrings
description: Lexical search unions FTS prefixes with LIKE substrings
resource: src/cairn/graph/lexical.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:18:38Z'
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
- date: '2026-10-07T15:18:38Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:18:38Z'
---

Only pure trailing-wildcard patterns use a direct FTS prefix query; other shapes union a LIKE substring fallback. Why: unicode61 does not split camelCase and FTS prefixes cannot express suffix or interior matches. How to apply: keep fallback additive, deduplicated, and limit-capped.