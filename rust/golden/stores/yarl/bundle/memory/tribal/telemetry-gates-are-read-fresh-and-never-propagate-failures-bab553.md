---
type: Tribal-decision
title: Telemetry gates are read fresh and never propagate failures
description: Telemetry gates are read fresh and never propagate failures
resource: src/cairn/telemetry/events.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:24:00Z'
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
- date: '2026-10-07T14:24:00Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:24:00Z'
---

Public telemetry emitters read CAIRN_TELEMETRY and read-only mode on each call, suppress work before buffering when either disables writes, and contain serialization or enqueue failures at debug logging.
Why: environment toggles must work without a module reload, read-only processes cannot land inserts, and analytics must never break the caller it observes.
How to apply: gate each public emitter before buffering and keep emission paths best-effort rather than exposing exceptions.