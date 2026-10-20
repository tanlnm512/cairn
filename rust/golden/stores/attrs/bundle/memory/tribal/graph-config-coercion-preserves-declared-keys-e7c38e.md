---
type: Tribal-pattern
title: Graph config coercion preserves declared keys
description: Graph config coercion preserves declared keys
resource: src/cairn/graph/config.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:18:25Z'
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
- date: '2026-10-07T15:18:25Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:18:25Z'
---

Declared mapping and name-table categories survive empty values while malformed entries drop safely. Why: an explicitly empty list is meaningful configuration, whereas absent or malformed data should not invent keys or crash. How to apply: coerce per declared category and distinguish empty from invalid.