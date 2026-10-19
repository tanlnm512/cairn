---
type: Tribal-decision
title: Closure seeds only structural resolved edges
description: Closure seeds only structural resolved edges
resource: src/cairn/graph/dataflow.py
tags:
- decision
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
memory_type: decision
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

The closure extends structural call and inheritance edges through exact target ids, falling back only to unresolved target names.
Why: name collisions create spurious transitive edges and service topology is outside default impact semantics.
How to apply: keep the edge-kind and resolution filters aligned with closure readers.