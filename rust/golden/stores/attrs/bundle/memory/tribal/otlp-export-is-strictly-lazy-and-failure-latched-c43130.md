---
type: Tribal-decision
title: OTLP export is strictly lazy and failure-latched
description: OTLP export is strictly lazy and failure-latched
resource: src/cairn/telemetry/otel.py
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

OpenTelemetry imports run only after an endpoint is set, telemetry is enabled, and rows are pending. Import or construction failure warns once, latches the exporter disabled, clears pending OTLP rows, omits the endpoint from the message, and leaves SQLite events authoritative; events.warn_once is imported lazily to avoid the module cycle.
Why: OTLP is an optional extra, a bad endpoint can carry credentials, and retrying a permanently broken exporter every tick only creates noise while local telemetry remains valid.
How to apply: keep all SDK imports inside the gated builder and map every irreversible setup failure to the one-way disable path.