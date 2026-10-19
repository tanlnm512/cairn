---
type: Tribal-decision
title: Reranker downloads auto-enable by marker
description: Reranker downloads auto-enable by marker
resource: src/cairn/graph/reranker.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:18:47Z'
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
- date: '2026-10-07T15:18:47Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:18:47Z'
---

A successful reranker download writes a persistent marker that behaves like enabling the feature, while an explicit false environment value still wins. Why: a child CLI cannot export configuration to its parent shell, and users need a hard kill switch. How to apply: treat the marker as enablement evidence, never as an override of explicit off.