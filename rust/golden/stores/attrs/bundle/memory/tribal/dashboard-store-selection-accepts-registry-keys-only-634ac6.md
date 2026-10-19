---
type: Tribal-decision
title: Dashboard store selection accepts registry keys only
description: Dashboard store selection accepts registry keys only
resource: src/cairn/dashboard/app.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:57:29Z'
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
- date: '2026-10-07T16:57:29Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:57:29Z'
---

An absent store selector keeps the launch store; a present selector must name a populated registry key, with a settings form value only as the query-parameter fallback.
Why: treating the parameter as a path would turn the dashboard selector into an arbitrary-file-open vector and split validation across transports.
How to apply: validate every store input through resolve_selection and map unknown keys to the missing-store page.