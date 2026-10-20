---
type: Tribal-decision
title: Dataflow symbol caps bound BFS work
description: Dataflow symbol caps bound BFS work
resource: src/cairn/graph/dataflow.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:05:16Z'
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
- date: '2026-10-07T15:05:16Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:05:16Z'
---

The resolved cap limits public symbols per build, warns when truncating, and leaves the partial table usable.
Why: each symbol triggers graph traversal, so uncapped builds can hang on large repositories.
How to apply: keep explicit, environment, and default resolution ordered and report partial coverage.