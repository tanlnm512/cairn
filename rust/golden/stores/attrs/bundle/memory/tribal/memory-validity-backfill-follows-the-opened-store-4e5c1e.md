---
type: Tribal-pattern
title: Memory validity backfill follows the opened store
description: Memory validity backfill follows the opened store
resource: src/cairn/graph/schema.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:52:50Z'
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
- date: '2026-10-07T14:52:50Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:52:50Z'
---

Validity backfill is sentinel-guarded once per database, resolves valid_from from existing extension then recorded timestamp then now, and uses the bundle sibling of the opened database rather than a process default.
Why: Why: a database override must not backfill from a foreign bundle, while read failures should remain retryable.
How to apply: How to apply: keep bundle resolution relative to the opened store and record the sentinel only when no retryable work remains.