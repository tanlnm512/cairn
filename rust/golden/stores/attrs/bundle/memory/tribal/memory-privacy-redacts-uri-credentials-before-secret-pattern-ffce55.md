---
type: Tribal-decision
title: Memory privacy redacts URI credentials before secret patterns
description: Memory privacy redacts URI credentials before secret patterns
resource: src/cairn/memory/privacy.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T14:39:28Z'
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
- date: '2026-10-07T14:39:28Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T14:39:28Z'
---

URI credential redaction runs before generic secret-pattern redaction and replaces the entire user:password segment while preserving the scheme and host.
Why: a password that is itself secret-shaped would otherwise be partially substituted first, and usernames are frequently service-account material.
How to apply: extend the URI scheme list and tests in strip_private_data while preserving redaction order and the debugging context retained in scheme and host.