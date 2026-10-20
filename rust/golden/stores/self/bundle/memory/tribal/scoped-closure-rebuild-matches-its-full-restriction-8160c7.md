---
type: Tribal-pattern
title: Scoped closure rebuild matches its full restriction
description: Scoped closure rebuild matches its full restriction
resource: src/cairn/graph/dataflow.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:05:18Z'
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
- date: '2026-10-07T15:05:18Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:05:18Z'
---

Incremental closure maintenance deletes affected sources and recomputes exactly those rows through the full builder core.
Why: affected-set callers need maintained rows indistinguishable from a fresh full closure.
How to apply: fall back to a full build when no trusted closure prestate exists.