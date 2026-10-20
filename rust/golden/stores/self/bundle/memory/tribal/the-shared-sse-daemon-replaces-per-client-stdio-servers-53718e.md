---
type: Tribal-decision
title: The shared SSE daemon replaces per-client stdio servers
description: The shared SSE daemon replaces per-client stdio servers
resource: src/cairn/cli/serve.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:27:11Z'
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
- date: '2026-10-07T16:27:11Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes:
- memory/tribal/the-shared-sse-daemon-replaces-per-client-stdio-servers-70c2ba
memory_superseded_by: null
valid_from: '2026-10-07T16:27:11Z'
---

cairn serve start installs one launchd-managed `cairn serve --port N` process shared by every MCP client, auto-starting at login and restarting on crash via KeepAlive.
Why: the earlier one-stdio-server-per-client model had every client process holding its own connection and produced "database is locked" contention; one shared server serializes access behind the daemon.
How to apply: keep client registration pointing at the shared daemon rather than spawning per-client stdio servers, and preserve KeepAlive and idempotent start semantics.