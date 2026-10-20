---
type: Tribal-decision
title: Model warmup never downloads weights
description: Model warmup never downloads weights
resource: src/cairn/graph/model_warmup.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:18:42Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.617
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.88
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T15:18:42Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:18:42Z'
---

Warmup gates local models on a verified cache and uses the effective backend resolution, not the raw configured value. Why: warmup is an optimization and must not turn a first query into an unrequested download or probe an unsupported backend. How to apply: check local cache and effective backend before any warm I/O.