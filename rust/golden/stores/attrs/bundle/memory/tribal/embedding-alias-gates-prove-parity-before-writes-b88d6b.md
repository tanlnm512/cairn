---
type: Tribal-decision
title: Embedding alias gates prove parity before writes
description: Embedding alias gates prove parity before writes
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

The alias preflight runs for stamped server backends, caches one verdict per process and stamp, treats zero stored rows as a vacuous pass, and raises before any write on parity or dimension failure.
Why: Why: a model-id alias must not silently mix incompatible producer vectors into one corpus.
How to apply: How to apply: invoke the preflight before every embedding writer path and reset its verdict when the stamp changes in tests.