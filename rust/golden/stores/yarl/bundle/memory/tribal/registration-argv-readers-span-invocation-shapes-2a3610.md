---
type: Tribal-pattern
title: Registration argv readers span invocation shapes
description: Registration argv readers span invocation shapes
resource: src/cairn/agent_install/__init__.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:50:05Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.63
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.94
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T17:50:05Z'
  action: captured
  score: 0.94
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:50:05Z'
---

Registration argv extraction supports command plus args entries and OpenCode/Kilo single command arrays.
Why: Both forms are installed contracts, and verification must spawn the exact argv a client will use.
How to apply: Update argv extraction whenever a client generator introduces a new invocation encoding.