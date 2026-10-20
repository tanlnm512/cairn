---
type: Tribal-pattern
title: Resolver repair tracks changed candidate counts
description: Resolver repair tracks changed candidate counts
resource: src/cairn/graph/incremental.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:04:43Z'
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
- date: '2026-10-07T15:04:43Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:04:43Z'
---

Repair registers removed and newly introduced names only when their global candidate count changed.
Why: deletions, additions, and ambiguity flips must match a fresh build while repair stays proportional to the edit.
How to apply: seed repair from both old and new names and skip unchanged candidate sets.