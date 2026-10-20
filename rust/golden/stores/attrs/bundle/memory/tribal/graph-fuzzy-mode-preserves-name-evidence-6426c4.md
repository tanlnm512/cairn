---
type: Tribal-decision
title: Graph fuzzy mode preserves name evidence
description: Graph fuzzy mode preserves name evidence
resource: src/cairn/graph/traversal.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:19:05Z'
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
- date: '2026-10-07T15:19:05Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:19:05Z'
---

Precise traversal follows resolved target ids; fuzzy mode also matches preserved target names. Why: precise mode avoids homonym false positives while fuzzy mode remains useful for common names and unresolved calls. How to apply: keep both evidence modes explicit at each traversal boundary.