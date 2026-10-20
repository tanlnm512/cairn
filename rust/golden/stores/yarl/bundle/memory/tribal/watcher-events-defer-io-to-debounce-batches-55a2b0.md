---
type: Tribal-decision
title: Watcher events defer IO to debounce batches
description: Watcher events defer IO to debounce batches
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

The watcher dispatch path performs only extension filtering and defers gitignore, config, and size checks to a batched update. Why: watchdog's dispatch thread must remain cheap and nonblocking. How to apply: add only constant-time gates to event handlers and move IO to flush.