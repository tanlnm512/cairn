---
type: Tribal-decision
title: Watcher flushes never overlap updates
description: Watcher flushes never overlap updates
resource: src/cairn/graph/watcher.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:19:08Z'
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
- date: '2026-10-07T15:19:08Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:19:08Z'
---

Watcher flushes leave pending events for a re-armed timer while another update is running. Why: overlapping passes would contend on the build lock, while external CLI builds are already absorbed by lock handling. How to apply: serialize update passes per watcher service and preserve pending events.