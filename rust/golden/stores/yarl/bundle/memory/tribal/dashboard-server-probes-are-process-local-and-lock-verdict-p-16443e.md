---
type: Tribal-decision
title: Dashboard server probes are process-local and lock verdict publication
description: Dashboard server probes are process-local and lock verdict publication
resource: src/cairn/dashboard/app.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:57:28Z'
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
- date: '2026-10-07T16:57:28Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:57:28Z'
---

Each dashboard process performs at most one uncached embedding-server probe, sharing the result across the banner and status view while holding the lock through verdict assignment.
Why: the embed ladder cache is process-local and two surfaces must not pay or race separate probes; a failed probe must seed this process verdict.
How to apply: call the app server_probe_once seam and never probe directly from render paths.