---
type: Tribal-pattern
title: Resolver signals travel in abstain-safe tuple slots
description: Resolver signals travel in abstain-safe tuple slots
resource: src/cairn/graph/builder.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:04:20Z'
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
- date: '2026-10-07T15:04:20Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:04:20Z'
---

Receiver type, call arity, and generic tier are carried beside each raw edge; missing signals leave resolver tiers unchanged.
Why: parser-only evidence must reach the resolver without persisting speculative metadata.
How to apply: treat additions or reorderings as resolver contract changes and keep None abstaining.