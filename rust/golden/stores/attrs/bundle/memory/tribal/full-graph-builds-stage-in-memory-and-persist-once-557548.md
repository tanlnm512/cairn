---
type: Tribal-decision
title: Full graph builds stage in memory and persist once
description: Full graph builds stage in memory and persist once
resource: src/cairn/graph/builder.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:04:17Z'
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
- date: '2026-10-07T15:04:17Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:04:17Z'
---

Full-workspace builds use bulk-load pragmas in memory then back up once; single-repo rebuilds stay on disk.
Why: a whole-store replacement can be atomic and fast, while a repo rebuild must preserve sibling repositories.
How to apply: choose the storage path from rebuild scope and preserve the final backup boundary.