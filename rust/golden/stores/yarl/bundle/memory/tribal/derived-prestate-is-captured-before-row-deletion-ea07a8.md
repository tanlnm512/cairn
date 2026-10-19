---
type: Tribal-pattern
title: Derived prestate is captured before row deletion
description: Derived prestate is captured before row deletion
resource: src/cairn/graph/incremental.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:04:45Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T15:04:45Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:04:45Z'
---

Old ids, names, repairable edges, resolved targets, and closure ancestors are snapshotted before reindex deletes old rows.
Why: affected-set inputs exist only while the old graph is still present.
How to apply: extend the prestate rather than reconstructing old identity after mutation.