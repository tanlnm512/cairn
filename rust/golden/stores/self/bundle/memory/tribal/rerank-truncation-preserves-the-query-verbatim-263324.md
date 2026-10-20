---
type: Tribal-decision
title: Rerank truncation preserves the query verbatim
description: Rerank truncation preserves the query verbatim
resource: src/cairn/graph/reranker.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:18:50Z'
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
- date: '2026-10-07T15:18:50Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:18:50Z'
---

Candidate text is pre-truncated to the token budget left after the query. Why: cross-encoder longest-first truncation can otherwise trim either side and change the query. How to apply: reserve query tokens first and remove only candidate tail content.