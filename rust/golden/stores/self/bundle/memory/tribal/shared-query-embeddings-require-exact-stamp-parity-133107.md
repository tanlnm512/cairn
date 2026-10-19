---
type: Tribal-decision
title: Shared query embeddings require exact stamp parity
description: Shared query embeddings require exact stamp parity
resource: src/cairn/graph/federation.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:18:36Z'
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
- date: '2026-10-07T15:18:36Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:18:36Z'
---

A query is embedded once across stores only when every reachable vector store carries exactly the current model stamp. Why: embedding a query with one model against another model's vectors invalidates score comparability. How to apply: retain per-store embedding for absent, foreign, ambiguous, or unreadable stamp mixes.