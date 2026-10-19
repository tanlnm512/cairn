---
type: Tribal-decision
title: check_overlap recency window defaults to 7 days
description: check_overlap recency window defaults to 7 days
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-09-17T00:31:33Z'
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
- date: '2026-09-17T00:31:33Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-17T00:31:33Z'
---

check_overlap in src/cairn/memory/store.py parameterizes the overlap recency window as recency_days with OVERLAP_RECENCY_DAYS = 7, matching the existing raw-memory decay window (raw_max_days=7 in promotion.py decay). The spec (FR-002/D-002) pins no window value; rows are compared lexicographically on the UTC ISO ts column. Callers may override recency_days; values < 1 raise ValueError.