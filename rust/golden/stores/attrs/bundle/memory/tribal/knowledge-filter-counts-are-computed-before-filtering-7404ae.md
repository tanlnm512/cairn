---
type: Tribal-decision
title: Knowledge filter counts are computed before filtering
description: Knowledge filter counts are computed before filtering
resource: src/cairn/dashboard/data.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:57:36Z'
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
- date: '2026-10-07T16:57:36Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:57:36Z'
---

Family and status totals accumulate across the full knowledge corpus before any single-value filter narrows the returned rows.
Why: filter controls must show stable totals rather than collapse to the selected value after every request.
How to apply: compute vocabulary counts in list_knowledge_docs before applying family, status, and tag predicates.