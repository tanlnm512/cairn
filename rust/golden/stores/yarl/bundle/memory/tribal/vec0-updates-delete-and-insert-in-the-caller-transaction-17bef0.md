---
type: Tribal-pattern
title: vec0 updates delete and insert in the caller transaction
description: vec0 updates delete and insert in the caller transaction
resource: src/cairn/graph/ann_index.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:18:20Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T15:18:20Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:18:20Z'
---

vec0 rows are updated with delete-then-insert and never committed by the sync helper. Why: vec0 lacks usable replace semantics, and vector writes must land atomically with their embeddings row while failures remain doctor-detectable drift. How to apply: join vec0 mutations to the caller transaction and leave repair to doctor and rebuild commands.