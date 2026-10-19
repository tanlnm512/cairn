---
type: Tribal-decision
title: Lock-contention telemetry filters schema failures
description: Lock-contention telemetry filters schema failures
resource: src/cairn/graph/schema.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:52:49Z'
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
- date: '2026-10-07T14:52:49Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:52:49Z'
---

Contention detection accepts only locked or busy operational errors, warns once per process and site under a shared guard, emits durable telemetry independently, stays silent for expected duplicate columns, and warns once before propagating genuine contention.
Why: Why: schema, missing-module, and duplicate-column failures would dilute doctor and contention metrics with false positives.
How to apply: How to apply: route operational-error swallow sites through note_contention and keep human warning and telemetry gates separate.