---
type: Tribal-pattern
title: semantic_search embed seam is the global emb.embed_query attribute
description: semantic_search embed seam is the global emb.embed_query attribute
resource: src/cairn/graph/federation.py
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-09-17T09:40:37Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.621
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.9
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-09-17T09:40:37Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-17T09:40:37Z'
---

semantic_search (src/cairn/graph/semantic.py) reaches the embedding backend only through the process-global embeddings.embed_query attribute, resolved at call time in _dense_embed_guarded; it takes no precomputed-vector or backend parameter. Cross-call query-vector sharing (federation._shared_query_embed) swaps that module attribute under a lock and restores it in finally; memoizing by text is safe because backends are deterministic per text. Why: any caller wanting to share one query vector across stores has no parameter seam. How to apply: swap emb.embed_query, never semantic_search internals; keep the swap lock-serialized.