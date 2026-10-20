---
type: Tribal-decision
title: Query enrichment stays pure and preserves originals
description: Query enrichment stays pure and preserves originals
resource: src/cairn/graph/query_enrich.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:18:46Z'
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
- date: '2026-10-07T15:18:46Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:18:46Z'
---

Enrichment is deterministic with injected document-frequency data and never removes original query text. Why: callers need reproducibility and enrichment must not manufacture matches when no identifiers are found. How to apply: inject corpus signals at the search boundary and omit an empty dense tail.