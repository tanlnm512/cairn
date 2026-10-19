---
type: Tribal-pattern
title: Semantic DF lookups stay memoized and indexed
description: Semantic DF lookups stay memoized and indexed
resource: src/cairn/graph/semantic.py
tags:
- pattern
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
memory_type: pattern
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

Semantic search constructs a per-call term-document-frequency lookup that probes the primary key once per distinct token. Why: enrichment remains pure while retrieval bounds corpus I/O. How to apply: inject the read-only lookup only at semantic_search boundary and keep cutoff decisions in enrichment.