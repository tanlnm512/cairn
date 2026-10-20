---
type: Tribal-decision
title: Claude settings detection separates hooks from MCP
description: Claude settings detection separates hooks from MCP
resource: src/cairn/agent_install/detect.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:50:13Z'
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
- date: '2026-10-07T17:50:13Z'
  action: captured
  score: 0.97
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:50:13Z'
---

A .claude/settings.json install is detected by cairn hooks, not by MCP probes.
Why: Claude hooks and workspace MCP registrations live in different files, so an MCP probe cannot represent a hooks-only installation.
How to apply: Keep settings detection keyed on hook markers and MCP detection keyed on .mcp.json or user-scope registration.