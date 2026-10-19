---
type: Tribal-pattern
title: Server availability probes settle on one verdict
description: Server availability probes settle on one verdict
resource: src/cairn/graph/embeddings.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:52:38Z'
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
- date: '2026-10-07T14:52:38Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:52:38Z'
---

Server probes perform network I/O outside the backend lock, then atomically stamp the first completed true-or-false verdict for the process.
Why: Why: concurrent first calls may duplicate a probe, but holding the lock across network I/O or accepting conflicting verdicts is worse.
How to apply: How to apply: cache both success and failure outcomes and invalidate only through reset_backend_cache.