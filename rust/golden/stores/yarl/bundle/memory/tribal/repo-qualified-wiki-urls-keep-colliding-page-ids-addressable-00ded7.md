---
type: Tribal-decision
title: Repo-qualified wiki URLs keep colliding page ids addressable
description: Repo-qualified wiki URLs keep colliding page ids addressable
resource: src/cairn/dashboard/routes/wiki.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:57:46Z'
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
- date: '2026-10-07T16:57:46Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:57:46Z'
---

Canonical wiki URLs include the owning repository and previous/next navigation walks only that repo’s promoted pages; legacy one-segment URLs redirect.
Why: multiple repositories can plan the same page id and unpromoted rows have no readable article to link to.
How to apply: resolve pages with the repo qualifier and derive navigation from promoted manifest rows in plan order.