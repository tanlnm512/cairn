---
type: Tribal-workaround
title: Drive stdio MCP test sessions sequentially
description: Drive stdio MCP test sessions sequentially
resource: tests/test_http_transport.py
tags:
- workaround
generated:
  by: cairn/0.21.2
  at: '2026-10-08T16:52:01Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.632
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.95
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: workaround
session_origin: ''
promotion_history:
- date: '2026-10-08T16:52:01Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-08T16:52:01Z'
---

Piping a whole JSON-RPC batch into a spawned MCP stdio server races the session's post-initialize readiness under load: initialize is answered, tools/list is dropped, and the failure only shows on slow CI legs. Drive the exchange sequentially instead — send initialize, wait for its response, then the initialized notification and tools/list.
Why: the server consumes piped input on its own schedule; only sequential request/response turns make the handshake deterministic.
How to apply: in tests, use Popen plus a per-message selector wait rather than subprocess.run(input=...) batches for any stdio MCP session.