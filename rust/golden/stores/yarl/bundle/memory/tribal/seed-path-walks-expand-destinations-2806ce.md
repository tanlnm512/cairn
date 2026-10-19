---
type: Tribal-pattern
title: Seed path walks expand destinations
description: Seed path walks expand destinations
resource: src/cairn/graph/taint.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:19:03Z'
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
- date: '2026-10-07T15:19:03Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:19:03Z'
---

Structural seed walks record a destination path then continue expanding it. Why: another pair's shortest route may pass through that destination. How to apply: separate taint sink termination from all-pairs seed traversal.