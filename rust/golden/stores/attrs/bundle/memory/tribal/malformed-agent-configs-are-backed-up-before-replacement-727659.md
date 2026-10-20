---
type: Tribal-decision
title: Malformed agent configs are backed up before replacement
description: Malformed agent configs are backed up before replacement
resource: src/cairn/agent_install/merge.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:50:14Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.64
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.99
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T17:50:14Z'
  action: captured
  score: 0.99
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:50:14Z'
---

Missing, malformed, or structurally unusable configs return a sentinel to callers, which back them up before starting from a fresh object.
Why: Silently replacing a malformed user-edited config destroys hand-maintained client settings.
How to apply: Never treat an unreadable or non-object merge target as an empty object; preserve it beside itself and report the backup.