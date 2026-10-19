---
type: Tribal-decision
title: ANN source tables keep isolated vec0 indexes
description: ANN source tables keep isolated vec0 indexes
resource: src/cairn/graph/ann_index.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:18:18Z'
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
- date: '2026-10-07T15:18:18Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:18:18Z'
---

Each embedding source table gets its own per-model vec0 table with rowid keys. Why: base and multi-vector source populations differ, and sharing one index would violate each table's rowid contract. How to apply: map every new embedding source to a distinct prefixed vec0 table and preserve source rowids.