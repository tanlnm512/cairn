---
type: Tribal-pattern
title: Mypy compound-branch Optional narrowing
description: Mypy compound-branch Optional narrowing
resource: src/cairn/graph/traversal.py
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-09-15T19:12:40Z'
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
- date: '2026-09-15T19:12:40Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-15T19:12:40Z'
---

When a branch starts with `if fuzzy and symbol_id is None`, mypy does not treat a following bare `elif fuzzy` as proving `symbol_id is not None`. Use a positive guard (`elif fuzzy and symbol_id is not None`) so tuple assignments receive the narrowed type.