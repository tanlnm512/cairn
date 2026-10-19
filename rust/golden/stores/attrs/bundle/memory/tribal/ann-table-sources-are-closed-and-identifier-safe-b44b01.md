---
type: Tribal-decision
title: ANN table sources are closed and identifier-safe
description: ANN table sources are closed and identifier-safe
resource: src/cairn/graph/ann_index.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:18:19Z'
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
- date: '2026-10-07T15:18:19Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:18:19Z'
---

ANN table names accept only a closed source set and sanitize the model identifier before SQL interpolation. Why: model identifiers originate outside the process and free-form table names would create an injection surface. Why2: invalid model characters still need deterministic valid SQLite identifiers. How to apply: reject unknown sources before SQL and map accepted sources through the shared sanitizer.