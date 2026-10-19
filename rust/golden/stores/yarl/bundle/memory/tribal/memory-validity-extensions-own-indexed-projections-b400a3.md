---
type: Tribal-pattern
title: Memory validity extensions own indexed projections
description: Memory validity extensions own indexed projections
resource: src/cairn/memory/store.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:39:34Z'
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
- date: '2026-10-07T14:39:34Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:39:34Z'
---

Validity extensions are the source of truth; write_validity mirrors them into the indexed memory_validity projection, writes a complete interval, and leaves commit ownership with the caller. Timestamp parsing returns None for naive values.
Why: duplicated interval state can drift, and mixing aware query instants with naive stored timestamps raises instead of degrading.
How to apply: update extensions and projection in one operation, clear missing bounds on both sides, and keep caller-owned transaction boundaries.