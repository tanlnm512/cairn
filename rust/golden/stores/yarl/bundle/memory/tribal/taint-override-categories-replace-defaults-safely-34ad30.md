---
type: Tribal-decision
title: Taint override categories replace defaults safely
description: Taint override categories replace defaults safely
resource: src/cairn/graph/taint.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:19:01Z'
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
- date: '2026-10-07T15:19:01Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:19:01Z'
---

Registry overrides replace same-name default categories, treat empty overrides as disablement, and copy defaults. Why: callers need complete category control without mutating shared tables. How to apply: copy defaults and replace only the named override category.