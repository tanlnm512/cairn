---
type: Tribal-decision
title: Receiver types outrank file scope
description: Receiver types outrank file scope
resource: src/cairn/graph/resolver.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:18:55Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.617
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.88
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T15:18:55Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:18:55Z'
---

A known receiver type resolves before same-file candidates, but a global member collision binds only a unique same-file survivor. Why: typed dispatch is the strongest signal, while same-named types elsewhere are not evidence. How to apply: walk receiver ancestors first and continue through name tiers when typed scope is ambiguous.