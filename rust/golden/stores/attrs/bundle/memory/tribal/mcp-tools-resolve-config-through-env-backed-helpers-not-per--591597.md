---
type: Tribal-decision
title: MCP tools resolve config through env-backed helpers, not per-request context
description: MCP tools resolve config through env-backed helpers, not per-request
  context
resource: src/cairn/mcp_server/_server_core.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:35:20Z'
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
- date: '2026-10-07T15:35:20Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:35:20Z'
---

Every MCP tool resolves DB path, knowledge path, and read-only mode through the module-level _conn()/_store()/_read_only_mode() helpers, which read CAIRN_* env vars set by cairn serve and fall back to the workspace store; there is no per-request AppContext threaded through ctx.request_context.lifespan_context.
Why: a single source of truth keeps tool signatures free of config plumbing and matches the serve-spawned env contract.
How to apply: per-request config, if ever needed, means wiring ctx: Context through the tools and reviving a lifespan context — do not add ad-hoc config params.