---
type: Tribal-pattern
title: Reset telemetry subsystem state without restarting the shared flusher
description: Reset telemetry subsystem state without restarting the shared flusher
resource: src/cairn/telemetry/cli_metrics.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:23:58Z'
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
- date: '2026-10-07T14:23:58Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:23:58Z'
---

Tests may clear a telemetry subsystem buffer, injected factory, and subsystem started flag, but must leave sink._FLUSHER_STARTED and the registered-flusher list untouched; registration is idempotent by callable identity.
Why: resetting the shared lifecycle can spawn a second daemon thread or duplicate atexit drain, while identity registration makes subsystem re-registration safe.
How to apply: reset only subsystem-owned globals and rely on identity-based register_flusher to make re-record a no-op.