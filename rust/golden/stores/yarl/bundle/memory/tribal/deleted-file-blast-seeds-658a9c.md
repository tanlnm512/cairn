---
type: Tribal-decision
title: Deleted-file blast seeds
description: Deleted-file blast seeds
resource: src/cairn/graph/blast.py
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-10-09T14:43:18Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.623
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.91
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-09T14:43:18Z'
  action: captured
  score: 0.91
  tier: tribal
memory_is_latest: true
memory_supersedes:
- memory/tribal/deleted-file-blast-seeds-c8363a
memory_superseded_by: null
valid_from: '2026-10-09T14:43:18Z'
---

Blast captures deleted-file symbols and their reverse radius before refresh, then refreshes live files and merges both results.\n\nWhy: deletion hunks have no post-image spans and refresh removes the stale file rows needed to identify removed declarations.\n\nHow to apply: preserve pre-refresh seed identity for deleted entries whenever a query refreshes drift before diff seeding.