---
type: Tribal-decision
title: Incremental writes serialize with graph rebuilds
description: Incremental writes serialize with graph rebuilds
resource: src/cairn/graph/incremental.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:04:45Z'
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
- date: '2026-10-07T15:04:45Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:04:45Z'
---

The write phase takes the non-blocking schema build lock and uses a longer busy timeout; the diff scan stays outside the lock.
Why: a repo clear cannot interleave with update writes, while long scans must not block builders or servers.
How to apply: surface lock contention as retryable and keep read-only discovery before the lock.