---
type: Tribal-pattern
title: Droid stdio commands stay one argument
description: Droid stdio commands stay one argument
resource: src/cairn/agent_install/clients/droid.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:50:25Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.636
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.97
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T17:50:25Z'
  action: captured
  score: 0.97
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:50:25Z'
---

Droid stdio registration passes the complete server command as one argument after its option delimiter.
Why: Droid splits that single argument itself; allowing server flags as separate CLI arguments lets them be parsed as Droid options.
How to apply: Quote or array-join the full server invocation according to Droid’s documented CLI shape.