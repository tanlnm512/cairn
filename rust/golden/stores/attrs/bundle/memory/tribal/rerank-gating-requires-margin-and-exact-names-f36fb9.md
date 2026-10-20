---
type: Tribal-decision
title: Rerank gating requires margin and exact names
description: Rerank gating requires margin and exact names
resource: src/cairn/graph/semantic.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:18:57Z'
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
- date: '2026-10-07T15:18:57Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:18:57Z'
---

Rerank is skipped only when a fused ranking has the configured margin and its top result exactly matches the raw query, and never under hash vectors. Why: a wide rank margin proves stability, not relevance, while hash vectors need reranking as their only semantic signal. How to apply: gate before pair construction and keep the raw query for corroboration.