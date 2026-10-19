---
type: Tribal-pattern
title: Telemetry sinks inject connections and share one flusher
description: Telemetry sinks inject connections and share one flusher
resource: src/cairn/telemetry/sink.py
tags:
- pattern
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
memory_type: pattern
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

Telemetry modules receive a connection factory from their boot caller, open a fresh connection per flush, and register subsystem flushers by identity on one daemon plus atexit drain.
Why: injection keeps telemetry free of schema and store boot dependencies and avoids import cycles, while one shared lifecycle prevents per-subsystem threads and exit handlers.
How to apply: configure factories once at boot, register subsystem flushers lazily, and isolate each registered callable so one failure cannot stop the rest.