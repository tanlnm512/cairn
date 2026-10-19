---
type: Tribal-pattern
title: Same-file edge lookup stays linear in file symbols
description: Same-file edge lookup stays linear in file symbols
resource: src/cairn/graph/builder.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:04:19Z'
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
- date: '2026-10-07T15:04:19Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:04:19Z'
---

Edge source resolution builds one name-to-id map from the current file and excludes its synthetic module row.
Why: consulting the global symbol map per edge makes insertion quadratic across the store.
How to apply: keep lookup scoped to symbols declared by the file being inserted.