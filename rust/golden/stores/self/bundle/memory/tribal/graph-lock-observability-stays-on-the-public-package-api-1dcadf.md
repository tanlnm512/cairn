---
type: Tribal-decision
title: Graph lock observability stays on the public package API
description: Graph lock observability stays on the public package API
resource: src/cairn/graph/__init__.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:18:16Z'
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
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T15:18:16Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:18:16Z'
---

note_contention is called at every swallowed sqlite OperationalError site, including higher layers, and layer tests require imports through cairn.graph rather than cairn.graph.schema. Why: lock contention is cross-cutting observability, and reaching into the schema implementation would couple higher layers to storage internals. How to apply: expose shared graph primitives on the package public API and keep higher-layer imports on that boundary.