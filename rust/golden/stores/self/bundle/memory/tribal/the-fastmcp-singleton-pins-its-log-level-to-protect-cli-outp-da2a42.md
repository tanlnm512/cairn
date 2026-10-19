---
type: Tribal-decision
title: The FastMCP singleton pins its log level to protect CLI output
description: The FastMCP singleton pins its log level to protect CLI output
resource: src/cairn/mcp_server/_server_core.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:35:21Z'
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
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T15:35:21Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:35:21Z'
---

The shared FastMCP instance is constructed with log_level=WARNING.
Why: the singleton is imported by every CLI invocation; constructing it at a lower level would reconfigure the root logger and clobber other command output.
How to apply: never construct FastMCP without an explicit log level on this import path.