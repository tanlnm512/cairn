---
type: Tribal-decision
title: Whole-file swaps preserve analytics history
description: Whole-file swaps preserve analytics history
resource: src/cairn/graph/schema.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:52:53Z'
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
- date: '2026-10-07T14:52:53Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:52:53Z'
---

Whole-file swaps copy retained analytics tables with fresh ids in source order and deliberately exclude operational pending-sync state; missing or incompatible old tables degrade to fresh history.
Why: Why: build trends, degradation events, and tool-health windows must survive rebuilds, while pending edits belong to the replaced graph.
How to apply: How to apply: update the retained-table contract when adding durable analytics and keep operational state out of it.