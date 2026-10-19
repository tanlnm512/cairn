---
type: Tribal-decision
title: Semantic retrieval keeps one embed call per pass
description: Semantic retrieval keeps one embed call per pass
resource: src/cairn/graph/semantic.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:19:00Z'
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
- date: '2026-10-07T15:19:00Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:19:00Z'
---

Semantic search embeds once per retrieval pass; optional PRF is the only second pass and replaces reranking rather than stacking it. Why: query embeddings dominate latency and optional stages must stay budget-neutral. How to apply: enrich once before embedding and force rerank off when PRF runs.