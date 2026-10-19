---
type: Tribal-decision
title: Lexical degradation separates lock contention
description: Lexical degradation separates lock contention
resource: src/cairn/graph/lexical.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:18:40Z'
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
- date: '2026-10-07T15:18:40Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:18:40Z'
---

Only busy or locked SQLite failures become contention telemetry; other FTS failures degrade to LIKE with one warning. Why: syntax, missing-index, and corruption failures are expected degradation rather than cross-process lock events. How to apply: discriminate SQLite failures before contention telemetry and keep search-mode warn keys separate.