---
type: Tribal-pattern
title: Additive graph migrations avoid FTS trigger drift
description: Additive graph migrations avoid FTS trigger drift
resource: src/cairn/graph/schema.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:52:47Z'
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
- date: '2026-10-07T14:52:47Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:52:47Z'
---

Additive ALTER statements expose provenance and parser-signal columns without referencing new columns in existing FTS triggers; legacy nulls keep the prior interpretation.
Why: Why: additive columns compose with old databases and triggers while preserving existing query behavior.
How to apply: How to apply: audit trigger column references before adding columns and keep legacy-null semantics explicit.