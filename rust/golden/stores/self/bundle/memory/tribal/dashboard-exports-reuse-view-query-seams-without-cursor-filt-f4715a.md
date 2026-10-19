---
type: Tribal-decision
title: Dashboard exports reuse view query seams without cursor filters
description: Dashboard exports reuse view query seams without cursor filters
resource: src/cairn/dashboard/routes/history.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:57:42Z'
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
- date: '2026-10-07T16:57:42Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:57:42Z'
---

CSV and JSON exports use the same store, window, filter, and data functions as their views, but omit pagination cursors and fetch one bounded export page.
Why: exports must match displayed data by construction while representing a complete filtered result rather than one UI page.
How to apply: share the view’s helpers and data function, then pass the export row limit instead of a view cursor.