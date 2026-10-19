---
type: Tribal-decision
title: Tool metric nulls encode absent evidence
description: Tool metric nulls encode absent evidence
resource: src/cairn/graph/schema.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:52:48Z'
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
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T14:52:48Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:52:48Z'
---

Tool metric origin defaults legacy rows to mcp while CLI writers state cli explicitly; truncation magnitudes and argument summaries stay null unless that evidence was recorded.
Why: Why: views can distinguish absent pre-migration or non-truncated evidence from a real zero-length or default value.
How to apply: How to apply: never backfill parser or truncation nulls with guessed zeros; make each writer state its origin.