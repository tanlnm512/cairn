---
type: Tribal-pattern
title: Knowledge graph edges require resolvable endpoints
description: Knowledge graph edges require resolvable endpoints
resource: src/cairn/dashboard/data.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:57:36Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.625
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.92
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T16:57:36Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:57:36Z'
---

Knowledge and memory edges are emitted only when both endpoint concepts resolve, while stored supersede pairs are mirrored two-way.
Why: rows left between rebuilds must not create phantom canvas nodes, and a supersede pair remains navigable when either half loses its file.
How to apply: build the node-id set first and pass every candidate through the guarded edge adder.