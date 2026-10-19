---
type: Tribal-decision
title: 'Rerank per-call override precedence: explicit True beats env gate'
description: 'Rerank per-call override precedence: explicit True beats env gate'
resource: src/cairn/graph/search_pipeline.py
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-10-16T16:43:26Z'
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
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-16T16:43:26Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes:
- memory/tribal/rerank-per-call-override-precedence-explicit-true-beats-env--7805ce
memory_superseded_by: null
valid_from: '2026-10-16T16:43:26Z'
---

semantic_search(rerank=...) is an honest tri-state: True forces the stage on whenever the CrossEncoder is importable (cache/weights guards still apply), False forces off, None defers to CAIRN_RERANK env/marker gate. The env kill switch therefore only binds None/False, not an explicit True. Supersedes the older CAIRN_RERANK=0-beats-everything pin in test_rerank_gating.py.