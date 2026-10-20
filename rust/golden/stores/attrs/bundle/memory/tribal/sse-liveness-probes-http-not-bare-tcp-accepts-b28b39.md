---
type: Tribal-pattern
title: SSE liveness probes HTTP, not bare TCP accepts
description: SSE liveness probes HTTP, not bare TCP accepts
resource: src/cairn/mcp_server/lifecycle.py
tags:
- pattern
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
memory_type: pattern
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

sse_responds() connects, sends GET / HTTP/1.1, and peeks one response byte; any status line (including 404) counts.
Why: the listen backlog accepts connections even when uvicorn is wedged, so a bare accept is a false positive; hitting / avoids the SSE handler request validation while still proving uvicorn parsed a request.
How to apply: treat accepted AND emitted-a-response-byte-within-timeout as the only healthy signal.