---
type: Tribal-decision
title: Telemetry event names and enums are shared contracts
description: Telemetry event names and enums are shared contracts
resource: src/cairn/telemetry/events.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:23:59Z'
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
- date: '2026-10-07T14:23:59Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:23:59Z'
---

Telemetry event names live as exported module constants and reason or surface values as bounded frozensets shared by producers and consumers.
Why: a misspelled literal silently drops a signal whose purpose is observation, and unbounded values defeat doctor bucketing and cardinality control.
How to apply: add a new event or enum through the events catalog and update producers, consumers, and cardinality tests together rather than passing fresh literals.