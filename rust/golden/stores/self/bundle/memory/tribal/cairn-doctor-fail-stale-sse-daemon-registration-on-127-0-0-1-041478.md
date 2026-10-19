---
type: Tribal-mistake
title: 'cairn doctor FAIL: stale SSE daemon registration on 127.0.0.1:9876'
description: 'cairn doctor FAIL: stale SSE daemon registration on 127.0.0.1:9876'
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-09-17T14:30:46Z'
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
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-09-17T14:30:46Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: false
memory_supersedes:
- memory/raw/2026-09-17-cairn-doctor-environment-fail-sse-endpoint-127-0-0-1-9876-un-5e9218
- memory/tribal/doctor-environment-fail-agent-sse-registrations-point-at-uns-4aceb5
- memory/tribal/cairndoctorssedaemondown-538d66
- memory/tribal/cairn-doctor-fail-agent-sse-registrations-without-a-serving--c26a8a
- memory/tribal/cairn-doctor-environment-fail-stale-sse-daemon-registrations-4e30e6
- memory/tribal/cairn-doctor-environment-fail-sse-registrations-without-daem-8cd3bb
memory_superseded_by: memory/tribal/doctor-environment-fail-stale-sse-client-registrations-0e5d82
valid_from: '2026-09-17T14:30:46Z'
---

cairn doctor exits 1: claude/zcode/agy MCP registrations point at http://127.0.0.1:9876/sse but no daemon serves it. Fix before shipping: start the shared daemon (cairn serve start) or re-register with cairn install-agents --stdio. Why: the environment FAIL makes every doctor run non-zero, masking real regressions. How to apply: run cairn doctor before relying on doctor gating; treat this FAIL as environmental unless the port answers after the daemon starts.