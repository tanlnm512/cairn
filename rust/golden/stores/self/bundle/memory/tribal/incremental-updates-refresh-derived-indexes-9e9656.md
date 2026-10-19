---
type: Tribal-decision
title: Incremental updates refresh derived indexes
description: Incremental updates refresh derived indexes
resource: src/cairn/graph/incremental.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:04:44Z'
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
- date: '2026-10-07T15:04:44Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:04:44Z'
---

Completed reindexes incrementally maintain dataflow and closure when a trusted affected set exists, falling back to full rebuild only for never-built tables.
Why: cached impact and traversal data must not survive graph changes, and unrelated rows should not be rebuilt.
How to apply: keep maintenance best-effort, phase-isolated, and report failures without undoing reindex.