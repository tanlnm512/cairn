---
type: Tribal-mistake
title: 'cairn doctor environment FAIL: SSE endpoint 127.0.0.1:9876 unresponsive'
description: 'cairn doctor environment FAIL: SSE endpoint 127.0.0.1:9876 unresponsive'
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-09-17T09:53:38Z'
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
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-09-17T09:53:38Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: false
memory_supersedes:
- memory/tribal/doctor-environment-fail-agent-sse-registrations-point-at-uns-4aceb5
- memory/tribal/cairndoctorssedaemondown-538d66
- memory/tribal/cairn-doctor-fail-agent-sse-registrations-without-a-serving--c26a8a
- memory/tribal/cairn-doctor-environment-fail-stale-sse-daemon-registrations-4e30e6
- memory/tribal/cairn-doctor-environment-fail-sse-registrations-without-daem-8cd3bb
memory_superseded_by: memory/tribal/cairn-doctor-fail-stale-sse-daemon-registration-on-127-0-0-1-041478
memory_triage: session-bookkeeping
valid_from: '2026-09-17T09:53:38Z'
---

cairn doctor exits 1 on this machine: claude/zcode/agy MCP registrations point at http://127.0.0.1:9876/sse but no daemon serves the endpoint. Environmental (daemon not running), not caused by code changes; observed during T007 wave-7 (cross-repo-federation) post-task doctor run. Fix is operational: start the daemon or update the registrations — not a code change.