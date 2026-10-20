---
type: Tribal-pattern
title: Claude and Droid CLI registrations carry store env
description: Claude and Droid CLI registrations carry store env
resource: src/cairn/agent_install/clients/claude.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:50:22Z'
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
- date: '2026-10-07T17:50:22Z'
  action: captured
  score: 0.97
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:50:22Z'
---

Claude uses -e and Droid uses --env to persist a non-default CAIRN_HOME in user-scope registrations; command delimiters preserve the exact server argv.
Why: CLI-registered servers spawn outside the installer process, so without persisted env they resolve the default store.
How to apply: Keep environment flags and delimiter handling aligned with each CLI’s documented registration grammar.