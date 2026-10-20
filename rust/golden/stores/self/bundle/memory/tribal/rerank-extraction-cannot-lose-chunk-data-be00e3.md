---
type: Tribal-pattern
title: Rerank extraction cannot lose chunk data
description: Rerank extraction cannot lose chunk data
resource: src/cairn/graph/reranker.py
tags:
- pattern
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
memory_type: pattern
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

Structured rerank text promotes identity and signature fields to the head while always retaining the full chunk in the tail. Why: best-effort section parsing must only fail to prioritize, never discard content. How to apply: treat extracted fields as optional additions over the complete stored chunk.