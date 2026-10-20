---
type: Tribal-decision
title: Exact token estimates calibrate from bounded window samples
description: Exact token estimates calibrate from bounded window samples
resource: src/cairn/dashboard/data.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:57:39Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.625
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.92
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T16:57:39Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:57:39Z'
---

Exact tokenizer mode derives a chars-per-token divisor from at most a bounded sample of the queried window’s stored summaries and falls back to the shared bench constant until the sample is large enough.
Why: stored rows already contain character counts that cannot be re-tokenized retroactively, and tiny samples produce unstable ratios.
How to apply: call _estimate_divisor with the exact WHERE clauses and window used by the displayed aggregate.