---
type: Tribal-pattern
title: impact_analysis max_depth counts hops beyond direct callers
description: impact_analysis max_depth counts hops beyond direct callers
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-09-17T14:06:09Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.578
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.7
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-09-17T14:06:09Z'
  action: captured
  score: 0.7
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-17T14:06:09Z'
---

graph.traversal.impact_analysis(max_depth=N) returns impacted rows at depths 0..N: direct callers are depth 0 and max_depth bounds hops BEYOND them. A total-hop contract (depth 1 = direct callers only) must call it with max_depth=depth-1. Exposed by the D-004 /editor/symbol depth clamp pins.