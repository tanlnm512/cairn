---
type: Tribal-decision
title: Namespace maps resolve per workspace
description: Namespace maps resolve per workspace
resource: src/cairn/graph/cross_repo.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:18:26Z'
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
- date: '2026-10-07T15:18:26Z'
  action: captured
  score: 0.88
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:18:26Z'
---

Cross-repo namespace caches key by resolved workspace and fall back env, config, then defaults without raising. Why: long-lived processes can switch workspaces, and malformed namespace input must not break the build. How to apply: reset the cache on workspace/config changes and keep resolution fail-soft.