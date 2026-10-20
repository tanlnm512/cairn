---
type: Tribal-pattern
title: Graph schema initialization is path-scoped and atomic
description: Graph schema initialization is path-scoped and atomic
resource: src/cairn/graph/schema.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:52:51Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T14:52:51Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:52:51Z'
---

Schema initialization is cached per resolved database path under a lock, migration commit succeeds before the path is marked initialized, connection failures include resolved path and environment context, and read-only opens skip writable migration work.
Why: Why: concurrent flusher and tool threads must not observe an initialized path with unapplied migrations or lose actionable open diagnostics.
How to apply: How to apply: keep migration, commit, and cache marking in one locked sequence and enrich both open modes at one choke point.