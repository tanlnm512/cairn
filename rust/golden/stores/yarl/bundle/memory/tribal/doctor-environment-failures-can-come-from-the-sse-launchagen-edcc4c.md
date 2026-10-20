---
type: Tribal-mistake
title: Doctor environment failures can come from the SSE LaunchAgent store
description: Doctor environment failures can come from the SSE LaunchAgent store
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-09-15T18:49:38Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.621
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.9
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-09-15T18:49:38Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-15T18:49:38Z'
---

cairn doctor may fail only because the configured SSE daemon cannot open a store for another workspace.\n\nWhy: the LaunchAgent environment is independent of the shell that ran doctor.\n\nHow to apply: inspect cairn serve status and cairn-sse.log before attributing the failure to repository changes.