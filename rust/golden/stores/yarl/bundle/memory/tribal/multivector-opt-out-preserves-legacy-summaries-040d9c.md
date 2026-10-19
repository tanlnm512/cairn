---
type: Tribal-decision
title: Multivector opt-out preserves legacy summaries
description: Multivector opt-out preserves legacy summaries
resource: src/cairn/graph/embeddings.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:52:43Z'
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
- date: '2026-10-07T14:52:43Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:52:43Z'
---

Disabling the multivector flag performs zero embeddings_mv writes and leaves the base embeddings flow and summary shape unchanged; enabling it adds only the mv_embedded key.
Why: Why: callers and tests rely on the single-vector build remaining byte-compatible when the newer parallel table is declined.
How to apply: How to apply: new optional embedding passes must preserve the prior summary when disabled and add keys only when active.