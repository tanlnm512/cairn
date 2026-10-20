---
type: Tribal-decision
title: The live file watcher starts on the shared server boot path
description: The live file watcher starts on the shared server boot path
resource: src/cairn/mcp_server/server.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:35:55Z'
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
- date: '2026-10-07T15:35:55Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:35:55Z'
---

run() starts FileWatcherService for both stdio and SSE (unless read-only or CAIRN_WATCH=0), after the DB guard and boot catch-up, passing the server-resolved workspace/db explicitly.
Why: both transports need edit freshness within the debounce window; the graph layer must not import mcp_server, so identity travels inward by argument; the watcher writes (pending_sync plus reindex), so it never runs in the read-only daemon; the PYTEST_CURRENT_TEST guard keeps in-process test boots from leaving a real observer thread on the developer machine.
How to apply: start() stays a logged no-op when the watch extra is absent.