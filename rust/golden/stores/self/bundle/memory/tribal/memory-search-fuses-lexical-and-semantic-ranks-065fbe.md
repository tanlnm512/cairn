---
type: Tribal-decision
title: Memory search fuses lexical and semantic ranks
description: Memory search fuses lexical and semantic ranks
resource: src/cairn/memory/promotion.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:39:30Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.632
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.95
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T14:39:30Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:39:30Z'
---

Memory recall combines substring and multi-token lexical matching across title, description, and body with a best-chunk cosine scan over persisted embeddings, then fuses the ranked lists with RRF; semantic misses degrade to lexical results.
Why: related memories can share no keyword, the curated memory corpus is small enough for a brute-force scan, and the existing ANN index belongs to the code-embedding table.
How to apply: extend lexical and semantic sources together, preserve the no-backend fallback, and keep fused attribution and validity/supersession filters tested.