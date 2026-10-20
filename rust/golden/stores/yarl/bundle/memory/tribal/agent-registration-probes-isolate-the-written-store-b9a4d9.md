---
type: Tribal-decision
title: Agent registration probes isolate the written store
description: Agent registration probes isolate the written store
resource: src/cairn/agent_install/__init__.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:49:59Z'
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
- date: '2026-10-07T17:49:59Z'
  action: captured
  score: 0.98
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:49:59Z'
---

Registration verification replaces only the trailing serve argument with config --json, merges the written env before pinning the intended CAIRN_HOME and workspace, and runs from the target workspace.
Why: The probe must prove that the exact registered binary, environment, and client cwd resolve the intended store; ambient substitutions can hide a PATH-shadowed wrapper.
How to apply: Keep install and doctor on the shared spawn probe and preserve env merge order before the intended-store pins.