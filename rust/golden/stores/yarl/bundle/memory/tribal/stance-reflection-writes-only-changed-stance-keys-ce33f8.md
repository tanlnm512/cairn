---
type: Tribal-pattern
title: Stance reflection writes only changed stance keys
description: Stance reflection writes only changed stance keys
resource: src/cairn/memory/stance.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:39:33Z'
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
- date: '2026-10-07T14:39:33Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:39:33Z'
---

Stance reflection processes memories in sorted concept-id order and atomically writes only when a stance key changes, leaving tiers, scores, titles, bodies, and supersession untouched.
Why: recomputation must not create version churn or mutate unrelated memory lifecycle state.
How to apply: keep reflection writes keyed to actual stance-key diffs and extend its count contract when stance verdicts change.