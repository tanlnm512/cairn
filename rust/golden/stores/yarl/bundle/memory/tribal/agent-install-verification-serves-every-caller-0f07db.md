---
type: Tribal-decision
title: Agent install verification serves every caller
description: Agent install verification serves every caller
resource: src/cairn/agent_install/__init__.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:50:01Z'
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
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T17:50:01Z'
  action: captured
  score: 0.97
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:50:01Z'
---

Registration verification runs inside the agent-install API rather than the CLI rendering layer.
Why: Every install caller needs the same verdict; placing it in agents.py would let library and MCP callers install unverified registrations.
How to apply: Keep spawn verification on the install path and keep CLI code limited to rendering InstallResult verdicts.