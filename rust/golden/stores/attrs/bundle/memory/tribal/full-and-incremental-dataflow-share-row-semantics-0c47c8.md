---
type: Tribal-pattern
title: Full and incremental dataflow share row semantics
description: Full and incremental dataflow share row semantics
resource: src/cairn/graph/dataflow.py
tags:
- pattern
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
memory_type: pattern
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

Both dataflow build paths compute rows through one shared function and may memoize only repo-invariant cross-repository dependencies.
Why: maintained rows must match a fresh full build and repeated cross-repo lookups dominate bulk builds.
How to apply: change payload semantics in the shared computation, not in one caller.