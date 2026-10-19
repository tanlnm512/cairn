---
type: Tribal-decision
title: OTLP exports are synchronous, bounded, and at-least-once
description: OTLP exports are synchronous, bounded, and at-least-once
resource: src/cairn/telemetry/otel.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:24:02Z'
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
- date: '2026-10-07T14:24:02Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:24:02Z'
---

OTLP uses a simple synchronous processor with a result-tracking exporter and a five-second timeout; only acknowledged batches are popped, failed or rejected batches remain for retry, and telemetry-off or read-only flush clears pending export rows.
Why: synchronous emission makes success observable before buffer removal, the timeout bounds shared flusher and atexit time, and read-only processes must not open network egress.
How to apply: keep the OTLP side buffer and flush-cycle lock, stop a batch at first rejection, and retain unacknowledged rows for the next tick.