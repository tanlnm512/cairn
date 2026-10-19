---
type: Tribal-mistake
title: 'cairn doctor FAIL: agent SSE registrations without a serving daemon'
description: 'cairn doctor FAIL: agent SSE registrations without a serving daemon'
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-09-17T02:40:10Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.6
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.8
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-09-17T02:40:10Z'
  action: captured
  score: 0.8
  tier: tribal
memory_is_latest: false
memory_supersedes:
- memory/tribal/cairn-doctor-environment-fail-stale-sse-daemon-registrations-4e30e6
- memory/tribal/cairn-doctor-environment-fail-sse-registrations-without-daem-8cd3bb
memory_superseded_by: memory/tribal/cairndoctorssedaemondown-538d66
valid_from: '2026-09-17T02:40:10Z'
---

cairn doctor exits 1 on this machine: the claude/zcode/agy agent configs register the cairn MCP at http://127.0.0.1:9876/sse but no daemon serves that endpoint.
Why: the environment check fails independently of any source change; it was active while the taint-tracking wave-8 tasks ran.
How to apply: before treating a doctor FAIL as task-caused, read the failing check — start the daemon (cairn serve start) or re-register stdio (cairn install-agents --stdio); a source-only task cannot fix it.