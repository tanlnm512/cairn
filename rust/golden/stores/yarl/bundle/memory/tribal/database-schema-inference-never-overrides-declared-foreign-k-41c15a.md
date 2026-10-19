---
type: Tribal-pattern
title: Database schema inference never overrides declared foreign keys
description: Database schema inference never overrides declared foreign keys
resource: src/cairn/dashboard/data.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:57:35Z'
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
- date: '2026-10-07T16:57:35Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:57:35Z'
---

The schema graph records declared foreign keys first and infers relationships from unused *_id columns only when no declared edge already covers them.
Why: inferred naming conventions are useful hints but must not rewrite explicit schema truth.
How to apply: retain the declared-key set and skip inferred edges whose column or table pair is already represented.