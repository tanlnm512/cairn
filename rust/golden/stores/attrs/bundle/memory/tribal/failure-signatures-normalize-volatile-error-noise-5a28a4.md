---
type: Tribal-pattern
title: Failure signatures normalize volatile error noise
description: Failure signatures normalize volatile error noise
resource: src/cairn/memory/recurrence.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:39:32Z'
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
- date: '2026-10-07T14:39:32Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:39:32Z'
---

Failure signatures hash a normalized tool/error shape: case, whitespace runs, absolute paths, UUIDs, hex runs, and digit runs collapse before hashing; occurrence upserts are atomic on the signature primary key.
Why: recurrence means the same failure shape, not distinct pids, paths, line numbers, or timestamps, and concurrent recorders must not race SELECT-then-INSERT.
How to apply: add volatile error noise to the normalizer and keep note_failure_signature on its atomic upsert.