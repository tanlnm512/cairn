---
type: Tribal-decision
title: Supersede walks stay deterministic on stale or branched chains
description: Supersede walks stay deterministic on stale or branched chains
resource: src/cairn/knowledge/index.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:31:09Z'
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
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T17:31:09Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:31:09Z'
---

Supersede walks visit-guard cycles, skip stale endpoints, sort branch candidates, and assert that the queried document remains in its chain.
Why: cyclic or stale index edges must terminate, and a branched DAG must not silently return a branch that excludes the requested document.
How to apply: keep the writer to one successor and retain the loud dropped-start assertion.