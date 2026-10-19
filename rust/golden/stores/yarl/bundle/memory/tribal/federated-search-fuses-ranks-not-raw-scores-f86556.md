---
type: Tribal-decision
title: Federated search fuses ranks not raw scores
description: Federated search fuses ranks not raw scores
resource: src/cairn/graph/federation.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:18:35Z'
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
- date: '2026-10-07T15:18:35Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:18:35Z'
---

Federated search merges stores by reciprocal rank fusion, substitutes BM25 for stores without current embeddings, and reports dropped stores without aborting. Why: per-store similarity and BM25 scores are not comparable, and one unavailable store should not erase the query. How to apply: fuse rankings and classify every store in the result states.