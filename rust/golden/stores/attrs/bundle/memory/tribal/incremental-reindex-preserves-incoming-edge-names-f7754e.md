---
type: Tribal-decision
title: Incremental reindex preserves incoming edge names
description: Incremental reindex preserves incoming edge names
resource: src/cairn/graph/incremental.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:04:40Z'
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
- date: '2026-10-07T15:04:40Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:04:40Z'
---

Before deleting symbols, incoming edges and imports are backfilled with target names, nulled, and queued for name repair.
Why: resolved edges drop their bare names, so deleting only ids permanently orphans precise callers.
How to apply: capture old target names before nulling and repair both modified and deleted symbol names.