---
type: Tribal-decision
title: Blast seeds from diff hunks intersecting stored spans
description: Blast seeds from diff hunks intersecting stored spans
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-09-15T18:05:09Z'
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
- date: '2026-09-15T18:05:09Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-15T18:05:09Z'
---

cairn blast refreshes stored spans, parses new-side unified-diff hunks, selects the innermost inclusive symbol span for each hunk, de-duplicates symbol ids, and uses the existing reverse impact traversal for dependents. Base comparisons resolve through git merge-base.