---
type: Tribal-decision
title: Import edge materialization waits for every module symbol
description: Import edge materialization waits for every module symbol
resource: src/cairn/graph/builder.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:04:16Z'
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
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T15:04:16Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:04:16Z'
---

Module-to-module import edges materialize only after all files and module symbols are inserted and resolved.
Why: suffix matching needs the complete module universe, and imports remain outside structural traversal kinds.
How to apply: keep this pass after resolver commits and exclude its kind from structural edge filters.