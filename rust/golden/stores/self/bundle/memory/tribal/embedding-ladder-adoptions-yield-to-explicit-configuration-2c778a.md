---
type: Tribal-decision
title: Embedding ladder adoptions yield to explicit configuration
description: Embedding ladder adoptions yield to explicit configuration
resource: src/cairn/graph/embeddings.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:52:37Z'
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
- date: '2026-10-07T14:52:37Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:52:37Z'
---

Session ladder adoptions store alias, model, and backend overrides only after parity checks; explicit environment configuration wins, reset clears all adoptions, and writes are single locked assignments.
Why: Why: a candidate must serve the already-stored corpus safely while user configuration remains authoritative and observable.
How to apply: How to apply: read session overrides under the backend lock and never mutate process environment to implement adoption.