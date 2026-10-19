---
type: Tribal-decision
title: Knowledge family options union classifier seeds with corpus values
description: Knowledge family options union classifier seeds with corpus values
resource: src/cairn/dashboard/routes/knowledge.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:57:43Z'
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
- date: '2026-10-07T16:57:43Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:57:43Z'
---

Knowledge family choices combine the classifier seed vocabulary with every family actually present, and an out-of-vocabulary selection falls back to no filter and refetches matching rows.
Why: custom concept families remain reachable while the rendered rows and selected filter never disagree after a fallback.
How to apply: derive options from seed plus pre-filter corpus counts and requery when the normalized selection changes.