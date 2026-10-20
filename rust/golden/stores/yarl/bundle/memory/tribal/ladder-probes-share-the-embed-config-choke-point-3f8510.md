---
type: Tribal-decision
title: Ladder probes share the embed config choke point
description: Ladder probes share the embed config choke point
resource: src/cairn/graph/embed_ladder.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:18:31Z'
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
- date: '2026-10-07T15:18:31Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:18:31Z'
---

Ladder probes resolve environment and persisted embedding configuration through the same config path as the main embed route. Why: parity checks produce false failures when authenticated server or local-model settings differ between paths. How to apply: keep one config resolver for probing and serving.