---
type: Tribal-decision
title: MCP write tools take a separate writable connection that may contend with CLI
  writers
description: MCP write tools take a separate writable connection that may contend
  with CLI writers
resource: src/cairn/mcp_server/_server_core.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:35:23Z'
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
- date: '2026-10-07T15:35:23Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:35:23Z'
---

Tools whose purpose is to write call _rw_conn() (always read-write) instead of _conn() (read-only under CAIRN_READ_ONLY).
Why: in a read-only daemon the pooled connection can never write; a write tool running there contends with the CLI writer and can fail with database-is-locked, surfaced as an error string.
How to apply: make the write requirement explicit at the call site by choosing _rw_conn(); never widen _conn() to writable.