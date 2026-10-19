---
type: Tribal-decision
title: Partial hook installs heal on reinstall
description: Partial hook installs heal on reinstall
resource: src/cairn/agent_install/merge.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:50:17Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.638
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.98
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T17:50:17Z'
  action: captured
  score: 0.98
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:50:17Z'
---

The already-installed hooks test requires every cairn hook entrypoint in either supported on-disk shape.
Why: A partially stripped hooks config is not healthy; reporting it as present would prevent reinstall from restoring the missing hook.
How to apply: Treat any missing required hook entrypoint as absent and let install merge the complete set.