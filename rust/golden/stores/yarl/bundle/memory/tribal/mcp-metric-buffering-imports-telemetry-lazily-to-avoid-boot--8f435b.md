---
type: Tribal-pattern
title: MCP metric buffering imports telemetry lazily to avoid boot cycles
description: MCP metric buffering imports telemetry lazily to avoid boot cycles
resource: src/cairn/mcp_server/metric_buffering.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:35:50Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.61
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.85
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T15:35:50Z'
  action: captured
  score: 0.85
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:35:50Z'
---

configure_conn(), _start_metric_flusher(), and the truncation/empty-result emitters import cairn.telemetry inside the function.
Why: the metric module is imported during server boot before the tools register; eager telemetry imports create boot-order cycles with the sink/schema modules.
How to apply: keep new telemetry touchpoints in the server surface lazy.