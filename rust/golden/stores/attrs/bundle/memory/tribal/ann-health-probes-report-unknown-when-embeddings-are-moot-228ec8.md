---
type: Tribal-decision
title: ANN health probes report unknown when embeddings are moot
description: ANN health probes report unknown when embeddings are moot
resource: src/cairn/dashboard/data.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:57:34Z'
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
- date: '2026-10-07T16:57:34Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:57:34Z'
---

With zero embeddings or an unresolved model, ANN index existence and row counts read None rather than False or zero.
Why: a fresh store intentionally has no vec0 table and warming probes cannot identify its model; reporting absence would mislabel a normal state as an index failure.
How to apply: keep index fields optional and gate index probes on both embedding rows and a resolved model.