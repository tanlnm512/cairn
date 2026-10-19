---
type: Tribal-mistake
title: 'Doctor FAIL: stale /sse agent registrations predate HTTP MCP endpoint'
description: 'Doctor FAIL: stale /sse agent registrations predate HTTP MCP endpoint'
tags:
- mistake
generated:
  by: cairn/0.22.0
  at: '2026-10-18T19:04:19Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.578
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.7
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-10-18T19:04:19Z'
  action: captured
  score: 0.7
  tier: tribal
memory_is_latest: true
memory_supersedes:
- memory/tribal/cairn-doctor-fail-stale-sse-agent-registrations-point-at-ret-5d790e
- memory/tribal/doctor-environment-fail-stale-sse-client-registrations-0e5d82
- memory/tribal/cairn-doctor-fail-stale-sse-daemon-registration-on-127-0-0-1-041478
- memory/raw/2026-09-17-cairn-doctor-environment-fail-sse-endpoint-127-0-0-1-9876-un-5e9218
- memory/tribal/doctor-environment-fail-agent-sse-registrations-point-at-uns-4aceb5
- memory/tribal/cairndoctorssedaemondown-538d66
- memory/tribal/cairn-doctor-fail-agent-sse-registrations-without-a-serving--c26a8a
- memory/tribal/cairn-doctor-environment-fail-stale-sse-daemon-registrations-4e30e6
- memory/tribal/cairn-doctor-environment-fail-sse-registrations-without-daem-8cd3bb
memory_superseded_by: null
valid_from: '2026-10-18T19:04:19Z'
---

cairn doctor exits 1: ~/.zcode/cli/config.json and ~/.gemini/config/mcp_config.json register http://127.0.0.1:9876/sse but the daemon serves /mcp.

Why: those user-owned configs predate the streamable-HTTP migration.

How to apply: environmental, not from rust-migration code; fix with cairn install-agents or cairn doctor --fix.