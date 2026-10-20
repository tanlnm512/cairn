---
type: Tribal-decision
title: Knowledge search hides archived history but keeps superseded docs
description: Knowledge search hides archived history but keeps superseded docs
resource: src/cairn/knowledge/search.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:31:11Z'
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
- date: '2026-10-07T17:31:11Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:31:11Z'
---

Default knowledge search excludes archived documents while retaining superseded ones; callers can explicitly include archived results.
Why: archived documents are inactive, but superseded records remain useful history and audit context.
How to apply: route visibility through the shared helper and let callers apply doc_status policy.