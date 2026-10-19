---
type: Tribal-decision
title: SQLite IN batches stay portable and deterministic
description: SQLite IN batches stay portable and deterministic
resource: src/cairn/graph/dataflow.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:05:15Z'
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
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T15:05:15Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:05:15Z'
---

Affected-set IN clauses use bounded, deduplicated, sorted batches.
Why: older SQLite hosts parameterize near 999 values, and deterministic batches make logs and tests reproducible.
How to apply: keep the chunk below host limits rather than relying on connection-specific limits.