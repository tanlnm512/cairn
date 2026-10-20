---
type: Tribal-decision
title: Query freshness service at graph boundaries
description: Query freshness service at graph boundaries
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-09-15T17:41:11Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.621
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.9
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-09-15T17:41:11Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-15T17:41:11Z'
---

Graph queries call watcher.refresh_for_query, which probes _detect_changed and repairs exact drifted paths through reindex_paths before reading. Read-only server mode disables repair on the same connection; refresh errors propagate to the query boundary.