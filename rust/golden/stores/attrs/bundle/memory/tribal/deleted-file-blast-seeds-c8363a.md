---
type: Tribal-decision
title: Deleted-file blast seeds
description: Deleted-file blast seeds
resource: src/cairn/graph/blast.py
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-10-09T14:31:28Z'
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
- date: '2026-10-09T14:31:28Z'
  action: captured
  score: 0.91
  tier: tribal
memory_is_latest: false
memory_supersedes: []
memory_superseded_by: memory/tribal/deleted-file-blast-seeds-658a9c
valid_from: '2026-10-09T14:31:28Z'
---

Deleted diff entries seed every indexed non-module symbol from the stale file row and count a missing file row as unindexed.\n\nWhy: deletion hunks have no post-image spans, so hunk intersection cannot identify the removed declarations.\n\nHow to apply: preserve symbol identity before incremental deletion or query blast with refresh disabled when testing deleted-file radius.