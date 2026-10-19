---
type: Tribal-pattern
title: Skip recording is scoped and best-effort
description: Skip recording is scoped and best-effort
resource: src/cairn/graph/builder.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:04:14Z'
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
- date: '2026-10-07T15:04:14Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:04:14Z'
---

Repo clears replace skip rows before recording, and an individual skip-row insert failure is swallowed.
Why: skips are diagnostics and must neither survive a rebuild nor abort indexing.
How to apply: keep clear-before-record ordering and per-row failure isolation when changing rebuild or skip storage.