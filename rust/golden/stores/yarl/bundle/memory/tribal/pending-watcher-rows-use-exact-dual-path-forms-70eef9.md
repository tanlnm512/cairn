---
type: Tribal-decision
title: Pending watcher rows use exact dual path forms
description: Pending watcher rows use exact dual path forms
resource: src/cairn/graph/watcher.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:19:10Z'
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
- date: '2026-10-07T15:19:10Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:19:10Z'
---

Watcher writes pending rows in both path forms that reindex deletion and staleness matching use. Why: primary-key deduplication, row deletion, and staleness display read different path forms. How to apply: insert and clear exactly the repository-relative and reconstructed absolute forms.