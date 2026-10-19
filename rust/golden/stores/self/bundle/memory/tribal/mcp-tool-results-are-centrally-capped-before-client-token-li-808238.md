---
type: Tribal-decision
title: MCP tool results are centrally capped before client token limits
description: MCP tool results are centrally capped before client token limits
resource: src/cairn/mcp_server/metric_buffering.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:35:49Z'
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
- date: '2026-10-07T15:35:49Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:35:49Z'
---

instrument() caps every tool result at MAX_RESULT_CHARS (default 60000) regardless of shape, degrading over-cap results to a head-plus-truncation-notice string; non-str results are measured via str().
Why: MCP clients hard-fail on results exceeding their token ceiling; roughly 4 chars per token keeps the cap well under typical 25k-token ceilings, a tool that forgets its own pagination still degrades instead of being rejected opaquely, and a structured model return must not bypass the cap.
How to apply: cut at the last newline so the notice lands on its own line and tell the caller how to narrow the query.