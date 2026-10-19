---
type: Tribal-pattern
title: Watcher clears restored-content leftovers
description: Watcher clears restored-content leftovers
resource: src/cairn/graph/watcher.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:19:10Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.617
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.88
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T15:19:10Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:19:10Z'
---

After reindexing, watcher clears pending rows that git did not report. Why: an edit restoring identical content can otherwise leave a permanent staleness banner. How to apply: treat the pending-sync row set, not git output, as authoritative for cleanup.