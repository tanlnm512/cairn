---
type: Tribal-decision
title: Knowledge status transitions move forward only
description: Knowledge status transitions move forward only
resource: src/cairn/knowledge/store.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:31:15Z'
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
- date: '2026-10-07T17:31:15Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:31:15Z'
---

Knowledge statuses progress only from active to superseded to archived; unknown or backward transitions return False, and a missing current status is treated as active.
Why: lifecycle history must not be resurrected through the status API.
How to apply: keep the ordered status vocabulary and compare indexes before writing.