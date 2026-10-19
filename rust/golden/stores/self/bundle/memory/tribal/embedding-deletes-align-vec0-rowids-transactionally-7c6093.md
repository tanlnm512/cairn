---
type: Tribal-pattern
title: Embedding deletes align vec0 rowids transactionally
description: Embedding deletes align vec0 rowids transactionally
resource: src/cairn/graph/embeddings.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:52:41Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T14:52:41Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:52:41Z'
---

Base-row deletion first collects each doomed model and rowid, removes matching vec0 entries in the same caller-owned transaction, and covers orphaned base and multi-vector rows.
Why: Why: SQLite can reuse a rowid, so a stale vec0 entry can otherwise join an unrelated embedding and return wrong results.
How to apply: How to apply: every embeddings delete path must collect model-scoped rowids before SQL deletion and remain transaction-neutral.