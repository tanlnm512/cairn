---
type: Tribal-decision
title: Workspace probes cap all DB opens and expose capped counts
description: Workspace probes cap all DB opens and expose capped counts
resource: src/cairn/dashboard/workspaces.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:57:49Z'
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
- date: '2026-10-07T16:57:49Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:57:49Z'
---

The overview bounds database opens whether they succeed or fail; rows beyond the cap keep filesystem stats with an unknown count and explicit capped marker.
Why: count opens dominate probing hundreds of stores and hidden degradation would look like an empty workspace.
How to apply: increment the budget for every attempted populated-row open and render counts_capped rather than fabricating zero.