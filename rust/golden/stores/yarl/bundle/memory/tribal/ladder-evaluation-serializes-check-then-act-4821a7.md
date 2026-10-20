---
type: Tribal-pattern
title: Ladder evaluation serializes check-then-act
description: Ladder evaluation serializes check-then-act
resource: src/cairn/graph/embed_ladder.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:18:28Z'
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
- date: '2026-10-07T15:18:28Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:18:28Z'
---

Ladder cache evaluation and degradation notification membership use double-checked locking, while telemetry emits outside the lock. Why: concurrent threads otherwise duplicate expensive probes and notifications, while telemetry must never hold the ladder lock. How to apply: recheck mutable cache state under its lock and perform side effects after release.