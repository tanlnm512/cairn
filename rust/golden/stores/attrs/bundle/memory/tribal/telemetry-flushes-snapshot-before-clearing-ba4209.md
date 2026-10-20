---
type: Tribal-pattern
title: Telemetry flushes snapshot before clearing
description: Telemetry flushes snapshot before clearing
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

A buffered telemetry flush snapshots under the buffer lock, performs write or export under a dedicated flush-cycle lock, and removes exactly the committed or acknowledged batch; failed cycles retain the batch and rows appended meanwhile stay buffered.
Why: daemon ticks, synchronous drains, watchdogs, and atexit can overlap; clearing first loses retries, while an unprotected snapshot can double-write and then count-pop rows that were never written.
How to apply: keep buffer mutation and flush-cycle locks separate, never clear on failure, and pop only len(batch) leftmost rows after success.