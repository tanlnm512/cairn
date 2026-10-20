---
type: Tribal-pattern
title: Fixture flow chains must respect the resolver's same-file tier
description: Fixture flow chains must respect the resolver's same-file tier
resource: src/cairn/graph/taint.py
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-09-17T02:08:22Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.578
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.7
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-09-17T02:08:22Z'
  action: captured
  score: 0.7
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-17T02:08:22Z'
---

A fixture function that calls a name defined in the SAME file resolves exact to the local definition even when another same-named definition exists elsewhere; the call never reaches the intended cross-file symbol. Why: discovered while verifying the taint propagation engine — a local run_migration stub made the real sink-calling run_migration unreachable, so the ambiguous-hop flow silently dead-ended. How to apply: when authoring multi-file flow fixtures (ambiguous-hop-style), define each intermediate function exactly once (in one file) and have every variant call that single definition; keep the ambiguous name itself free of same-file namesakes.