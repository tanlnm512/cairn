---
type: Tribal-decision
title: Agent install shared helpers stay sibling-free
description: Agent install shared helpers stay sibling-free
resource: src/cairn/agent_install/_common.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:50:07Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.634
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.96
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T17:50:07Z'
  action: captured
  score: 0.96
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:50:07Z'
---

Shared installer constants, result types, generators, and instruction helpers live in _common and must not import sibling clients or installer modules.
Why: The shared module underpins client modules; sibling imports create package cycles and make client imports fail together.
How to apply: Keep client-specific behavior in its client module and expose only genuinely shared contracts from _common.