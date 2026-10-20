---
type: Tribal-decision
title: OpenCode consumes only shared agent skills
description: OpenCode consumes only shared agent skills
resource: src/cairn/agent_install/clients/opencode.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:50:27Z'
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
- date: '2026-10-07T17:50:27Z'
  action: captured
  score: 0.96
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:50:27Z'
---

OpenCode reaches the shared .agents skill tree but does not discover shared slash commands or subagents; those require OpenCode-specific locations.
Why: Assuming all cross-tool assets are universal installs files the client never reads.
How to apply: Add OpenCode-native command or agent generation before advertising those capabilities to OpenCode.