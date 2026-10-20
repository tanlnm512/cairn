---
type: Tribal-decision
title: Knowledge islands queue disconnected pairs without duplicates
description: Knowledge islands queue disconnected pairs without duplicates
resource: src/cairn/knowledge/islands.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:31:10Z'
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
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T17:31:10Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:31:10Z'
---

Only two-doc components and deterministically paired singletons queue doc-link work; larger connected components skip, and any prior task status with the same member set covers an island.
Why: connected documents do not need model-proposed links, and re-ingests must not duplicate tasks.
How to apply: derive units from components and compare frozenset members across every existing task status.