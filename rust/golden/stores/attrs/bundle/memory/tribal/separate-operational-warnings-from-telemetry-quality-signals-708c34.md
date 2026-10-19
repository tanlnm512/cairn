---
type: Tribal-decision
title: Separate operational warnings from telemetry quality signals
description: Separate operational warnings from telemetry quality signals
resource: src/cairn/telemetry/events.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:24:01Z'
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
- date: '2026-10-07T14:24:01Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:24:01Z'
---

Operational warnings such as lock contention remain visible when telemetry is off; quality signals such as semantic_unavailable suppress both event and warning under the telemetry kill switch. Both use process-scoped once guards keyed by degradation class or surface.
Why: an outage must stay observable even when analytics are disabled, whereas a quality signal is itself telemetry and should honor the switch; repeated fallbacks otherwise spam logs.
How to apply: place operational warnings at the failure site, reserve telemetry helpers for gated quality signals, and key once guards by the smallest stable diagnostic unit.