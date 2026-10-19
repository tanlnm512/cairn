---
type: Tribal-decision
title: Unavailable parsers converge to fresh-build skips
description: Unavailable parsers converge to fresh-build skips
resource: src/cairn/graph/incremental.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:04:42Z'
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
- date: '2026-10-07T15:04:42Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:04:42Z'
---

Files whose parser is unavailable are deleted or deferred like a fresh build, counted as drift resolution, and left for rediscovery.
Why: strict refresh convergence and stale banners must not force an indexing attempt that cannot succeed.
How to apply: keep unavailable-language handling on the deletion path and clear pending sync only after settling it.