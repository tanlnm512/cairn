---
type: Tribal-decision
title: Workspace call counts distinguish zero from unreadable
description: Workspace call counts distinguish zero from unreadable
resource: src/cairn/dashboard/workspaces.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:57:48Z'
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
- date: '2026-10-07T16:57:48Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:57:48Z'
---

A successful workspace count query returns a real count including zero; a failed read-only open or unusable query returns None and reclassifies the row unreadable.
Why: a pre-metrics store with no table is healthy and counts zero, while None is the only signal for locked or corrupt databases.
How to apply: preserve the three-state result and reclassify only None to unreadable.