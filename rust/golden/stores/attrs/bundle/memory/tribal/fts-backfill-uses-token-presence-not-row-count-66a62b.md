---
type: Tribal-pattern
title: FTS backfill uses token presence not row count
description: FTS backfill uses token presence not row count
resource: src/cairn/graph/schema.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:52:49Z'
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
- date: '2026-10-07T14:52:49Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:52:49Z'
---

FTS backfill triggers only when the token shadow table is empty, even when FTS metadata rows already exist.
Why: Why: an index populated before triggers were wired can have structure but zero tokens.
How to apply: How to apply: test FTS recovery by token count, not the presence of FTS rows.