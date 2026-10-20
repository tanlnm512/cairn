---
type: Tribal-pattern
title: Model warmup isolates per-model failures
description: Model warmup isolates per-model failures
resource: src/cairn/graph/model_warmup.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:18:41Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T15:18:41Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:18:41Z'
---

Warmup executes each enabled model independently and converts failures to warnings. Why: one missing or unavailable model must not prevent other models from warming or break boot. How to apply: wrap each warm step independently and never raise into the caller.