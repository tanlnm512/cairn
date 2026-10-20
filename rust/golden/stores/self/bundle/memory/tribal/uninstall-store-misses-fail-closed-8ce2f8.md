---
type: Tribal-decision
title: Uninstall store misses fail closed
description: Uninstall store misses fail closed
resource: src/cairn/cli/uninstall.py
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-10-08T16:24:40Z'
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
- date: '2026-10-08T16:24:40Z'
  action: captured
  score: 0.96
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-08T16:24:40Z'
---

A workspace-scoped cairn uninstall with no matching resolved store must name that workspace and delete nothing; only --full may target the entire CAIRN home.\nWhy: Path-key misses must never widen destructively to unrelated workspace stores.\nHow to apply: Keep workspace store resolution and deletion targeting fail-closed in cli/uninstall.py.