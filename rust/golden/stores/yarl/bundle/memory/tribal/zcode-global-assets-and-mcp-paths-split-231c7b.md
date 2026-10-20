---
type: Tribal-decision
title: ZCode global assets and MCP paths split
description: ZCode global assets and MCP paths split
resource: src/cairn/agent_install/clients/zcode.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:50:28Z'
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
- date: '2026-10-07T17:50:28Z'
  action: captured
  score: 0.98
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:50:28Z'
---

ZCode global skills, commands, and agents write under ~/.zcode while MCP writes ~/.zcode/cli/config.json; legacy MCP under ~/.zcode/config.json is stripped.
Why: ZCode reads global assets from the top level but user-scope MCP only from cli/config.json, leaving the old path unread.
How to apply: Keep asset and MCP scope resolution separate and clean legacy entries during global install.