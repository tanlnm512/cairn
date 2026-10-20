---
type: Tribal-pattern
title: One public-symbol predicate keeps dataflow parity
description: One public-symbol predicate keeps dataflow parity
resource: src/cairn/graph/dataflow.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:05:15Z'
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
- date: '2026-10-07T15:05:15Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:05:15Z'
---

Full and incremental dataflow paths share one language-aware public-symbol predicate.
Why: private renames and visibility changes must delete or replace exactly the rows a full build would index.
How to apply: route every dataflow row selection through the shared predicate.