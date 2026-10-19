---
type: Tribal-pattern
title: 'mcp SDK FastMCP: session manager snapshots settings on first streamable_http_app()'
description: 'mcp SDK FastMCP: session manager snapshots settings on first streamable_http_app()'
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-10-05T10:11:26Z'
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
memory_tier: archived
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-05T10:11:26Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
memory_triage: session-bookkeeping
valid_from: '2026-10-05T10:11:26Z'
---

FastMCP.streamable_http_app() creates the StreamableHTTPSessionManager lazily on the FIRST call, snapshotting stateless_http and transport_security at that moment; later settings mutations are ignored until a fresh process. Also: custom_route appends to _custom_starlette_routes unconditionally (register-once guard needed for repeated boots), and starlette TestClient's default Host header is "testserver", which the SDK's loopback DNS-rebinding protection 421-rejects — pass an explicit allowed Host (e.g. 127.0.0.1:port) on every TestClient request against a protected app.
Why: landing http-mcp-serving T004-T007; per-bind-class Host policy could not be tested by mutating settings across in-process boots, and sandbox tests failed with inexplicable 421s until the Host default was identified.
How to apply: set mcp.settings.{host,port,stateless_http,transport_security} BEFORE the first streamable_http_app() call per process; test differing transport-security via real subprocess boots; always send an explicit Host header in TestClient requests.