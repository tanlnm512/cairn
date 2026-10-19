---
type: Tribal-decision
title: Read-only MCP servers skip tool_metrics writes entirely
description: Read-only MCP servers skip tool_metrics writes entirely
resource: src/cairn/mcp_server/metric_buffering.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:35:51Z'
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
- date: '2026-10-07T15:35:51Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:35:51Z'
---

_log_metric returns early when CAIRN_READ_ONLY is set, before buffering.
Why: read-only daemons open the DB mode=ro, so INSERT INTO tool_metrics would fail every flush and buffer indefinitely (capped only by deque maxlen); tool_metrics is analytics, not correctness — skipping keeps the flush thread from spinning on a guaranteed failure and the table from silently misreporting.
How to apply: the writable side still records normally; CAIRN_TELEMETRY=off remains the master kill switch honored per call.