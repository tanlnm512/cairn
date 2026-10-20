---
type: Tribal-decision
title: Repo rebuilds purge embeddings and ANN rows together
description: Repo rebuilds purge embeddings and ANN rows together
resource: src/cairn/graph/builder.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:04:22Z'
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
- date: '2026-10-07T15:04:22Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:04:22Z'
---

Full repo clears delete embedding and multivector rows and remove doomed rowids from each model-scoped ANN index.
Why: foreign keys forbid orphan rows and reused rowids can otherwise pair with unrelated vectors.
How to apply: keep relational and ANN cleanup in the same rebuild path; multivector indexes rebuild wholesale.