---
type: Tribal-pattern
title: Dataflow maintenance follows changed caller names
description: Dataflow maintenance follows changed caller names
resource: src/cairn/graph/dataflow.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:05:19Z'
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
- date: '2026-10-07T15:05:19Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:05:19Z'
---

Dataflow rows are keyed by name and refreshed when a symbol caller chain changes; surviving public duplicates upsert per name and repository while vanished names delete.
Why: name identity drives lookup, and visibility changes can add or remove the only public candidate.
How to apply: seed affected names from graph deltas and use the shared public predicate.