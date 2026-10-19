---
type: Tribal-decision
title: Prune telemetry by timestamps inside the flush transaction
description: Prune telemetry by timestamps inside the flush transaction
resource: src/cairn/telemetry/sink.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:24:03Z'
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
- date: '2026-10-07T14:24:03Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:24:03Z'
---

Telemetry retention keeps the newest rows by event timestamp, build start time, or invocation time inside the flush transaction; invalid retention settings fall back safely and per-table prune failures are ignored. Tool-metrics age pruning is disabled when unset, unparseable, or negative.
Why: whole-file rebuilds append carried historical rows with fresh ids, so id order is not recency; pruning in the same transaction keeps insert and bounds atomic; old or read-only databases must not fail inserts.
How to apply: order each retention DELETE by its timestamp column, guard tables independently, and keep invalid knob handling non-raising.