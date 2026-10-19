---
type: Tribal-decision
title: Route heuristics stay bounded and labeled
description: Route heuristics stay bounded and labeled
resource: src/cairn/parsers/routes.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:15:12Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.623
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.91
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T17:15:12Z'
  action: captured
  score: 0.91
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:15:12Z'
---

JSX and object-literal route extraction uses regexes whose gaps cannot cross angle brackets or object boundaries and marks every result provenance=heuristic.

Why: these constructs are outside the generic Symbol and Edge AST model, and unbounded matching can pair unrelated paths with handlers after multiline reformatting.
How to apply: keep route regexes element-bounded, accept misses on unusual formatting rather than over-pairing, and preserve the heuristic provenance marker.