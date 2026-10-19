---
type: Tribal-pattern
title: Approved ingest resolves refs, preserves inferred links, and refreshes indexes
description: Approved ingest resolves refs, preserves inferred links, and refreshes
  indexes
resource: src/cairn/knowledge/ingest/executor.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:31:21Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T17:31:21Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:31:21Z'
---

Approved execution resolves backticked refs against the live graph, merges existing inferred relationships before overwrite, rebuilds knowledge indexes, commits that rebuild, and queues island tasks idempotently.
Why: staging has no graph to verify refs, source rewrites would drop critic-approved links, and the caller closes its connection immediately after execution.
How to apply: merge inferred entries before add_document and commit only the executor-owned derived-index boundary.