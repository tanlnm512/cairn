---
type: Tribal-pattern
title: ANN row counts detect wholesale index drift
description: ANN row counts detect wholesale index drift
resource: src/cairn/graph/ann_index.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:18:23Z'
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
- date: '2026-10-07T15:18:23Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:18:23Z'
---

Comparing vec0 row count with its source embedding count identifies indexes left stale by incremental writes. Why: incremental embedding writes can bypass a wholesale vec0 rebuild. How to apply: use read-only count comparisons in doctor checks and return None on unreadable indexes.