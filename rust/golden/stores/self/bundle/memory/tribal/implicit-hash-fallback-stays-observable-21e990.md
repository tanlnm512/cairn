---
type: Tribal-decision
title: Implicit hash fallback stays observable
description: Implicit hash fallback stays observable
resource: src/cairn/graph/embeddings.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:52:39Z'
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
- date: '2026-10-07T14:52:39Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:52:39Z'
---

Hash fallback telemetry and a human warning fire once per process only when local is configured, sentence-transformers is missing, and hash was not explicitly selected.
Why: Why: implicit token-overlap vectors degrade semantic results, while an explicitly chosen hash backend is not degradation.
How to apply: How to apply: query paths should surface is_hash_fallback and warning call sites should pass a stable context without warning for explicit hash use.