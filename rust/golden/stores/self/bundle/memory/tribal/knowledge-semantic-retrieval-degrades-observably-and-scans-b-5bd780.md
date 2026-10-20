---
type: Tribal-decision
title: Knowledge semantic retrieval degrades observably and scans bundle metadata
description: Knowledge semantic retrieval degrades observably and scans bundle metadata
resource: src/cairn/knowledge/search.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:31:13Z'
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
- date: '2026-10-07T17:31:13Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:31:13Z'
---

Knowledge semantic search reads embeddings without symbol joins, reads concept metadata from the bundle, brute-force scans the curated corpus, and reports every degrade as semantic_unavailable without raising.
Why: backend absence must be distinguishable from no matches, and the small knowledge corpus does not justify the code-corpus ANN path.
How to apply: preserve telemetry on all degrade branches and keep bundle metadata independent of graph tables.