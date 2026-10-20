---
type: Tribal-decision
title: Build-run telemetry is direct structured analytics
description: Build-run telemetry is direct structured analytics
resource: src/cairn/graph/builder.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:04:18Z'
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
- date: '2026-10-07T15:04:18Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:04:18Z'
---

Build-run rows use a direct best-effort insert on a short-lived connection, honor the telemetry kill switch, and never propagate failure.
Why: structured per-run data is not a buffered low-cardinality event, and analytics must not own build correctness.
How to apply: keep failures debug-only and gate recording with global telemetry configuration.