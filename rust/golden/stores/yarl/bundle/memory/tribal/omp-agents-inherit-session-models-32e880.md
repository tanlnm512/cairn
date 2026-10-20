---
type: Tribal-decision
title: omp agents inherit session models
description: omp agents inherit session models
resource: src/cairn/agent_install/_common.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:50:11Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.628
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.93
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T17:50:11Z'
  action: captured
  score: 0.93
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:50:11Z'
---

Generated omp agent frontmatter leaves model unset and names MCP tools with mcp__server_tool, using one underscore between server and tool.
Why: omp has no inherit selector, so an unset model is its inheritance path, and its registry spells MCP tool names differently from Claude.
How to apply: Preserve both format differences when translating Claude or Cursor agent definitions for omp.