---
type: Tribal-pattern
title: Server watchdog drains buffers directly because os._exit skips atexit
description: Server watchdog drains buffers directly because os._exit skips atexit
resource: src/cairn/mcp_server/server.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:35:52Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T15:35:52Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:35:52Z'
---

_watch_parent_loop calls the per-sink flush functions directly before os._exit(0).
Why: the parent-death watchdog runs on a non-main thread and exits via os._exit(0), which bypasses atexit entirely — the telemetry sink, OTLP side buffer, tool_metrics, and queued memory embeddings would be silently lost on a normal client disconnect, and there is nothing to hook from the watchdog thread.
How to apply: keep each flush individually isolated so one failing sink cannot block the others or the exit.