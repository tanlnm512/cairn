---
type: Tribal-decision
title: Empty graph filters draw capped overviews, not empty graphs
description: Empty graph filters draw capped overviews, not empty graphs
resource: src/cairn/dashboard/data.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:57:30Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.625
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.92
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T16:57:30Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:57:30Z'
---

An empty graph filter invokes the scope-specific overview or unfiltered graph; only a supplied filter that matches nothing produces an empty graph.
Why: an absent focus or repo is a request for the whole workspace, not a search for a symbol named empty.
How to apply: preserve each get_graph scope empty-filter branch and let viz-layer caps and metadata report truncation.